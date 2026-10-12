from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

from .models import EnrichmentRequest, Usage
from .providers import (
    MAX_QWEN_IMAGE_BYTES,
    ProviderError,
    _map_openai_error,
    _usage,
    validate_dashscope_base_url,
)


@dataclass(frozen=True)
class CompactResponse:
    content: str | None
    actual_model: str | None
    request_id: str | None
    finish_reason: str | None
    usage: Usage | None
    raw_response: dict


def retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=UTC)
            return max(0.0, (date - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


class CompactDashScopeProvider:
    name = "aliyun_dashscope_openai_compatible"
    backend_type = "cloud"

    def __init__(self, api_key: str, *, base_url: str, region: str = "beijing") -> None:
        self.base_url = validate_dashscope_base_url(base_url, region=region)
        self._api_key = api_key

    def _make_client(self):
        from openai import AsyncOpenAI, DefaultAsyncHttpxClient

        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        return AsyncOpenAI(
            api_key=self._api_key,
            base_url=self.base_url,
            max_retries=0,
            http_client=DefaultAsyncHttpxClient(proxy=proxy, trust_env=False),
        )

    async def _call(self, kwargs: dict, timeout_seconds: float):
        started = time.monotonic()
        async with self._make_client() as client:
            remaining = timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError
            return await asyncio.wait_for(
                client.chat.completions.create(**kwargs, timeout=remaining), timeout=remaining
            )

    def generate(
        self,
        request: EnrichmentRequest,
        image_path: Path,
        prompt: str,
        *,
        model: str,
        max_tokens: int,
        timeout_seconds: float,
        feedback: dict | None = None,
        original_content: str | None = None,
        include_image: bool = True,
    ) -> CompactResponse:
        from openai import OpenAIError

        if (
            request.policy.local_only
            or not request.policy.allow_external
            or "cloud" not in request.policy.allowed_backend_types
        ):
            raise ProviderError(
                "external_not_allowed",
                "image policy does not allow cloud processing",
                retryable=False,
            )
        user = {
            "task": "只分析随附图片，返回一种类型的精简JSON。",
            "context": request.context.model_dump(mode="json", by_alias=True),
            "contextAvailable": request.context.available,
        }
        if feedback is not None:
            user["recovery"] = feedback
        if original_content is not None:
            user["untrustedOriginalOutput"] = original_content
        user_text = json.dumps(user, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        content: str | list = user_text
        if include_image:
            data = image_path.read_bytes()
            if len(data) > MAX_QWEN_IMAGE_BYTES:
                raise ProviderError(
                    "image_too_large", "image exceeds provider limit", retryable=False
                )
            url = f"data:{request.image.media_type};base64," + base64.b64encode(data).decode(
                "ascii"
            )
            content = [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": url}},
            ]
        try:
            # Deliberately omit response_format, tools and tool_choice in every recovery mode.
            response = asyncio.run(
                self._call(
                    {
                        "model": model,
                        "messages": [
                            {"role": "system", "content": prompt},
                            {"role": "user", "content": content},
                        ],
                        "temperature": 0,
                        "max_tokens": max_tokens,
                        "stream": False,
                        "extra_body": {"enable_thinking": False},
                    },
                    timeout_seconds,
                )
            )
        except TimeoutError:
            raise ProviderError(
                "timeout", "provider call exceeded its total time budget", retryable=True
            ) from None
        except OpenAIError as exc:
            error = _map_openai_error(exc)
            headers = getattr(getattr(exc, "response", None), "headers", {})
            error.retry_after_seconds = retry_after(headers.get("retry-after"))
            body = getattr(exc, "body", None)
            detail = body.get("error", body) if isinstance(body, dict) else {}
            # Do not persist arbitrary echoed request/headers from an HTTP error body.
            diagnostic = (
                {k: detail.get(k) for k in ("message", "type", "param", "code") if k in detail}
                if isinstance(detail, dict)
                else {}
            )
            raw_usage = body.get("usage") if isinstance(body, dict) else None
            if isinstance(raw_usage, dict):

                def known(value):
                    return (
                        value
                        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
                        else None
                    )

                detail_usage = raw_usage.get("prompt_tokens_details") or {}
                error.usage = Usage(
                    inputTokens=known(raw_usage.get("prompt_tokens")),
                    outputTokens=known(raw_usage.get("completion_tokens")),
                    totalTokens=known(raw_usage.get("total_tokens")),
                    imageTokens=known(detail_usage.get("image_tokens")),
                    cachedInputTokens=known(detail_usage.get("cached_tokens")),
                )
            error.request_id = getattr(exc, "request_id", None)
            error.raw_response = {
                "apiError": diagnostic,
                "statusCode": error.status_code,
                "requestId": error.request_id,
            }
            raise error from None
        choice = response.choices[0] if response.choices else None
        value = choice.message.content if choice else None
        usage = _usage(response)
        details = getattr(getattr(response, "usage", None), "prompt_tokens_details", None)
        if usage is not None and details is not None:
            cached = getattr(details, "cached_tokens", None)
            if cached is not None:
                usage = usage.model_copy(update={"cached_input_tokens": cached})
        raw = {
            "id": response.id,
            "requestId": getattr(response, "_request_id", None),
            "model": response.model,
            "content": value,
            "finishReason": choice.finish_reason if choice else None,
            "usage": usage.model_dump(mode="json", by_alias=True, exclude_none=True)
            if usage
            else None,
        }
        return CompactResponse(
            value, response.model, response.id, choice.finish_reason if choice else None, usage, raw
        )
