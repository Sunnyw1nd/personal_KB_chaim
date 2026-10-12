from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, unquote, urlparse

IDENTITY_VERSION = "1"


@dataclass(frozen=True)
class SourceIdentityValue:
    source_type: str
    raw_value: str | None
    normalized_value: str
    method: str
    confidence: str
    native_id: str | None = None

    @property
    def reliable(self) -> bool:
        return self.confidence == "high"


def normalize_zhihu_identity(original_url: str | None) -> SourceIdentityValue | None:
    parsed = urlparse(original_url or "")
    if parsed.hostname and parsed.hostname.casefold() in {
        "zhuanlan.zhihu.com",
        "www.zhihu.com",
        "zhihu.com",
    }:
        match = re.fullmatch(r"/p/([^/]+)/*", parsed.path)
        if match:
            native_id = unquote(match.group(1)).strip()
            if native_id:
                return SourceIdentityValue(
                    source_type="zhihu_column_saved_html",
                    raw_value=original_url,
                    normalized_value=f"zhihu:column:{native_id}",
                    native_id=native_id,
                    method="zhihu_column_url_path_id",
                    confidence="high",
                )
    return None


def normalize_wechat_identity(original_url: str | None) -> SourceIdentityValue | None:
    parsed = urlparse(original_url or "")
    if not parsed.hostname or parsed.hostname.casefold() != "mp.weixin.qq.com":
        return None
    match = re.fullmatch(r"/s/([^/]+)/*", parsed.path)
    if match:
        native_id = unquote(match.group(1)).strip()
        if native_id:
            return SourceIdentityValue(
                source_type="wechat_saved_html",
                raw_value=original_url,
                normalized_value=f"wechat:article:s:{native_id}",
                native_id=native_id,
                method="wechat_s_path_token",
                confidence="high",
            )

    values: dict[str, str] = {}
    for key, value in parse_qsl(parsed.query, keep_blank_values=False):
        if key in {"__biz", "mid", "idx", "sn"} and key not in values:
            values[key] = value
    reliable = bool(values.get("sn")) or all(values.get(key) for key in ("__biz", "mid", "idx"))
    if parsed.path.rstrip("/") == "/s" and reliable:
        ordered = [(key, values[key]) for key in ("__biz", "mid", "idx", "sn") if key in values]
        identity_text = "&".join(f"{key}={value}" for key, value in ordered)
        return SourceIdentityValue(
            source_type="wechat_saved_html",
            raw_value=original_url,
            normalized_value=f"wechat:article:query:{identity_text}",
            native_id=identity_text,
            method="wechat_identity_query_fields",
            confidence="high",
        )
    return None


def unresolved_identity(
    source_type: str,
    original_url: str | None,
    html_sha256: str,
) -> SourceIdentityValue:
    return SourceIdentityValue(
        source_type=source_type,
        raw_value=original_url,
        normalized_value=f"unresolved:{source_type}:html:{html_sha256}",
        native_id=None,
        method="html_sha256_isolated_fallback",
        confidence="unresolved",
    )


def document_id_for_identity(prefix: str, identity: SourceIdentityValue) -> str:
    payload = (
        f"source-identity-v{IDENTITY_VERSION}\0{identity.source_type}\0{identity.normalized_value}"
    ).encode()
    return f"{prefix}-{hashlib.sha256(payload).hexdigest()[:16]}"
