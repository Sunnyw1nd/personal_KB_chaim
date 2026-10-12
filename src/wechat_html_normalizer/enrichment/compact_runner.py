"""Bounded recovery for compact JSON; standard run/cache records remain the public output."""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .compact import (
    COMPACT_ADAPTER,
    MAPPING_VERSION,
    PROFILE,
    RECOVERY_VERSION,
    VALIDATION_VERSION,
    CompactOutputError,
    correction_differences,
    parse_compact,
)
from .compact_provider import CompactResponse
from .dataset import DatasetSummary, PreparedImage, _linked, verify_unchanged
from .models import (
    BatchRunItem,
    BatchRunManifest,
    BatchRunMetrics,
    EnrichmentAttemptRecord,
    EnrichmentRunRecord,
    ReviewStatus,
    RunStatus,
)
from .providers import ProviderError
from .runner import (
    PROCESSOR,
    _atomic_json,
    _new_run_id,
    _publish_success,
    _relative,
    _usage_sum,
    inspect_cache,
    stable_sha,
    utc_now,
)
from .security import redact_sensitive_object


def profile_config(
    *,
    model: str,
    endpoint: str | None,
    prompt_sha256: str,
    max_tokens: int,
    hard_token_limit: int,
    task_timeout: float,
) -> dict:
    if not 1 <= max_tokens <= hard_token_limit <= 8192:
        raise ValueError("compact token budget requires 1 <= initial <= hard limit <= 8192")
    if not 1 <= task_timeout <= 600:
        raise ValueError("compact task timeout must be between 1 and 600 seconds")
    return {
        "profile": PROFILE,
        "model": model,
        "endpoint": endpoint,
        "promptSha256": prompt_sha256,
        "schemaSha256": stable_sha(COMPACT_ADAPTER.json_schema()),
        "mappingVersion": MAPPING_VERSION,
        "validationVersion": VALIDATION_VERSION,
        "recoveryVersion": RECOVERY_VERSION,
        "responseFormat": "omitted",
        "temperature": 0,
        "enableThinking": False,
        "initialMaxTokens": max_tokens,
        "hardTokenLimit": hard_token_limit,
        "taskTimeoutSeconds": task_timeout,
        "perCallTimeoutSeconds": 90,
        "maxCalls": 3,
        "maxBatchCalls": 18,
        "transportRecoveryLimit": 1,
        "resultRecoveryLimit": 1,
        "sdkRetries": 0,
    }


def compact_cache_key(item: PreparedImage, config: dict) -> str:
    return stable_sha(
        {
            "imageSha256": item.request.image.sha256,
            "contextFingerprintSha256": item.request.context_fingerprint_sha256,
            "promptVersion": PROFILE,
            "resultSchemaVersion": "1.0.0",
            **config,
        }
    )


def _task_directory(run_directory: Path, image_sha256: str) -> Path:
    return run_directory / "tasks" / ("image-" + image_sha256[:12])


def _write(path: Path, value: Any) -> None:
    safe, _ = redact_sensitive_object(value)
    _atomic_json(path, safe)


def _temporary(error: ProviderError) -> bool:
    return error.code in {"timeout", "connection_error", "rate_limited"} or (
        error.status_code in {500, 502, 503, 504} and error.retryable
    )


def _permanent(error: ProviderError) -> bool:
    return (
        error.code
        in {"authentication_failed", "permission_denied", "bad_request", "model_mismatch"}
        or error.status_code == 404
    )


def _policy_ok(item: PreparedImage, policy_path: Path | None) -> bool:
    policy = item.request.policy
    if (
        policy.local_only
        or not policy.allow_external
        or "cloud" not in policy.allowed_backend_types
    ):
        return False
    if policy_path is not None:
        data = json.loads(policy_path.read_text(encoding="utf-8"))
        if data.get("schemaVersion") != "1.0.0":
            raise ValueError("unsupported policy version")
        if data.get("images", {}).get(item.request.image.sha256, {}).get("localOnly"):
            return False
    return True


