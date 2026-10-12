from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .credentials import DEFAULT_ENV_FILE, load_api_key
from .dataset import prepare_dataset
from .evaluation import evaluate_run
from .models import (
    BatchRunManifest,
    EnrichmentRequest,
    EnrichmentResult,
    EnrichmentRunRecord,
    QualityReviewDecision,
)
from .providers import (
    DEFAULT_DASHSCOPE_BASE_URL,
    DEFAULT_MODEL,
    DashScopeOpenAIProvider,
    validate_dashscope_base_url,
)
from .runner import load_prompt, run_batch

PROMPT_VERSION = "image-enrichment-v1"
DEFAULT_PROMPT_PATH = Path(__file__).with_name("prompts") / f"{PROMPT_VERSION}.md"
FROZEN_PROMPT_SHA256 = {
    "compact-json-v1": "7db1cca91f4131ff32ea18b367603ab6147d2a1bc9fc2df9ddcc8905da0621f2",
    "image-enrichment-v1": "d81daa6b18608d8cc2dae1ff46b83bd69abf0a3e82172015de4c76e5660eb4f9",
    "image-enrichment-v2": "4096d161eeb0f364523619e3d48de73e802d78a8da1e90643b251feede0267a9",
}


def _run_parser(subparsers) -> None:
    parser = subparsers.add_parser("run", help="Run or preview image enrichment.")
    parser.add_argument("--input-root", type=Path, default=Path("test_pictures"))
    parser.add_argument(
        "--inventory",
        type=Path,
        default=Path("output/image-annotation/annotation-002/inventory.json"),
    )
    parser.add_argument("--output", type=Path, default=Path("output/image-enrichment"))
    parser.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT_PATH)
    parser.add_argument("--prompt-version", default=PROMPT_VERSION)
    parser.add_argument(
        "--policy",
        type=Path,
        default=Path("config/image-enrichment-policy.json"),
        help="Local-only and external-processing overrides keyed by full image SHA-256.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=DEFAULT_DASHSCOPE_BASE_URL)
    parser.add_argument("--region", default="beijing", choices=["beijing"])
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
        help="UTF-8 credential file (default: .env); process environment takes precedence.",
    )
    parser.add_argument("--run-name")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-external", action="store_true")
    parser.add_argument("--max-images", type=int)
    parser.add_argument(
        "--image-id",
        action="append",
        help="Process one full SHA-256 imageId; repeat to select a small representative set.",
    )
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument(
        "--max-structured-retries",
        type=int,
        default=1,
        help="Retry strict structured-output validation failures at most this many times.",
    )
    parser.add_argument("--requests-per-minute", type=float, default=12)
    parser.add_argument("--max-completion-tokens", type=int, default=4096)
    parser.add_argument("--enable-thinking", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--profile", choices=["legacy", "compact-json-v1"], default="legacy")
    parser.add_argument(
        "--task-timeout",
        type=float,
        default=180,
        help="Compact profile: per-image total seconds (1-600).",
    )
    parser.add_argument(
        "--recovery-max-tokens",
        type=int,
        default=8192,
        help="Compact profile hard output ceiling (<=8192).",
    )


def _evaluate_parser(subparsers) -> None:
    parser = subparsers.add_parser("evaluate", help="Compare a completed run with benchmark v1.")
    parser.add_argument("--run-manifest", type=Path, required=True)
    parser.add_argument(
        "--workbook",
        type=Path,
        default=Path("output/image-annotation/annotation-002/图片辅助标注.xlsx"),
    )
    parser.add_argument("--input-root", type=Path, default=Path("test_pictures"))
    parser.add_argument("--output", type=Path, default=Path("output/image-enrichment/evaluations"))


def _schema_parser(subparsers) -> None:
    parser = subparsers.add_parser("write-schemas", help="Export enrichment JSON Schemas.")
    parser.add_argument("--output", type=Path, default=Path("schemas"))


def _observe_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "observe", help="Build or inspect the offline read-only observer."
    )
    parser.add_argument(
        "operation", choices=["build", "open", "audit", "cleanup-preview", "strict"]
    )
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--input-root", type=Path, default=Path("test_pictures"))
    parser.add_argument(
        "--inventory",
        type=Path,
        default=Path("output/image-annotation/annotation-002/inventory.json"),
    )
    parser.add_argument(
        "--workbook",
        type=Path,
        default=Path("output/image-annotation/annotation-002/图片辅助标注.xlsx"),
    )
    parser.add_argument("--enrichment-root", type=Path, default=Path("output/image-enrichment"))
    parser.add_argument("--site-root", type=Path, default=Path("output/image-enrichment/observer"))
    parser.add_argument("--policy", type=Path, default=Path("config/image-enrichment-policy.json"))
    parser.add_argument("--aliases", type=Path, default=Path("config/image-observer-aliases.json"))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="kb-enrich-images",
        description="Rebuildable image enrichment without modifying normalized documents.",
    )
    subparsers = result.add_subparsers(dest="command", required=True)
    _run_parser(subparsers)
    _evaluate_parser(subparsers)
    _schema_parser(subparsers)
    _observe_parser(subparsers)
    recover = subparsers.add_parser("recover", help="Offline rebuild of a compact run manifest.")
    recover.add_argument("--run-manifest", type=Path, required=True)
    review = subparsers.add_parser(
        "review", help="Record a user's quality decision without rewriting machine results."
    )
    review.add_argument("--output", type=Path, default=Path("output/image-enrichment"))
    review.add_argument("--cache-key", required=True)
    review.add_argument(
        "--status", choices=["pending", "needs_review", "approved", "rejected"], required=True
    )
    review.add_argument("--reason", required=True)
    return result


