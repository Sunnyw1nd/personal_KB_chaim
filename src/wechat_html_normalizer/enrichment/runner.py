from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import uuid
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .dataset import DatasetSummary, PreparedImage, verify_unchanged
from .models import (
    RUN_RECORD_SCHEMA_VERSION,
    BatchRunItem,
    BatchRunManifest,
    BatchRunMetrics,
    EnrichmentAttemptRecord,
    EnrichmentRunRecord,
    RunStatus,
    Usage,
    validate_current_result,
)
from .providers import ImageEnrichmentProvider, ProviderError
from .security import redact_sensitive_object

PROCESSOR = "image_enrichment"
RUN_SCHEMA_VERSION = "1.0.0"
_RUN_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def stable_sha(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_prompt(path: Path) -> tuple[str, str]:
    raw = path.resolve(strict=True).read_bytes()
    text = raw.decode("utf-8")
    return text, hashlib.sha256(raw).hexdigest()


def cache_key(
    item: PreparedImage,
    *,
    provider: str,
    model: str,
    prompt_sha256: str,
    region: str,
    enable_thinking: bool,
    max_completion_tokens: int,
) -> str:
    return stable_sha(
        {
            "imageSha256": item.request.image.sha256,
            "provider": provider,
            "model": model,
            "promptVersion": item.request.prompt_version,
            "promptSha256": prompt_sha256,
            "resultSchemaVersion": item.request.result_schema_version,
            "contextFingerprintSha256": item.request.context_fingerprint_sha256,
            "region": region,
            "enableThinking": enable_thinking,
            "maxCompletionTokens": max_completion_tokens,
            "temperature": 0,
            "responseFormat": "json_schema_strict",
        }
    )


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Same-directory atomic publication without repeating long final filenames.
    temp = path.with_name(f".{uuid.uuid4().hex}.tmp")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, path)


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def inspect_cache(path: Path, expected_key: str) -> tuple[EnrichmentRunRecord | None, str | None]:
    """Return a reusable record or a stable reason to preserve but reject this cache."""
    if not path.exists() and not path.is_symlink():
        return None, None
    if path.is_symlink() or not path.is_dir():
        return None, "unsafe_cached_path"
    record_path = path / "record.json"
    raw_path = path / "raw-response.json"
    if (
        record_path.is_symlink()
        or raw_path.is_symlink()
        or not record_path.is_file()
        or not raw_path.is_file()
    ):
        return None, "incomplete_cached_record"
    try:
        record = EnrichmentRunRecord.model_validate_json(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "invalid_cached_record"
    if record.cache_key != expected_key or record.status != RunStatus.SUCCESS:
        return None, "cache_identity_or_status_mismatch"
    if record.result is None:
        return None, "invalid_cached_record"
    from .models import ReviewStatus
    from .reviews import read_review

    review, review_issue = read_review(path.parent.parent, expected_key)
    if review_issue:
        return None, review_issue
    if review is not None:
        record = record.model_copy(update={"review_status": review})
    try:
        validate_current_result(record.result)
    except ValueError:
        return None, "cache_business_validation_failed"
    if record.processing_profile == "compact-json-v1":
        from .compact import parse_compact

        try:
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
            if raw.get("finishReason") != "stop" or raw.get("model") != record.requested_model:
                return None, "compact_cache_response_invalid"
            _, mapped, _ = parse_compact(raw["content"])
            expected = record.result.model_dump(mode="json", by_alias=True)
            actual = mapped.model_dump(mode="json", by_alias=True)
            if "credential_like_text_redacted_before_persistence" in expected.get("warnings", []):
                actual["warnings"] = expected["warnings"]
            if actual != expected:
                return None, "compact_cache_mapping_mismatch"
        except (OSError, ValueError, KeyError, TypeError):
            return None, "compact_cache_validation_failed"
    if review == ReviewStatus.REJECTED or record.review_status == ReviewStatus.REJECTED:
        return None, "cache_review_rejected"
    return record, None


def _publish_success(
    parent: Path,
    name: str,
    record: EnrichmentRunRecord,
    raw_response: dict,
    attempt_responses: dict[str, dict] | None = None,
) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / name
    temp = parent / f".tmp-{uuid.uuid4().hex}"
    temp.mkdir()
    try:
        _atomic_json(temp / "raw-response.json", raw_response)
        for relative_path, response in sorted((attempt_responses or {}).items()):
            _atomic_json(temp / Path(*relative_path.split("/")), response)
        _atomic_json(
            temp / "record.json",
            record.model_dump(mode="json", by_alias=True, exclude_none=True),
        )
        if target.exists():
            existing, issue = inspect_cache(target, record.cache_key)
            if existing is not None:
                return target
            raise FileExistsError(f"existing cache is {issue}; refusing to replace historical data")
        os.replace(temp, target)
        return target
    finally:
        if temp.exists():
            shutil.rmtree(temp)


def _publish_failure(
    parent: Path,
    name: str,
    record: EnrichmentRunRecord,
    attempt_responses: dict[str, dict],
) -> Path:
    """Atomically publish one failed task and all retained attempt evidence."""
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / name
    if target.exists():
        raise FileExistsError(f"failed task output already exists: {target}")
    temp = parent / f".tmp-{uuid.uuid4().hex}"
    temp.mkdir()
    try:
        for relative_path, response in sorted(attempt_responses.items()):
            _atomic_json(temp / Path(*relative_path.split("/")), response)
        _atomic_json(
            temp / "record.json",
            record.model_dump(mode="json", by_alias=True, exclude_none=True),
        )
        os.replace(temp, target)
        return target
    finally:
        if temp.exists():
            shutil.rmtree(temp)


def _status(error: ProviderError) -> RunStatus:
    return {
        "authentication_failed": RunStatus.AUTHENTICATION_FAILED,
        "rate_limited": RunStatus.RATE_LIMITED,
        "timeout": RunStatus.TIMEOUT,
        "invalid_structured_output": RunStatus.INVALID_OUTPUT,
        "external_not_allowed": RunStatus.POLICY_REJECTED,
        "backend_not_allowed": RunStatus.POLICY_REJECTED,
    }.get(error.code, RunStatus.PROVIDER_ERROR)


def _usage_sum(values: list[Usage]) -> Usage | None:
    if not values:
        return None

    def total(field: str) -> int | None:
        numbers = [getattr(value, field) for value in values if getattr(value, field) is not None]
        return sum(numbers) if numbers else None

    return Usage(
        inputTokens=total("input_tokens"),
        outputTokens=total("output_tokens"),
        totalTokens=total("total_tokens"),
        imageTokens=total("image_tokens"),
        cachedInputTokens=total("cached_input_tokens"),
    )


def _new_run_id(value: str | None) -> str:
    if value is None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        return f"run-{stamp}-{uuid.uuid4().hex[:6]}"
    if not _RUN_NAME.fullmatch(value):
        raise ValueError(
            "run name must use 1-80 ASCII letters, digits, dots, dashes or underscores"
        )
    return value


def _configuration(
    *,
    provider_name: str,
    model: str,
    prompt_sha256: str,
    inventory_sha256: str,
    region: str,
    enable_thinking: bool,
    max_completion_tokens: int,
    max_retries: int,
    max_structured_retries: int,
    force: bool,
    dry_run: bool,
) -> dict:
    return {
        "provider": provider_name,
        "model": model,
        "promptSha256": prompt_sha256,
        "inventorySha256": inventory_sha256,
        "region": region,
        "enableThinking": enable_thinking,
        "maxCompletionTokens": max_completion_tokens,
        "maxRetries": max_retries,
        "maxStructuredRetries": max_structured_retries,
        "force": force,
        "dryRun": dry_run,
    }


def run_batch(
    prepared: list[PreparedImage],
    dataset: DatasetSummary,
    *,
    inventory_sha256: str,
    output_root: Path,
    prompt: str,
    prompt_sha256: str,
    provider: ImageEnrichmentProvider | None,
    provider_name: str,
    model: str,
    endpoint: str | None,
    region: str,
    enable_thinking: bool = False,
    max_completion_tokens: int = 4096,
    max_retries: int = 2,
    max_structured_retries: int = 1,
    requests_per_minute: float = 12,
    dry_run: bool = False,
    force: bool = False,
    run_name: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[Path, BatchRunManifest]:
    output_root = output_root.resolve()
    if max_retries < 0:
        raise ValueError("max_retries cannot be negative")
    if max_structured_retries < 0:
        raise ValueError("max_structured_retries cannot be negative")
    output_root.mkdir(parents=True, exist_ok=True)
    for item in prepared:
        if output_root == item.path.parent or output_root.is_relative_to(item.path.parent):
            raise ValueError("enrichment output must not be inside the image input directory")
    run_id = _new_run_id(run_name)
    run_directory = output_root / "runs" / run_id
    if run_directory.exists():
        raise FileExistsError(
            f"run already exists: {run_id}; choose a new name (cached results will still be reused)"
        )
    run_directory.mkdir(parents=True)
    cache_root = output_root / "cache"
    config = _configuration(
        provider_name=provider_name,
        model=model,
        prompt_sha256=prompt_sha256,
        inventory_sha256=inventory_sha256,
        region=region,
        enable_thinking=enable_thinking,
        max_completion_tokens=max_completion_tokens,
        max_retries=max_retries,
        max_structured_retries=max_structured_retries,
        force=force,
        dry_run=dry_run,
    )
    created_at = utc_now()
    items: list[BatchRunItem] = []
    direct_usage: list[Usage] = []
    actual_models: set[str] = set()
    metric_values = {
        "imageTasks": len(prepared),
        "apiCalls": 0,
        "firstAttemptSuccesses": 0,
        "retrySuccesses": 0,
        "structuredRetrySuccesses": 0,
        "finalFailures": 0,
        "structuredOutputFailures": 0,
        "cacheHits": 0,
        "apiCallsWithUsage": 0,
        "apiCallsWithoutUsage": 0,
    }
    manifest = BatchRunManifest(
        schemaVersion=RUN_SCHEMA_VERSION,
        runId=run_id,
        status="in_progress",
        createdAt=created_at,
        provider=provider_name,
        requestedModel=model,
        actualModels=[],
        endpoint=endpoint,
        region=region,
        promptVersion=prepared[0].request.prompt_version if prepared else "image-enrichment-v1",
        promptSha256=prompt_sha256,
        resultSchemaVersion=(prepared[0].request.result_schema_version if prepared else "1.0.0"),
        inventorySha256=inventory_sha256,
        configurationFingerprintSha256=stable_sha(config),
        enableThinking=enable_thinking,
        force=force,
        dryRun=dry_run,
        dataset={
            "files": dataset.files,
            "selected": dataset.selected,
            "uniqueImages": dataset.unique_images,
            "withContext": dataset.with_context,
            "withAssetReferences": dataset.with_asset_references,
            "localOnly": dataset.local_only,
        },
        summary={},
        metrics=BatchRunMetrics(**metric_values),
        items=[],
        notes=[
            "Human benchmark labels and reference answers were not read by the inference runner.",
            "Cost was not calculated; returned usage is preserved when available.",
            (
                "Manifest usage is the sum of provider usage fields that were returned; "
                "metrics reports calls without usage instead of treating unknown usage as zero."
            ),
        ],
    )
    manifest_path = run_directory / "run-manifest.json"
    _atomic_json(manifest_path, manifest.model_dump(mode="json", by_alias=True, exclude_none=True))
    last_request_started = 0.0
    for item in prepared:
        key = cache_key(
            item,
            provider=provider_name,
            model=model,
            prompt_sha256=prompt_sha256,
            region=region,
            enable_thinking=enable_thinking,
            max_completion_tokens=max_completion_tokens,
        )
        request_path = run_directory / "requests" / (item.request.image.sha256 + ".json")
        _atomic_json(
            request_path,
            item.request.model_dump(mode="json", by_alias=True, exclude_none=True),
        )
        request_relative = _relative(request_path, output_root)
        cached, cache_issue = (None, None) if force else inspect_cache(cache_root / key, key)
        if cached is not None:
            metric_values["cacheHits"] += 1
            batch_item = BatchRunItem(
                imageSha256=item.request.image.sha256,
                relativePath=item.request.image.relative_path,
                cacheKey=key,
                status=RunStatus.CACHED,
                requestPath=request_relative,
                recordPath=_relative(cache_root / key / "record.json", output_root),
            )
        elif cache_issue is not None:
            metric_values["finalFailures"] += 1
            historical_record = cache_root / key / "record.json"
            batch_item = BatchRunItem(
                imageSha256=item.request.image.sha256,
                relativePath=item.request.image.relative_path,
                cacheKey=key,
                status=RunStatus.INVALID_OUTPUT,
                requestPath=request_relative,
                recordPath=(
                    _relative(historical_record, output_root)
                    if historical_record.is_file() and not historical_record.is_symlink()
                    else None
                ),
                errorCode=cache_issue,
            )
        elif item.request.policy.local_only or not item.request.policy.allow_external:
            batch_item = BatchRunItem(
                imageSha256=item.request.image.sha256,
                relativePath=item.request.image.relative_path,
                cacheKey=key,
                status=RunStatus.POLICY_REJECTED,
                requestPath=request_relative,
                errorCode=item.request.policy.reason_code,
            )
        elif dry_run:
            batch_item = BatchRunItem(
                imageSha256=item.request.image.sha256,
                relativePath=item.request.image.relative_path,
                cacheKey=key,
                status=RunStatus.DRY_RUN,
                requestPath=request_relative,
            )
        elif provider is None:
            batch_item = BatchRunItem(
                imageSha256=item.request.image.sha256,
                relativePath=item.request.image.relative_path,
                cacheKey=key,
                status=RunStatus.SKIPPED,
                requestPath=request_relative,
                errorCode="provider_unavailable",
            )
        else:
            started = time.monotonic()
            attempt_records: list[EnrichmentAttemptRecord] = []
            attempt_responses: dict[str, dict] = {}
            transport_retries = 0
            structured_retries = 0
            error: ProviderError | None = None
            output = None
            while True:
                if requests_per_minute > 0 and last_request_started:
                    wait = 60 / requests_per_minute - (time.monotonic() - last_request_started)
                    if wait > 0:
                        sleep(wait)
                call_started = time.monotonic()
                last_request_started = call_started
                attempt_number = len(attempt_records) + 1
                metric_values["apiCalls"] += 1
                try:
                    output = provider.enrich(
                        item.request,
                        item.path,
                        prompt,
                        model=model,
                        enable_thinking=enable_thinking,
                        max_completion_tokens=max_completion_tokens,
                    )
                    call_elapsed_ms = round((time.monotonic() - call_started) * 1000)
                    if output.actual_model:
                        actual_models.add(output.actual_model)
                    if output.usage:
                        direct_usage.append(output.usage)
                        metric_values["apiCallsWithUsage"] += 1
                    else:
                        metric_values["apiCallsWithoutUsage"] += 1
                    attempt_records.append(
                        EnrichmentAttemptRecord(
                            attemptNumber=attempt_number,
                            structuredRetryNumber=structured_retries,
                            transportRetryNumber=transport_retries,
                            status=RunStatus.SUCCESS,
                            completedAt=utc_now(),
                            elapsedMs=call_elapsed_ms,
                            providerResponseReceived=True,
                            requestId=output.request_id,
                            actualModel=output.actual_model,
                            usage=output.usage,
                            rawResponsePath="raw-response.json",
                        )
                    )
                    break
                except ProviderError as exc:
                    error = exc
                    call_elapsed_ms = round((time.monotonic() - call_started) * 1000)
                    if exc.actual_model:
                        actual_models.add(exc.actual_model)
                    if exc.usage:
                        direct_usage.append(exc.usage)
                        metric_values["apiCallsWithUsage"] += 1
                    else:
                        metric_values["apiCallsWithoutUsage"] += 1
                    if exc.code == "invalid_structured_output":
                        metric_values["structuredOutputFailures"] += 1
                    raw_response_path: str | None = None
                    if exc.raw_response is not None:
                        safe_error_response, response_redactions = redact_sensitive_object(
                            exc.raw_response
                        )
                        if response_redactions and isinstance(safe_error_response, dict):
                            safe_error_response["redactionCount"] = response_redactions
                        raw_response_path = (
                            f"attempts/attempt-{attempt_number:03d}.raw-response.json"
                        )
                        attempt_responses[raw_response_path] = safe_error_response
                    attempt_records.append(
                        EnrichmentAttemptRecord(
                            attemptNumber=attempt_number,
                            structuredRetryNumber=structured_retries,
                            transportRetryNumber=transport_retries,
                            status=_status(exc),
                            completedAt=utc_now(),
                            elapsedMs=call_elapsed_ms,
                            providerResponseReceived=bool(
                                exc.raw_response is not None
                                or exc.request_id
                                or exc.usage is not None
                            ),
                            requestId=exc.request_id,
                            actualModel=exc.actual_model,
                            usage=exc.usage,
                            rawResponsePath=raw_response_path,
                            errorCode=exc.code,
                            errorMessage=exc.safe_message,
                        )
                    )
                    if (
                        exc.code == "invalid_structured_output"
                        and structured_retries < max_structured_retries
                    ):
                        structured_retries += 1
                        continue
                    if exc.retryable and transport_retries < max_retries:
                        transport_retries += 1
                        sleep(min(2**transport_retries, 8))
                        continue
                    break
            elapsed_ms = round((time.monotonic() - started) * 1000)
            if output is not None:
                safe_result_value, result_redactions = redact_sensitive_object(
                    output.result.model_dump(mode="json", by_alias=True)
                )
                safe_result = output.result.model_validate(safe_result_value)
                safe_raw_response, raw_redactions = redact_sensitive_object(output.raw_response)
                if len(attempt_records) == 1:
                    metric_values["firstAttemptSuccesses"] += 1
                else:
                    metric_values["retrySuccesses"] += 1
                    if structured_retries:
                        metric_values["structuredRetrySuccesses"] += 1
                record = EnrichmentRunRecord(
                    schemaVersion=RUN_RECORD_SCHEMA_VERSION,
                    cacheKey=key,
                    imageSha256=item.request.image.sha256,
                    status=RunStatus.SUCCESS,
                    processor=PROCESSOR,
                    provider=provider_name,
                    requestedModel=model,
                    actualModel=output.actual_model,
                    promptVersion=item.request.prompt_version,
                    promptSha256=prompt_sha256,
                    resultSchemaVersion=item.request.result_schema_version,
                    processedAt=utc_now(),
                    requestId=output.request_id,
                    contextFingerprintSha256=item.request.context_fingerprint_sha256,
                    elapsedMs=elapsed_ms,
                    attempts=len(attempt_records),
                    retryCount=len(attempt_records) - 1,
                    structuredRetryCount=structured_retries,
                    transportRetryCount=transport_retries,
                    attemptRecords=attempt_records,
                    enableThinking=enable_thinking,
                    usage=output.usage,
                    taskUsage=_usage_sum(
                        [attempt.usage for attempt in attempt_records if attempt.usage]
                    ),
                    result=safe_result,
                    rawResponsePath="raw-response.json",
                )
                if result_redactions or raw_redactions:
                    assert record.result is not None
                    record = record.model_copy(
                        update={
                            "result": record.result.model_copy(
                                update={
                                    "warnings": [
                                        *record.result.warnings,
                                        "credential_like_text_redacted_before_persistence",
                                    ]
                                }
                            )
                        }
                    )
                if force:
                    record_directory = _publish_success(
                        run_directory / "forced-records",
                        item.request.image.sha256,
                        record,
                        safe_raw_response,
                        attempt_responses,
                    )
                else:
                    record_directory = _publish_success(
                        cache_root,
                        key,
                        record,
                        safe_raw_response,
                        attempt_responses,
                    )
                batch_item = BatchRunItem(
                    imageSha256=item.request.image.sha256,
                    relativePath=item.request.image.relative_path,
                    cacheKey=key,
                    status=RunStatus.SUCCESS,
                    requestPath=request_relative,
                    recordPath=_relative(record_directory / "record.json", output_root),
                )
            else:
                assert error is not None
                metric_values["finalFailures"] += 1
                final_attempt = attempt_records[-1]
                record = EnrichmentRunRecord(
                    schemaVersion=RUN_RECORD_SCHEMA_VERSION,
                    cacheKey=key,
                    imageSha256=item.request.image.sha256,
                    status=_status(error),
                    processor=PROCESSOR,
                    provider=provider_name,
                    requestedModel=model,
                    actualModel=error.actual_model,
                    promptVersion=item.request.prompt_version,
                    promptSha256=prompt_sha256,
                    resultSchemaVersion=item.request.result_schema_version,
                    processedAt=utc_now(),
                    requestId=error.request_id,
                    contextFingerprintSha256=item.request.context_fingerprint_sha256,
                    elapsedMs=elapsed_ms,
                    attempts=len(attempt_records),
                    retryCount=max(0, len(attempt_records) - 1),
                    structuredRetryCount=structured_retries,
                    transportRetryCount=transport_retries,
                    attemptRecords=attempt_records,
                    enableThinking=enable_thinking,
                    usage=error.usage,
                    taskUsage=_usage_sum(
                        [attempt.usage for attempt in attempt_records if attempt.usage]
                    ),
                    rawResponsePath=final_attempt.raw_response_path,
                    errorCode=error.code,
                    errorMessage=error.safe_message,
                )
                failure_directory = _publish_failure(
                    run_directory / "failed-records",
                    item.request.image.sha256,
                    record,
                    attempt_responses,
                )
                batch_item = BatchRunItem(
                    imageSha256=item.request.image.sha256,
                    relativePath=item.request.image.relative_path,
                    cacheKey=key,
                    status=record.status,
                    requestPath=request_relative,
                    recordPath=_relative(failure_directory / "record.json", output_root),
                    errorCode=error.code,
                )
        items.append(batch_item)
        counts = Counter(entry.status.value for entry in items)
        manifest = manifest.model_copy(
            update={
                "items": items,
                "summary": dict(sorted(counts.items())),
                "actual_models": sorted(actual_models),
                "usage": _usage_sum(direct_usage),
                "metrics": BatchRunMetrics(**metric_values),
            }
        )
        _atomic_json(
            manifest_path, manifest.model_dump(mode="json", by_alias=True, exclude_none=True)
        )
    verify_unchanged(prepared)
    final_status = "dry_run" if dry_run else "completed"
    manifest = manifest.model_copy(update={"status": final_status, "completed_at": utc_now()})
    _atomic_json(manifest_path, manifest.model_dump(mode="json", by_alias=True, exclude_none=True))
    return manifest_path, manifest