def _structure_only(parsed: dict | None, issues: list[dict]) -> bool:
    if not isinstance(parsed, dict):
        return False
    # Missing/invalid visual payloads or image-type ambiguity require seeing the image again.
    image_type = parsed.get("imageType")
    body_key = (
        "codeText"
        if image_type == "code"
        else (
            "description"
            if image_type in {"architecture_flow", "ui_screenshot", "photo_scene", "chart"}
            else (
                "rows"
                if image_type == "table"
                else "extractedText"
                if image_type in {"text_image", "decorative_text", "logo_mark"}
                else None
            )
        )
    )
    if body_key is None or not parsed.get(body_key):
        return False
    metadata = {"knowledgeValue", "language", "uncertainties", "retrievalDecision"}
    for issue in issues:
        path = issue.get("path") or []
        if not path:
            return False
        field = path[-1]
        if field in metadata:
            continue
        if issue.get("type") == "extra_forbidden" and parsed.get(field) in (None, "", [], {}):
            continue
        return False
    return bool(issues)


def process_task(
    item: PreparedImage,
    *,
    key: str,
    provider: Any,
    prompt: str,
    config: dict,
    task_dir: Path,
    policy_path: Path | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    call_limit: int = 3,
) -> dict:
    """One first call + at most one transport and one result recovery, all sharing a deadline."""
    started = clock()
    deadline = started + config["taskTimeoutSeconds"]
    attempts: list[EnrichmentAttemptRecord] = []
    raws: dict[str, dict] = {}
    feedback = None
    original_content = None
    include_image = True
    mode = "initial"
    tokens = config["initialMaxTokens"]
    transport_used = 0
    result_used = 0
    result = None
    final_error: str | None = None
    issues: list[dict] = []
    last_response: CompactResponse | None = None
    previous_parsed = None
    review = ReviewStatus.PENDING
    halt_batch = False
    for number in range(1, min(3, call_limit) + 1):
        if clock() >= deadline:
            final_error = "task_deadline_exhausted"
            break
        if not _policy_ok(item, policy_path):
            final_error = "external_not_allowed"
            break
        verify_unchanged([item])
        reservation = task_dir / f"attempt-{number:03d}.reservation.json"
        if reservation.exists():
            raise FileExistsError(
                "attempt already reserved; use offline recovery instead of resending"
            )
        _write(
            reservation,
            {
                "attemptNumber": number,
                "cacheKey": key,
                "createdAt": utc_now(),
                "recoveryMode": mode,
                "structuredRetryNumber": result_used,
                "transportRetryNumber": transport_used,
                "maxTokens": tokens,
                "includeImage": include_image,
                "remainingSeconds": max(0, deadline - clock()),
                "feedback": feedback,
                "originalContentPersisted": False,
            },
        )
        call_started = clock()
        response = None
        error = None
        raw_path = f"attempts/attempt-{number:03d}.raw-response.json"
        try:
            response = provider.generate(
                item.request,
                item.path,
                prompt,
                model=config["model"],
                max_tokens=tokens,
                timeout_seconds=min(config["perCallTimeoutSeconds"], deadline - clock()),
                feedback=feedback,
                original_content=original_content,
                include_image=include_image,
            )
        except ProviderError as exc:
            error = exc
        elapsed = max(0, round((clock() - call_started) * 1000))
        metadata: dict = {
            "receivedAt": utc_now(),
            "attemptNumber": number,
            "recoveryMode": mode,
            "maxTokens": tokens,
            "includeImage": include_image,
            "elapsedMs": elapsed,
            "structuredRetryNumber": result_used,
            "transportRetryNumber": transport_used,
        }
        # Publish the received response before parsing or any aggregate update.
        if response is not None:
            raw = {
                **response.raw_response,
                **metadata,
                "content": response.content,
                "model": response.actual_model,
                "requestId": response.request_id,
                "finishReason": response.finish_reason,
                "usage": response.usage.model_dump(mode="json", by_alias=True, exclude_none=True)
                if response.usage
                else None,
            }
            last_response = response
        else:
            assert error is not None
            raw = {
                **(error.raw_response or {}),
                **metadata,
                "transportError": error.code,
                "retryAfterSeconds": error.retry_after_seconds,
                "usage": error.usage.model_dump(mode="json", by_alias=True, exclude_none=True)
                if error.usage
                else None,
            }
        safe, _ = redact_sensitive_object(raw)
        raws[raw_path] = safe
        _write(task_dir / Path(raw_path), safe)
        parsed_details = None
        diff = None
        issues = []
        final_error = None
        if response is not None:
            if response.actual_model != config["model"]:
                final_error = "model_mismatch"
                halt_batch = True
            elif response.finish_reason == "length":
                final_error = "truncated_output"
            elif response.finish_reason != "stop":
                final_error = "unsupported_finish_reason"
            elif not isinstance(response.content, str):
                final_error = "invalid_response_content"
            else:
                try:
                    _, result, parsed_details = parse_compact(response.content)
                    if result_used > 0:
                        diff = correction_differences(previous_parsed, parsed_details["parsed"])
                        if diff["lossless"] is not True:
                            review = ReviewStatus.NEEDS_REVIEW
                    if result.retrieval_decision.value == "manual_review":
                        review = ReviewStatus.NEEDS_REVIEW
                except CompactOutputError as exc:
                    final_error = exc.code
                    issues = exc.issues
                    parsed_details = {"parsed": exc.parsed, "issues": issues}
        else:
            assert error is not None
            final_error = error.code
            halt_batch = _permanent(error)
        if clock() > deadline:
            result = None
            final_error = "task_deadline_exhausted"
        attempt = EnrichmentAttemptRecord(
            attemptNumber=number,
            structuredRetryNumber=result_used,
            transportRetryNumber=transport_used,
            status=RunStatus.SUCCESS
            if result is not None and final_error is None
            else (
                RunStatus.INVALID_OUTPUT
                if response is not None
                else (RunStatus.TIMEOUT if final_error == "timeout" else RunStatus.PROVIDER_ERROR)
            ),
            completedAt=utc_now(),
            elapsedMs=elapsed,
            providerResponseReceived=response is not None
            or (error is not None and error.status_code is not None),
            requestId=response.request_id if response else (error.request_id if error else None),
            actualModel=response.actual_model if response else None,
            usage=response.usage if response else (error.usage if error else None),
            rawResponsePath=raw_path,
            errorCode=final_error,
            finishReason=response.finish_reason if response else None,
            recoveryMode=mode,
            validationStatus="passed" if result is not None and final_error is None else "failed",
            validationIssues=issues or None,
            recoveryDetailsPath=f"attempts/attempt-{number:03d}.validation.json",
        )
        attempts.append(attempt)
        validation_evidence = {
            "attempt": attempt.model_dump(mode="json", by_alias=True, exclude_none=True),
            "details": parsed_details,
            "contentComparison": diff,
            "mappedResult": result.model_dump(mode="json", by_alias=True)
            if result is not None and final_error is None
            else None,
        }
        raws[f"attempts/attempt-{number:03d}.validation.json"] = redact_sensitive_object(
            validation_evidence
        )[0]
        _write(
            task_dir / "attempts" / f"attempt-{number:03d}.validation.json",
            validation_evidence,
        )
        if result is not None and final_error is None:
            break
        result = None
        if halt_batch or number == min(3, call_limit) or final_error == "task_deadline_exhausted":
            break
        if error is not None:
            if not _temporary(error) or transport_used >= 1:
                break
            delay = error.retry_after_seconds if error.retry_after_seconds is not None else 2.0
            if delay >= deadline - clock():
                final_error = "retry_after_exceeds_deadline"
                break
            transport_used += 1
            mode = "transport_retry"
            sleep(delay)
            # A transport retry keeps the same operation and payload, including result-recovery state.
            continue
        if result_used >= 1 or final_error not in {
            "truncated_output",
            "json_syntax_error",
            "compact_schema_error",
            "business_validation_error",
            "invalid_response_content",
        }:
            break
        result_used += 1
        assert response is not None
        previous_parsed = (
            parsed_details.get("parsed")
            if parsed_details and isinstance(parsed_details.get("parsed"), dict)
            else None
        )
        feedback = {
            "errorCode": final_error,
            "issues": issues,
            "instruction": "重新查看同一原图，按具体本地错误生成完整精简JSON。不引入其他事实，不修改可见代码。",
        }
        if final_error == "truncated_output":
            if tokens >= config["hardTokenLimit"]:
                final_error = "output_token_ceiling_reached"
                break
            tokens = config["hardTokenLimit"]
            mode = "truncation_regeneration"
            original_content = None
        elif (
            final_error == "compact_schema_error"
            and parsed_details
            and isinstance(parsed_details.get("parsed"), dict)
            and _structure_only(parsed_details["parsed"], issues)
        ):
            # Only parseable structurally invalid output can be corrected without the image.
            previous_parsed = parsed_details["parsed"]
            original_content = response.content
            include_image = False
            mode = "text_correction"
            feedback["instruction"] = (
                "只依据原输出纠正结构，禁止增删事实、改变代码字符/缩进或数字；无法保证完整则返回uncertain。"
            )
        else:
            mode = "regenerate"
            original_content = None
            include_image = True
        feedback["mode"] = mode
    status = (
        RunStatus.SUCCESS
        if result is not None
        else (
            RunStatus.POLICY_REJECTED
            if final_error == "external_not_allowed"
            else (
                RunStatus.INVALID_OUTPUT if last_response is not None else RunStatus.PROVIDER_ERROR
            )
        )
    )
    final = {
        "status": status.value,
        "reviewStatus": review.value if result is not None else "needs_review",
        "result": result.model_dump(mode="json", by_alias=True, exclude_none=True)
        if result
        else None,
        "attempts": [a.model_dump(mode="json", by_alias=True, exclude_none=True) for a in attempts],
        "taskUsage": _usage_sum([a.usage for a in attempts if a.usage]),
        "elapsedMs": max(0, round((clock() - started) * 1000)),
        "errorCode": final_error,
        "haltBatch": halt_batch,
        "transportRecoveries": transport_used,
        "resultRecoveries": result_used,
    }
    serializable = {
        **final,
        "taskUsage": final["taskUsage"].model_dump(mode="json", by_alias=True, exclude_none=True)
        if final["taskUsage"]
        else None,
    }
    _write(task_dir / "task-result.json", serializable)
    return {**final, "raws": raws, "lastResponse": last_response}


