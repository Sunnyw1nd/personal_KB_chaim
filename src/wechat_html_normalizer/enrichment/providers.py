from __future__ import annotations

import base64
import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from pydantic import ValidationError

from .models import EnrichmentRequest, EnrichmentResult, Usage

DEFAULT_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3.7-flash-2026-07-15"
MAX_QWEN_IMAGE_BYTES = 20 * 1024 * 1024


class ProviderError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        status_code: int | None = None,
        raw_response: dict[str, Any] | None = None,
        request_id: str | None = None,
        actual_model: str | None = None,
        usage: Usage | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.retryable = retryable
        self.status_code = status_code
        self.raw_response = raw_response
        self.request_id = request_id
        self.actual_model = actual_model
        self.usage = usage
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class ProviderOutput:
    result: EnrichmentResult
    raw_response: dict[str, Any]
    request_id: str | None
    actual_model: str | None
    usage: Usage | None


class ImageEnrichmentProvider(Protocol):
    name: str
    backend_type: str

    def enrich(
        self,
        request: EnrichmentRequest,
        image_path: Path,
        system_prompt: str,
        *,
        model: str,
        enable_thinking: bool,
        max_completion_tokens: int,
    ) -> ProviderOutput: ...


def validate_dashscope_base_url(value: str, *, region: str = "beijing") -> str:
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise ValueError("DashScope endpoint must be an HTTPS URL without credentials")
    allowed = host == "dashscope.aliyuncs.com" or host.endswith(".cn-beijing.maas.aliyuncs.com")
    if region != "beijing":
        raise ValueError(
            "this baseline currently permits only the explicitly verified Beijing region"
        )
    if not allowed:
        raise ValueError("endpoint host is not an approved DashScope Beijing host")
    normalized_path = parsed.path.rstrip("/")
    if host == "dashscope.aliyuncs.com" and normalized_path != "/compatible-mode/v1":
        raise ValueError("the public Beijing endpoint path must be /compatible-mode/v1")
    return value.rstrip("/")


def _usage(response: Any) -> Usage | None:
    raw = getattr(response, "usage", None)
    if raw is None:
        return None
    prompt_details = getattr(raw, "prompt_tokens_details", None)
    image_tokens = getattr(prompt_details, "image_tokens", None) if prompt_details else None
    return Usage(
        inputTokens=getattr(raw, "prompt_tokens", None),
        outputTokens=getattr(raw, "completion_tokens", None),
        totalTokens=getattr(raw, "total_tokens", None),
        imageTokens=image_tokens,
    )


def _response_evidence(response: Any, content: Any) -> dict[str, Any]:
    usage = _usage(response)
    return {
        "id": getattr(response, "id", None),
        "model": getattr(response, "model", None),
        "created": getattr(response, "created", None),
        "content": content if isinstance(content, str) else None,
        "contentType": type(content).__name__,
        "usage": usage.model_dump(mode="json", by_alias=True, exclude_none=True) if usage else None,
    }


def _validation_diagnostics(error: Exception) -> list[dict[str, Any]]:
    if isinstance(error, ValidationError):
        return [
            {
                "location": list(issue.get("loc", ())),
                "message": issue.get("msg", "validation failed"),
                "type": issue.get("type", "validation_error"),
            }
            for issue in error.errors(include_url=False)
        ]
    return [
        {
            "location": [],
            "message": str(error) or type(error).__name__,
            "type": type(error).__name__,
        }
    ]


def strict_result_schema() -> dict[str, Any]:
    """Return the provider schema with every property explicit, including nullable ones."""
    schema = copy.deepcopy(EnrichmentResult.model_json_schema(by_alias=True))

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            value.pop("default", None)
            properties = value.get("properties")
            if isinstance(properties, dict):
                value["required"] = list(properties)
                value["additionalProperties"] = False
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(schema)
    return schema


