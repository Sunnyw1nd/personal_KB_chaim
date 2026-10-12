from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

from .batch import BatchNormalizationError, normalize_sample_archives
from .migration import MigrationPreviewError, preview_legacy_outputs
from .models import Document, DocumentRecord, ProcessingReport, ResourceManifest
from .normalizer import (
    NormalizationError,
    normalize_saved_article,
    normalize_wechat_article,
    normalize_zhihu_article,
)
from .sample_cache import SamplePreparationError, prepare_sample


def _configure_console_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")


def _write_schemas(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    models = {
        "document.schema.json": Document,
        "processing-report.schema.json": ProcessingReport,
        "resource-manifest.schema.json": ResourceManifest,
        "document-record.schema.json": DocumentRecord,
    }
    for name, model in models.items():
        (directory / name).write_text(
            json.dumps(
                model.model_json_schema(by_alias=True), ensure_ascii=False, indent=2, sort_keys=True
            )
            + "\n",
            encoding="utf-8",
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Normalize one article or every ZIP sample using recognized offline WeChat "
            "and Zhihu adapters without executing or downloading resources."
        )
    )
    parser.add_argument("html_path", nargs="?", type=Path)
    parser.add_argument("resource_directory", nargs="?", type=Path)
    parser.add_argument("output_directory", nargs="?", type=Path)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--write-schemas", type=Path, metavar="DIRECTORY")
    actions.add_argument("--prepare-sample", type=Path, metavar="ZIP")
    actions.add_argument(
        "--normalize-samples",
        action="store_true",
        help="Prepare and normalize every ZIP in --samples-root.",
    )
    actions.add_argument(
        "--preview-migration",
        type=Path,
        metavar="LEGACY_OUTPUT_ROOT",
        help="Scan legacy output directories and produce a read-only migration preview.",
    )
    parser.add_argument("--adapter", choices=["auto", "wechat", "zhihu"], default="auto")
    parser.add_argument("--samples-root", type=Path, default=Path("samples"))
    parser.add_argument("--work-root", type=Path, default=Path(".work"))
    parser.add_argument(
        "--output-directory",
        dest="batch_output_directory",
        type=Path,
        default=Path("output/documents"),
        help="Batch output root used with --normalize-samples.",
    )
    parser.add_argument(
        "--migration-report",
        type=Path,
        default=Path("output/migration-preview.json"),
        help="Report path used with --preview-migration.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_console_utf8()
    args = build_parser().parse_args(argv)
    if args.write_schemas:
        _write_schemas(args.write_schemas)
        print(args.write_schemas)
        return 0
    if args.prepare_sample:
        try:
            prepared = prepare_sample(
                args.prepare_sample,
                args.samples_root,
                args.work_root,
            )
        except SamplePreparationError as error:
            print(
                json.dumps(
                    {"status": "failed", "code": error.code, "message": str(error)},
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 2
        print(
            json.dumps(
                {
                    "sample": prepared.archive_path.name,
                    "cache": "hit" if prepared.cache_hit else "extracted",
                    "htmlPath": str(prepared.html_path),
                    "resourceDirectory": str(prepared.resource_directory),
                    "manifestPath": str(prepared.manifest_path),
                },
                ensure_ascii=False,
            )
        )
        return 0
    if args.normalize_samples:
        if any((args.html_path, args.resource_directory, args.output_directory)):
            build_parser().error(
                "positional article paths cannot be combined with --normalize-samples"
            )
        if args.adapter != "auto":
            build_parser().error(
                "--normalize-samples always uses reliable automatic source detection"
            )
        try:
            summary = normalize_sample_archives(
                args.samples_root,
                args.work_root,
                args.batch_output_directory,
            )
        except BatchNormalizationError as error:
            print(
                json.dumps(
                    {"status": "failed", "code": error.code, "message": str(error)},
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 2
        print(json.dumps(summary.as_dict(), ensure_ascii=False))
        return 1 if summary.statistics["failed"] or summary.statistics["conflicts"] else 0
    if args.preview_migration:
        if any((args.html_path, args.resource_directory, args.output_directory)):
            build_parser().error(
                "positional article paths cannot be combined with --preview-migration"
            )
        try:
            preview = preview_legacy_outputs(args.preview_migration, args.migration_report)
        except MigrationPreviewError as error:
            print(
                json.dumps(
                    {"status": "failed", "code": error.code, "message": str(error)},
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 2
        print(json.dumps(preview["statistics"], ensure_ascii=False))
        return 0
    if not all((args.html_path, args.resource_directory, args.output_directory)):
        build_parser().error("html_path, resource_directory, and output_directory are required")
    try:
        normalizers = {
            "auto": normalize_saved_article,
            "wechat": normalize_wechat_article,
            "zhihu": normalize_zhihu_article,
        }
        result = normalizers[args.adapter](
            args.html_path, args.resource_directory, args.output_directory
        )
    except NormalizationError as error:
        print(
            json.dumps(
                {"status": "failed", "code": error.code, "message": str(error)}, ensure_ascii=False
            ),
            file=sys.stderr,
        )
        return 2
    print(result.output_directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