def _record(
    item: PreparedImage,
    outcome: dict,
    *,
    key: str,
    config: dict,
    provider_name: str,
    prompt_sha256: str,
) -> EnrichmentRunRecord:
    attempts = [EnrichmentAttemptRecord.model_validate(a) for a in outcome["attempts"]]
    last = outcome.get("lastResponse")
    record = EnrichmentRunRecord(
        schemaVersion="1.1.0",
        cacheKey=key,
        imageSha256=item.request.image.sha256,
        status=outcome["status"],
        processor=PROCESSOR,
        provider=provider_name,
        requestedModel=config["model"],
        actualModel=last.actual_model if last else None,
        promptVersion=PROFILE,
        promptSha256=prompt_sha256,
        resultSchemaVersion="1.0.0",
        processedAt=utc_now(),
        requestId=last.request_id if last else None,
        contextFingerprintSha256=item.request.context_fingerprint_sha256,
        elapsedMs=outcome["elapsedMs"],
        attempts=len(attempts),
        retryCount=max(0, len(attempts) - 1),
        structuredRetryCount=outcome["resultRecoveries"],
        transportRetryCount=outcome["transportRecoveries"],
        attemptRecords=attempts,
        enableThinking=False,
        usage=last.usage if last else None,
        taskUsage=outcome["taskUsage"],
        result=outcome["result"],
        errorCode=outcome["errorCode"],
        rawResponsePath=attempts[-1].raw_response_path if attempts else None,
        processingProfile=PROFILE,
        reviewStatus=outcome["reviewStatus"],
        validationStatus="passed" if outcome["result"] is not None else "failed",
    )
    safe, redactions = redact_sensitive_object(
        record.model_dump(mode="json", by_alias=True, exclude_none=True)
    )
    if redactions:
        safe["reviewStatus"] = "needs_review"
        if safe.get("result"):
            safe["result"]["warnings"] = [
                *safe["result"].get("warnings", []),
                "credential_like_text_redacted_before_persistence",
            ]
    return EnrichmentRunRecord.model_validate(safe)