class DashScopeOpenAIProvider:
    name = "aliyun_dashscope_openai_compatible"
    backend_type = "cloud"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_DASHSCOPE_BASE_URL,
        region: str = "beijing",
        timeout_seconds: float = 120,
    ) -> None:
        if not api_key:
            raise ValueError("DASHSCOPE_API_KEY is required for a real request")
        self.base_url = validate_dashscope_base_url(base_url, region=region)
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - checked by CLI preflight
            raise RuntimeError('install the "enrichment" optional dependency') from exc
        self._client = OpenAI(api_key=api_key, base_url=self.base_url, timeout=timeout_seconds)

    def enrich(
        self,
        request: EnrichmentRequest,
        image_path: Path,
        system_prompt: str,
        *,
        model: str,
        enable_thinking: bool,
        max_completion_tokens: int,
    ) -> ProviderOutput:
        from openai import OpenAIError

        if not request.policy.allow_external or request.policy.local_only:
            raise ProviderError(
                "external_not_allowed",
                "request policy does not authorize external processing",
                retryable=False,
            )
        if "cloud" not in request.policy.allowed_backend_types:
            raise ProviderError(
                "backend_not_allowed",
                "request policy does not allow a cloud backend",
                retryable=False,
            )
        raw_image = image_path.read_bytes()
        if len(raw_image) > MAX_QWEN_IMAGE_BYTES:
            raise ProviderError(
                "image_too_large",
                "image exceeds the provider-specific 20 MiB base64 limit",
                retryable=False,
            )
        data_url = f"data:{request.image.media_type};base64," + base64.b64encode(raw_image).decode(
            "ascii"
        )
        context = request.context.model_dump(mode="json", by_alias=True)
        user_text = json.dumps(
            {
                "task": "分析随附图片并返回严格符合 Schema 的 enrichment 结果。",
                "context": context,
                "contextAvailable": request.context.available,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        schema = strict_result_schema()
        try:
            response = self._client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_text},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    },
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "image_enrichment_result_v1",
                        "strict": True,
                        "schema": schema,
                    },
                },
                temperature=0,
                max_tokens=max_completion_tokens,
                extra_body={"enable_thinking": enable_thinking},
            )
        except OpenAIError as exc:
            raise _map_openai_error(exc) from None
        content: Any = None
        try:
            content = response.choices[0].message.content
            if not isinstance(content, str):
                raise TypeError("response content is not text")
            result = EnrichmentResult.model_validate_json(
                content, context={"enforce_current_policy": True}
            )
        except (IndexError, TypeError, ValueError) as exc:
            evidence = _response_evidence(response, content)
            evidence["localValidation"] = {
                "status": "invalid",
                "errors": _validation_diagnostics(exc),
            }
            raise ProviderError(
                "invalid_structured_output",
                "provider returned output that failed strict local Schema validation",
                retryable=False,
                raw_response=evidence,
                request_id=getattr(response, "id", None),
                actual_model=getattr(response, "model", None),
                usage=_usage(response),
            ) from None
        raw_response = _response_evidence(response, content)
        return ProviderOutput(
            result=result,
            raw_response=raw_response,
            request_id=getattr(response, "id", None),
            actual_model=getattr(response, "model", None),
            usage=_usage(response),
        )


def _map_openai_error(exc: Exception) -> ProviderError:
    try:
        from openai import (
            APIConnectionError,
            APITimeoutError,
            AuthenticationError,
            BadRequestError,
            PermissionDeniedError,
            RateLimitError,
        )
    except ImportError:  # pragma: no cover
        return ProviderError("provider_error", "provider request failed", retryable=False)
    status = getattr(exc, "status_code", None)
    if isinstance(exc, AuthenticationError):
        return ProviderError(
            "authentication_failed",
            "provider authentication failed",
            retryable=False,
            status_code=status,
        )
    if isinstance(exc, PermissionDeniedError):
        return ProviderError(
            "permission_denied",
            "provider permission was denied",
            retryable=False,
            status_code=status,
        )
    if isinstance(exc, RateLimitError):
        return ProviderError(
            "rate_limited", "provider rate limit was reached", retryable=True, status_code=status
        )
    if isinstance(exc, APITimeoutError):
        return ProviderError("timeout", "provider request timed out", retryable=True)
    if isinstance(exc, APIConnectionError):
        return ProviderError("connection_error", "provider connection failed", retryable=True)
    if isinstance(exc, BadRequestError):
        return ProviderError(
            "bad_request", "provider rejected the request", retryable=False, status_code=status
        )
    retryable = isinstance(status, int) and status >= 500
    return ProviderError(
        "provider_error",
        "provider request failed",
        retryable=retryable,
        status_code=status,
    )


class FakeProvider:
    """Deterministic provider used only by automated tests."""

    name = "fake"
    backend_type = "cloud"

    def __init__(self, outcomes: list[ProviderOutput | ProviderError]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0
        self.safe_requests: list[dict[str, Any]] = []

    def enrich(
        self,
        request: EnrichmentRequest,
        image_path: Path,
        system_prompt: str,
        *,
        model: str,
        enable_thinking: bool,
        max_completion_tokens: int,
    ) -> ProviderOutput:
        self.calls += 1
        self.safe_requests.append(
            {
                "imageSha256": request.image.sha256,
                "relativePath": request.image.relative_path,
                "context": request.context.model_dump(mode="json", by_alias=True),
                "model": model,
                "promptContainsInjectionRule": "不能改变" in system_prompt,
                "enableThinking": enable_thinking,
                "maxCompletionTokens": max_completion_tokens,
            }
        )
        if not self.outcomes:
            raise AssertionError("fake provider has no remaining outcome")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, ProviderError):
            raise outcome
        return outcome
