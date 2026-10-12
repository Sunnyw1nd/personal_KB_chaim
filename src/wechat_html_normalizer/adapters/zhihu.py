from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse, urlunparse

from bs4 import BeautifulSoup, Tag

from ..html_utils import meta_value, normalize_datetime, selector_text
from ..identity import SourceIdentityValue, normalize_zhihu_identity, unresolved_identity
from ..models import Block, InlineSpan, Message, Metadata, Severity
from ..parser import clean_text, parse_body_blocks
from .base import Detection


def _decoded_link_target(source_href: str) -> str:
    parsed = urlparse(source_href)
    if parsed.hostname == "link.zhihu.com":
        target = parse_qs(parsed.query).get("target", [None])[0]
        if target:
            decoded = unquote(target)
            if urlparse(decoded).scheme in {"http", "https"}:
                return decoded
    return source_href


def _same_instant(first: str | None, second: str | None) -> bool:
    if not first or not second:
        return False
    try:
        difference = abs(
            datetime.fromisoformat(first).timestamp() - datetime.fromisoformat(second).timestamp()
        )
        # The visible Zhihu timestamp is minute-precision, while JSON-LD/meta
        # timestamps commonly retain seconds and milliseconds.
        return difference < 60
    except ValueError:
        return False


def _safe_video_source(value: object) -> str | None:
    parsed = urlparse(str(value or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


class ZhihuColumnAdapter:
    name = "zhihu_column_saved_html"
    source_type = "zhihu_column_saved_html"
    document_prefix = "zhihu"
    schema_version = "2.0.0"
    adapter_version = "0.2.0"
    body_selector = ".Post-RichText"

    def detect(self, soup: BeautifulSoup) -> Detection | None:
        original_url = meta_value(soup, [("property", "og:url")])
        parsed = urlparse(original_url or "")
        host_matches = parsed.hostname in {"zhuanlan.zhihu.com", "www.zhihu.com", "zhihu.com"}
        path_matches = parsed.path.startswith("/p/")
        structures = [
            bool(soup.select_one(".Post-Title")),
            bool(soup.select_one(".AuthorInfo-name")),
            len(soup.select(self.body_selector)) == 1,
        ]
        if not (host_matches and path_matches and all(structures)):
            return None
        return Detection(
            (
                f"og:url host {parsed.hostname}",
                "og:url path /p/",
                "structure:.Post-Title+.AuthorInfo-name+.Post-RichText",
            )
        )

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
        title = selector_text(soup, [".Post-Title"]) or meta_value(soup, [("property", "og:title")])
        author = selector_text(soup, [".AuthorInfo-name"])
        original_url = meta_value(soup, [("property", "og:url")])
        if original_url and urlparse(original_url).scheme not in {"http", "https"}:
            messages.append(
                Message(
                    code="metadata_original_url_invalid",
                    level=Severity.WARNING,
                    message="The extracted original URL was not HTTP(S) and was discarded.",
                )
            )
            original_url = None
        published_raw = meta_value(soup, [("itemprop", "datePublished")])
        modified_raw = meta_value(soup, [("itemprop", "dateModified")])
        if not title:
            title = "Untitled Zhihu column article"
            messages.append(
                Message(
                    code="metadata_title_missing",
                    level=Severity.WARNING,
                    message="Zhihu article title was not found; a placeholder was used.",
                )
            )
        for field, value in [
            ("author", author),
            ("publishedAt", published_raw),
            ("modifiedAt", modified_raw),
            ("originalUrl", original_url),
        ]:
            if value is None:
                messages.append(
                    Message(
                        code=f"metadata_{field.lower()}_missing",
                        level=Severity.WARNING,
                        message=f"Metadata field {field} was not available.",
                    )
                )
        published = normalize_datetime(published_raw)
        modified = normalize_datetime(modified_raw)
        visible_time_raw = selector_text(soup, [".ContentItem-time"])
        visible_time = normalize_datetime(visible_time_raw)
        if visible_time_raw and visible_time:
            compared = modified if "编辑" in visible_time_raw else published
            field = "last-modified" if "编辑" in visible_time_raw else "published"
            if compared and not _same_instant(compared, visible_time):
                messages.append(
                    Message(
                        code=f"metadata_{field.replace('-', '_')}_time_mismatch",
                        level=Severity.WARNING,
                        message=(
                            f"The visible {field} time differs from the structured value; "
                            "the structured value was retained."
                        ),
                        location=".ContentItem-time",
                    )
                )
        if published_raw and not published:
            messages.append(
                Message(
                    code="metadata_published_at_unparsed",
                    level=Severity.WARNING,
                    message="Publication time was retained but could not be normalized.",
                )
            )
        if modified_raw and not modified:
            messages.append(
                Message(
                    code="metadata_modified_at_unparsed",
                    level=Severity.WARNING,
                    message="Last-modified time was retained but could not be normalized.",
                )
            )
        return Metadata(
            title=title,
            accountName=None,
            author=author,
            publishedAt=published,
            publishedAtRaw=published_raw,
            modifiedAt=modified,
            modifiedAtRaw=modified_raw,
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
        identity = normalize_zhihu_identity(metadata.original_url)
        if identity is not None:
            return identity
        messages.append(
            Message(
                code="source_identity_unresolved",
                level=Severity.WARNING,
                message=(
                    "No reliable Zhihu column article ID was available; this input is isolated "
                    "by its HTML hash and will never be merged by title."
                ),
                location="metadata.originalUrl",
            )
        )
        return unresolved_identity(self.source_type, metadata.original_url, html_sha256)

    def _special_handler(
        self, root: Tag, messages: list[Message]
    ) -> Callable[[Tag, str], list[dict[str, object]] | None]:
        def handle(tag: Tag, locator: str) -> list[dict[str, object]] | None:
            classes = {str(value) for value in tag.get("class", [])}
            if "RichText-LinkCardContainer" in classes:
                anchor = tag.select_one("a.LinkCard[href]")
                if not isinstance(anchor, Tag):
                    return None
                source_href = str(anchor.get("href"))
                target = _decoded_link_target(source_href)
                title_node = anchor.select_one(".LinkCard-title")
                description_node = anchor.select_one(".LinkCard-desc")
                title = clean_text(
                    title_node.get_text(" ", strip=True)
                    if title_node
                    else anchor.get_text(" ", strip=True)
                )
                description = clean_text(
                    description_node.get_text(" ", strip=True) if description_node else ""
                )
                content = title
                if description and description not in title:
                    content = f"{title} — {description}"
                return [
                    {
                        "type": "paragraph",
                        "content": content,
                        "inlines": [InlineSpan(text=content, href=target, sourceHref=source_href)],
                    }
                ]
            video = tag.find("video")
            if video:
                # Consume the highest body child containing the video so its poster is not parsed as an image.
                if tag.parent is not root:
                    return None
                visible = clean_text(tag.get_text(" ", strip=True))
                content = "嵌入视频待支持"
                if visible:
                    content += f"：{visible}"
                messages.append(
                    Message(
                        code="unsupported_embedded_video",
                        level=Severity.WARNING,
                        message="An embedded Zhihu video was preserved as a placeholder; no video resource was requested.",
                        location=locator,
                    )
                )
                return [
                    {
                        "type": "unknown",
                        "content": content,
                        "originalTag": "video",
                        "sourceUrl": _safe_video_source(video.get("src")),
                    }
                ]
            return None

        return handle

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
            special_handler=self._special_handler(root, messages),
            associate_figure_captions=True,
        )

    def ancillary_resource_roles(
        self,
        root: Tag,
        resolve_local: Callable[[str | None], tuple[Path | None, bool]],
    ) -> dict[Path, str]:
        roles: dict[Path, str] = {}
        for image in root.select(".RichText-LinkCardContainer img"):
            path, safe = resolve_local(str(image.get("src")) if image.get("src") else None)
            if path is not None and safe:
                roles[path.absolute()] = "link_card_thumbnail"
        for video in root.find_all("video"):
            container: Tag | None = video.parent if isinstance(video.parent, Tag) else None
            selected: Tag | None = None
            while container is not None and container is not root:
                if container.find("img"):
                    selected = container
                if container.parent is root:
                    break
                container = container.parent if isinstance(container.parent, Tag) else None
            if selected:
                for image in selected.find_all("img"):
                    path, safe = resolve_local(str(image.get("src")) if image.get("src") else None)
                    if path is not None and safe:
                        roles[path.absolute()] = "video_poster"
        return roles

    def excluded_regions(self) -> list[dict[str, str]]:
        return [
            {
                "selector": "outside .Post-RichText",
                "reasonCode": "page_ui_asset",
                "details": "Navigation, author controls, comments, recommendations, and other page UI are outside the selected article body.",
            },
            {
                "selector": ".Post-RichText svg, script, iframe, style, noscript, template",
                "reasonCode": "executable_or_interface_content",
                "details": "Inline interface SVG and active or external content are excluded without execution or loading.",
            },
        ]