def _recover_attempts(task_dir: Path, model: str) -> list[EnrichmentAttemptRecord]:
    attempts = []
    for path in sorted(task_dir.glob("attempt-*.reservation.json")):
        reservation = json.loads(path.read_text(encoding="utf-8"))
        number = reservation["attemptNumber"]
        validation_path = task_dir / "attempts" / f"attempt-{number:03d}.validation.json"
        raw_path = task_dir / "attempts" / f"attempt-{number:03d}.raw-response.json"
        if validation_path.exists():
            value = json.loads(validation_path.read_text(encoding="utf-8"))
            attempts.append(EnrichmentAttemptRecord.model_validate(value["attempt"]))
            continue
        raw = json.loads(raw_path.read_text(encoding="utf-8")) if raw_path.exists() else {}
        status = RunStatus.PROVIDER_ERROR
        error_code = raw.get("transportError") or "interrupted_outcome_unknown"
        issues = []
        if raw_path.exists() and "transportError" not in raw:
            status = RunStatus.INVALID_OUTPUT
            if raw.get("model") != model:
                error_code = "model_mismatch"
            elif raw.get("finishReason") == "length":
                error_code = "truncated_output"
            elif raw.get("finishReason") == "stop":
                try:
                    parse_compact(raw.get("content"))
                    status = RunStatus.SUCCESS
                    error_code = None
                except (CompactOutputError, TypeError) as exc:
                    issues = (
                        exc.issues
                        if isinstance(exc, CompactOutputError)
                        else [{"message": "content is not text"}]
                    )
                    error_code = (
                        exc.code
                        if isinstance(exc, CompactOutputError)
                        else "invalid_response_content"
                    )
        attempt = EnrichmentAttemptRecord(
            attemptNumber=number,
            structuredRetryNumber=reservation.get("structuredRetryNumber", 0),
            transportRetryNumber=reservation.get("transportRetryNumber", 0),
            status=status,
            completedAt=raw.get("receivedAt"),
            elapsedMs=raw.get("elapsedMs"),
            providerResponseReceived=raw_path.exists() and "transportError" not in raw,
            requestId=raw.get("requestId"),
            actualModel=raw.get("model"),
            usage=raw.get("usage"),
            rawResponsePath=f"attempts/{raw_path.name}" if raw_path.exists() else None,
            errorCode=error_code,
            finishReason=raw.get("finishReason"),
            recoveryMode=reservation.get("recoveryMode"),
            validationStatus="passed" if status == RunStatus.SUCCESS else "failed",
            validationIssues=issues or None,
        )
        _write(
            validation_path,
            {
                "attempt": attempt.model_dump(mode="json", by_alias=True, exclude_none=True),
                "recoveredWithoutModelCall": True,
            },
        )
        attempts.append(attempt)
    return attempts


