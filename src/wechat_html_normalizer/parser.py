from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Any

from bs4 import Comment, NavigableString, Tag

from .models import Block, InlineSpan, ListItem, Message, Severity

SKIP_TAGS = {"script", "style", "noscript", "iframe", "template", "svg", "canvas"}
CONTAINER_TAGS = {"article", "div", "figure", "main", "section"}
BLOCK_TAGS = {
    "blockquote",
    "dl",
    "figure",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "ol",
    "p",
    "pre",
    "table",
    "ul",
}


def clean_text(value: str) -> str:
    value = value.replace("\xa0", " ").replace("\u200b", "")
    lines = [re.sub(r"[ \t\f\v]+", " ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def is_hidden(tag: Tag) -> bool:
    if tag.has_attr("hidden") or tag.get("aria-hidden") == "true":
        return True
    classes = {str(item).lower() for item in tag.get("class", [])}
    if classes & {"hidden", "hide", "js_hidden"}:
        return True
    style = re.sub(r"\s+", "", str(tag.get("style", "")).lower())
    return "display:none" in style or "visibility:hidden" in style


def dom_locator(node: Tag, root: Tag, root_selector: str) -> str:
    if node is root:
        return root_selector
    parts: list[str] = []
    current: Tag | None = node
    while current is not None and current is not root:
        siblings = (
            [item for item in current.parent.find_all(current.name, recursive=False)]
            if current.parent
            else []
        )
        index = siblings.index(current) + 1 if current in siblings else 1
        parts.append(f"{current.name}:nth-of-type({index})")
        current = current.parent if isinstance(current.parent, Tag) else None
    return root_selector + " > " + " > ".join(reversed(parts))


def inline_spans(node: Tag | Iterable[Any]) -> list[InlineSpan]:
    spans: list[InlineSpan] = []

    def append(text: str, href: str | None, marks: list[str]) -> None:
        if text == "\n":
            if spans and not spans[-1].text.endswith("\n"):
                spans[-1].text += "\n"
            return
        normalized = clean_text(text)
        if not normalized:
            return
        valid_marks = [mark for mark in marks if mark in {"strong", "emphasis", "code"}]
        if spans and spans[-1].href == href and spans[-1].marks == valid_marks:
            spans[-1].text += " " + normalized
        else:
            spans.append(InlineSpan(text=normalized, href=href, marks=valid_marks))

    def visit(item: Any, href: str | None = None, marks: list[str] | None = None) -> None:
        marks = list(marks or [])
        if isinstance(item, Comment):
            return
        if isinstance(item, NavigableString):
            append(str(item), href, marks)
            return
        if not isinstance(item, Tag) or item.name in SKIP_TAGS or is_hidden(item):
            return
        if item.name == "br":
            append("\n", href, marks)
            return
        next_href = str(item.get("href")) if item.name == "a" and item.get("href") else href
        source_href = (
            str(item.get("data-source-href"))
            if item.name == "a" and item.get("data-source-href")
            else None
        )
        if item.name in {"b", "strong"}:
            marks.append("strong")
        elif item.name in {"em", "i"}:
            marks.append("emphasis")
        elif item.name == "code":
            marks.append("code")
        for child in item.children:
            before = len(spans)
            visit(child, next_href, marks)
            if source_href:
                for span in spans[before:]:
                    span.source_href = source_href

    values = node.children if isinstance(node, Tag) else node
    for value in values:
        visit(value)
    return spans


def spans_text(spans: list[InlineSpan]) -> str:
    return clean_text(" ".join(span.text for span in spans))


def parse_body_blocks(
    root: Tag,
    image_handler: Callable[[Tag, str], str],
    messages: list[Message],
    *,
    root_selector: str,
    special_handler: Callable[[Tag, str], list[dict[str, Any]] | None] | None = None,
    associate_figure_captions: bool = False,
) -> list[Block]:
    blocks: list[Block] = []

    def emit(kind: str, locator: str, **values: Any) -> None:
        blocks.append(
            Block(
                blockId=f"block-{len(blocks) + 1:04d}",
                type=kind,
                order=len(blocks) + 1,
                sourceLocator=locator,
                **values,
            )
        )

    def emit_inline(
        tag: Tag, children: Iterable[Any] | None = None, kind: str = "paragraph"
    ) -> None:
        spans = inline_spans(children if children is not None else tag)
        content = spans_text(spans)
        if content:
            emit(kind, dom_locator(tag, root, root_selector), content=content, inlines=spans)

    def walk_mixed(tag: Tag) -> None:
        inline_buffer: list[Any] = []

        def flush() -> None:
            if inline_buffer:
                emit_inline(tag, list(inline_buffer))
                inline_buffer.clear()

        for child in tag.children:
            if isinstance(child, Comment):
                continue
            if isinstance(child, NavigableString):
                inline_buffer.append(child)
                continue
            if not isinstance(child, Tag) or is_hidden(child) or child.name in SKIP_TAGS:
                continue
            if child.name == "img":
                flush()
                asset_id = image_handler(child, dom_locator(child, root, root_selector))
                emit("image", dom_locator(child, root, root_selector), assetId=asset_id)
            elif child.name in BLOCK_TAGS or child.name in CONTAINER_TAGS:
                flush()
                walk(child)
            elif child.find(["img", *BLOCK_TAGS, *CONTAINER_TAGS]):
                flush()
                walk_mixed(child)
            else:
                inline_buffer.append(child)
        flush()

    def walk(tag: Tag) -> None:
        if is_hidden(tag) or tag.name in SKIP_TAGS:
            return
        locator = dom_locator(tag, root, root_selector)
        if tag is not root and special_handler:
            special = special_handler(tag, locator)
            if special is not None:
                for draft in special:
                    values = dict(draft)
                    kind = str(values.pop("type"))
                    emit(kind, locator, **values)
                return
        if tag.name == "img":
            emit("image", locator, assetId=image_handler(tag, locator))
        elif re.fullmatch(r"h[1-6]", tag.name or ""):
            content = clean_text(tag.get_text(" ", strip=True))
            if content:
                emit(
                    "heading",
                    locator,
                    level=int(tag.name[1]),
                    content=content,
                    inlines=inline_spans(tag),
                )
        elif tag.name == "p":
            walk_mixed(tag)
        elif tag.name == "figure":
            if not associate_figure_captions:
                walk_mixed(tag)
                return
            images = [image for image in tag.find_all("img") if image.find_parent("figure") is tag]
            caption_node = tag.find("figcaption", recursive=False) or tag.find("figcaption")
            caption_spans = inline_spans(caption_node) if isinstance(caption_node, Tag) else []
            caption = spans_text(caption_spans) or None
            for image in images:
                image_locator = dom_locator(image, root, root_selector)
                emit(
                    "image",
                    image_locator,
                    assetId=image_handler(image, image_locator),
                    caption=caption,
                    captionInlines=caption_spans or None,
                )
        elif tag.name in {"ul", "ol"}:
            items = []
            for item in tag.find_all("li", recursive=False):
                spans = inline_spans(item)
                content = spans_text(spans)
                if content:
                    items.append(ListItem(content=content, inlines=spans))
            if items:
                emit("list", locator, ordered=tag.name == "ol", items=items)
            for image in tag.find_all("img"):
                image_locator = dom_locator(image, root, root_selector)
                emit("image", image_locator, assetId=image_handler(image, image_locator))
        elif tag.name == "blockquote":
            content = clean_text(tag.get_text("\n", strip=True))
            if content:
                emit("quote", locator, content=content, inlines=inline_spans(tag))
        elif tag.name == "pre":
            code = tag.find("code") or tag
            language = None
            for class_name in code.get("class", []):
                if str(class_name).startswith("language-"):
                    language = str(class_name).removeprefix("language-")
            if language is None:
                for class_name in tag.get("class", []):
                    if str(class_name).startswith("language-"):
                        language = str(class_name).removeprefix("language-")
            language = language or tag.get("data-language") or code.get("data-language")
            emit(
                "code",
                locator,
                content=(
                    code.get_text("", strip=False)
                    .replace("\r\n", "\n")
                    .replace("\r", "\n")
                    .replace("\u200b", "")
                    .strip("\n")
                ),
                language=str(language) if language else None,
            )
        elif tag.name == "table":
            rows = [
                [
                    clean_text(cell.get_text(" ", strip=True))
                    for cell in row.find_all(["th", "td"], recursive=False)
                ]
                for row in tag.find_all("tr")
            ]
            rows = [row for row in rows if row]
            if rows:
                emit("table", locator, rows=rows)
        elif tag.name in CONTAINER_TAGS or tag is root:
            walk_mixed(tag)
        elif tag.name in {"figcaption", "hr"}:
            return
        else:
            content = clean_text(tag.get_text(" ", strip=True))
            if content:
                emit("unknown", locator, content=content, originalTag=tag.name)
                messages.append(
                    Message(
                        code="body_unknown_element",
                        level=Severity.WARNING,
                        message=f"Body element <{tag.name}> was preserved as an unknown block.",
                        location=locator,
                    )
                )

    walk(root)
    return blocks
