"""Offline local-format adaptation and explicit local document identity.

No model calls, HTML renderer, workbook labels or implicit filename-based merging.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import uuid
import warnings
from pathlib import Path
from urllib.parse import unquote, urlsplit
from zipfile import ZipFile

from charset_normalizer import from_bytes
from markdown_it import MarkdownIt
from PIL import Image

from ..enrichment.dataset import _linked
from ..enrichment.runner import stable_sha, utc_now
from ..media import detect_media, sha256_file
from ..models import (
    Asset,
    Block,
    Document,
    DocumentRecord,
    InlineSpan,
    ListItem,
    LocalSource,
    Message,
    Metadata,
    OutputLocations,
    Processing,
    ProcessingReport,
    ResourceEntry,
    ResourceManifest,
    RevisionInfo,
    Severity,
    SourceIdentity,
)
from ..revision import (
    asset_completeness,
    asset_fingerprint,
    content_fingerprint,
    revision_fingerprint,
)
from ..sample_cache import ArchiveLimits, _validate_entries
from ..storage import _json_bytes, publish_revision
from .image_tasks import safe_child

RULE_VERSION = "local-v1-md3"
RASTER_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_IMAGE_PIXELS = 40_000_000
MAX_TEXT_BYTES = 10 * 1024 * 1024


class DuplicateCandidate(ValueError):
    def __init__(self, candidates):
        super().__init__("duplicate_candidate_choose_update_or_new")
        self.candidates = candidates


def decode_text(data: bytes, encoding_hint: str | None = None) -> tuple[str, str, bool]:
    if not data or len(data) > MAX_TEXT_BYTES:
        raise ValueError("empty_or_oversized_text")
    if encoding_hint:
        if encoding_hint not in {"utf-8", "utf-8-sig", "utf-16", "gb18030", "big5"}:
            raise ValueError("unsupported_text_encoding")
        text, encoding, uncertain = data.decode(encoding_hint), encoding_hint, False
    elif data.startswith(b"\xef\xbb\xbf"):
        text, encoding, uncertain = data.decode("utf-8-sig"), "utf-8-sig", False
    elif data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text, encoding, uncertain = data.decode("utf-16"), "utf-16", False
    else:
        try:
            text, encoding, uncertain = data.decode("utf-8"), "utf-8", False
        except UnicodeDecodeError:
            # Short Chinese text often scores as Big5 despite being GBK. Prefer a valid mainland
            # codec, but always report heuristic uncertainty; callers can explicitly select Big5.
            match = from_bytes(data, cp_isolation=["gb18030"]).best()
            if not match:
                match = from_bytes(data, cp_isolation=["big5"]).best()
            if not match or match.chaos > 0.1:
                raise ValueError("text_encoding_unknown") from None
            text, encoding, uncertain = str(match), match.encoding, True
    if not text.strip() or "\ufffd" in text or any(ord(c) < 32 and c not in "\n\r\t" for c in text):
        raise ValueError("binary_or_invalid_text")
    return text.replace("\r\n", "\n").replace("\r", "\n"), encoding, uncertain


def image_info(path: Path):
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("image_size_limit")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            media = detect_media(path)
            if not media.decodable_image or media.media_type not in RASTER_TYPES:
                raise ValueError("unsupported_or_damaged_image")
            if media.width * media.height > MAX_IMAGE_PIXELS:
                raise ValueError("image_pixel_limit")
            with Image.open(path) as image:
                if getattr(image, "n_frames", 1) != 1:
                    raise ValueError("animated_or_multiframe_image_not_supported")
                image.load()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValueError("image_pixel_limit") from error
    return media


def input_files(root: Path):
    """Bounded traversal: never follow a link/junction, even before checking resource refs."""
    pending, result, total, count = [root], [], 0, 0
    while pending:
        directory = pending.pop()
        if _linked(directory):
            raise ValueError("linked_input_refused")
        with os.scandir(directory) as entries:
            for entry in entries:
                count += 1
                if count > ArchiveLimits().max_entries:
                    raise ValueError("input_set_entry_limit")
                path = Path(entry.path)
                if _linked(path):
                    raise ValueError("linked_input_refused")
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False):
                    result.append(path)
                    total += path.stat().st_size
                    if (
                        path.stat().st_size > ArchiveLimits().max_file_bytes
                        or len(result) > ArchiveLimits().max_entries
                        or total > ArchiveLimits().max_total_bytes
                    ):
                        raise ValueError("input_set_size_limit")
    return sorted(result)


def extract_markdown_bundle(archive: Path, work_root: Path) -> Path:
    """Reuse ZIP entry/Windows/size/ratio rules; no candidate guessing or execution."""
    destination = safe_child(work_root, f"md-{uuid.uuid4().hex[:8]}", exists=False)
    destination.mkdir()
    with ZipFile(archive) as zipped:
        checked = _validate_entries(zipped.infolist(), ArchiveLimits())
        for entry, relative in checked:
            path = safe_child(destination, relative.as_posix(), exists=False)
            if entry.is_dir():
                path.mkdir(parents=True, exist_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            size = 0
            with zipped.open(entry) as source, path.open("xb") as target:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    if size > entry.file_size or size > ArchiveLimits().max_file_bytes:
                        raise ValueError("archive_file_size_limit")
                    target.write(chunk)
            if size != entry.file_size:
                raise ValueError("archive_size_mismatch")
    return destination


def _records(output: Path):
    for path in sorted(output.glob("*/document-record.json")):
        checked = safe_child(output, path.relative_to(output).as_posix())
        yield DocumentRecord.model_validate_json(checked.read_text(encoding="utf-8"))


class LocalParser:
    def __init__(self, root: Path, main: Path):
        self.root, self.main = root, main
        self.blocks, self.assets, self.copies, self.resources, self.messages = [], [], [], {}, []
        self.occurrences = {}

    def warn(self, code, text, locator=None):
        self.messages.append(
            Message(code=code, level=Severity.WARNING, message=text, location=locator)
        )

    def block(self, kind, locator, **values):
        order = len(self.blocks) + 1
        self.blocks.append(
            Block(
                blockId=f"block-{order:04d}",
                type=kind,
                order=order,
                sourceLocator=locator,
                **values,
            )
        )

    def picture(self, url, alt, locator, *, standalone=False):
        if len(self.assets) >= ArchiveLimits().max_entries:
            raise ValueError("body_image_reference_limit")
        # Relative URLs are decoded exactly once, then normalized within the declared input root.
        # Remaining encoded path syntax is refused instead of decoded a second time.
        parts = urlsplit(url)
        relative, path, failure = None, None, None
        if parts.scheme or parts.netloc:
            failure = (
                "remote_image_not_fetched"
                if parts.scheme in {"http", "https"}
                else "unsafe_image_reference"
            )
        else:
            decoded = unquote(parts.path)
            if (
                not decoded
                or "\\" in decoded
                or ":" in decoded
                or decoded.startswith("/")
                or re.search(r"%[0-9a-fA-F]{2}", decoded)
            ):
                failure = "unsafe_image_reference"
            else:
                relative = posixpath.normpath(
                    posixpath.join(self.main.parent.relative_to(self.root).as_posix(), decoded)
                )
                try:
                    path = safe_child(self.root, relative, exists=False)
                except (OSError, ValueError):
                    failure = "unsafe_image_reference"
        key_base = "standalone-image" if standalone else "reference:" + url
        self.occurrences[key_base] = self.occurrences.get(key_base, 0) + 1
        identity = f"{key_base}#{self.occurrences[key_base]}"
        asset_id = "asset-" + hashlib.sha256(identity.encode()).hexdigest()[:12]
        found = bool(path and not failure and path.is_file())
        digest = sha256_file(path) if found else None
        size = path.stat().st_size if found else None
        media, output = None, None
        if found:
            try:
                media = image_info(path)
                output = f"assets/{asset_id}.{media.extension}"
                self.copies.append((path, output))
            except (
                OSError,
                ValueError,
                Image.DecompressionBombError,
                Image.DecompressionBombWarning,
            ) as error:
                if standalone:
                    raise ValueError("unsupported_or_damaged_image") from error
                failure = "unsupported_or_damaged_image"
        else:
            failure = failure or "missing_resource"
        if failure:
            self.warn(
                failure,
                "Image not copied; no remote request or out-of-root read was made.",
                locator,
            )
        self.assets.append(
            Asset(
                assetId=asset_id,
                role="article_body_image",
                sourcePath=relative if found else None,
                outputPath=output,
                localSrc=url,
                originalUrl=url if parts.scheme in {"http", "https"} else None,
                declaredMediaType=None,
                detectedMediaType=media.media_type if media else None,
                originalFileName=path.name if found else None,
                sizeBytes=size,
                width=media.width if media else None,
                height=media.height if media else None,
                sha256=digest,
                identityKey=identity,
                identityMethod="local_reference_occurrence"
                if not standalone
                else "standalone_position",
                found=found,
                descriptionStatus="pending",
            )
        )
        resource_key = relative if path else url
        self.resources[resource_key] = ResourceEntry(
            path=resource_key,
            detectedType=media.media_type if media else None,
            referencedByPage=True,
            referencedByBody=True,
            decision="kept"
            if output
            else "ignored"
            if found
            else "refused"
            if failure == "unsafe_image_reference"
            else "missing",
            reasonCode="article_body_image" if output else failure,
            sizeBytes=size,
            sha256=digest,
        )
        self.block("image", locator, assetId=asset_id, caption=alt or None)

    def text(self, text):
        lines = text.splitlines(keepends=True)
        start, paragraph = 0, []
        for i, line in enumerate([*lines, ""], 1):
            if not line.strip():
                if paragraph:
                    self.block(
                        "paragraph",
                        f"lines:{start}-{i - 1}",
                        content="".join(paragraph).removesuffix("\n"),
                        plainText=True,
                    )
                    paragraph = []
            else:
                if not paragraph:
                    start = i
                paragraph.append(line)

    def markdown(self, text):
        lines = text.splitlines(keepends=True)
        # Front matter is preserved, never evaluated as YAML. No implicit metadata keys in v1.
        if lines and lines[0].strip() == "---":
            end = next(
                (i for i in range(1, len(lines)) if lines[i].strip() in {"---", "..."}), None
            )
            if end is not None:
                self.block(
                    "code", f"lines:1-{end + 1}", content="".join(lines[: end + 1]), language="yaml"
                )
                self.warn(
                    "front_matter_preserved",
                    "Front matter is preserved literally; no YAML metadata keys are interpreted.",
                )
                text = "\n" * (end + 1) + "".join(lines[end + 1 :])
        environment = {}
        tokens = (
            MarkdownIt("commonmark", {"html": True, "maxNesting": 30})
            .enable("table")
            .parse(text, environment)
        )
        lists, quote, heading, rows, cell, table_open = [], 0, None, None, None, None
        item_start = True
        current_list = None
        counters, item_number = [], None
        for token in tokens:
            line_range = token.map or [0, 1]
            loc = f"lines:{line_range[0] + 1}-{line_range[1]}"
            if token.type in {"bullet_list_open", "ordered_list_open"}:
                if not lists:
                    current_list = None
                lists.append(token.type == "ordered_list_open")
                counters.append(int(token.attrGet("start") or 1))
            elif token.type in {"bullet_list_close", "ordered_list_close"}:
                lists.pop()
                counters.pop()
            elif token.type == "list_item_open":
                item_start = True
                item_number = counters[-1] if lists[-1] else None
                counters[-1] += 1
            elif token.type == "blockquote_open":
                quote += 1
            elif token.type == "blockquote_close":
                quote -= 1
            elif token.type == "heading_open":
                heading = int(token.tag[1:])
            elif token.type == "heading_close":
                heading = None
            elif token.type in {"fence", "code_block"}:
                self.block(
                    "code",
                    loc,
                    content=token.content,
                    language=token.info.split()[0] if token.info.strip() else None,
                )
                if lists or quote:
                    self.warn(
                        "complex_container_flattened",
                        "A code block in a list/quote is preserved as a separate block.",
                        loc,
                    )
            elif token.type == "hr":
                self.block("unknown", loc, originalTag="thematic_break", content="---")
            elif token.type == "html_block":
                self.block("paragraph", loc, content=token.content, plainText=True)
                self.warn(
                    "raw_html_literal",
                    "Raw HTML is displayed as literal text; it is never executed.",
                    loc,
                )
            elif token.type == "table_open":
                rows, table_open = [], loc
                self.warn(
                    "table_inline_plain_text",
                    "Table cell formatting is reduced to visible text; values and column order are retained.",
                    loc,
                )
            elif token.type == "tr_open":
                rows.append([])
            elif token.type in {"td_open", "th_open"}:
                cell = True
            elif token.type in {"td_close", "th_close"}:
                cell = None
            elif token.type == "table_close":
                self.block("table", table_open, rows=rows)
                rows = None
            elif token.type == "inline":
                spans, marks, href = [], [], None

                def flush(
                    cell=cell,
                    rows=rows,
                    heading=heading,
                    loc=loc,
                    quote=quote,
                    item_number=item_number,
                ):
                    nonlocal spans, item_start, current_list
                    if not spans:
                        return
                    content = "".join(s.text for s in spans)
                    if cell:
                        rows[-1].append(content)
                    elif heading:
                        self.block("heading", loc, content=content, level=heading, inlines=spans)
                    elif lists:
                        if self.blocks and self.blocks[-1] is current_list:
                            self.blocks[-1].items.append(
                                ListItem(
                                    content=content,
                                    inlines=spans,
                                    depth=len(lists) - 1,
                                    ordered=lists[-1],
                                    number=item_number,
                                )
                            )
                        else:
                            self.block(
                                "list",
                                loc,
                                ordered=lists[0],
                                items=[
                                    ListItem(
                                        content=content,
                                        inlines=spans,
                                        depth=len(lists) - 1,
                                        ordered=lists[-1],
                                        number=item_number,
                                    )
                                ],
                            )
                            current_list = self.blocks[-1]
                        if not item_start:
                            self.warn(
                                "list_paragraph_flattened",
                                "Continuation list paragraph retained as a separate list item.",
                                loc,
                            )
                        item_start = False
                    else:
                        self.block(
                            "quote" if quote else "paragraph", loc, content=content, inlines=spans
                        )
                    spans = []

                for child in token.children or []:
                    if child.type == "image":
                        if cell:
                            spans.append(InlineSpan(text=child.content))
                            self.warn(
                                "table_image_not_extracted",
                                "Image inside table retained as alt text only.",
                                loc,
                            )
                        else:
                            flush()
                            self.picture(child.attrGet("src") or "", child.content, loc)
                    elif child.type in {"strong_open", "em_open"}:
                        marks.append("strong" if child.type == "strong_open" else "emphasis")
                    elif child.type in {"strong_close", "em_close"}:
                        if marks:
                            marks.pop()
                    elif child.type == "link_open":
                        href = child.attrGet("href")
                    elif child.type == "link_close":
                        href = None
                    elif child.type in {
                        "text",
                        "code_inline",
                        "softbreak",
                        "hardbreak",
                        "html_inline",
                    }:
                        value = "\n" if child.type.endswith("break") else child.content
                        spans.append(
                            InlineSpan(
                                text=value,
                                href=href,
                                marks=marks + (["code"] if child.type == "code_inline" else []),
                            )
                        )
                        if child.type == "html_inline":
                            self.warn(
                                "raw_html_literal",
                                "Inline HTML is retained as text, never executed.",
                                loc,
                            )
                flush()
        for label, definition in environment.get("references", {}).items():
            if label.startswith("^"):
                start, end = definition["map"]
                self.block(
                    "paragraph",
                    f"lines:{start + 1}-{end}",
                    content="".join(lines[start:end]),
                    plainText=True,
                )
                self.warn(
                    "footnote_definition_literal",
                    "Unsupported footnote definition retained as text; source Markdown remains authoritative.",
                )
        if any(marker in text for marker in ("$$", "[^", "\\(", "\\[")):
            self.warn(
                "extension_syntax_literal",
                "Math/footnote/plugin syntax is not evaluated; visible source text is retained where parsed.",
            )


def import_local_file(
    input_root: Path,
    main_path: str,
    output_root: Path,
    *,
    original_name=None,
    target_document_id=None,
    create_new=False,
    package_sha256=None,
    encoding_hint=None,
):
    """Create local identity or explicitly update one; exact duplicate candidates never auto-merge."""
    if target_document_id and create_new:
        raise ValueError("choose_update_or_new_not_both")
    root = input_root.absolute()
    output_root = output_root.absolute()
    if any(_linked(p) for p in (output_root, *output_root.parents)):
        raise ValueError("linked_output_refused")
    if output_root.resolve().is_relative_to(root.resolve()) or root.resolve().is_relative_to(
        output_root.resolve()
    ):
        raise ValueError("input_output_roots_must_not_overlap")
    main = safe_child(root, main_path)
    files = input_files(root)
    if not main.is_file():
        raise ValueError("main_file_missing")
    name = original_name or main.name
    extension = Path(name).suffix.lower()
    source_type = {
        ".txt": "local_text",
        ".md": "local_markdown",
        ".markdown": "local_markdown",
        ".jpg": "local_image",
        ".jpeg": "local_image",
        ".png": "local_image",
        ".webp": "local_image",
    }.get(extension)
    if not source_type:
        raise ValueError("unsupported_local_format")
    parser = LocalParser(root, main)
    encoding = None
    title_method = "file_name"
    title = Path(name).stem
    if source_type == "local_image":
        media = image_info(main)
        parser.picture(main.relative_to(main.parent).as_posix(), "", "image:1", standalone=True)
        media_type = media.media_type
    else:
        if main.stat().st_size > MAX_TEXT_BYTES:
            raise ValueError("text_size_limit")
        data = main.read_bytes()
        media = detect_media(main)
        if media.media_type not in {None, "text/html", "image/svg+xml"} or data.startswith(
            (b"PK\x03\x04", b"%PDF")
        ):
            raise ValueError("binary_disguised_as_text")
        text, encoding, uncertain = decode_text(data, encoding_hint)
        if uncertain:
            parser.warn(
                "encoding_detected_uncertain",
                f"Encoding {encoding} was detected heuristically; review decoded text.",
            )
        media_type = "text/plain" if source_type == "local_text" else "text/markdown"
        if source_type == "local_text":
            parser.text(text)
        else:
            parser.markdown(text)
            first = next((b for b in parser.blocks if b.type == "heading"), None)
            if first:
                title, title_method = first.content, "first_markdown_heading"
    if not parser.blocks:
        raise ValueError("document_body_empty")
    # Inventory all declared input attachments, but only referenced resources participate in identity.
    if (
        len(files) > ArchiveLimits().max_entries
        or sum(p.stat().st_size for p in files) > ArchiveLimits().max_total_bytes
    ):
        raise ValueError("input_set_size_limit")
    for path in files:
        relative = path.relative_to(root).as_posix()
        checked = safe_child(root, relative)
        if checked == main or relative in parser.resources:
            continue
        parser.resources[relative] = ResourceEntry(
            path=relative,
            detectedType=None,
            referencedByPage=False,
            referencedByBody=False,
            decision="ignored",
            reasonCode="unreferenced_attachment",
            sizeBytes=checked.stat().st_size,
            sha256=sha256_file(checked),
        )
    input_sha = sha256_file(main)
    if (
        sum(a.size_bytes or 0 for a in parser.assets if a.output_path)
        > ArchiveLimits().max_total_bytes
    ):
        raise ValueError("body_asset_total_size_limit")
    input_set = stable_sha(
        {
            "mainSha256": input_sha,
            "resources": []
            if source_type == "local_image"
            else [
                r.model_dump(mode="json", by_alias=True)
                for _, r in sorted(parser.resources.items())
                if r.referenced_by_body
            ],
        }
    )
    records = list(_records(output_root))
    if target_document_id:
        record = next((r for r in records if r.document_id == target_document_id), None)
        if (
            not record
            or record.source_identity.source_type != source_type
            or not record.document_id.startswith("local-")
        ):
            raise ValueError("update_target_missing_or_format_mismatch")
        document_id, identity = record.document_id, record.source_identity
    else:
        duplicates = [
            {"documentId": r.document_id, "title": r.title_at_creation, "sourceType": source_type}
            for r in records
            if r.source_identity.source_type == source_type
            and any(input_set in (v.input_set_sha256s or []) for v in r.revisions)
        ]
        if duplicates and not create_new:
            raise DuplicateCandidate(duplicates)
        document_id = "local-" + uuid.uuid4().hex[:16]
        identity = SourceIdentity(
            sourceType=source_type,
            rawValue=None,
            normalizedValue=document_id,
            nativeId=document_id,
            method="explicit_local_document",
            normalizationVersion="local-identity-v1",
            confidence="high",
            reliable=True,
        )
    metadata = Metadata(
        title=title,
        accountName=None,
        author=None,
        publishedAt=None,
        publishedAtRaw=None,
        originalUrl=None,
        sourceType=source_type,
        importedAt=utc_now(),
    )
    content_sha, asset_sha = (
        content_fingerprint(metadata, parser.blocks),
        asset_fingerprint(parser.assets),
    )
    fingerprint = revision_fingerprint(
        schema_version="3.0.0",
        adapter=source_type,
        adapter_version=RULE_VERSION,
        content_sha256=content_sha,
        asset_sha256=asset_sha,
    )
    revision = RevisionInfo(
        revisionId="revision-" + fingerprint[:16],
        fingerprintSha256=fingerprint,
        contentFingerprintSha256=content_sha,
        assetFingerprintSha256=asset_sha,
        packageSha256=package_sha256,
        fingerprintVersion="1",
    )
    completeness = asset_completeness(parser.assets)
    stats = {
        "blocks_total": len(parser.blocks),
        "body_image_references": len(parser.assets),
        "body_images_missing": completeness.missing,
        "body_images_copied": completeness.available,
        "resource_files": len(parser.resources),
    }
    processing = Processing(
        adapter=source_type,
        adapterVersion=RULE_VERSION,
        status="completed_with_warnings" if parser.messages else "completed",
        statistics=stats,
        messages=parser.messages,
    )
    source = LocalSource(
        format=source_type.removeprefix("local_"),
        inputFileName=name,
        inputSha256=input_sha,
        inputSetSha256=input_set,
        encoding=encoding,
        mediaType=media_type,
        titleMethod=title_method,
        mainPath=main_path,
        archiveSha256=package_sha256,
        importOrigin="markdown_bundle" if package_sha256 else "local_file",
    )
    document = Document(
        schemaVersion="3.0.0",
        documentId=document_id,
        sourceIdentity=identity,
        revision=revision,
        assetCompleteness=completeness,
        metadata=metadata,
        source=source,
        blocks=parser.blocks,
        assets=parser.assets,
        processing=processing,
    )
    if len(_json_bytes(document)) > MAX_TEXT_BYTES:
        raise ValueError("normalized_document_size_limit")
    manifest = ResourceManifest(
        schemaVersion="3.0.0",
        documentId=document_id,
        resources=[r for _, r in sorted(parser.resources.items())],
    )
    report = ProcessingReport(
        schemaVersion="3.0.0",
        documentId=document_id,
        adapter=source_type,
        adapterVersion=RULE_VERSION,
        status=processing.status,
        inputSummary=source.model_dump(mode="json", by_alias=True),
        metadata=metadata,
        statistics=stats,
        excludedRegions=[],
        messages=parser.messages,
        outputs=OutputLocations(
            documentJson="document.json",
            documentMarkdown="document.md",
            processingReport="processing-report.json",
            resourceManifest="resource-manifest.json",
            assetsDirectory="assets",
        ),
        sourceIdentity=identity,
        revision=revision,
        assetCompleteness=completeness,
    )
    return publish_revision(output_root, document, report, manifest, parser.copies)


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        description="Import TXT, Markdown with a declared resource root, or JPEG/PNG/WebP; offline only"
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("--input-root", type=Path)
    parser.add_argument("--output-directory", type=Path, default=Path("output/documents"))
    parser.add_argument("--update-document")
    parser.add_argument("--create-new", action="store_true")
    parser.add_argument("--encoding", choices=["utf-8", "utf-8-sig", "utf-16", "gb18030", "big5"])
    args = parser.parse_args(argv)
    root = (args.input_root or args.input.parent).absolute()
    path = args.input.absolute().relative_to(root).as_posix()
    try:
        result = import_local_file(
            root,
            path,
            args.output_directory,
            target_document_id=args.update_document,
            create_new=args.create_new,
            encoding_hint=args.encoding,
        )
        print(
            json.dumps(
                {
                    "documentId": result.document.document_id,
                    "decision": result.decision,
                    "revisionId": result.current_revision_id,
                },
                ensure_ascii=False,
            )
        )
    except DuplicateCandidate as error:
        print(
            json.dumps(
                {"decision": "duplicate_candidate", "candidates": error.candidates},
                ensure_ascii=False,
            )
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