def _incomplete_record(
    task_dir: Path,
    task: dict,
    attempts: list[EnrichmentAttemptRecord],
    manifest: BatchRunManifest,
    output_root: Path,
) -> Path | None:
    if not attempts:
        return None
    request_file = output_root / Path(*task["requestPath"].split("/"))
    if not request_file.resolve(strict=True).is_relative_to(output_root.resolve()):
        raise ValueError("recovery request escapes the output root")
    from .models import EnrichmentRequest

    request = EnrichmentRequest.model_validate_json(request_file.read_text(encoding="utf-8"))
    record = EnrichmentRunRecord(
        schemaVersion="1.1.0",
        cacheKey=task["cacheKey"],
        imageSha256=task["imageSha256"],
        status="invalid_output",
        processor=PROCESSOR,
        provider=manifest.provider,
        requestedModel=manifest.requested_model,
        actualModel=attempts[-1].actual_model,
        promptVersion=PROFILE,
        promptSha256=manifest.prompt_sha256,
        resultSchemaVersion="1.0.0",
        processedAt=utc_now(),
        contextFingerprintSha256=request.context_fingerprint_sha256,
        elapsedMs=None,
        attempts=len(attempts),
        retryCount=max(0, len(attempts) - 1),
        attemptRecords=attempts,
        enableThinking=False,
        taskUsage=_usage_sum([a.usage for a in attempts if a.usage]),
        errorCode="pipeline_interrupted_after_response",
        reviewStatus="needs_review",
        validationStatus="incomplete",
        processingProfile=PROFILE,
        rawResponsePath=attempts[-1].raw_response_path,
    )
    target = task_dir / "recovered-record.json"
    _write(target, record.model_dump(mode="json", by_alias=True, exclude_none=True))
    return target


