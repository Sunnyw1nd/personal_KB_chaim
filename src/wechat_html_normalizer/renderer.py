from __future__ import annotations

import re
from urllib.parse import urlparse

from .models import Block, Document, InlineSpan


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\\", "\\\\")
        .replace("`", "\\`")
        .replace("*", "\\*")
        .replace("_", "\\_")
    )


def _safe_link(value: str) -> str | None:
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"http", "https", "mailto"}:
        return None
    return value.replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def _inline(spans: list[InlineSpan] | None, fallback: str, *, literal_join: bool = False) -> str:
    if not spans:
        return fallback
    output: list[str] = []
    for span in spans:
        text = _escape(span.text)
        for mark in span.marks:
            if mark == "code":
                text = f"`{span.text.replace('`', '``')}`"
            elif mark == "strong":
                text = f"**{text}**"
            elif mark == "emphasis":
                text = f"*{text}*"
        safe_href = _safe_link(span.href) if span.href else None
        if safe_href:
            text = f"[{text}]({safe_href})"
        output.append(text)
    return ("" if literal_join else " ").join(output).replace("\n", "  \n")


def _table(block: Block, *, literal: bool = False) -> str:
    rows = block.rows or []
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    escaped = [
        [
            (_escape(cell) if literal else cell).replace("|", "\\|").replace("\n", "<br>")
            for cell in row
        ]
        for row in normalized
    ]
    header = escaped[0]
    body = escaped[1:]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * width) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def render_markdown(document: Document) -> str:
    metadata = document.metadata
    lines = [f"# {_escape(metadata.title)}", "", "| 字段 | 内容 |", "| --- | --- |"]
    account_label = "公众号" if metadata.source_type == "wechat_saved_html" else "账号"
    values = [
        (account_label, metadata.account_name),
        ("作者", metadata.author),
        ("发布时间", metadata.published_at_raw or metadata.published_at),
    ]
    if metadata.modified_at_raw or metadata.modified_at:
        values.append(("最后编辑时间", metadata.modified_at_raw or metadata.modified_at))
    values.append(("原始 URL", metadata.original_url))
    values.append(("逻辑文档 ID", document.document_id))
    if document.revision:
        values.append(("修订 ID", document.revision.revision_id))
    if document.asset_completeness:
        completeness = document.asset_completeness
        completeness_display = (
            f"{completeness.available}/{completeness.expected} "
            f"({completeness.completeness_rate:.0%})"
        )
        values.append(
            (
                "正文图片完整性",
                completeness_display,
            )
        )
    for label, value in values:
        display = _escape(value) if value else "未知"
        safe_url = _safe_link(value) if label == "原始 URL" and value else None
        if safe_url:
            display = f"[{_escape(value)}]({safe_url})"
        display = display.replace("|", "\\|")
        lines.append(f"| {label} | {display} |")
    lines.extend(["", "---", ""])

    assets = {asset.asset_id: asset for asset in document.assets}
    local = document.schema_version == "3.0.0"
    for block in document.blocks:
        if block.plain_text:
            # A literal fence prevents #, numbering and indentation in TXT from becoming markup.
            runs = [len(m.group()) for m in re.finditer(r"`+", block.content or "")]
            fence = "`" * max(3, max(runs, default=0) + 1)
            lines.extend([fence + "text", block.content or "", fence, ""])
            continue
        if block.type == "heading":
            heading = (
                _inline(block.inlines, block.content or "", literal_join=True)
                if local
                else _escape(block.content or "")
            )
            lines.append(f"{'#' * (block.level or 2)} {heading}")
        elif block.type == "paragraph":
            lines.append(_inline(block.inlines, block.content or "", literal_join=local))
        elif block.type == "list":
            for index, item in enumerate(block.items or [], 1):
                marker = (
                    f"{item.number if item.number is not None else index}."
                    if (item.ordered if item.ordered is not None else block.ordered)
                    else "-"
                )
                lines.append(
                    f"{'    ' * (item.depth or 0)}{marker} {_inline(item.inlines, item.content, literal_join=local)}"
                )
        elif block.type == "quote":
            quote = (
                _inline(block.inlines, block.content or "", literal_join=True)
                if local
                else block.content or ""
            )
            lines.extend(f"> {line}" for line in quote.splitlines())
        elif block.type == "code":
            runs = [len(match.group(0)) for match in re.finditer(r"`+", block.content or "")]
            fence = "`" * max(3, (max(runs) + 1) if runs else 3)
            lines.extend([f"{fence}{block.language or ''}", block.content or "", fence])
        elif block.type == "table":
            lines.append(_table(block, literal=local))
        elif block.type == "image":
            asset = assets[block.asset_id or ""]
            original = asset.original_file_name or asset.local_src or "未知"
            if asset.output_path:
                lines.append(
                    f"![图片 {asset.asset_id}｜原文件：{original}｜待描述]({asset.output_path})"
                )
            else:
                lines.append(f"> ⚠ 图片 {asset.asset_id} 无法显示：本地资源缺失或不支持。")
            lines.extend(["", f"> 图片 {asset.asset_id}：待描述。原始文件为 `{original}`。"])
            if block.caption:
                lines.extend(["", f"> 图注：{_inline(block.caption_inlines, block.caption)}"])
        else:
            if local and block.original_tag == "thematic_break":
                lines.append("---")
            elif block.original_tag == "video":
                lines.append(f"> [嵌入视频待支持] {_escape(block.content or '')}")
                safe_source = _safe_link(block.source_url) if block.source_url else None
                if safe_source:
                    lines.append(f"> 来源地址：[{safe_source}]({safe_source})（未请求）")
            else:
                lines.extend(
                    [
                        f"> [未结构化的 `{block.original_tag}` 正文节点]",
                        ">",
                        *[f"> {line}" for line in (block.content or "").splitlines()],
                    ]
                )
        lines.append("")

    warnings = [
        message
        for message in document.processing.messages
        if message.level.value in {"warning", "error"}
    ]
    if warnings:
        lines.extend(["---", "", "## 解析提示", ""])
        for message in warnings:
            where = f"（{message.location}）" if message.location else ""
            lines.append(f"- `{message.code}`：{message.message}{where}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
