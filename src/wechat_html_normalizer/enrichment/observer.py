"""Read-only source inventory for the local enrichment observer.

This is a derived view. It never writes into images, cache, runs, evaluations,
or the human workbook, and deliberately does not load provider raw responses.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from PIL import Image

from ..media import sha256_file
from .dataset import prepare_dataset
from .evaluation import read_benchmark
from .models import BatchRunManifest, EnrichmentRunRecord
from .providers import DEFAULT_MODEL
from .reviews import read_review
from .runner import cache_key, inspect_cache, load_prompt
from .security import redact_sensitive_object
from .settings import DEFAULT_PROMPT_PATH, PROMPT_VERSION

INDEX_VERSION = "1.0.0"
MAX_JSON_BYTES = 10 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}")


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError("JSON source is missing, linked or too large")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("JSON root must be an object")
    return value


def _safe_relative(root: Path, relative: str) -> Path:
    parsed = PurePosixPath(relative)
    if (
        parsed.is_absolute()
        or not parsed.parts
        or any(part in ("..", ".") or ":" in part or "\\" in part for part in parsed.parts)
    ):
        raise ValueError("unsafe relative path")
    target = root.joinpath(*parsed.parts)
    if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("relative path escapes its source root")
    return target


def _read_attempt_evidence(raw_file: Path, issues: list[dict]) -> dict[str, Any] | None:
    """Expose only persisted, already-redacted response evidence to the static observer."""
    try:
        evidence = _read_json(raw_file)
    except (OSError, ValueError, TypeError):
        _issue(issues, "attempt_raw_response_unreadable", raw_file, severity="error")
        return None
    content = evidence.get("content")
    if content is not None and not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True)
    validation = evidence.get("localValidation")
    validation_errors = validation.get("errors", []) if isinstance(validation, dict) else []
    if not isinstance(validation_errors, list):
        validation_errors = []
    return {
        "content": content,
        "contentType": evidence.get("contentType"),
        "validationStatus": validation.get("status") if isinstance(validation, dict) else None,
        "validationErrors": [row for row in validation_errors if isinstance(row, dict)],
    }


def _issue(issues: list[dict], code: str, path: Path | str, *, severity: str = "warning") -> None:
    issues.append({"code": code, "severity": severity, "path": str(path)})


def _bytes_and_files(directory: Path) -> tuple[int, int]:
    if not directory.exists() or directory.is_symlink():
        return 0, 0
    files = [path for path in directory.rglob("*") if path.is_file() and not path.is_symlink()]
    return sum(path.stat().st_size for path in files), len(files)


def _source_files(directory: Path) -> list[Path]:
    if not directory.is_dir() or directory.is_symlink():
        return []
    return sorted(
        (path for path in directory.rglob("*") if path.is_file() and not path.is_symlink()),
        key=lambda path: str(path).casefold(),
    )


def _document_positions(
    document_path: str | None,
    *,
    project_root: Path,
    cache: dict[str, dict[str, tuple[int, str]]],
    issues: list[dict],
) -> dict[str, tuple[int, str]]:
    if not document_path:
        return {}
    if document_path in cache:
        return cache[document_path]
    path = Path(document_path)
    try:
        if not path.resolve().is_relative_to(project_root.resolve()):
            raise ValueError("document path escapes project root")
        document = _read_json(path)
        result = {
            block["assetId"]: (block.get("order", 0), block.get("sourceLocator") or "")
            for block in document.get("blocks", [])
            if isinstance(block, dict) and block.get("type") == "image" and block.get("assetId")
        }
    except (OSError, ValueError, KeyError, TypeError):
        _issue(issues, "document_relation_unreadable", path)
        result = {}
    cache[document_path] = result
    return result


def _doc_references(project_root: Path, run_ids: list[str]) -> dict[str, list[str]]:
    paths = [project_root / "README.md", *sorted((project_root / "docs").glob("*.md"))]
    contents = {}
    for path in paths:
        if path.is_file() and not path.is_symlink():
            contents[path.relative_to(project_root).as_posix()] = path.read_text(encoding="utf-8")
    return {
        run_id: sorted(path for path, content in contents.items() if run_id in content)
        for run_id in run_ids
    }


def _default_cache_keys(
    image_root: Path, inventory_path: Path, policy_path: Path | None, issues: list[dict]
) -> set[str]:
    try:
        _, prompt_sha = load_prompt(DEFAULT_PROMPT_PATH)
        prepared, _, _ = prepare_dataset(
            image_root,
            inventory_path,
            prompt_version=PROMPT_VERSION,
            allow_external=False,
            policy_path=policy_path,
        )
        return {
            cache_key(
                item,
                provider="aliyun_dashscope_openai_compatible",
                model=DEFAULT_MODEL,
                prompt_sha256=prompt_sha,
                region="beijing",
                enable_thinking=False,
                max_completion_tokens=4096,
            )
            for item in prepared
        }
    except (OSError, ValueError, TypeError, KeyError) as exc:
        _issue(issues, f"default_cache_identity_unavailable:{type(exc).__name__}", inventory_path)
        return set()


def _load_policy(path: Path | None, issues: list[dict]) -> dict[str, dict]:
    if path is None or not path.exists():
        return {}
    try:
        payload = _read_json(path)
        images = payload.get("images", {})
        if not isinstance(images, dict):
            raise TypeError("policy images must be an object")
        return images
    except (OSError, ValueError, TypeError):
        _issue(issues, "policy_unreadable", path, severity="error")
        return {}


def collect_index(
    *,
    project_root: Path,
    image_root: Path,
    inventory_path: Path,
    workbook_path: Path | None,
    enrichment_root: Path,
    policy_path: Path | None,
    alias_path: Path | None = None,
) -> dict[str, Any]:
    """Collect a bounded, reproducible map without modifying source files."""
    project_root = project_root.resolve(strict=True)
    image_root = image_root.resolve(strict=True)
    enrichment_root = enrichment_root.resolve(strict=True)
    if not image_root.is_relative_to(project_root) or not enrichment_root.is_relative_to(
        project_root
    ):
        raise ValueError("observer sources must stay inside the project root")
    issues: list[dict] = []
    inventory = _read_json(inventory_path)
    if inventory.get("schemaVersion") != "1.0.0" or not isinstance(inventory.get("files"), list):
        raise ValueError("unsupported image inventory")
    benchmark: dict[str, dict] = {}
    benchmark_summary: dict[str, Any] | None = None
    if workbook_path is not None:
        try:
            rows, benchmark_summary = read_benchmark(workbook_path, image_root)
            benchmark = {row["imageSha256"]: row["benchmark"] for row in rows}
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            _issue(issues, "benchmark_unreadable", workbook_path)
    aliases: dict[str, str] = {}
    if alias_path is not None and alias_path.exists():
        try:
            raw_aliases = _read_json(alias_path).get("aliases", {})
            if not isinstance(raw_aliases, dict):
                raise TypeError("aliases must be an object")
            aliases = {
                key: value.strip()
                for key, value in raw_aliases.items()
                if isinstance(key, str) and isinstance(value, str) and value.strip()
            }
        except (OSError, ValueError, TypeError):
            _issue(issues, "aliases_unreadable", alias_path)
    policy = _load_policy(policy_path, issues)
    images: dict[str, dict] = {}
    for row in inventory["files"]:
        if not isinstance(row, dict) or not isinstance(row.get("imageId"), str):
            _issue(issues, "inventory_file_unrecognized", inventory_path)
            continue
        digest = row["imageId"]
        if not _SHA256.fullmatch(digest):
            _issue(issues, "inventory_image_id_unsafe", inventory_path, severity="error")
            continue
        relative = row.get("relativePath") or ""
        file_path: Path | None = None
        image_state = "missing"
        try:
            file_path = _safe_relative(image_root, relative)
            if file_path.is_file():
                image_state = "available" if sha256_file(file_path) == digest else "hash_mismatch"
                if image_state == "available":
                    try:
                        with Image.open(file_path) as image:
                            image.verify()
                    except (OSError, ValueError):
                        image_state = "decode_failed"
            if image_state != "available":
                _issue(issues, f"image_{image_state}", file_path, severity="error")
        except ValueError:
            _issue(issues, "image_unsafe_path", relative, severity="error")
        label = benchmark.get(digest, {})
        alias = aliases.get(digest)
        file_name = row.get("fileName") or Path(relative).name
        display = alias or f"{label.get('imageTypeRaw') or '图片'} · {file_name}"
        images[digest] = {
            "imageSha256": digest,
            "shortHash": digest[:10],
            "relativePath": relative,
            "originalPath": str(file_path) if file_path else None,
            "fileName": file_name,
            "displayName": display,
            "alias": alias,
            "format": row.get("format"),
            "width": row.get("width"),
            "height": row.get("height"),
            "sizeBytes": row.get("sizeBytes"),
            "imageState": image_state,
            "humanType": label.get("imageTypeRaw"),
            "humanValue": label.get("knowledgeValueRaw"),
            "humanAction": label.get("actionRaw"),
            "reviewStatus": label.get("reviewStatus"),
            "referenceAnswerPresent": bool(label.get("referenceAnswer")),
            "localOnly": bool(policy.get(digest, {}).get("localOnly")),
            "policyReason": policy.get(digest, {}).get("reasonCode"),
            "relations": [],
            "runRefs": [],
            "cacheRefs": [],
            "evaluationRefs": [],
            "unresolvedFailure": False,
        }
    document_cache: dict[str, dict[str, tuple[int, str]]] = {}
    for relation in inventory.get("relations", []):
        if not isinstance(relation, dict):
            _issue(issues, "relation_unrecognized", inventory_path)
            continue
        digest = relation.get("imageId")
        if digest not in images:
            _issue(issues, "relation_image_missing", inventory_path, severity="error")
            continue
        position = _document_positions(
            relation.get("documentJson"),
            project_root=project_root,
            cache=document_cache,
            issues=issues,
        ).get(relation.get("assetId"))
        images[digest]["relations"].append(
            {
                "documentId": relation.get("documentId"),
                "revisionId": relation.get("revisionId"),
                "assetId": relation.get("assetId"),
                "title": relation.get("title"),
                "role": relation.get("role"),
                "documentJson": relation.get("documentJson"),
                "blockOrder": position[0] if position else None,
                "sourceLocator": position[1] if position else None,
            }
        )

    runs: dict[str, dict] = {}
    run_root = enrichment_root / "runs"
    for directory in sorted(run_root.iterdir()) if run_root.is_dir() else []:
        if not directory.is_dir() or directory.is_symlink():
            _issue(issues, "run_directory_unrecognized", directory)
            continue
        manifest_path = directory / "run-manifest.json"
        try:
            manifest = BatchRunManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            _issue(issues, "run_manifest_unreadable", manifest_path, severity="error")
            continue
        if manifest.run_id != directory.name:
            _issue(issues, "run_directory_identity_mismatch", manifest_path, severity="error")
        if not _SAFE_NAME.fullmatch(directory.name) or not _SAFE_NAME.fullmatch(manifest.run_id):
            _issue(issues, "run_name_unsafe", directory, severity="error")
            continue
        items = []
        for item in manifest.items:
            record: EnrichmentRunRecord | None = None
            record_path: Path | None = None
            if item.record_path:
                try:
                    record_path = _safe_relative(enrichment_root, item.record_path)
                    record = EnrichmentRunRecord.model_validate_json(
                        record_path.read_text(encoding="utf-8")
                    )
                    if (
                        record.image_sha256 != item.image_sha256
                        or record.cache_key != item.cache_key
                    ):
                        raise ValueError("run item and record identity differ")
                except (OSError, ValueError):
                    _issue(
                        issues, "run_record_missing_or_invalid", item.record_path, severity="error"
                    )
            request_exists = False
            try:
                request_exists = _safe_relative(enrichment_root, item.request_path).is_file()
            except ValueError:
                pass
            if not request_exists:
                _issue(issues, "run_request_missing", item.request_path, severity="error")
            result = (
                record.result.model_dump(mode="json", by_alias=True)
                if record and record.result
                else None
            )
            attempts = (
                [
                    attempt.model_dump(mode="json", by_alias=True)
                    for attempt in record.attempt_records
                ]
                if record
                else []
            )
            for attempt in attempts:
                relative_raw = attempt.get("rawResponsePath")
                try:
                    raw_file = (
                        _safe_relative(record_path.parent, relative_raw)
                        if record_path and isinstance(relative_raw, str)
                        else None
                    )
                except ValueError:
                    raw_file = None
                    _issue(issues, "attempt_raw_response_unsafe", relative_raw or "")
                if raw_file and raw_file.resolve().is_relative_to(enrichment_root):
                    attempt["rawResponseFile"] = str(raw_file)
                    attempt["rawResponseExists"] = raw_file.is_file()
                    if not raw_file.is_file():
                        _issue(issues, "attempt_raw_response_missing", raw_file, severity="error")
                    elif attempt.get("status") != "success":
                        attempt["evidence"] = _read_attempt_evidence(raw_file, issues)
                        if attempt.get("validationIssues") and isinstance(
                            attempt.get("evidence"), dict
                        ):
                            attempt["evidence"]["localValidation"] = {
                                "status": "failed",
                                "errors": attempt["validationIssues"],
                            }
                else:
                    attempt["rawResponseFile"] = None
                    attempt["rawResponseExists"] = False
            review_override, review_issue = read_review(enrichment_root, item.cache_key)
            if review_issue:
                _issue(issues, review_issue, item.cache_key, severity="error")
            item_view = {
                "imageSha256": item.image_sha256,
                "relativePath": item.relative_path,
                "cacheKey": item.cache_key,
                "status": item.status.value,
                "requestPath": item.request_path,
                "recordPath": item.record_path,
                "errorCode": item.error_code or (record.error_code if record else None),
                "attempts": attempts,
                "historicalAttemptCount": record.attempts if record else None,
                "usage": (
                    record.task_usage.model_dump(mode="json", by_alias=True)
                    if record and record.task_usage
                    else None
                ),
                "elapsedMs": record.elapsed_ms if record else None,
                "result": result,
                "processingProfile": record.processing_profile if record else None,
                "validationStatus": record.validation_status if record else None,
                "reviewStatus": review_override.value
                if review_override
                else (record.review_status.value if record and record.review_status else "pending"),
            }
            items.append(item_view)
            if item.image_sha256 in images:
                images[item.image_sha256]["runRefs"].append(
                    {
                        "runId": manifest.run_id,
                        "cacheKey": item.cache_key,
                        "status": item.status.value,
                    }
                )
                if item.error_code or item.status.value in (
                    "invalid_output",
                    "timeout",
                    "provider_error",
                    "authentication_failed",
                    "rate_limited",
                ):
                    images[item.image_sha256]["unresolvedFailure"] = True
            else:
                _issue(issues, "run_image_not_in_inventory", item.image_sha256)
        observed_calls = (
            manifest.metrics.api_calls
            if manifest.metrics
            else sum(item["historicalAttemptCount"] or 0 for item in items)
        )
        if manifest.dry_run:
            run_type = "dry_run"
        elif observed_calls or any(
            item["status"] in ("success", "invalid_output") for item in items
        ):
            run_type = "api_run"
        elif items and all(item["status"] == "cached" for item in items):
            run_type = "cache_check"
        else:
            run_type = "other_or_incomplete"
        byte_count, file_count = _bytes_and_files(directory)
        runs[manifest.run_id] = {
            "runId": manifest.run_id,
            "type": run_type,
            "status": manifest.status,
            "manifestPath": str(manifest_path),
            "createdAt": manifest.created_at,
            "completedAt": manifest.completed_at,
            "provider": manifest.provider,
            "model": manifest.requested_model,
            "actualModels": manifest.actual_models,
            "promptVersion": manifest.prompt_version,
            "promptSha256": manifest.prompt_sha256,
            "resultSchemaVersion": manifest.result_schema_version,
            "enableThinking": manifest.enable_thinking,
            "processingProfile": manifest.processing_profile,
            "dryRun": manifest.dry_run,
            "summary": manifest.summary,
            "metrics": (
                manifest.metrics.model_dump(mode="json", by_alias=True)
                if manifest.metrics
                else None
            ),
            "observedApiCalls": observed_calls,
            "usage": manifest.usage.model_dump(mode="json", by_alias=True)
            if manifest.usage
            else None,
            "items": items,
            "sizeBytes": byte_count,
            "fileCount": file_count,
            "evaluationRefs": [],
            "documentedBy": [],
        }
    references = _doc_references(project_root, list(runs))
    for run_id, paths in references.items():
        runs[run_id]["documentedBy"] = paths

    default_keys = _default_cache_keys(image_root, inventory_path, policy_path, issues)
    caches: dict[str, dict] = {}
    cache_root = enrichment_root / "cache"
    cache_to_runs: dict[str, list[str]] = defaultdict(list)
    for run in runs.values():
        for item in run["items"]:
            cache_to_runs[item["cacheKey"]].append(run["runId"])
    for directory in sorted(cache_root.iterdir()) if cache_root.is_dir() else []:
        if not directory.is_dir() or directory.is_symlink():
            _issue(issues, "cache_directory_unrecognized", directory)
            continue
        if not _SHA256.fullmatch(directory.name):
            _issue(issues, "cache_key_unsafe", directory, severity="error")
            continue
        record, validation_issue = inspect_cache(directory, directory.name)
        historical: EnrichmentRunRecord | None = record
        if historical is None and (directory / "record.json").is_file():
            try:
                historical = EnrichmentRunRecord.model_validate_json(
                    (directory / "record.json").read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                pass
        if validation_issue and validation_issue != "cache_review_rejected":
            _issue(issues, validation_issue, directory, severity="error")
        byte_count, file_count = _bytes_and_files(directory)
        referenced_by = sorted(set(cache_to_runs.get(directory.name, [])))
        caches[directory.name] = {
            "cacheKey": directory.name,
            "imageSha256": historical.image_sha256 if historical else None,
            "path": str(directory),
            "recordPath": str(directory / "record.json"),
            "rawResponsePath": str(directory / "raw-response.json"),
            "storedStatus": historical.status.value if historical else None,
            "currentValidation": "valid"
            if record or validation_issue == "cache_review_rejected"
            else (validation_issue or "unknown"),
            "cacheEligibility": "review_rejected"
            if validation_issue == "cache_review_rejected"
            else ("reusable" if record else "unusable"),
            "provider": historical.provider if historical else None,
            "model": historical.requested_model if historical else None,
            "promptVersion": historical.prompt_version if historical else None,
            "promptSha256": historical.prompt_sha256 if historical else None,
            "resultSchemaVersion": historical.result_schema_version if historical else None,
            "contextFingerprintSha256": (
                historical.context_fingerprint_sha256 if historical else None
            ),
            "processedAt": historical.processed_at if historical else None,
            "runRefs": referenced_by,
            "currentDefaultKey": directory.name in default_keys,
            "orphan": not referenced_by,
            "sizeBytes": byte_count,
            "fileCount": file_count,
            "reviewStatus": (
                read_review(enrichment_root, directory.name)[0]
                or (
                    historical.review_status
                    if historical and historical.review_status
                    else "pending"
                )
            ),
        }
        if historical and historical.image_sha256 in images:
            images[historical.image_sha256]["cacheRefs"].append(directory.name)
        elif historical:
            _issue(issues, "cache_image_not_in_inventory", directory)
    for key, run_ids in cache_to_runs.items():
        if key not in caches and any(
            item["status"] in ("cached", "success")
            for run_id in run_ids
            for item in runs[run_id]["items"]
            if item["cacheKey"] == key
        ):
            _issue(issues, "referenced_cache_missing", key, severity="error")

    evaluations: dict[str, dict] = {}
    evaluation_root = enrichment_root / "evaluations"
    for directory in sorted(evaluation_root.iterdir()) if evaluation_root.is_dir() else []:
        if not directory.is_dir() or directory.is_symlink():
            _issue(issues, "evaluation_directory_unrecognized", directory)
            continue
        if not _SAFE_NAME.fullmatch(directory.name):
            _issue(issues, "evaluation_name_unsafe", directory, severity="error")
            continue
        report_path = directory / "baseline-evaluation.json"
        review_path = directory / "quality-review.json"
        report: dict = {}
        review: dict = {}
        if report_path.exists():
            try:
                report = _read_json(report_path)
            except (OSError, ValueError, TypeError):
                _issue(issues, "evaluation_report_unreadable", report_path, severity="error")
        if review_path.exists():
            try:
                review = _read_json(review_path)
            except (OSError, ValueError, TypeError):
                _issue(issues, "quality_review_unreadable", review_path, severity="error")
        run_id = (report.get("run") or {}).get("runId") or review.get("runId")
        if not isinstance(run_id, str):
            _issue(issues, "evaluation_run_unknown", directory, severity="error")
            run_id = None
        elif run_id not in runs:
            _issue(issues, "evaluation_run_missing", directory, severity="error")
        grades = {}
        for item in review.get("items", []):
            if isinstance(item, dict) and isinstance(item.get("imageSha256"), str):
                grades[item["imageSha256"]] = {
                    "grade": item.get("grade"),
                    "note": item.get("note"),
                }
        result_images = {
            item.get("imageSha256")
            for item in report.get("items", [])
            if isinstance(item, dict) and item.get("machine") is not None
        }
        for digest in sorted(result_images | grades.keys()):
            if digest in images:
                images[digest]["evaluationRefs"].append(
                    {"evaluationId": directory.name, "runId": run_id, **grades.get(digest, {})}
                )
            else:
                _issue(issues, "evaluation_image_not_in_inventory", digest)
        byte_count, file_count = _bytes_and_files(directory)
        evaluations[directory.name] = {
            "evaluationId": directory.name,
            "runId": run_id,
            "path": str(directory),
            "reportPath": str(report_path) if report_path.exists() else None,
            "reviewPath": str(review_path) if review_path.exists() else None,
            "benchmarkSha256": (report.get("benchmark") or {}).get("workbookSha256"),
            "benchmarkConfirmedRows": (report.get("benchmark") or {}).get("confirmedRows"),
            "gradeCounts": dict(Counter(row.get("grade") for row in grades.values())),
            "grades": grades,
            "reviewBasis": review.get("reviewBasis"),
            "sizeBytes": byte_count,
            "fileCount": file_count,
        }
        if run_id in runs:
            runs[run_id]["evaluationRefs"].append(directory.name)

    for image in images.values():
        image["relations"].sort(
            key=lambda row: (
                str(row["documentId"]),
                str(row["revisionId"]),
                row["blockOrder"] or 0,
                str(row["assetId"]),
            )
        )
        image["runRefs"].sort(key=lambda row: (row["runId"], row["cacheKey"]))
        image["cacheRefs"].sort()
        image["evaluationRefs"].sort(key=lambda row: row["evaluationId"])
        image["latestExperiment"] = max(
            (runs[ref["runId"]] for ref in image["runRefs"] if ref["runId"] in runs),
            key=lambda run: run["createdAt"],
            default=None,
        )
        if image["latestExperiment"]:
            image["latestExperiment"] = image["latestExperiment"]["runId"]
        successful = [
            runs[ref["runId"]]
            for ref in image["runRefs"]
            if ref["status"] in ("success", "cached") and ref["runId"] in runs
        ]
        image["latestSuccess"] = (
            max(successful, key=lambda run: run["createdAt"])["runId"] if successful else None
        )
        graded = [ref for ref in image["evaluationRefs"] if ref.get("grade")]
        image["latestManualGrade"] = (
            max(
                graded,
                key=lambda ref: runs[ref["runId"]]["createdAt"] if ref["runId"] in runs else "",
            )["grade"]
            if graded
            else None
        )
        actual_runs = [
            runs[ref["runId"]]
            for ref in image["runRefs"]
            if ref["runId"] in runs and runs[ref["runId"]]["type"] == "api_run"
        ]
        image["latestActualRun"] = (
            max(actual_runs, key=lambda run: run["createdAt"])["runId"] if actual_runs else None
        )

    source_storage = storage_audit(enrichment_root, issues)
    safe_index, redaction_count = redact_sensitive_object(
        {
            "schemaVersion": INDEX_VERSION,
            "generatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "source": {
                "projectRoot": str(project_root),
                "imageRoot": str(image_root),
                "inventoryPath": str(inventory_path.resolve()),
                "inventorySha256": sha256_file(inventory_path),
                "workbookPath": str(workbook_path.resolve()) if workbook_path else None,
                "benchmark": benchmark_summary,
                "enrichmentRoot": str(enrichment_root),
                "policyPath": str(policy_path.resolve())
                if policy_path and policy_path.exists()
                else None,
            },
            "defaults": {
                "model": DEFAULT_MODEL,
                "promptVersion": PROMPT_VERSION,
                "promptSha256": load_prompt(DEFAULT_PROMPT_PATH)[1],
                "resultSchemaVersion": "1.0.0",
                "validationPolicy": "current_cross_field_v1",
            },
            "images": sorted(
                images.values(), key=lambda row: (row["relativePath"], row["imageSha256"])
            ),
            "runs": sorted(runs.values(), key=lambda row: (row["createdAt"], row["runId"])),
            "caches": sorted(caches.values(), key=lambda row: row["cacheKey"]),
            "evaluations": sorted(evaluations.values(), key=lambda row: row["evaluationId"]),
            "storage": source_storage,
            "inputStorage": {
                "testImages": {
                    "files": len(_source_files(image_root)),
                    "bytes": _bytes_and_files(image_root)[0],
                },
                "inventoryBytes": inventory_path.stat().st_size,
                "workbookBytes": workbook_path.stat().st_size
                if workbook_path and workbook_path.is_file()
                else None,
            },
            "summary": {
                "images": len(images),
                "mappedImages": sum(bool(row["relations"]) for row in images.values()),
                "localOnly": sum(row["localOnly"] for row in images.values()),
                "runs": len(runs),
                "caches": len(caches),
                "evaluations": len(evaluations),
                "currentInvalidCaches": sum(
                    row["currentValidation"] != "valid" for row in caches.values()
                ),
                "orphanCaches": sum(row["orphan"] for row in caches.values()),
                "unresolvedImageFailures": sum(row["unresolvedFailure"] for row in images.values()),
            },
            "issues": sorted(issues, key=lambda row: (row["code"], row["path"])),
        }
    )
    safe_index["redactionCount"] = redaction_count
    return safe_index


def storage_audit(enrichment_root: Path, issues: list[dict] | None = None) -> dict[str, Any]:
    """Count only known source areas; generated observer files are intentionally excluded."""
    issues = issues if issues is not None else []
    groups: dict[str, dict] = {}
    all_files: list[tuple[str, Path]] = []
    for category in ("cache", "runs", "evaluations", "reviews"):
        directory = enrichment_root / category
        files = _source_files(directory)
        directories = (
            [p for p in directory.rglob("*") if p.is_dir() and not p.is_symlink()]
            if directory.is_dir()
            else []
        )
        groups[category] = {
            "files": len(files),
            "directories": len(directories),
            "bytes": sum(path.stat().st_size for path in files),
        }
        all_files.extend((category, path) for path in files)
    for path in enrichment_root.iterdir():
        if path.is_symlink():
            _issue(issues, "linked_enrichment_root_entry", path, severity="error")
        elif path.name not in ("cache", "runs", "evaluations", "reviews", "observer"):
            _issue(issues, "unknown_enrichment_root_entry", path)
    hashes: dict[str, list[str]] = defaultdict(list)
    for _, path in all_files:
        hashes[sha256_file(path)].append(str(path))
    duplicates = [sorted(paths) for paths in hashes.values() if len(paths) > 1]
    duplicates.sort(key=lambda paths: paths[0])
    largest = sorted(all_files, key=lambda row: (-row[1].stat().st_size, str(row[1])))[:10]
    return {
        "categories": groups,
        "totalFiles": len(all_files),
        "totalBytes": sum(group["bytes"] for group in groups.values()),
        "duplicateGroups": duplicates,
        "largestFiles": [
            {"category": category, "path": str(path), "bytes": path.stat().st_size}
            for category, path in largest
        ],
    }


def cleanup_preview(index: dict[str, Any]) -> dict[str, Any]:
    """Explain candidates only. Nothing here is an executable deletion instruction."""
    root = Path(index["source"]["enrichmentRoot"]).resolve(strict=True)
    candidates: list[dict] = []

    def add(
        path: Path,
        *,
        kind: str,
        identifier: str,
        bytes_used: int,
        references: list[str],
        action: str,
        reason: str,
        rebuild: str,
        cloud: bool | None,
        risk: str,
    ) -> None:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root) or resolved == root:
            raise ValueError("cleanup preview target must be inside the enrichment root")
        candidates.append(
            {
                "path": str(resolved),
                "kind": kind,
                "id": identifier,
                "bytes": bytes_used,
                "action": action,
                "reasonCode": reason,
                "referencedBy": sorted(set(references)),
                "rebuild": rebuild,
                "requiresCloud": cloud,
                "estimatedReleaseBytes": bytes_used if action == "cleanup_candidate" else 0,
                "risk": risk,
                "recovery": "No deletion is performed. Keep the original sources and rerun the relevant command.",
            }
        )

    for run in index["runs"]:
        actual_calls = run.get("observedApiCalls") or 0
        references = [*run["documentedBy"], *run["evaluationRefs"]]
        has_failure = any(item.get("errorCode") for item in run["items"])
        if references or actual_calls or has_failure:
            action, reason = "keep", "documented_evaluated_or_api_evidence"
        elif run["type"] in ("dry_run", "cache_check") and run["status"] in (
            "dry_run",
            "completed",
        ):
            action, reason = "cleanup_candidate", "reproducible_no_call_run_without_references"
        else:
            action, reason = "review", "run_value_not_proven"
        add(
            root / "runs" / run["runId"],
            kind="run",
            identifier=run["runId"],
            bytes_used=run["sizeBytes"],
            references=references,
            action=action,
            reason=reason,
            rebuild=(
                "Repeat the dry-run or cache check without a model call."
                if action == "cleanup_candidate"
                else "A repeated provider result may differ; preserve the run evidence."
            ),
            cloud=bool(actual_calls),
            risk=(
                "Low: no API attempts, evaluation or documentation found."
                if action == "cleanup_candidate"
                else "May contain unique provider usage, failures or review context."
            ),
        )
    documented_runs = {run["runId"] for run in index["runs"] if run["documentedBy"]}
    evaluated_runs = {evaluation["runId"] for evaluation in index["evaluations"]}
    for cache in index["caches"]:
        references = [f"run:{run_id}" for run_id in cache["runRefs"]]
        if cache["currentValidation"] != "valid":
            action, reason = "keep", "invalid_cache_is_diagnostic_evidence"
        elif documented_runs.intersection(cache["runRefs"]) or evaluated_runs.intersection(
            cache["runRefs"]
        ):
            action, reason = "keep", "documented_or_evaluated_result"
        else:
            action, reason = "review", "reusable_result_may_cost_to_recreate"
        add(
            root / "cache" / cache["cacheKey"],
            kind="cache",
            identifier=cache["cacheKey"],
            bytes_used=cache["sizeBytes"],
            references=references,
            action=action,
            reason=reason,
            rebuild="A new provider call may be needed and can produce different output.",
            cloud=True,
            risk="Do not treat a model cache as cost-free disposable data.",
        )
    for evaluation in index["evaluations"]:
        grades = evaluation["grades"]
        action = "keep" if grades or evaluation["benchmarkSha256"] else "review"
        add(
            root / "evaluations" / evaluation["evaluationId"],
            kind="evaluation",
            identifier=evaluation["evaluationId"],
            bytes_used=evaluation["sizeBytes"],
            references=[f"run:{evaluation['runId']}"] if evaluation["runId"] else [],
            action=action,
            reason="human_review_or_benchmark_provenance"
            if action == "keep"
            else "evaluation_unknown",
            rebuild="The benchmark can be re-evaluated, but manual grades need independent preservation.",
            cloud=False,
            risk="May contain human assessment or a benchmark snapshot.",
        )
    review_root = root / "reviews"
    if review_root.is_dir() and not review_root.is_symlink():
        add(
            review_root,
            kind="quality_review_decisions",
            identifier="reviews",
            bytes_used=_bytes_and_files(review_root)[0],
            references=["cache quality eligibility"],
            action="keep",
            reason="human_review_decision_and_history",
            rebuild="Human approval/rejection cannot be reconstructed from model output; keep event history.",
            cloud=False,
            risk="Losing these records may allow rejected machine caches to be reused.",
        )
    for issue in index["issues"]:
        if issue["code"] != "unknown_enrichment_root_entry":
            continue
        path = Path(issue["path"])
        if not path.exists() or path.is_symlink():
            continue
        bytes_used = _bytes_and_files(path)[0] if path.is_dir() else path.stat().st_size
        add(
            path,
            kind="unknown",
            identifier=path.name,
            bytes_used=bytes_used,
            references=[],
            action="review",
            reason="purpose_and_references_unknown",
            rebuild="Not established; inspect manually before considering any cleanup.",
            cloud=None,
            risk="Unknown content must not be treated as disposable.",
        )
    candidates.sort(key=lambda row: (row["kind"], row["id"], row["path"]))
    counts = Counter(row["action"] for row in candidates)
    return {
        "schemaVersion": "1.0.0",
        "mode": "preview_only",
        "sourceRoot": str(root),
        "summary": {
            "keep": counts["keep"],
            "cleanupCandidate": counts["cleanup_candidate"],
            "review": counts["review"],
            "estimatedCandidateBytes": sum(row["estimatedReleaseBytes"] for row in candidates),
        },
        "items": candidates,
        "notes": [
            "No file is deleted, moved, compressed or renamed by this preview.",
            "Generated observer pages and thumbnails are excluded from source accounting.",
            "A cleanup candidate still requires the user's separate approval and a new task.",
        ],
    }