def rebuild_manifest(run_directory: Path, output_root: Path) -> BatchRunManifest:
    """Recompute aggregates from durable tasks and call reservations; no provider is constructed."""
    manifest = BatchRunManifest.model_validate_json(
        (run_directory / "run-manifest.json").read_text(encoding="utf-8")
    )
    items = []
    attempts: list[EnrichmentAttemptRecord] = []
    halt = False
    for task_dir in sorted((run_directory / "tasks").iterdir()):
        task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
        task_attempts = _recover_attempts(task_dir, manifest.requested_model)
        attempts.extend(task_attempts)
        result_path = task_dir / "batch-item.json"
        if result_path.exists():
            item = BatchRunItem.model_validate_json(result_path.read_text(encoding="utf-8"))
        else:
            recovered_record = _incomplete_record(
                task_dir, task, task_attempts, manifest, output_root
            )
            item = BatchRunItem(
                imageSha256=task["imageSha256"],
                relativePath=task["relativePath"],
                cacheKey=task["cacheKey"],
                status="skipped",
                requestPath=task["requestPath"],
                errorCode="incomplete_task_recoverable_evidence",
                reviewStatus="needs_review",
                recordPath=_relative(recovered_record, output_root) if recovered_record else None,
            )
        items.append(item)
        task_result = task_dir / "task-result.json"
        if task_result.exists():
            halt = halt or json.loads(task_result.read_text(encoding="utf-8")).get(
                "haltBatch", False
            )
    reservations = list((run_directory / "tasks").glob("*/attempt-*.reservation.json"))
    observed_calls = len(reservations)
    metrics = BatchRunMetrics(
        imageTasks=len(items),
        apiCalls=observed_calls,
        firstAttemptSuccesses=sum(
            a.status == RunStatus.SUCCESS and a.attempt_number == 1 for a in attempts
        ),
        retrySuccesses=sum(
            a.status == RunStatus.SUCCESS and a.attempt_number > 1 for a in attempts
        ),
        structuredRetrySuccesses=sum(
            a.status == RunStatus.SUCCESS and a.structured_retry_number > 0 for a in attempts
        ),
        finalFailures=sum(
            i.status not in (RunStatus.SUCCESS, RunStatus.CACHED, RunStatus.DRY_RUN) for i in items
        ),
        structuredOutputFailures=sum(a.status == RunStatus.INVALID_OUTPUT for a in attempts),
        cacheHits=sum(i.status == RunStatus.CACHED for i in items),
        apiCallsWithUsage=sum(a.usage is not None for a in attempts),
        apiCallsWithoutUsage=observed_calls - sum(a.usage is not None for a in attempts),
        transportRecoveryAttempts=sum(a.recovery_mode == "transport_retry" for a in attempts),
        resultRecoveryAttempts=sum(
            a.structured_retry_number > 0 and a.recovery_mode != "transport_retry" for a in attempts
        ),
        pendingReview=sum(
            i.review_status in (ReviewStatus.PENDING, ReviewStatus.NEEDS_REVIEW) for i in items
        ),
    )
    manifest = manifest.model_copy(
        update={
            "items": items,
            "summary": dict(Counter(i.status.value for i in items)),
            "metrics": metrics,
            "usage": _usage_sum([a.usage for a in attempts if a.usage]),
            "actual_models": sorted({a.actual_model for a in attempts if a.actual_model}),
            "status": "dry_run"
            if manifest.dry_run
            else (
                "blocked"
                if halt
                else (
                    "failed"
                    if any(i.error_code == "incomplete_task_recoverable_evidence" for i in items)
                    else "completed"
                )
            ),
            "completed_at": manifest.completed_at or utc_now(),
        }
    )
    _write(
        run_directory / "run-manifest.json",
        manifest.model_dump(mode="json", by_alias=True, exclude_none=True),
    )
    return manifest


