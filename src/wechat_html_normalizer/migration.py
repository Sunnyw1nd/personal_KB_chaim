from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from .identity import (
    IDENTITY_VERSION,
    SourceIdentityValue,
    document_id_for_identity,
    normalize_wechat_identity,
    normalize_zhihu_identity,
    unresolved_identity,
)
from .models import Document
from .revision import (
    FINGERPRINT_VERSION,
    asset_completeness,
    asset_fingerprint,
    content_fingerprint,
    revision_fingerprint,
)
from .storage import DOCUMENT_RECORD_NAME, document_directory_name


class MigrationPreviewError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _identity(document: Document) -> SourceIdentityValue:
    source_type = document.metadata.source_type
    if source_type == "wechat_saved_html":
        identity = normalize_wechat_identity(document.metadata.original_url)
    else:
        identity = normalize_zhihu_identity(document.metadata.original_url)
    return identity or unresolved_identity(
        source_type, document.metadata.original_url, document.source.html_sha256
    )


def _candidate(path: Path, document: Document) -> dict[str, object]:
    identity = _identity(document)
    prefix = "wechat" if document.metadata.source_type == "wechat_saved_html" else "zhihu"
    document_id = document_id_for_identity(prefix, identity)
    completeness = asset_completeness(document.assets)
    content_sha = content_fingerprint(document.metadata, document.blocks)
    asset_sha = asset_fingerprint(document.assets)
    fingerprint = revision_fingerprint(
        schema_version=document.schema_version,
        adapter=document.processing.adapter,
        adapter_version=document.processing.adapter_version,
        content_sha256=content_sha,
        asset_sha256=asset_sha,
    )
    return {
        "legacyPath": path.name,
        "legacyDocumentId": document.document_id,
        "proposedDocumentId": document_id,
        "proposedDirectoryName": document_directory_name(
            document.metadata.source_type, document.metadata.title, document_id
        ),
        "proposedRevisionId": f"revision-{fingerprint[:16]}",
        "title": document.metadata.title,
        "sourceType": document.metadata.source_type,
        "sourceIdentity": {
            "rawValue": identity.raw_value,
            "normalizedValue": identity.normalized_value,
            "nativeId": identity.native_id,
            "method": identity.method,
            "normalizationVersion": IDENTITY_VERSION,
            "confidence": identity.confidence,
            "reliable": identity.reliable,
        },
        "packageSha256": document.source.archive_sha256,
        "htmlSha256": document.source.html_sha256,
        "contentFingerprintSha256": content_sha,
        "assetFingerprintSha256": asset_sha,
        "revisionFingerprintSha256": fingerprint,
        "fingerprintVersion": FINGERPRINT_VERSION,
        "assetCompleteness": completeness.model_dump(mode="json", by_alias=True),
        "publishedAt": document.metadata.published_at,
        "modifiedAt": document.metadata.modified_at,
    }


def _quality(candidate: dict[str, object]) -> tuple[float, int, int, str, str]:
    completeness = candidate["assetCompleteness"]
    assert isinstance(completeness, dict)
    return (
        float(completeness["completenessRate"]),
        int(completeness["available"]),
        -int(completeness["invalid"]),
        str(candidate.get("modifiedAt") or candidate.get("publishedAt") or ""),
        str(candidate["legacyPath"]),
    )


def preview_legacy_outputs(
    legacy_output_root: Path,
    report_path: Path | None = None,
) -> dict[str, object]:
    root = legacy_output_root.absolute()
    if not root.is_dir() or root.is_symlink():
        raise MigrationPreviewError(
            "legacy_output_root_invalid",
            "The legacy output root must be an existing non-symbolic-link directory.",
        )
    candidates: list[dict[str, object]] = []
    unrecognized: list[dict[str, str]] = []
    already_managed: list[str] = []
    for directory in sorted(root.iterdir(), key=lambda item: (item.name.casefold(), item.name)):
        if not directory.is_dir() or directory.is_symlink():
            continue
        if (directory / DOCUMENT_RECORD_NAME).is_file():
            already_managed.append(directory.name)
            continue
        document_path = directory / "document.json"
        if not document_path.is_file():
            continue
        try:
            document = Document.model_validate_json(document_path.read_text(encoding="utf-8"))
            candidates.append(_candidate(directory, document))
        except (OSError, ValueError) as error:
            unrecognized.append(
                {
                    "path": directory.name,
                    "reasonCode": "legacy_document_invalid",
                    "details": f"Could not validate document.json: {type(error).__name__}",
                }
            )

    groups_by_id: dict[str, list[dict[str, object]]] = {}
    unresolved: list[dict[str, object]] = []
    for candidate in candidates:
        identity = candidate["sourceIdentity"]
        assert isinstance(identity, dict)
        if not identity["reliable"]:
            unresolved.append(candidate)
            continue
        groups_by_id.setdefault(str(candidate["proposedDocumentId"]), []).append(candidate)
    groups: list[dict[str, object]] = []
    for document_id, members in sorted(groups_by_id.items()):
        recommended = max(members, key=_quality)
        groups.append(
            {
                "proposedDocumentId": document_id,
                "candidateCount": len(members),
                "possibleDuplicate": len(members) > 1,
                "legacyPaths": [str(item["legacyPath"]) for item in members],
                "recommendedCurrentLegacyPath": recommended["legacyPath"],
                "recommendationReason": (
                    "Highest verified body-image completeness; ties prefer the latest explicit "
                    "article timestamp and then a stable path ordering."
                ),
            }
        )
    payload: dict[str, object] = {
        "schemaVersion": "1.0.0",
        "operation": "legacy_output_migration_preview",
        "sourceRoot": root.name,
        "statistics": {
            "legacyDocuments": len(candidates),
            "reliableGroups": len(groups),
            "possibleDuplicateGroups": sum(bool(group["possibleDuplicate"]) for group in groups),
            "unresolved": len(unresolved),
            "invalid": len(unrecognized),
            "alreadyManaged": len(already_managed),
        },
        "groups": groups,
        "mapping": candidates,
        "unresolved": unresolved,
        "invalid": unrecognized,
        "alreadyManaged": already_managed,
        "migrationExecuted": False,
    }
    if report_path is not None:
        destination = report_path.absolute()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex[:8]}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.replace(temporary, destination)
    return payload
