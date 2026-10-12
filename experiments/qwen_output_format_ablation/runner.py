from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.metadata
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openai import DefaultHttpxClient, OpenAI, OpenAIError
from pydantic import ValidationError

from wechat_html_normalizer.enrichment.cli import DEFAULT_PROMPT_PATH, PROMPT_VERSION
from wechat_html_normalizer.enrichment.credentials import load_api_key
from wechat_html_normalizer.enrichment.dataset import prepare_dataset, verify_unchanged
from wechat_html_normalizer.enrichment.models import EnrichmentResult
from wechat_html_normalizer.enrichment.providers import (
    DEFAULT_DASHSCOPE_BASE_URL,
    DEFAULT_MODEL,
    MAX_QWEN_IMAGE_BYTES,
    _map_openai_error,
    strict_result_schema,
    validate_dashscope_base_url,
)
from wechat_html_normalizer.enrichment.security import (
    redact_sensitive_object,
    redact_sensitive_text,
)

from .models import CodeResult, DiagramResult

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[1]
VERSION = "qwen-output-format-ablation-v1"
VALIDATION_VERSION = "format-ablation-validation-v1"
SAMPLES = {
    "code": "b3d854ff82aee737216c582c6014e075e6c01f7e3a80d387c39a42eafe76debc",
    "diagram": "50933b2e0795615983dbae141f9c641af658c01428fc74634edd8e9742edad86",
}
# Frozen before any real output or benchmark score is read.
ORDER = [
    ("code", "A"),
    ("diagram", "B"),
    ("code", "C"),
    ("diagram", "D"),
    ("code", "B"),
    ("diagram", "A"),
    ("code", "D"),
    ("diagram", "C"),
]
PARAMETERS = {
    "model": DEFAULT_MODEL,
    "temperature": 0,
    "max_tokens": 4096,
    "stream": False,
    "extra_body": {"enable_thinking": False},
}


def now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_client(key: str, endpoint: str) -> OpenAI:
    # Reuse the installed SDK and existing HTTPS proxy; do not load an unused SOCKS proxy.
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    return OpenAI(
        api_key=key,
        base_url=validate_dashscope_base_url(endpoint),
        timeout=120,
        max_retries=0,
        http_client=DefaultHttpxClient(proxy=proxy, trust_env=False),
    )