def run_compact_batch(
    prepared: list[PreparedImage],
    dataset: DatasetSummary,
    *,
    inventory_sha256: str,
    output_root: Path,
    prompt: str,
    prompt_sha256: str,
    provider: Any,
    model: str,
    endpoint: str | None,
    region: str,
    max_completion_tokens: int = 4096,
    hard_token_limit: int = 8192,
    task_timeout: float = 180,
    run_name: str | None = None,
    dry_run: bool = False,
    policy_path: Path | None = None,
    requests_per_minute: float = 12,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[Path, BatchRunManifest]:
    config = profile_config(
        model=model,
        endpoint=endpoint,
        prompt_sha256=prompt_sha256,
        max_tokens=max_completion_tokens,
        hard_token_limit=hard_token_limit,
        task_timeout=task_timeout,
    )
    if any(_linked(p) for p in (output_root, *output_root.parents)):
        raise ValueError("linked output root is not allowed")
    output_root = output_root.resolve()
    for item in prepared:
        if output_root == item.path.parent or output_root.is_relative_to(item.path.parent):
            raise ValueError("output must not overlap image input")
    for name in ("runs", "cache", "reviews"):
        if _linked(output_root / name):
            raise ValueError("linked output subtree is not allowed")
    run_id = _new_run_id(run_name)
    directory = output_root / "runs" / run_id
    directory.mkdir(parents=True, exist_ok=False)
    manifest = BatchRunManifest(
        schemaVersion="1.1.0",
        runId=run_id,
        status="in_progress",
        createdAt=utc_now(),
        provider="aliyun_dashscope_openai_compatible",
        requestedModel=model,
        endpoint=endpoint,
        region=region,
        promptVersion=PROFILE,
        promptSha256=prompt_sha256,
        resultSchemaVersion="1.0.0",
        inventorySha256=inventory_sha256,
        configurationFingerprintSha256=stable_sha(config),
        enableThinking=False,
        force=False,
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
        items=[],
        processingProfile=PROFILE,
        profileConfiguration=config,
        notes=[
            "Machine validation is separate from human approval; no benchmark was read during inference.",
            "At most 3 actual calls per image: one first call, one transport recovery and one result recovery.",
            "Unknown usage is reported separately; a timeout may still have been billed by the service.",
        ],
    )
    _write(
        directory / "run-manifest.json",
        manifest.model_dump(mode="json", by_alias=True, exclude_none=True),
    )
    # Write every task identity before any call so a halted batch still has explicit exits.
    names = [item.request.image.sha256[:12] for item in prepared]
    if len(names) != len(set(names)):
        raise ValueError("duplicate image identity or shortened task-directory collision")
    for item in prepared:
        key = compact_cache_key(item, config)
        request_path = directory / "requests" / f"{item.request.image.sha256}.json"
        _write(request_path, item.request.model_dump(mode="json", by_alias=True, exclude_none=True))
        _write(
            _task_directory(directory, item.request.image.sha256) / "task.json",
            {
                "imageSha256": item.request.image.sha256,
                "relativePath": item.request.image.relative_path,
                "cacheKey": key,
                "requestPath": _relative(request_path, output_root),
            },
        )
    halted = False
    last_start = None
    for item in prepared:
        key = compact_cache_key(item, config)
        task_dir = _task_directory(directory, item.request.image.sha256)
        task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
        common = {
            "imageSha256": item.request.image.sha256,
            "relativePath": item.request.image.relative_path,
            "cacheKey": key,
            "requestPath": task["requestPath"],
        }
        cached, issue = inspect_cache(output_root / "cache" / key, key)
        if not _policy_ok(item, policy_path):
            batch_item = BatchRunItem(
                **common, status="policy_rejected", errorCode="external_not_allowed"
            )
        elif halted:
            batch_item = BatchRunItem(
                **common, status="skipped", errorCode="batch_configuration_blocked"
            )
        elif cached is not None:
            batch_item = BatchRunItem(
                **common,
                status="cached",
                recordPath=f"cache/{key}/record.json",
                reviewStatus=cached.review_status or ReviewStatus.PENDING,
            )
        elif issue is not None:
            batch_item = BatchRunItem(
                **common,
                status="invalid_output",
                errorCode=issue,
                reviewStatus="rejected" if issue == "cache_review_rejected" else "needs_review",
            )
        elif dry_run:
            batch_item = BatchRunItem(**common, status="dry_run")
        elif provider is None:
            batch_item = BatchRunItem(**common, status="skipped", errorCode="provider_unavailable")
        elif (
            len(list((directory / "tasks").glob("*/attempt-*.reservation.json")))
            >= config["maxBatchCalls"]
        ):
            batch_item = BatchRunItem(
                **common, status="skipped", errorCode="batch_call_budget_exhausted"
            )
        else:
            if last_start is not None and requests_per_minute > 0:
                sleep(max(0, 60 / requests_per_minute - (clock() - last_start)))
            last_start = clock()
            print(f"compact image {item.request.image.relative_path}", flush=True)
            outcome = process_task(
                item,
                key=key,
                provider=provider,
                prompt=prompt,
                config=config,
                task_dir=task_dir,
                policy_path=policy_path,
                clock=clock,
                sleep=sleep,
                call_limit=min(
                    3,
                    config["maxBatchCalls"]
                    - len(list((directory / "tasks").glob("*/attempt-*.reservation.json"))),
                ),
            )
            record = _record(
                item,
                outcome,
                key=key,
                config=config,
                provider_name=manifest.provider,
                prompt_sha256=prompt_sha256,
            )
            # Task evidence already exists. Publish the same standard record locally before the manifest.
            _write(
                task_dir / "record.json",
                record.model_dump(mode="json", by_alias=True, exclude_none=True),
            )
            record_path = task_dir / "record.json"
            if record.status == RunStatus.SUCCESS:
                last = outcome["lastResponse"]
                safe_raw, _ = redact_sensitive_object(last.raw_response)
                target = _publish_success(
                    output_root / "cache", key, record, safe_raw, outcome["raws"]
                )
                record_path = target / "record.json"
            batch_item = BatchRunItem(
                **common,
                status=record.status,
                recordPath=_relative(record_path, output_root),
                errorCode=record.error_code,
                reviewStatus=record.review_status,
            )
            halted = outcome["haltBatch"]
        _write(
            task_dir / "batch-item.json",
            batch_item.model_dump(mode="json", by_alias=True, exclude_none=True),
        )
    verify_unchanged(prepared)
    manifest = rebuild_manifest(directory, output_root)
    return directory / "run-manifest.json", manifest
