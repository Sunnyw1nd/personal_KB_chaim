"""Prepare formal revision images without an annotation workbook or test inventory."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..enrichment.dataset import PreparedImage, _block_text, _linked, _policy, _stable_sha, _trim
from ..enrichment.models import (
    AssetReference,
    DataPolicy,
    EnrichmentRequest,
    ImageContext,
    ImageIdentity,
)
from ..media import detect_media, sha256_file
from ..models import Document


def safe_child(root: Path, relative: str, *, exists: bool = True) -> Path:
    parts = PurePosixPath(relative)
    if (
        not relative
        or parts.is_absolute()
        or ".." in parts.parts
        or "\\" in relative
        or ":" in relative
    ):
        raise ValueError("unsafe relative resource path")
    if any(_linked(p) for p in (root, *root.parents)):
        raise ValueError("linked root")
    current = root
    for part in parts.parts:
        current /= part
        if _linked(current):
            raise ValueError("linked resource")
    resolved = current.resolve(strict=exists)
    if not resolved.is_relative_to(root.resolve(strict=True)):
        raise ValueError("resource escapes root")
    return resolved


@dataclass
class RevisionImages:
    document: Document
    groups: list[dict]
    excluded: list[dict]


def prepare_revision_images(
    revision_directory: Path, *, policy_path: Path | None = None, allow_external: bool = False
) -> RevisionImages:
    root = revision_directory.absolute()
    path = safe_child(root, "document.json")
    if path.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("document exceeds safety limit")
    document = Document.model_validate_json(path.read_text(encoding="utf-8"))
    if document.revision is None:
        raise ValueError("formal image processing requires revision identity")
    _, policies = _policy(policy_path, set(), ignore_unrelated=True)
    assets = {a.asset_id: a for a in document.assets}
    grouped = {}
    excluded = []
    for position, block in enumerate(document.blocks):
        if block.type != "image":
            continue
        asset = assets[block.asset_id]
        location = {
            "documentId": document.document_id,
            "revisionId": document.revision.revision_id,
            "assetId": asset.asset_id,
            "blockId": block.block_id,
            "order": block.order,
            "sourceLocator": block.source_locator,
            "caption": block.caption,
        }
        try:
            if not asset.found or not asset.output_path or not asset.sha256:
                raise ValueError("missing_or_uncopied_image")
            image = safe_child(root, asset.output_path)
            if not image.is_file() or sha256_file(image) != asset.sha256:
                raise ValueError("image_binary_mismatch")
            media = detect_media(image)
            formats = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}
            if not media.decodable_image or media.media_type not in formats:
                raise ValueError("unsupported_or_damaged_image")
            if (media.width, media.height, image.stat().st_size) != (
                asset.width,
                asset.height,
                asset.size_bytes,
            ):
                raise ValueError("image_metadata_mismatch")
        except (OSError, ValueError) as exc:
            excluded.append(
                {
                    **location,
                    "reason": str(exc) if isinstance(exc, ValueError) else "missing_image_file",
                }
            )
            continue
        before = next(
            (_block_text(b) for b in reversed(document.blocks[:position]) if _block_text(b)), None
        )
        after = next(
            (_block_text(b) for b in document.blocks[position + 1 :] if _block_text(b)), None
        )
        fields = {
            "articleTitle": _trim(document.metadata.title, 200),
            "caption": _trim(block.caption, 300),
            "precedingText": _trim(before),
            "followingText": _trim(after),
        }
        context = ImageContext(available=any(fields.values()), **fields)
        fingerprint = _stable_sha(context.model_dump(mode="json", by_alias=True, exclude_none=True))
        identity = (asset.sha256, fingerprint)
        local_only = bool(policies.get(asset.sha256, {}).get("localOnly"))
        reference = AssetReference(
            documentId=document.document_id,
            revisionId=document.revision.revision_id,
            assetId=asset.asset_id,
            binarySha256=asset.sha256,
        )
        if identity not in grouped:
            request = EnrichmentRequest(
                schemaVersion="1.0.0",
                image=ImageIdentity(
                    sha256=asset.sha256,
                    relativePath=asset.output_path,
                    mediaType=media.media_type,
                    actualFormat=formats[media.media_type],
                    sizeBytes=image.stat().st_size,
                    width=media.width,
                    height=media.height,
                ),
                assetReferences=[reference],
                context=context,
                contextFingerprintSha256=fingerprint,
                policy=DataPolicy(
                    allowExternal=allow_external and not local_only,
                    localOnly=local_only,
                    allowedBackendTypes=["local"] if local_only else ["cloud"],
                    classification="local_only" if local_only else "formal_asset",
                    policyVersion="formal-selection-v1",
                    reasonCode="explicit-selected-batch-consent-required",
                ),
                promptVersion="compact-json-v1",
                resultSchemaVersion="1.0.0",
            )
            grouped[identity] = {"prepared": PreparedImage(request, image), "locations": []}
        group = grouped[identity]
        if reference not in group["prepared"].request.asset_references:
            group["prepared"].request.asset_references.append(reference)
        group["locations"].append(location)
    return RevisionImages(document, list(grouped.values()), excluded)
