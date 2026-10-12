from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .normalizer import NormalizationError, normalize_saved_article
from .sample_cache import SamplePreparationError, prepare_sample


class BatchNormalizationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class BatchItemResult:
    archive_file_name: str
    status: Literal["normalized", "conflict", "failed"]
    archive_sha256: str | None = None
    cache: Literal["hit", "extracted"] | None = None
    adapter: str | None = None
    document_id: str | None = None
    document_status: str | None = None
    output_path: str | None = None
    document_path: str | None = None
    revision_id: str | None = None
    current_revision_id: str | None = None
    import_decision: str | None = None
    warning_count: int = 0
    error_code: str | None = None
    error_message: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "archiveFileName": self.archive_file_name,
            "archiveSha256": self.archive_sha256,
            "cache": self.cache,
            "status": self.status,
            "adapter": self.adapter,
            "documentId": self.document_id,
            "documentStatus": self.document_status,
            "outputPath": self.output_path,
            "documentPath": self.document_path,
            "revisionId": self.revision_id,
            "currentRevisionId": self.current_revision_id,
            "importDecision": self.import_decision,
            "warningCount": self.warning_count,
            "errorCode": self.error_code,
            "errorMessage": self.error_message,
        }


@dataclass(frozen=True)
class BatchNormalizationSummary:
    status: Literal["completed", "completed_with_errors"]
    statistics: dict[str, int]
    items: tuple[BatchItemResult, ...]
    report_path: Path

    def as_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": "2.0.0",
            "operation": "normalize_saved_samples",
            "status": self.status,
            "statistics": self.statistics,
            "items": [item.as_dict() for item in self.items],
            "reportPath": self.report_path.name,
        }


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _sample_archives(samples_root: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in samples_root.iterdir()
            if (path.is_file() or path.is_symlink()) and path.suffix.casefold() == ".zip"
        ),
        key=lambda path: (path.name.casefold(), path.name),
    )


def normalize_sample_archives(
    samples_root: Path,
    work_root: Path,
    output_directory: Path,
    *,
    imported_at: str | None = None,
) -> BatchNormalizationSummary:
    samples_root = samples_root.absolute()
    work_root = work_root.absolute()
    output_directory = output_directory.absolute()
    if not samples_root.is_dir() or samples_root.is_symlink():
        raise BatchNormalizationError(
            "samples_root_invalid",
            "The samples root does not exist, is not a directory, or is a symbolic link.",
        )
    resolved_samples = samples_root.resolve()
    resolved_output = output_directory.resolve(strict=False)
    resolved_work = work_root.resolve(strict=False)
    if _is_within(resolved_output, resolved_samples):
        raise BatchNormalizationError(
            "batch_output_overlaps_samples",
            "The batch output directory must not equal or be inside the immutable samples root.",
        )
    if _is_within(resolved_output, resolved_work):
        raise BatchNormalizationError(
            "batch_output_overlaps_work_cache",
            "The batch output directory must not be inside the generated work cache.",
        )
    archives = _sample_archives(samples_root)
    if not archives:
        raise BatchNormalizationError(
            "sample_archives_missing",
            "The samples root does not contain any ZIP archives.",
        )

    output_directory.mkdir(parents=True, exist_ok=True)
    results: list[BatchItemResult] = []
    for archive in archives:
        prepared = None
        try:
            prepared = prepare_sample(archive, samples_root, work_root)
            normalized = normalize_saved_article(
                prepared.html_path,
                prepared.resource_directory,
                output_directory,
                imported_at=imported_at,
                source_sample=prepared,
            )
            warning_count = sum(
                message.level.value in {"warning", "error"}
                for message in normalized.document.processing.messages
            )
            results.append(
                BatchItemResult(
                    archive_file_name=archive.name,
                    archive_sha256=prepared.archive_sha256,
                    cache="hit" if prepared.cache_hit else "extracted",
                    status="normalized",
                    adapter=normalized.document.processing.adapter,
                    document_id=normalized.document.document_id,
                    document_status=normalized.document.processing.status,
                    output_path=normalized.output_directory.relative_to(
                        output_directory
                    ).as_posix(),
                    document_path=normalized.document_directory.relative_to(
                        output_directory
                    ).as_posix(),
                    revision_id=(
                        normalized.document.revision.revision_id
                        if normalized.document.revision
                        else None
                    ),
                    current_revision_id=normalized.current_revision_id,
                    import_decision=normalized.import_decision,
                    warning_count=warning_count,
                )
            )
        except (SamplePreparationError, NormalizationError) as error:
            conflict = error.code in {
                "document_identity_conflict",
                "document_directory_conflict",
                "revision_identity_conflict",
            }
            results.append(
                BatchItemResult(
                    archive_file_name=archive.name,
                    archive_sha256=(prepared.archive_sha256 if prepared else None),
                    cache=(
                        "hit"
                        if prepared and prepared.cache_hit
                        else "extracted"
                        if prepared
                        else None
                    ),
                    status="conflict" if conflict else "failed",
                    error_code=error.code,
                    error_message=str(error),
                )
            )
        except OSError as error:
            results.append(
                BatchItemResult(
                    archive_file_name=archive.name,
                    archive_sha256=(prepared.archive_sha256 if prepared else None),
                    cache=(
                        "hit"
                        if prepared and prepared.cache_hit
                        else "extracted"
                        if prepared
                        else None
                    ),
                    status="failed",
                    error_code="sample_io_error",
                    error_message=f"Sample processing failed with {type(error).__name__}.",
                )
            )

    normalized_count = sum(item.status == "normalized" for item in results)
    conflict_count = sum(item.status == "conflict" for item in results)
    failed_count = sum(item.status == "failed" for item in results)
    statistics = {
        "archives": len(results),
        "normalized": normalized_count,
        "failed": failed_count,
        "conflicts": conflict_count,
        "cache_hits": sum(item.cache == "hit" for item in results),
        "extracted": sum(item.cache == "extracted" for item in results),
        "documents_with_warnings": sum(
            item.document_status == "completed_with_warnings" for item in results
        ),
        "created": sum(item.import_decision == "created" for item in results),
        "updated": sum(
            item.import_decision in {"resource_improved", "resource_updated", "content_updated"}
            for item in results
        ),
        "unchanged": sum(item.import_decision == "unchanged" for item in results),
        "equivalent": sum(item.import_decision == "equivalent" for item in results),
        "degraded_not_current": sum(
            item.import_decision == "resource_degraded" for item in results
        ),
    }
    report_path = output_directory / "batch-processing-report.json"
    summary = BatchNormalizationSummary(
        status="completed_with_errors" if failed_count or conflict_count else "completed",
        statistics=statistics,
        items=tuple(results),
        report_path=report_path,
    )
    report_path.write_text(
        json.dumps(summary.as_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return summary