def write_json(path: Path, value: Any) -> None:
    safe, _ = redact_sensitive_object(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(safe, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    tmp.replace(path)


def write_text(path: Path, value: str) -> None:
    safe, _ = redact_sensitive_text(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(safe, encoding="utf-8")
    tmp.replace(path)


def strict_schema(model: Any) -> dict:
    schema = model.model_json_schema(by_alias=True)

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            value.pop("default", None)
            if isinstance(value.get("properties"), dict):
                value["required"] = list(value["properties"])
                value["additionalProperties"] = False
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(schema)
    return schema


def get_dataset():
    items, summary, inventory_sha = prepare_dataset(
        PROJECT / "test_pictures",
        PROJECT / "output/image-annotation/annotation-002/inventory.json",
        prompt_version=VERSION,
        allow_external=True,
        selected_image_ids=set(SAMPLES.values()),
        policy_path=PROJECT / "config/image-enrichment-policy.json",
    )
    if {i.request.image.sha256 for i in items} != set(SAMPLES.values()):
        raise ValueError("the two approved hard cases are not available")
    for item in items:
        policy = item.request.policy
        if (
            policy.local_only
            or not policy.allow_external
            or "cloud" not in policy.allowed_backend_types
        ):
            raise ValueError("selected hard case is not authorized for cloud processing")
    return (
        {
            name: next(i for i in items if i.request.image.sha256 == sha)
            for name, sha in SAMPLES.items()
        },
        summary,
        inventory_sha,
    )


def authority_snapshot() -> dict[str, str]:
    files: set[Path] = set()
    for folder in (
        "src",
        "schemas",
        "test_pictures",
        "output/image-enrichment/runs",
        "output/image-enrichment/cache",
        "output/image-enrichment/evaluations",
        "experiments/lan_vlm_qwen25_3b/runs",
        "experiments/lan_vlm_qwen25_3b/cache",
    ):
        files.update(
            p
            for p in (PROJECT / folder).rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
        )
    files.update(
        PROJECT / p
        for p in (
            "config/image-enrichment-policy.json",
            "pyproject.toml",
            "output/image-annotation/annotation-002/inventory.json",
            "output/image-annotation/annotation-002/图片辅助标注.xlsx",
        )
    )
    return {p.relative_to(PROJECT).as_posix(): file_sha(p) for p in sorted(files) if p.is_file()}


def make_cell(item: Any, sample: str, arm: str, endpoint: str = DEFAULT_DASHSCOPE_BASE_URL) -> dict:
    model = CodeResult if sample == "code" else DiagramResult
    schema = strict_result_schema() if arm == "A" else strict_schema(model)
    context = item.request.context.model_dump(mode="json", by_alias=True)
    if arm == "A":
        system = DEFAULT_PROMPT_PATH.read_text(encoding="utf-8")
        user = {
            "task": "分析随附图片并返回严格符合 Schema 的 enrichment 结果。",
            "context": context,
            "contextAvailable": item.request.context.available,
        }
        prompt_version = PROMPT_VERSION
    else:
        system = (ROOT / "prompts" / f"{sample}-v1.md").read_text(encoding="utf-8")
        suffix = "json-v1.md" if arm in ("B", "C") else f"{sample}-free-v1.md"
        system += "\n" + (ROOT / "prompts" / suffix).read_text(encoding="utf-8")
        user = {
            "task": "依据图片完成上述内容任务。",
            "context": context,
            "contextAvailable": item.request.context.available,
        }
        if arm in ("B", "C"):
            user["outputSchema"] = schema
        prompt_version = VERSION + "-" + sample + ("-json" if arm in ("B", "C") else "-free")
    user_text = json.dumps(user, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    cell = {
        "endpoint": endpoint,
        "cellId": f"{sample}--{arm}--{item.request.image.sha256[:8]}",
        "sample": sample,
        "arm": arm,
        "imageSha256": item.request.image.sha256,
        "imageRelativePath": item.request.image.relative_path,
        "mediaType": item.request.image.media_type,
        "image": item.request.image.model_dump(mode="json", by_alias=True),
        "policy": item.request.policy.model_dump(mode="json", by_alias=True),
        "contextFingerprintSha256": item.request.context_fingerprint_sha256,
        "messagesText": {"system": system, "user": user_text},
        "messagesFingerprintSha256": digest({"system": system, "user": user_text}),
        "promptVersion": prompt_version,
        "promptSha256": hashlib.sha256(system.encode()).hexdigest(),
        "schema": schema,
        "schemaSha256": digest(schema),
        "responseFormatMode": "json_schema_strict" if arm in ("A", "B") else "omitted",
        "parameters": PARAMETERS,
        "validationVersion": VALIDATION_VERSION,
        "imagePreprocessing": "original bytes; no resize/re-encoding; detail omitted as production",
    }
    cell["identitySha256"] = digest(cell)
    return cell


def generation_kwargs(cell: dict, data_url: str) -> dict:
    kwargs = {
        **cell["parameters"],
        "messages": [
            {"role": "system", "content": cell["messagesText"]["system"]},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": cell["messagesText"]["user"]},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
    }
    if cell["responseFormatMode"] == "json_schema_strict":
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "image_enrichment_result_v1"
                if cell["arm"] == "A"
                else "specialist_minimal_v1",
                "strict": True,
                "schema": cell["schema"],
            },
        }
    return kwargs


def validate_plan(plan: dict) -> None:
    if len(plan["cells"]) != 8 or len({c["cellId"] for c in plan["cells"]}) != 8:
        raise ValueError("experiment must contain exactly eight unique cells")
    if [(c["sample"], c["arm"]) for c in plan["cells"]] != ORDER:
        raise ValueError("unexpected frozen execution order")
    for sample in SAMPLES:
        cells = {c["arm"]: c for c in plan["cells"] if c["sample"] == sample}
        b, c = (
            generation_kwargs(cells["B"], "memory-only"),
            generation_kwargs(cells["C"], "memory-only"),
        )
        b.pop("response_format")
        if b != c:
            raise ValueError("B/C differ in more than response_format")
        for arm in ("C", "D"):
            request = generation_kwargs(cells[arm], "memory-only")
            if any(k in request for k in ("response_format", "tools", "tool_choice")):
                raise ValueError("C/D must omit response_format and tool modes")


def dry_run(args: Any) -> int:
    items, _, inv_sha = get_dataset()
    target = run_dir(args.run_name)
    target.mkdir(parents=True, exist_ok=False)
    plan = {
        "schemaVersion": "1.0.0",
        "experimentVersion": VERSION,
        "createdAt": now(),
        "endpoint": validate_dashscope_base_url(args.base_url),
        "region": "beijing",
        "inventorySha256": inv_sha,
        "sdkRetries": 0,
        "transportRetries": 0,
        "structuredRetries": 0,
        "generationBudget": 8,
        "cells": [make_cell(items[sample], sample, arm, args.base_url) for sample, arm in ORDER],
        "environment": {
            "python": sys.version,
            "project": importlib.metadata.version("wechat-html-normalizer"),
            "openai": importlib.metadata.version("openai"),
        },
    }
    validate_plan(plan)
    write_json(target / "plan.json", plan)
    for sample in SAMPLES:
        write_json(
            target / "schemas" / f"{sample}-minimal-v1.schema.json",
            strict_schema(CodeResult if sample == "code" else DiagramResult),
        )
    write_json(target / "schemas" / "full-current.schema.json", strict_result_schema())
    write_json(target / "authority-before.json", authority_snapshot())
    write_json(
        target / "request-differences.json",
        {
            "BvsC": "messages, image bytes, context, all non-format parameters identical; only response_format differs",
            "AvsB": "full generic task plus type/action routing vs known-type specialist; both Prompt and Schema change",
            "CvsD": "same facts requested, different output instructions; not a single decoding-variable experiment",
            "humanBenchmarkIncluded": False,
            "imageDataPersisted": False,
            "sdkAutomaticRetries": 0,
            "status": "offline_verified",
        },
    )
    print(
        json.dumps(
            {
                "run": args.run_name,
                "status": "dry_run",
                "tasks": 8,
                "apiCalls": 0,
                "BCEqualExceptFormat": True,
            }
        )
    )
    return 0


def run_dir(name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", name):
        raise ValueError("run name must be a simple filename")
    return ROOT / "runs" / name


def parse_content(cell: dict, response: dict) -> dict:
    content = response.get("content")
    parsed = None
    syntax, schema, business = "not_applicable", "not_applicable", "not_applicable"
    issues: list[dict] = []
    if not isinstance(content, str):
        return {"status": "invalid_response", "issues": ["response content is not text"]}
    if cell["arm"] != "D":
        try:
            parsed = json.loads(content)  # no fence removal or permissive JSON repair
            syntax = "valid"
        except ValueError as exc:
            syntax = "invalid"
            issues.append({"code": "invalid_json", "message": str(exc)})
        if syntax == "valid":
            try:
                model = (
                    EnrichmentResult
                    if cell["arm"] == "A"
                    else (CodeResult if cell["sample"] == "code" else DiagramResult)
                )
                model.model_validate(parsed)
                schema = "valid"
                if cell["arm"] == "A":
                    try:
                        model.model_validate(parsed, context={"enforce_current_policy": True})
                        business = "valid"
                    except ValidationError as exc:
                        business = "invalid"
                        issues.extend(_validation_issues(exc))
                else:
                    business = "pending_content_review"
            except ValidationError as exc:
                schema = "invalid"
                issues.extend(_validation_issues(exc))
    formal_code = None
    conversion = None
    if cell["sample"] == "code":
        if cell["arm"] == "D":
            match = re.fullmatch(r"```[^\n`]*\r?\n([\s\S]*?)\r?\n```\s*", content)
            formal_code = match.group(1) if match else content
            conversion = (
                "removed single outer code fence"
                if match
                else "verbatim; no matching single outer fence"
            )
        elif isinstance(parsed, dict):
            formal_code = (
                (parsed.get("code") or {}).get("text")
                if cell["arm"] == "A"
                else parsed.get("codeText")
            )
    status = "received"
    if response.get("model") != cell["parameters"]["model"]:
        status = "model_mismatch"
    elif response.get("finishReason") == "length":
        status = "truncated"
    elif cell["arm"] != "D" and (
        syntax == "invalid" or schema == "invalid" or business == "invalid"
    ):
        status = "invalid_output"
    return {
        "status": status,
        "jsonSyntax": syntax,
        "schemaValidation": schema,
        "businessValidation": business,
        "parsed": parsed,
        "formalCodeText": formal_code,
        "codeConversion": conversion,
        "issues": issues,
        "quality": "pending_review",
    }


def _validation_issues(exc: ValidationError) -> list[dict]:
    return [
        {"location": list(e["loc"]), "type": e["type"], "message": e["msg"]}
        for e in exc.errors(include_url=False, include_input=False)
    ]


def summarize(folder: Path, plan: dict) -> dict:
    items = []
    usage_sums: dict[str, int] = {}
    known = 0
    elapsed = 0
    for cell in plan["cells"]:
        directory = folder / "cells" / cell["cellId"]
        response_path = directory / "response.json"
        analysis_path = directory / "analysis.json"
        if response_path.exists():
            response = json.loads(response_path.read_text(encoding="utf-8"))
            if not (directory / "content.txt").exists():
                write_text(directory / "content.txt", response.get("content") or "")
            analysis = parse_content(cell, response)
            write_json(analysis_path, analysis)
            usage = response.get("usage")
            if isinstance(usage, dict):
                known += 1
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    if isinstance(usage.get(key), int):
                        usage_sums[key] = usage_sums.get(key, 0) + usage[key]
                details = usage.get("prompt_tokens_details") or {}
                for key in ("image_tokens", "cached_tokens"):
                    if isinstance(details.get(key), int):
                        usage_sums[key] = usage_sums.get(key, 0) + details[key]
            elapsed += response["elapsedMs"]
            status = analysis["status"]
        elif (directory / "error.json").exists():
            status = "transport_or_api_error"
        elif (directory / "reservation.json").exists():
            status = "interrupted_outcome_unknown_do_not_resend"
        else:
            status = "not_attempted"
        items.append(
            {
                "cellId": cell["cellId"],
                "sample": cell["sample"],
                "arm": cell["arm"],
                "imageSha256": cell["imageSha256"],
                "status": status,
                "responsePath": response_path.relative_to(folder).as_posix()
                if response_path.exists()
                else None,
                "analysisPath": analysis_path.relative_to(folder).as_posix()
                if analysis_path.exists()
                else None,
            }
        )
    reservations = list((folder / "cells").glob("*/reservation.json"))
    before = json.loads((folder / "authority-before.json").read_text(encoding="utf-8"))
    after = authority_snapshot()
    unchanged = before == after
    write_json(
        folder / "integrity.json",
        {
            "unchanged": unchanged,
            "files": len(before),
            "changedOrMissing": sorted(k for k in before if before[k] != after.get(k)),
            "newAuthorityPaths": sorted(set(after) - set(before)),
        },
    )
    manifest = {
        "schemaVersion": "1.0.0",
        "experimentVersion": VERSION,
        "updatedAt": now(),
        "planSha256": file_sha(folder / "plan.json"),
        "items": items,
        "apiCallsReserved": len(reservations),
        "responsesReceived": sum(i["responsePath"] is not None for i in items),
        "callsWithUsage": known,
        "receivedUsage": usage_sums or None,
        "elapsedMsReceived": elapsed,
        "authorityUnchanged": unchanged,
        "status": "completed_evidence"
        if all(i["status"] != "not_attempted" for i in items)
        else "incomplete",
        "qualityStatus": "pending_codex_and_user_review",
        "cost": None,
        "costNote": "usage preserved; billing not inferred from generation success",
    }
    review_path = folder / "evaluation" / "quality-review.json"
    if review_path.exists():
        manifest["qualityStatus"] = "codex_assessed_user_pending"
        manifest["qualityReviewPath"] = review_path.relative_to(folder).as_posix()
    write_json(folder / "run-manifest.json", manifest)
    return manifest


def execute(args: Any, client: Any = None) -> int:
    folder = run_dir(args.run_name).resolve(strict=True)
    plan = json.loads((folder / "plan.json").read_text(encoding="utf-8"))
    validate_plan(plan)
    if not args.allow_external:
        raise ValueError("run requires explicit --allow-external")
    items, _, inv_sha = get_dataset()
    if inv_sha != plan["inventorySha256"]:
        raise ValueError("inventory changed after dry-run")
    # New run plans must be created offline; completed cells are never resent.
    for cell in plan["cells"]:
        if make_cell(items[cell["sample"]], cell["sample"], cell["arm"], plan["endpoint"]) != cell:
            raise ValueError("frozen Prompt/Schema/context/policy changed; do not run this plan")
    lock = folder / "active.lock"
    with lock.open("x", encoding="utf-8") as lock_file:
        lock_file.write(now())
    calls_this_execution = 0
    try:
        for cell in plan["cells"]:
            directory = folder / "cells" / cell["cellId"]
            if (directory / "reservation.json").exists():
                continue
            if len(list((folder / "cells").glob("*/reservation.json"))) >= 8:
                raise RuntimeError("generation budget exhausted")
            item = items[cell["sample"]]
            # Recheck local-only policy, controlled path and actual binary immediately before POST.
            current, _, _ = get_dataset()
            policy = current[cell["sample"]].request.policy
            if policy.local_only or not policy.allow_external:
                raise ValueError("external policy was revoked")
            if file_sha(item.path) != cell["imageSha256"]:
                raise ValueError("image bytes changed")
            raw = item.path.read_bytes()
            if len(raw) > MAX_QWEN_IMAGE_BYTES:
                raise ValueError("image exceeds provider limit")
            if client is None:
                key, _ = load_api_key(PROJECT / ".env")
                if not key:
                    raise ValueError("DASHSCOPE_API_KEY is missing; dry-run is available")
                client = make_client(key, plan["endpoint"])
            data_url = f"data:{cell['mediaType']};base64," + base64.b64encode(raw).decode("ascii")
            write_json(directory / "request-metadata.json", cell)
            write_json(
                directory / "reservation.json",
                {
                    "createdAt": now(),
                    "attempt": 1,
                    "identitySha256": cell["identitySha256"],
                    "note": "never resend a reserved cell automatically",
                },
            )
            started = time.perf_counter()
            print(f"calling {cell['cellId']}", flush=True)
            try:
                calls_this_execution += 1
                response = client.chat.completions.create(**generation_kwargs(cell, data_url))
            except OpenAIError as exc:
                error = _map_openai_error(exc)
                body = getattr(exc, "body", None)
                write_json(
                    directory / "error.json",
                    {
                        "code": error.code,
                        "statusCode": error.status_code,
                        "message": error.safe_message,
                        "body": body,
                        "elapsedMs": round((time.perf_counter() - started) * 1000),
                    },
                )
                if error.code in ("authentication_failed", "permission_denied") or (
                    "model" in str(body).lower()
                    and any(
                        w in str(body).lower()
                        for w in ("not exist", "not found", "invalid model", "not supported")
                    )
                ):
                    break
                continue
            elapsed = round((time.perf_counter() - started) * 1000)
            choice = response.choices[0] if response.choices else None
            content = choice.message.content if choice else None
            raw_record = {
                "attempt": 1,
                "receivedAt": now(),
                "responseId": response.id,
                "requestId": getattr(response, "_request_id", None),
                "model": response.model,
                "finishReason": choice.finish_reason if choice else None,
                "content": content,
                "elapsedMs": elapsed,
                "usage": response.usage.model_dump(mode="json") if response.usage else None,
            }
            # Response first; even a subsequent parser/manifest crash cannot discard billed evidence.
            write_json(directory / "response.json", raw_record)
            write_text(directory / "content.txt", content if isinstance(content, str) else "")
            write_json(directory / "analysis.json", parse_content(cell, raw_record))
        verify_unchanged(list(items.values()))
        manifest = summarize(folder, plan)
        write_json(
            folder / "last-execution.json",
            {
                "completedAt": now(),
                "apiCallsThisExecution": calls_this_execution,
                "totalReserved": manifest["apiCallsReserved"],
                "responsesReceived": manifest["responsesReceived"],
                "note": "an execution summary, not an additional model run",
            },
        )
        print(
            json.dumps(
                {
                    "apiCallsThisExecution": calls_this_execution,
                    **{
                        k: manifest[k]
                        for k in (
                            "status",
                            "apiCallsReserved",
                            "responsesReceived",
                            "receivedUsage",
                        )
                    },
                },
                indent=2,
            )
        )
        return 0 if manifest["responsesReceived"] == 8 else 1
    finally:
        lock.unlink(
            missing_ok=True
        )  # transient exclusive lock only; no historical evidence removed


def recover(args: Any) -> int:
    folder = run_dir(args.run_name).resolve(strict=True)
    if (folder / "active.lock").exists():
        raise ValueError(
            "run is active or was interrupted; inspect its exclusive lock before recovery"
        )
    plan = json.loads((folder / "plan.json").read_text(encoding="utf-8"))
    validate_plan(plan)
    manifest = summarize(folder, plan)
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "apiCalls": 0,
                "responses": manifest["responsesReceived"],
            }
        )
    )
    return 0


def audit(args: Any) -> int:
    folder = run_dir(args.run_name).resolve(strict=True)
    plan = json.loads((folder / "plan.json").read_text(encoding="utf-8"))
    manifest = summarize(folder, plan)
    rows = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.name == "cleanup-preview.json":
            continue
        bytecode = path.suffix == ".pyc"
        raw_response = path.name in ("response.json", "content.txt")
        rows.append(
            {
                "path": str(path.resolve()),
                "relativePath": path.relative_to(ROOT).as_posix(),
                "sizeBytes": path.stat().st_size,
                "sha256": file_sha(path),
                "suggestedAction": "can_cleanup" if bytecode else "retain",
                "reason": "Regenerable Python bytecode"
                if bytecode
                else "Experiment contract, first-test evidence, assessment or repeatable code",
                "cloudRebuildRequired": raw_response,
                "risk": "Original first-test response cannot be reproduced exactly; new model call may cost money"
                if raw_response
                else None,
                "deleted": False,
            }
        )
    preview = {
        "schemaVersion": "1.0.0",
        "deleted": 0,
        "summary": {
            "files": len(rows),
            "bytes": sum(r["sizeBytes"] for r in rows),
            "retain": sum(r["suggestedAction"] == "retain" for r in rows),
            "canCleanup": sum(r["suggestedAction"] == "can_cleanup" for r in rows),
        },
        "items": rows,
    }
    write_json(ROOT / "reports" / "cleanup-preview.json", preview)
    print(
        json.dumps(
            {
                "apiCalls": 0,
                "authorityUnchanged": manifest["authorityUnchanged"],
                "cleanup": preview["summary"],
            }
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Round 11 isolated eight-call output-format experiment"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("dry-run", "run", "recover", "audit"):
        child = sub.add_parser(command)
        child.add_argument("--run-name", required=True)
        child.add_argument("--base-url", default=DEFAULT_DASHSCOPE_BASE_URL)
        child.add_argument("--allow-external", action="store_true")
    args = parser.parse_args(argv)
    return {"dry-run": dry_run, "run": execute, "recover": recover, "audit": audit}[args.command](
        args
    )


if __name__ == "__main__":
    raise SystemExit(main())
