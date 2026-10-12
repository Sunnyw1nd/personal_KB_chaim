from __future__ import annotations

import json
import os
import re
import shutil
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .models import (
    Document,
    DocumentRecord,
    ProcessingReport,
    ResourceManifest,
    StoredRevision,
)
from .renderer import render_markdown

DOCUMENT_RECORD_NAME = "document-record.json"
WINDOWS_RESERVED_NAMES = {
    "aux",
    "clock$",
    "com1",
    "com2",
    "com3",
    "com4",
    "com5",
    "com6",
    "com7",
    "com8",
    "com9",
    "con",
    "lpt1",
    "lpt2",
    "lpt3",
    "lpt4",
    "lpt5",
    "lpt6",
    "lpt7",
    "lpt8",
    "lpt9",
    "nul",
    "prn",
}
ImportDecision = Literal[
    "created",
    "unchanged",
    "equivalent",
    "resource_improved",
    "resource_updated",
    "resource_degraded",
    "content_updated",
]


class StorageError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PublicationResult:
    decision: ImportDecision
    document_directory: Path
    revision_directory: Path
    current_revision_id: str
    current_changed: bool
    document: Document
    report: ProcessingReport
    manifest: ResourceManifest


def _json_bytes(model: object) -> bytes:
    payload = model.model_dump(mode="json", by_alias=True, exclude_none=False)  # type: ignore[attr-defined]
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _safe_title(title: str) -> str:
    value = unicodedata.normalize("NFKC", title)
    value = " ".join(value.split())
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", value)
    value = re.sub(r"[\s_-]+", "-", value).strip(" .-_")
    if not value or value.split(".", 1)[0].casefold() in WINDOWS_RESERVED_NAMES:
        return "untitled"
    return value


def document_directory_name(
    source_type: str,
    title: str,
    document_id: str,
    *,
    output_root: Path | None = None,
    max_component_length: int = 96,
) -> str:
    label = "weixin" if source_type == "wechat_saved_html" else "zhihu"
    short_id = document_id.rsplit("-", 1)[-1][:10]
    suffix = f"--{short_id}"
    component_limit = max_component_length
    if output_root is not None:
        # Leave room for /revisions/revision-<id>/assets/asset-<id>.webp under
        # Windows environments where long-path support may still be disabled.
        remaining = 220 - len(str(output_root.absolute())) - 72
        minimum = len(label) + len(suffix) + len("-u")
        if remaining < minimum:
            raise StorageError(
                "output_path_too_long",
                "The output root is too deep for a safe Windows revision path.",
            )
        component_limit = min(component_limit, remaining)
    title_budget = max(1, component_limit - len(label) - len(suffix) - 1)
    safe = _safe_title(title)[:title_budget].rstrip(" .-_") or "untitled"
    return f"{label}-{safe}{suffix}"


def _load_record(path: Path) -> DocumentRecord:
    try:
        return DocumentRecord.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise StorageError(
            "document_record_invalid", f"Stored document record is invalid: {path.name}"
        ) from error


def _find_document_directory(
    output_root: Path, document_id: str
) -> tuple[Path, DocumentRecord] | None:
    matches: list[tuple[Path, DocumentRecord]] = []
    if not output_root.exists():
        return None
    for path in sorted(output_root.iterdir(), key=lambda item: (item.name.casefold(), item.name)):
        record_path = path / DOCUMENT_RECORD_NAME
        if not path.is_dir() or not record_path.is_file():
            continue
        record = _load_record(record_path)
        if record.document_id == document_id:
            matches.append((path, record))
    if len(matches) > 1:
        raise StorageError(
            "document_identity_conflict",
            f"Multiple stored directories claim logical document {document_id}.",
        )
    return matches[0] if matches else None