def _observe(args: argparse.Namespace) -> int:
    from .observer import cleanup_preview, collect_index
    from .observer_site import build_site, write_audit, write_preview

    enrichment_root = args.enrichment_root.resolve(strict=True)
    site_root = args.site_root.resolve()
    if site_root == enrichment_root or not site_root.is_relative_to(enrichment_root):
        raise ValueError("observer site must be a dedicated directory inside enrichment root")
    if site_root.parts[len(enrichment_root.parts)] in ("cache", "runs", "evaluations"):
        raise ValueError("observer site must not overlap cache, runs or evaluations")
    if args.operation == "open" and (site_root / "index.html").is_file():
        import webbrowser

        entry = site_root / "index.html"
        opened = webbrowser.open(entry.as_uri())
        print(json.dumps({"entry": str(entry), "opened": opened}, ensure_ascii=False))
        return 0 if opened else 2
    index = collect_index(
        project_root=args.project_root,
        image_root=args.input_root,
        inventory_path=args.inventory,
        workbook_path=args.workbook,
        enrichment_root=enrichment_root,
        policy_path=args.policy,
        alias_path=args.aliases,
    )
    if args.operation == "strict":
        errors = [issue for issue in index["issues"] if issue["severity"] == "error"]
        print(
            json.dumps(
                {"errors": errors, "summary": index["summary"]}, ensure_ascii=False, indent=2
            )
        )
        return 1 if errors else 0
    site_root.mkdir(parents=True, exist_ok=True)
    if args.operation == "audit":
        write_audit(index, site_root)
        result = {
            "audit": str(site_root / "storage-audit.json"),
            "summary": {
                "categories": index["storage"]["categories"],
                "totalFiles": index["storage"]["totalFiles"],
                "totalBytes": index["storage"]["totalBytes"],
                "duplicateGroups": len(index["storage"]["duplicateGroups"]),
                "issues": len(index["issues"]),
            },
        }
    elif args.operation == "cleanup-preview":
        preview = cleanup_preview(index)
        write_preview(preview, site_root)
        result = {"preview": str(site_root / "cleanup-preview.json"), "summary": preview["summary"]}
    else:
        result = build_site(index, site_root)
        if args.operation == "open":
            import webbrowser

            result["opened"] = webbrowser.open(Path(result["entry"]).as_uri())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _write_schemas(target: Path) -> list[Path]:
    target.mkdir(parents=True, exist_ok=True)
    models = {
        "image-enrichment-request.schema.json": EnrichmentRequest,
        "image-enrichment-result.schema.json": EnrichmentResult,
        "image-enrichment-record.schema.json": EnrichmentRunRecord,
        "image-enrichment-run.schema.json": BatchRunManifest,
        "image-enrichment-review.schema.json": QualityReviewDecision,
    }
    paths = []
    for name, model in models.items():
        path = target / name
        path.write_text(
            json.dumps(model.model_json_schema(by_alias=True), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        paths.append(path)
    from .compact import COMPACT_ADAPTER

    path = target / "image-enrichment-compact.schema.json"
    path.write_text(
        json.dumps(COMPACT_ADAPTER.json_schema(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    paths.append(path)
    return paths


def _run(args: argparse.Namespace) -> int:
    if args.max_images is not None and args.max_images < 1:
        raise ValueError("--max-images must be at least 1")
    if args.max_retries < 0:
        raise ValueError("--max-retries cannot be negative")
    if args.max_structured_retries < 0:
        raise ValueError("--max-structured-retries cannot be negative")
    if args.max_completion_tokens < 1:
        raise ValueError("--max-completion-tokens must be at least 1")
    if args.force and args.dry_run:
        raise ValueError("--force and --dry-run cannot be combined")
    base_url = validate_dashscope_base_url(args.base_url, region=args.region)
    if args.profile == "compact-json-v1":
        return _run_compact(args, base_url)
    prompt, prompt_sha256 = load_prompt(args.prompt)
    expected_prompt_hash = FROZEN_PROMPT_SHA256.get(args.prompt_version)
    if expected_prompt_hash and expected_prompt_hash != prompt_sha256:
        raise ValueError(
            f"frozen prompt {args.prompt_version!r} changed; create a new prompt version instead"
        )
    api_key, api_key_source = load_api_key(args.env_file)
    effective_dry_run = args.dry_run
    missing_key = not api_key and not effective_dry_run
    if missing_key:
        effective_dry_run = True
        print(
            "DASHSCOPE_API_KEY is not set; performing a dry-run preflight only.",
            file=sys.stderr,
        )
    elif api_key and not effective_dry_run:
        print(f"DASHSCOPE_API_KEY loaded from {api_key_source}.", file=sys.stderr)
    prepared, dataset, inventory_sha256 = prepare_dataset(
        args.input_root,
        args.inventory,
        prompt_version=args.prompt_version,
        allow_external=args.allow_external,
        max_images=args.max_images,
        selected_image_ids=set(args.image_id) if args.image_id else None,
        policy_path=args.policy,
    )
    provider = None
    if not effective_dry_run:
        if not args.allow_external:
            print(
                "External processing was not authorized; no request will be sent.", file=sys.stderr
            )
        else:
            provider = DashScopeOpenAIProvider(
                api_key or "",
                base_url=base_url,
                region=args.region,
            )
    path, manifest = run_batch(
        prepared,
        dataset,
        inventory_sha256=inventory_sha256,
        output_root=args.output,
        prompt=prompt,
        prompt_sha256=prompt_sha256,
        provider=provider,
        provider_name=DashScopeOpenAIProvider.name,
        model=args.model,
        endpoint=base_url,
        region=args.region,
        enable_thinking=args.enable_thinking,
        max_completion_tokens=args.max_completion_tokens,
        max_retries=args.max_retries,
        max_structured_retries=args.max_structured_retries,
        requests_per_minute=args.requests_per_minute,
        dry_run=effective_dry_run,
        force=args.force,
        run_name=args.run_name,
    )
    print(
        json.dumps(
            {
                "runManifest": str(path),
                "status": manifest.status,
                "dataset": manifest.dataset,
                "summary": manifest.summary,
                "metrics": (
                    manifest.metrics.model_dump(mode="json", by_alias=True)
                    if manifest.metrics
                    else None
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if missing_key or (not effective_dry_run and not args.allow_external):
        return 2
    return 0


def _run_compact(args: argparse.Namespace, base_url: str) -> int:
    from .compact import PROFILE
    from .compact_provider import CompactDashScopeProvider
    from .compact_runner import profile_config, run_compact_batch

    if args.enable_thinking or args.force:
        raise ValueError(
            "compact-json-v1 fixes thinking=false and does not force-overwrite machine cache"
        )
    if args.prompt != DEFAULT_PROMPT_PATH or args.prompt_version != PROMPT_VERSION:
        raise ValueError(
            "compact-json-v1 uses its own versioned Prompt; do not override legacy --prompt options"
        )
    prompt_path = Path(__file__).with_name("prompts") / "compact-json-v1.md"
    prompt, prompt_sha = load_prompt(prompt_path)
    if prompt_sha != FROZEN_PROMPT_SHA256[PROFILE]:
        raise ValueError(
            "compact-json-v1 Prompt changed; publish a new version instead of rewriting its identity"
        )
    profile_config(
        model=args.model,
        endpoint=base_url,
        prompt_sha256=prompt_sha,
        max_tokens=args.max_completion_tokens,
        hard_token_limit=args.recovery_max_tokens,
        task_timeout=args.task_timeout,
    )
    key, _ = load_api_key(args.env_file)
    missing_key = not key and not args.dry_run
    dry = args.dry_run or missing_key
    if missing_key:
        print("DASHSCOPE_API_KEY is missing; compact preflight only.", file=sys.stderr)
    prepared, dataset, inventory_sha = prepare_dataset(
        args.input_root,
        args.inventory,
        prompt_version=PROFILE,
        allow_external=args.allow_external,
        max_images=args.max_images,
        selected_image_ids=set(args.image_id) if args.image_id else None,
        policy_path=args.policy,
    )
    provider = (
        None
        if dry or not args.allow_external
        else CompactDashScopeProvider(key or "", base_url=base_url, region=args.region)
    )
    path, manifest = run_compact_batch(
        prepared,
        dataset,
        inventory_sha256=inventory_sha,
        output_root=args.output,
        prompt=prompt,
        prompt_sha256=prompt_sha,
        provider=provider,
        model=args.model,
        endpoint=base_url,
        region=args.region,
        max_completion_tokens=args.max_completion_tokens,
        hard_token_limit=args.recovery_max_tokens,
        task_timeout=args.task_timeout,
        run_name=args.run_name,
        dry_run=dry,
        policy_path=args.policy,
        requests_per_minute=args.requests_per_minute,
    )
    print(
        json.dumps(
            {
                "runManifest": str(path),
                "status": manifest.status,
                "summary": manifest.summary,
                "metrics": manifest.metrics.model_dump(mode="json", by_alias=True)
                if manifest.metrics
                else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return (
        2
        if missing_key or (not dry and not args.allow_external) or manifest.status == "blocked"
        else 0
    )


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    try:
        if args.command == "run":
            status = _run(args)
        elif args.command == "evaluate":
            path, report = evaluate_run(
                args.run_manifest, args.workbook, args.input_root, args.output
            )
            print(
                json.dumps(
                    {
                        "evaluation": str(path),
                        "results": report["results"],
                        "benchmark": report["benchmark"],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            status = 0
        elif args.command == "observe":
            status = _observe(args)
        elif args.command == "recover":
            from .compact_runner import rebuild_manifest

            path = args.run_manifest.resolve(strict=True)
            current = BatchRunManifest.model_validate_json(path.read_text(encoding="utf-8"))
            if current.processing_profile != "compact-json-v1" or path.name != "run-manifest.json":
                raise ValueError(
                    "offline recovery currently supports compact-json-v1 run manifests only"
                )
            manifest = rebuild_manifest(path.parent, path.parents[2])
            print(
                json.dumps(
                    {
                        "runManifest": str(path),
                        "apiCallsThisRecovery": 0,
                        "metrics": manifest.metrics.model_dump(mode="json", by_alias=True)
                        if manifest.metrics
                        else None,
                    },
                    indent=2,
                )
            )
            status = 0
        elif args.command == "review":
            from .models import ReviewStatus
            from .reviews import set_review

            path = set_review(args.output, args.cache_key, ReviewStatus(args.status), args.reason)
            print(json.dumps({"reviewPath": str(path), "reviewStatus": args.status}))
            status = 0
        else:
            paths = _write_schemas(args.output)
            print("\n".join(str(path) for path in paths))
            status = 0
    except (FileExistsError, OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        status = 2
    raise SystemExit(status)


if __name__ == "__main__":
    main()
