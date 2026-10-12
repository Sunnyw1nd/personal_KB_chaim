from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from ..html_utils import meta_value, normalize_datetime, selector_text
from ..identity import SourceIdentityValue, normalize_wechat_identity, unresolved_identity
from ..models import Block, Message, Metadata, Severity
from ..parser import parse_body_blocks
from .base import Detection


class WeChatAdapter:
    name = "wechat_saved_html"
    source_type = "wechat_saved_html"
    document_prefix = "wechat"
    schema_version = "2.0.0"
    adapter_version = "0.2.0"
    body_selector = "#js_content"

    def detect(self, soup: BeautifulSoup) -> Detection | None:
        root = soup.select_one(self.body_selector)
        if not isinstance(root, Tag):
            return None
        original_url = meta_value(soup, [("property", "og:url"), ("name", "original-url")])
        evidence = ["body:#js_content"]
        if original_url and urlparse(original_url).hostname == "mp.weixin.qq.com":
            evidence.append("og:url host mp.weixin.qq.com")
        elif soup.select_one("#activity-name") and soup.select_one("#js_name"):
            evidence.append("metadata:#activity-name+#js_name")
        else:
            return None
        return Detection(tuple(evidence))

    def locate_body(self, soup: BeautifulSoup) -> Tag | None:
        candidates = soup.select(self.body_selector)
        return candidates[0] if len(candidates) == 1 and isinstance(candidates[0], Tag) else None

    def extract_metadata(
        self,
        soup: BeautifulSoup,
        raw_html: str,
        imported_at: str,
        messages: list[Message],
    ) -> Metadata:
        title = selector_text(soup, ["#activity-name", ".rich_media_title", "h1"]) or meta_value(
            soup, [("property", "og:title"), ("name", "twitter:title")]
        )
        if not title and soup.title:
            title = soup.title.get_text(" ", strip=True)
        if not title:
            title = "Untitled WeChat article"
            messages.append(
                Message(
                    code="metadata_title_missing",
                    level=Severity.WARNING,
                    message="Article title was not found; a placeholder was used.",
                )
            )
        account = selector_text(soup, ["#js_name", ".rich_media_meta_nickname", "#profileBt a"])
        author = selector_text(
            soup, ["#js_author_name", ".rich_media_meta_text.rich_media_meta_text_expanded"]
        )
        published_raw = selector_text(
            soup, ["#publish_time", "em#publish_time", ".rich_media_meta_text#publish_time"]
        ) or meta_value(soup, [("property", "article:published_time"), ("name", "publishdate")])
        original_url = meta_value(soup, [("property", "og:url"), ("name", "original-url")])
        if not original_url:
            canonical = soup.find("link", rel=lambda value: value and "canonical" in value)
            if canonical and canonical.get("href"):
                original_url = str(canonical["href"]).strip()
        if not original_url:
            candidates = re.findall(
                r"https?://mp\.weixin\.qq\.com/s"
                r"(?:/[A-Za-z0-9_-]+|\?[A-Za-z0-9_=&%.-]+)?",
                raw_html,
            )
            original_url = candidates[0] if candidates else None
        if original_url and urlparse(original_url).scheme not in {"http", "https"}:
            messages.append(
                Message(
                    code="metadata_original_url_invalid",
                    level=Severity.WARNING,
                    message="The extracted original URL was not HTTP(S) and was discarded.",
                )
            )
            original_url = None
        missing = [
            ("accountName", account),
            ("author", author),
            ("publishedAt", published_raw),
            ("originalUrl", original_url),
        ]
        for field, value in missing:
            if value is None:
                messages.append(
                    Message(
                        code=f"metadata_{field.lower()}_missing",
                        level=Severity.WARNING,
                        message=f"Metadata field {field} was not available.",
                    )
                )
        published = normalize_datetime(published_raw)
        if published_raw and not published:
            messages.append(
                Message(
                    code="metadata_published_at_unparsed",
                    level=Severity.WARNING,
                    message="Publication time was retained as raw text but could not be normalized.",
                )
            )
        return Metadata(
            title=title,
            accountName=account,
            author=author,
            publishedAt=published,
            publishedAtRaw=published_raw,
            originalUrl=original_url,
            sourceType=self.source_type,
            importedAt=imported_at,
        )

    def source_identity(
        self,
        metadata: Metadata,
        html_sha256: str,
        messages: list[Message],
    ) -> SourceIdentityValue:
        identity = normalize_wechat_identity(metadata.original_url)
        if identity is not None:
            return identity
        messages.append(
            Message(
                code="source_identity_unresolved",
                level=Severity.WARNING,
                message=(
                    "No reliable WeChat native article identity was available; this input is "
                    "isolated by its HTML hash and will never be merged by title."
                ),
                location="metadata.originalUrl",
            )
        )
        return unresolved_identity(self.source_type, metadata.original_url, html_sha256)

    def parse_blocks(
        self,
        root: Tag,
        image_handler: Callable[[Tag, str], str],
        messages: list[Message],
    ) -> list[Block]:
        return parse_body_blocks(
            root,
            image_handler,
            messages,
            root_selector=self.body_selector,
        )

    def ancillary_resource_roles(
        self,
        root: Tag,
        resolve_local: Callable[[str | None], tuple[Path | None, bool]],
    ) -> dict[Path, str]:
        return {}

    def excluded_regions(self) -> list[dict[str, str]]:
        return [
            {
                "selector": "outside #js_content",
                "reasonCode": "page_ui_asset",
                "details": "Only the explicit WeChat body root is parsed.",
            },
            {
                "selector": "script, iframe, style, noscript, template",
                "reasonCode": "executable_or_external_content",
                "details": "Active and presentation-only nodes are never executed or added to body content.",
            },
        ]