def _atomic_record_write(path: Path, record: DocumentRecord) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        temporary.write_bytes(_json_bytes(record))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _stored_revision(document: Document, decision: str) -> StoredRevision:
    assert document.revision is not None
    assert document.asset_completeness is not None
    package_hashes = [document.revision.package_sha256] if document.revision.package_sha256 else []
    return StoredRevision(
        revisionId=document.revision.revision_id,
        fingerprintSha256=document.revision.fingerprint_sha256,
        contentFingerprintSha256=document.revision.content_fingerprint_sha256,
        assetFingerprintSha256=document.revision.asset_fingerprint_sha256,
        packageSha256s=package_hashes,
        htmlSha256s=[document.source.html_sha256],
        importedAt=document.metadata.imported_at,
        path=f"revisions/{document.revision.revision_id}",
        assetCompleteness=document.asset_completeness,
        decisionAtCreation=decision,
    )


def _load_revision(directory: Path) -> tuple[Document, ProcessingReport, ResourceManifest]:
    try:
        document = Document.model_validate_json(
            (directory / "document.json").read_text(encoding="utf-8")
        )
        report = ProcessingReport.model_validate_json(
            (directory / "processing-report.json").read_text(encoding="utf-8")
        )
        manifest = ResourceManifest.model_validate_json(
            (directory / "resource-manifest.json").read_text(encoding="utf-8")
        )
        return document, report, manifest
    except (OSError, ValueError) as error:
        raise StorageError(
            "stored_revision_invalid", f"Stored revision is incomplete or invalid: {directory.name}"
        ) from error


def _write_revision(
    document_directory: Path,
    document: Document,
    report: ProcessingReport,
    manifest: ResourceManifest,
    asset_copies: list[tuple[Path, str]],
) -> Path:
    assert document.revision is not None
    revisions = document_directory / "revisions"
    revisions.mkdir(parents=True, exist_ok=True)
    destination = revisions / document.revision.revision_id
    if destination.exists():
        stored, _, _ = _load_revision(destination)
        if (
            not stored.revision
            or stored.revision.fingerprint_sha256 != document.revision.fingerprint_sha256
        ):
            raise StorageError(
                "revision_identity_conflict",
                f"Revision directory {destination.name} contains a different fingerprint.",
            )
        return destination
    staging = revisions / f".tmp-{uuid.uuid4().hex[:8]}"
    staging.mkdir()
    try:
        (staging / "assets").mkdir()
        for source_path, relative_output in asset_copies:
            target = staging / relative_output
            if not _is_within(target.resolve(strict=False), staging.resolve()):
                raise StorageError("unsafe_output_path", "Generated asset path escaped revision.")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, target)
        (staging / "document.json").write_bytes(_json_bytes(document))
        (staging / "document.md").write_text(
            render_markdown(document), encoding="utf-8", newline="\n"
        )
        (staging / "processing-report.json").write_bytes(_json_bytes(report))
        (staging / "resource-manifest.json").write_bytes(_json_bytes(manifest))
        _load_revision(staging)
        os.replace(staging, destination)
        return destination
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def _append_provenance(stored: StoredRevision, document: Document) -> StoredRevision:
    package_hashes = list(stored.package_sha256s)
    html_hashes = list(stored.html_sha256s)
    if document.revision and document.revision.package_sha256:
        package_hashes.append(document.revision.package_sha256)
    html_hashes.append(document.source.html_sha256)
    payload = stored.model_dump(mode="python", by_alias=True)
    payload["packageSha256s"] = sorted(set(package_hashes))
    payload["htmlSha256s"] = sorted(set(html_hashes))
    return StoredRevision.model_validate(payload)


