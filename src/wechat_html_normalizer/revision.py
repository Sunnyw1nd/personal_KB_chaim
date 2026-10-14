from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse, urlunparse

from bs4 import Tag

from .models import Asset, AssetCompleteness, Block, Metadata

FINGERPRINT_VERSION = "1"


def _digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def logical_asset_base(tag: Tag, locator: str) -> tuple[str, str]:
    for attribute in ("data-original", "data-actualsrc", "data-src"):
        value = str(tag.get(attribute) or "").strip()
        parsed = urlparse(value)
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            normalized = urlunparse(
                (
                    parsed.scheme.casefold(),
                    parsed.netloc.casefold(),
                    unquote(parsed.path),
                    "",
                    "",
                    "",
                )
            )
            return f"remote:{normalized}", f"remote_url:{attribute}"
    value = str(tag.get("src") or "").strip()
    parsed = urlparse(value)
    if value and not parsed.scheme and not value.startswith(("//", "data:", "#")):
        normalized_path = unquote(parsed.path).replace("\\", "/").lstrip("./")
        if normalized_path:
            parts = PurePosixPath(normalized_path).parts
            if len(parts) > 1 and parts[0].casefold().endswith("_files"):
                normalized_path = PurePosixPath(*parts[1:]).as_posix()
            return f"local:{normalized_path}", "local_src"
    return f"locator:{locator}", "source_locator"


class AssetIdentityAllocator:
    def __init__(self) -> None:
        self._occurrences: dict[str, int] = defaultdict(int)

    def allocate(self, tag: Tag, locator: str) -> tuple[str, str, str]:
        base, method = logical_asset_base(tag, locator)
        self._occurrences[base] += 1
        identity_key = f"{base}#occurrence={self._occurrences[base]}"
        asset_id = f"asset-{hashlib.sha256(identity_key.encode('utf-8')).hexdigest()[:12]}"
        return asset_id, identity_key, method


def asset_completeness(assets: list[Asset]) -> AssetCompleteness:
    available = [asset.asset_id for asset in assets if asset.output_path]
    missing = [asset.asset_id for asset in assets if not asset.found]
    invalid = [asset.asset_id for asset in assets if asset.found and not asset.output_path]
    expected = len(assets)
    return AssetCompleteness(
        expected=expected,
        available=len(available),
        missing=len(missing),
        invalid=len(invalid),
        completenessRate=(len(available) / expected if expected else 1.0),
        missingAssetIds=missing,
        invalidAssetIds=invalid,
    )


def _inline_payload(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, list):
        return [_inline_payload(item) for item in value]
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=False)
    return value


def _block_payload(block: Block) -> dict[str, object]:
    payload = {
        "type": block.type,
        "content": block.content,
        "inlines": _inline_payload(block.inlines),
        "level": block.level,
        "ordered": block.ordered,
        "items": _inline_payload(block.items),
        "language": block.language,
        "rows": block.rows,
        "assetId": block.asset_id,
        "originalTag": block.original_tag,
        "caption": block.caption,
        "captionInlines": _inline_payload(block.caption_inlines),
        "sourceUrl": block.source_url,
    }
    if block.plain_text is not None:
        payload["plainText"] = block.plain_text
    return payload


def content_fingerprint(metadata: Metadata, blocks: list[Block]) -> str:
    payload = {
        "metadata": {
            "title": metadata.title,
            "accountName": metadata.account_name,
            "author": metadata.author,
            "publishedAt": metadata.published_at,
            "modifiedAt": metadata.modified_at,
        },
        "blocks": [_block_payload(block) for block in blocks],
    }
    return _digest(payload)


def asset_fingerprint(assets: list[Asset]) -> str:
    return _digest(
        [
            {
                "assetId": asset.asset_id,
                "found": asset.found,
                "detectedMediaType": asset.detected_media_type,
                "binarySha256": asset.sha256,
                "width": asset.width,
                "height": asset.height,
                "copyAvailable": bool(asset.output_path),
            }
            for asset in assets
        ]
    )


def revision_fingerprint(
    *,
    schema_version: str,
    adapter: str,
    adapter_version: str,
    content_sha256: str,
    asset_sha256: str,
) -> str:
    return _digest(
        {
            "fingerprintVersion": FINGERPRINT_VERSION,
            "schemaVersion": schema_version,
            "adapter": adapter,
            "adapterVersion": adapter_version,
            "contentFingerprintSha256": content_sha256,
            "assetFingerprintSha256": asset_sha256,
        }
    )
