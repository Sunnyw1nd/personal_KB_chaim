from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..media import sha256_file
from ..models import Block, Document
from .models import (
    REQUEST_SCHEMA_VERSION,
    RESULT_SCHEMA_VERSION,
    AssetReference,
    DataPolicy,
    EnrichmentRequest,
    ImageContext,
    ImageIdentity,
)
from .security import redact_sensitive_text

FORMAT_MEDIA_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


@dataclass(frozen=True)
class PreparedImage:
    request: EnrichmentRequest
    path: Path


@dataclass(frozen=True)
class DatasetSummary:
    files: int
    selected: int
    unique_images: int
    with_context: int
    with_asset_references: int
    local_only: int = 0


def _stable_sha(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _linked(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _safe_input(root: Path, relative_path: str) -> Path:
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError(f"unsafe inventory path: {relative_path!r}")
    root = root.resolve(strict=True)
    candidate = root.joinpath(*relative.parts)
    current = root
    if _linked(current):
        raise ValueError("linked input roots are not allowed")
    for part in relative.parts:
        current = current / part
        if _linked(current):
            raise ValueError(f"linked input entry is not allowed: {relative_path!r}")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError(f"input file escapes the allowed root: {relative_path!r}")
    return resolved


def _block_text(block: Block) -> str | None:
    if block.type in ("heading", "paragraph", "quote", "code", "unknown"):
        return block.content
    if block.type == "list" and block.items:
        return "\n".join(item.content for item in block.items)
    if block.type == "table" and block.rows:
        return "\n".join(" | ".join(row) for row in block.rows)
    return None


def _trim(value: str | None, limit: int = 500) -> str | None:
    if value is None:
        return None
    value, _ = redact_sensitive_text(" ".join(value.split()))
    return value[:limit] or None


def _policy(path: Path | None, available_ids: set[str]) -> tuple[str, dict[str, dict]]:
    if path is None or not path.exists():
        return "default-v1", {}
    path = path.resolve(strict=True)
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("image policy exceeds the 1 MiB safety limit")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schemaVersion") != "1.0.0" or not isinstance(value.get("images"), dict):
        raise ValueError("image policy must be schema 1.0.0 with an images object")
    if any(
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
        or not isinstance(rule, dict)
        for digest, rule in value["images"].items()
    ):
        raise ValueError("image policy keys must be lowercase SHA-256 and rules must be objects")
    unknown = sorted(set(value["images"]) - available_ids)
    if unknown:
        raise ValueError(
            "image policy contains imageId absent from inventory: " + ", ".join(unknown)
        )
    return "project-image-policy-v1", value["images"]


def _document_context(relation: dict, image_sha256: str) -> ImageContext | None:
    document_path = relation.get("documentJson")
    asset_id = relation.get("assetId")
    if not isinstance(document_path, str) or not isinstance(asset_id, str):
        return None
    try:
        path = Path(document_path).resolve(strict=True)
        if path.name != "document.json" or path.stat().st_size > 10 * 1024 * 1024:
            return None
        document = Document.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    asset = next((item for item in document.assets if item.asset_id == asset_id), None)
    if asset is None or asset.sha256 != image_sha256:
        return None
    position = next(
        (index for index, block in enumerate(document.blocks) if block.asset_id == asset_id), None
    )
    if position is None:
        return None
    image_block = document.blocks[position]
    before = next(
        (
            _block_text(document.blocks[index])
            for index in range(position - 1, -1, -1)
            if _block_text(document.blocks[index])
        ),
        None,
    )
    after = next(
        (
            _block_text(document.blocks[index])
            for index in range(position + 1, len(document.blocks))
            if _block_text(document.blocks[index])
        ),
        None,
    )
    values = {
        "articleTitle": _trim(document.metadata.title, 200),
        "caption": _trim(image_block.caption, 300),
        "precedingText": _trim(before),
        "followingText": _trim(after),
    }
    return ImageContext(available=any(values.values()), **values)


def _relations(
    raw_relations: list[dict], image_sha256: str
) -> tuple[list[AssetReference], ImageContext]:
    matches = [item for item in raw_relations if item.get("imageId") == image_sha256]
    matches.sort(
        key=lambda item: (
            item.get("availability") != "available",
            item.get("method") != "verified_binary_sha256",
            item.get("documentId") or "",
            item.get("revisionId") or "",
            item.get("assetId") or "",
        )
    )
    references: list[AssetReference] = []
    seen: set[tuple[str, str | None, str]] = set()
    context: ImageContext | None = None
    for relation in matches:
        doc_id, revision_id, asset_id = (
            relation.get("documentId"),
            relation.get("revisionId"),
            relation.get("assetId"),
        )
        if isinstance(doc_id, str) and isinstance(asset_id, str):
            key = (doc_id, revision_id if isinstance(revision_id, str) else None, asset_id)
            if key not in seen:
                seen.add(key)
                references.append(
                    AssetReference(
                        documentId=doc_id,
                        revisionId=key[1],
                        assetId=asset_id,
                        binarySha256=image_sha256,
                    )
                )
        if context is None:
            context = _document_context(relation, image_sha256)
    return references, context or ImageContext(available=False)


def prepare_dataset(
    input_root: Path,
    inventory_path: Path,
    *,
    prompt_version: str,
    allow_external: bool,
    max_images: int | None = None,
    selected_image_ids: set[str] | None = None,
    policy_path: Path | None = None,
) -> tuple[list[PreparedImage], DatasetSummary, str]:
    root = input_root.resolve(strict=True)
    inventory_path = inventory_path.resolve(strict=True)
    if inventory_path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("inventory exceeds the 20 MiB safety limit")
    inventory_bytes = inventory_path.read_bytes()
    inventory_sha256 = hashlib.sha256(inventory_bytes).hexdigest()
    data = json.loads(inventory_bytes.decode("utf-8"))
    if data.get("schemaVersion") != "1.0.0" or data.get("status") != "complete":
        raise ValueError("inventory must be a completed schema 1.0.0 manifest")
    files = data.get("files")
    relations = data.get("relations", [])
    if not isinstance(files, list) or not isinstance(relations, list):
        raise TypeError("inventory files and relations must be arrays")
    available_ids = {row.get("imageId") for row in files if isinstance(row, dict)}
    policy_version, policy_overrides = _policy(policy_path, available_ids)
    if selected_image_ids:
        missing = sorted(selected_image_ids - available_ids)
        if missing:
            raise ValueError("selected imageId is absent from inventory: " + ", ".join(missing))
        files_to_process = [row for row in files if row.get("imageId") in selected_image_ids]
    else:
        files_to_process = files
    selected_rows = files_to_process[:max_images] if max_images is not None else files_to_process
    prepared: list[PreparedImage] = []
    for row in selected_rows:
        if not row.get("decodable"):
            raise ValueError(f"inventory image is not decodable: {row.get('relativePath')!r}")
        path = _safe_input(root, row["relativePath"])
        actual_sha256 = sha256_file(path)
        if actual_sha256 != row.get("imageId"):
            raise ValueError(f"image changed since inventory: {row['relativePath']!r}")
        if path.stat().st_size != row.get("sizeBytes"):
            raise ValueError(f"image size changed since inventory: {row['relativePath']!r}")
        actual_format = row.get("format")
        media_type = FORMAT_MEDIA_TYPES.get(actual_format)
        if media_type is None:
            raise ValueError(f"unsupported image format in inventory: {actual_format!r}")
        asset_references, context = _relations(relations, actual_sha256)
        context_dump = context.model_dump(mode="json", by_alias=True, exclude_none=True)
        policy_override = policy_overrides.get(actual_sha256, {})
        local_only = bool(policy_override.get("localOnly"))
        reason_code = policy_override.get("reasonCode")
        if local_only and not isinstance(reason_code, str):
            raise ValueError(f"local-only policy requires reasonCode for {actual_sha256}")
        prepared.append(
            PreparedImage(
                path=path,
                request=EnrichmentRequest(
                    schemaVersion=REQUEST_SCHEMA_VERSION,
                    image=ImageIdentity(
                        sha256=actual_sha256,
                        relativePath=row["relativePath"],
                        mediaType=media_type,
                        actualFormat=actual_format,
                        sizeBytes=path.stat().st_size,
                        width=row["width"],
                        height=row["height"],
                    ),
                    assetReferences=asset_references,
                    context=context,
                    contextFingerprintSha256=_stable_sha(context_dump),
                    policy=DataPolicy(
                        allowExternal=allow_external and not local_only,
                        localOnly=local_only,
                        allowedBackendTypes=["local"] if local_only else ["cloud"],
                        classification="local_only" if local_only else "external_test_sample",
                        policyVersion=policy_version,
                        reasonCode=reason_code or "explicit-external-cli-consent-required",
                    ),
                    promptVersion=prompt_version,
                    resultSchemaVersion=RESULT_SCHEMA_VERSION,
                ),
            )
        )
    summary = DatasetSummary(
        files=len(files),
        selected=len(prepared),
        unique_images=len({item.request.image.sha256 for item in prepared}),
        with_context=sum(item.request.context.available for item in prepared),
        with_asset_references=sum(bool(item.request.asset_references) for item in prepared),
        local_only=sum(item.request.policy.local_only for item in prepared),
    )
    return prepared, summary, inventory_sha256


def verify_unchanged(prepared: list[PreparedImage]) -> None:
    for item in prepared:
        if sha256_file(item.path) != item.request.image.sha256:
            raise RuntimeError(f"input image changed during processing: {item.path.name}")