def publish_revision(
    output_root: Path,
    document: Document,
    report: ProcessingReport,
    manifest: ResourceManifest,
    asset_copies: list[tuple[Path, str]],
) -> PublicationResult:
    if not document.source_identity or not document.revision or not document.asset_completeness:
        raise StorageError("document_revision_missing", "A revision-aware document is required.")
    output_root = output_root.absolute()
    output_root.mkdir(parents=True, exist_ok=True)
    found = _find_document_directory(output_root, document.document_id)
    if found:
        document_directory, record = found
        if (
            record.source_identity.source_type != document.source_identity.source_type
            or record.source_identity.normalized_value != document.source_identity.normalized_value
        ):
            raise StorageError(
                "document_identity_conflict",
                "The stable document ID is already assigned to a different source identity.",
            )
    else:
        name = document_directory_name(
            document.metadata.source_type,
            document.metadata.title,
            document.document_id,
            output_root=output_root,
        )
        document_directory = output_root / name
        if document_directory.exists():
            raise StorageError(
                "document_directory_conflict",
                f"The proposed document directory already exists without a matching record: {name}",
            )
        document_directory.mkdir()
        record = None

    if record is not None:
        same = next(
            (
                item
                for item in record.revisions
                if item.fingerprint_sha256 == document.revision.fingerprint_sha256
            ),
            None,
        )
        if same:
            same_package = bool(
                document.revision.package_sha256
                and document.revision.package_sha256 in same.package_sha256s
            )
            same_direct_input = bool(
                not document.revision.package_sha256
                and document.source.html_sha256 in same.html_sha256s
            )
            updated = _append_provenance(same, document)
            decision: ImportDecision = (
                "unchanged" if same_package or same_direct_input else "equivalent"
            )
            current_changed = False
            if same.revision_id != record.current_revision_id:
                current = next(
                    item
                    for item in record.revisions
                    if item.revision_id == record.current_revision_id
                )
                if same.content_fingerprint_sha256 == current.content_fingerprint_sha256:
                    old_quality = (
                        current.asset_completeness.completeness_rate,
                        current.asset_completeness.available,
                        -current.asset_completeness.invalid,
                    )
                    imported_quality = (
                        same.asset_completeness.completeness_rate,
                        same.asset_completeness.available,
                        -same.asset_completeness.invalid,
                    )
                    if imported_quality < old_quality:
                        decision = "resource_degraded"
                    elif imported_quality > old_quality:
                        decision = "resource_improved"
                        current_changed = True
            if updated != same or current_changed:
                revisions = [
                    updated if item.revision_id == same.revision_id else item
                    for item in record.revisions
                ]
                record = record.model_copy(
                    update={
                        "revisions": revisions,
                        "current_revision_id": (
                            same.revision_id if current_changed else record.current_revision_id
                        ),
                    }
                )
                _atomic_record_write(document_directory / DOCUMENT_RECORD_NAME, record)
            revision_directory = document_directory / same.path
            stored_document, stored_report, stored_manifest = _load_revision(revision_directory)
            return PublicationResult(
                decision=decision,
                document_directory=document_directory,
                revision_directory=revision_directory,
                current_revision_id=record.current_revision_id,
                current_changed=current_changed,
                document=stored_document,
                report=stored_report,
                manifest=stored_manifest,
            )

    decision: ImportDecision
    make_current: bool
    if record is None:
        decision, make_current = "created", True
    else:
        current = next(
            item for item in record.revisions if item.revision_id == record.current_revision_id
        )
        if current.content_fingerprint_sha256 != document.revision.content_fingerprint_sha256:
            decision, make_current = "content_updated", True
        else:
            old = current.asset_completeness
            new = document.asset_completeness
            old_quality = (old.completeness_rate, old.available, -old.invalid)
            new_quality = (new.completeness_rate, new.available, -new.invalid)
            if new_quality > old_quality:
                decision, make_current = "resource_improved", True
            elif new_quality < old_quality:
                decision, make_current = "resource_degraded", False
            else:
                decision, make_current = "resource_updated", True

    revision_directory = _write_revision(
        document_directory, document, report, manifest, asset_copies
    )
    stored = _stored_revision(document, decision)
    if record is None:
        record = DocumentRecord(
            schemaVersion="1.0.0",
            documentId=document.document_id,
            sourceIdentity=document.source_identity,
            directoryName=document_directory.name,
            titleAtCreation=document.metadata.title,
            currentRevisionId=stored.revision_id,
            revisions=[stored],
        )
    else:
        record = record.model_copy(
            update={
                "current_revision_id": stored.revision_id
                if make_current
                else record.current_revision_id,
                "revisions": [*record.revisions, stored],
            }
        )
    try:
        _atomic_record_write(document_directory / DOCUMENT_RECORD_NAME, record)
    except Exception:
        if found is None and document_directory.exists():
            shutil.rmtree(document_directory)
        raise
    return PublicationResult(
        decision=decision,
        document_directory=document_directory,
        revision_directory=revision_directory,
        current_revision_id=record.current_revision_id,
        current_changed=make_current,
        document=document,
        report=report,
        manifest=manifest,
    )
