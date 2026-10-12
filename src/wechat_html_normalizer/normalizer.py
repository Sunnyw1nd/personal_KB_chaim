from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup, Tag
from charset_normalizer import from_bytes

from .adapters import ADAPTERS, ArticleAdapter, WeChatAdapter, ZhihuColumnAdapter
from .identity import IDENTITY_VERSION, document_id_for_identity
from .media import declared_media_type, detect_media, sha256_file
from .models import (
    Asset,
    AssetCompleteness,
    Document,
    Message,
    OutputLocations,
    Processing,
    ProcessingReport,
    ResourceEntry,
    ResourceManifest,
    RevisionInfo,
    Severity,
    Source,
    SourceIdentity,
)
from .revision import (
    FINGERPRINT_VERSION,
    AssetIdentityAllocator,
    asset_completeness,
    asset_fingerprint,
    content_fingerprint,
    revision_fingerprint,
)
from .sample_cache import PreparedSample
from .storage import ImportDecision, StorageError, publish_revision


@dataclass(frozen=True)
class Limits:
    max_html_bytes: int = 10 * 1024 * 1024
    max_resource_files: int = 2_000
    max_resource_bytes: int = 50 * 1024 * 1024
    max_total_resource_bytes: int = 250 * 1024 * 1024


@dataclass(frozen=True)
class NormalizationResult:
    document: Document
    report: ProcessingReport
    manifest: ResourceManifest
    output_directory: Path
    document_directory: Path
    import_decision: ImportDecision
    current_revision_id: str
    current_changed: bool


class NormalizationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _decode_html(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), "utf-16"
    head = data[:4096].decode("ascii", errors="ignore")
    match = re.search(r"<meta[^>]+charset\s*=\s*[\"']?\s*([\w.-]+)", head, re.IGNORECASE)
    if not match:
        match = re.search(r"content\s*=\s*[\"'][^\"']*charset=([\w.-]+)", head, re.IGNORECASE)
    if match:
        encoding = match.group(1)
        try:
            return data.decode(encoding), encoding.lower()
        except (LookupError, UnicodeDecodeError):
            pass
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        result = from_bytes(data).best()
        if result is None or result.encoding is None:
            raise NormalizationError(
                "html_encoding_unknown", "HTML encoding could not be determined."
            )
        return str(result), result.encoding.lower()


def _validate_inputs(
    html_path: Path, resource_directory: Path, output_directory: Path, limits: Limits
) -> tuple[Path, Path, Path]:
    html_path = html_path.absolute()
    resource_directory = resource_directory.absolute()
    output_directory = output_directory.absolute()
    if not html_path.is_file():
        raise NormalizationError(
            "html_not_readable", "The HTML path does not exist or is not a readable file."
        )
    if html_path.is_symlink():
        raise NormalizationError(
            "html_symlink_refused", "A symbolic-link HTML input is not accepted."
        )
    if html_path.stat().st_size > limits.max_html_bytes:
        raise NormalizationError(
            "html_size_limit", "The HTML file exceeds the configured size limit."
        )
    if not resource_directory.is_dir():
        raise NormalizationError(
            "resource_directory_missing", "The resource directory does not exist."
        )
    if resource_directory.is_symlink():
        raise NormalizationError(
            "resource_directory_symlink_refused",
            "A symbolic-link resource directory is not accepted.",
        )
    if not _is_within(resource_directory.resolve(), html_path.parent.resolve()):
        raise NormalizationError(
            "resource_directory_outside_input",
            "The resource directory must be inside the HTML input directory.",
        )
    if (
        output_directory == html_path.parent
        or _is_within(html_path, output_directory)
        or _is_within(resource_directory, output_directory)
        or _is_within(output_directory, resource_directory)
    ):
        raise NormalizationError(
            "output_overlaps_input",
            "The output directory must not contain or equal the original inputs.",
        )
    return html_path, resource_directory, output_directory


def _local_reference(value: str | None, html_parent: Path) -> tuple[Path | None, bool]:
    if not value or value.startswith(("data:", "javascript:", "blob:", "#")):
        return None, False
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"} or value.startswith("//"):
        return None, False
    if parsed.scheme == "file":
        raw_path = unquote(parsed.path.lstrip("/"))
        candidate = Path(raw_path)
    elif parsed.scheme:
        return None, False
    else:
        candidate = html_parent / Path(unquote(parsed.path.replace("/", os.sep)))
    normalized = Path(os.path.abspath(candidate))
    return normalized, _is_within(normalized, html_parent.resolve())


def _page_references(
    soup: BeautifulSoup, html_parent: Path
) -> tuple[set[Path], set[Path], set[Path], set[Path], int, int, int]:
    local: set[Path] = set()
    script_paths: set[Path] = set()
    iframe_paths: set[Path] = set()
    stylesheet_paths: set[Path] = set()
    external = scripts = iframes = 0
    for tag in soup.find_all(True):
        if tag.name == "script":
            scripts += 1
        if tag.name == "iframe":
            iframes += 1
        for attribute in ("src", "data-src", "href", "poster"):
            value = tag.get(attribute)
            if not isinstance(value, str):
                continue
            parsed = urlparse(value)
            if parsed.scheme in {"http", "https"} or value.startswith("//"):
                external += 1
            else:
                path, safe = _local_reference(value, html_parent)
                if path is not None and safe:
                    local.add(path)
                    if tag.name == "script" and attribute == "src":
                        script_paths.add(path)
                    if tag.name == "iframe" and attribute == "src":
                        iframe_paths.add(path)
                    if (
                        tag.name == "link"
                        and attribute == "href"
                        and "stylesheet" in {str(item).lower() for item in tag.get("rel", [])}
                    ):
                        stylesheet_paths.add(path)
    return local, script_paths, iframe_paths, stylesheet_paths, external, scripts, iframes


def _resource_reason(
    path: Path,
    media_type: str | None,
    referenced: bool,
    referenced_as_script: bool,
    referenced_as_iframe: bool,
    referenced_as_stylesheet: bool,
    ancillary_role: str | None,
) -> tuple[str, str]:
    suffix = path.suffix.lower()
    lower_name = path.name.lower()
    if ancillary_role:
        return "ignored", ancillary_role
    if referenced_as_script or suffix in {".js", ".mjs"} or ".js." in lower_name:
        return "refused", "executable_script"
    if referenced_as_stylesheet or suffix == ".css" or ".css." in lower_name:
        return "ignored", "stylesheet"
    if referenced_as_iframe or suffix in {".html", ".htm"} or media_type == "text/html":
        return "refused", "auxiliary_frame"
    if media_type and media_type.startswith("image/"):
        return "ignored", "page_ui_asset" if referenced else "not_referenced_by_article_body"
    if suffix in {".woff", ".woff2", ".ttf", ".otf", ".eot"}:
        return "ignored", "font"
    return (
        "ignored",
        "not_referenced_by_article_body" if referenced else "unsupported_resource_type",
    )


def _select_adapter(
    soup: BeautifulSoup,
    explicit_adapter: ArticleAdapter | None,
) -> tuple[ArticleAdapter, tuple[str, ...]]:
    if explicit_adapter is not None:
        return explicit_adapter, ()
    matches = [
        (adapter, detection)
        for adapter in ADAPTERS
        if (detection := adapter.detect(soup)) is not None
    ]
    if not matches:
        raise NormalizationError(
            "source_unsupported",
            "The saved HTML is not a reliably recognized WeChat article or Zhihu column article; no generic page fallback was used.",
        )
    if len(matches) != 1:
        raise NormalizationError(
            "source_ambiguous",
            f"Multiple source adapters matched the page: {[adapter.name for adapter, _ in matches]}",
        )
    adapter, detection = matches[0]
    return adapter, detection.evidence


def _normalize_article(
    html_path: Path,
    resource_directory: Path,
    output_directory: Path,
    *,
    explicit_adapter: ArticleAdapter | None,
    limits: Limits | None = None,
    imported_at: str | None = None,
    source_sample: PreparedSample | None = None,
) -> NormalizationResult:
    limits = limits or Limits()
    html_path, resource_directory, output_directory = _validate_inputs(
        html_path, resource_directory, output_directory, limits
    )
    raw = html_path.read_bytes()
    html_sha256 = sha256_file(html_path)
    html_text, encoding = _decode_html(raw)
    soup = BeautifulSoup(html_text, "lxml")
    adapter, detection_evidence = _select_adapter(soup, explicit_adapter)
    imported_at = imported_at or datetime.now(UTC).isoformat().replace("+00:00", "Z")
    root = adapter.locate_body(soup)
    if not isinstance(root, Tag):
        raise NormalizationError(
            "body_root_missing",
            f"Required {adapter.name} body root {adapter.body_selector} was missing or ambiguous; no whole-page fallback was used.",
        )

    messages: list[Message] = []
    expected_directory = f"{html_path.stem}_files"
    if resource_directory.name != expected_directory:
        messages.append(
            Message(
                code="resource_directory_name_unexpected",
                level=Severity.WARNING,
                message=f"Resource directory name differs from the conventional {expected_directory!r}.",
            )
        )
    (
        page_refs,
        page_script_paths,
        page_iframe_paths,
        page_stylesheet_paths,
        external_count,
        script_count,
        iframe_count,
    ) = _page_references(soup, html_path.parent)
    if script_count:
        messages.append(
            Message(
                code="scripts_refused",
                level=Severity.INFO,
                message=f"Found {script_count} script element(s); none were executed.",
            )
        )
    if iframe_count:
        messages.append(
            Message(
                code="iframes_refused",
                level=Severity.INFO,
                message=f"Found {iframe_count} iframe element(s); none were loaded.",
            )
        )
    if external_count:
        messages.append(
            Message(
                code="external_resources_not_loaded",
                level=Severity.INFO,
                message=f"Found {external_count} external page reference(s); no network requests were made.",
            )
        )

    if source_sample is not None and (
        source_sample.html_path.resolve() != html_path.resolve()
        or source_sample.resource_directory.resolve() != resource_directory.resolve()
    ):
        raise NormalizationError(
            "sample_provenance_mismatch",
            "Prepared sample provenance does not match the supplied HTML and resource directory.",
        )

    resource_paths = sorted(
        (path for path in resource_directory.rglob("*") if path.is_file() or path.is_symlink()),
        key=lambda path: path.relative_to(resource_directory).as_posix(),
    )
    if len(resource_paths) > limits.max_resource_files:
        raise NormalizationError(
            "resource_count_limit",
            "The resource directory exceeds the configured file count limit.",
        )
    total_size = 0
    resource_info: dict[Path, tuple[str | None, int, str | None, bool]] = {}
    for path in resource_paths:
        if path.is_symlink():
            resource_info[path.absolute()] = (None, 0, None, True)
            continue
        size = path.stat().st_size
        if size > limits.max_resource_bytes:
            raise NormalizationError(
                "resource_size_limit", f"Resource {path.name!r} exceeds the per-file size limit."
            )
        total_size += size
        if total_size > limits.max_total_resource_bytes:
            raise NormalizationError(
                "resource_total_size_limit", "Resources exceed the configured total size limit."
            )
        info = detect_media(path)
        resource_info[path.absolute()] = (info.media_type, size, sha256_file(path), False)

    assets: list[Asset] = []
    body_paths: set[Path] = set()
    ancillary_roles = adapter.ancillary_resource_roles(
        root, lambda value: _local_reference(value, html_path.parent)
    )
    ancillary_roles = {
        path: role for path, role in ancillary_roles.items() if _is_within(path, resource_directory)
    }
    body_reference_paths: set[Path] = set(ancillary_roles)
    asset_copies: list[tuple[Path, str]] = []
    asset_identities = AssetIdentityAllocator()

    def image_handler(tag: Tag, locator: str) -> str:
        asset_id, identity_key, identity_method = asset_identities.allocate(tag, locator)
        src = (
            str(tag.get("src") or tag.get("data-src") or tag.get("data-original") or "").strip()
            or None
        )
        remote = (
            str(
                tag.get("data-original") or tag.get("data-actualsrc") or tag.get("data-src") or ""
            ).strip()
            or None
        )
        if remote and urlparse(remote).scheme not in {"http", "https"}:
            remote = None
        declared = declared_media_type(tag.get("data-type"), tag.get("type"))
        local_path, safe = _local_reference(src, html_path.parent)
        if local_path is None:
            for attribute in ("data-src", "data-original", "data-actualsrc"):
                if tag.get(attribute):
                    local_path, safe = _local_reference(str(tag.get(attribute)), html_path.parent)
                    if local_path is not None:
                        break
        if local_path is not None and safe and _is_within(local_path, resource_directory):
            body_reference_paths.add(local_path.absolute())
        found = False
        media_type = extension = source_path = output_path = original_name = digest = None
        size = width = height = None
        if local_path is not None and not safe:
            messages.append(
                Message(
                    code="unsafe_path",
                    level=Severity.WARNING,
                    message="A body image reference escaped the allowed input directory and was not read.",
                    location=locator,
                )
            )
        elif local_path is not None and (
            local_path.is_symlink()
            or any(
                parent.is_symlink()
                for parent in local_path.parents
                if _is_within(parent, html_path.parent)
            )
        ):
            messages.append(
                Message(
                    code="symlink_resource_refused",
                    level=Severity.WARNING,
                    message="A symbolic-link body image was not followed.",
                    location=locator,
                )
            )
        elif (
            local_path is not None
            and local_path.is_file()
            and _is_within(local_path, resource_directory)
        ):
            found = True
            body_paths.add(local_path.absolute())
            original_name = local_path.name
            source_path = local_path.relative_to(html_path.parent).as_posix()
            size = local_path.stat().st_size
            digest = sha256_file(local_path)
            detected = detect_media(local_path)
            media_type, extension, width, height = (
                detected.media_type,
                detected.extension,
                detected.width,
                detected.height,
            )
            if detected.decodable_image and extension:
                output_path = f"assets/{asset_id}.{extension}"
                asset_copies.append((local_path, output_path))
            else:
                messages.append(
                    Message(
                        code="unsupported_resource_type",
                        level=Severity.WARNING,
                        message="The referenced body resource is not a supported, decodable raster image and was not copied.",
                        location=locator,
                    )
                )
            if declared and media_type and declared != media_type:
                messages.append(
                    Message(
                        code="declared_type_mismatch",
                        level=Severity.WARNING,
                        message=f"Declared media type {declared} differs from detected type {media_type}.",
                        location=locator,
                    )
                )
        else:
            messages.append(
                Message(
                    code="missing_resource",
                    level=Severity.WARNING,
                    message="A body image has no readable local file; remote download is disabled.",
                    location=locator,
                )
            )
        assets.append(
            Asset(
                assetId=asset_id,
                role="article_body_image",
                sourcePath=source_path,
                outputPath=output_path,
                localSrc=src,
                originalUrl=remote,
                declaredMediaType=declared,
                detectedMediaType=media_type,
                originalFileName=original_name,
                sizeBytes=size,
                width=width,
                height=height,
                sha256=digest,
                identityKey=identity_key,
                identityMethod=identity_method,
                found=found,
                description=None,
                descriptionStatus="pending",
                descriptionMethod=None,
                ocrText=None,
            )
        )
        return asset_id

    metadata = adapter.extract_metadata(soup, html_text, imported_at, messages)
    identity_value = adapter.source_identity(metadata, html_sha256, messages)
    document_id = document_id_for_identity(adapter.document_prefix, identity_value)
    source_identity = SourceIdentity(
        sourceType=identity_value.source_type,
        rawValue=identity_value.raw_value,
        normalizedValue=identity_value.normalized_value,
        nativeId=identity_value.native_id,
        method=identity_value.method,
        normalizationVersion=IDENTITY_VERSION,
        confidence=identity_value.confidence,
        reliable=identity_value.reliable,
    )
    blocks = adapter.parse_blocks(root, image_handler, messages)
    if not blocks:
        raise NormalizationError(
            "body_empty",
            f"{adapter.body_selector} was present but no supported body content was found.",
        )

    manifest_entries: list[ResourceEntry] = []
    for path in resource_paths:
        absolute = path.absolute()
        media_type, size, digest, symlink = resource_info[absolute]
        relative = path.relative_to(html_path.parent).as_posix()
        if symlink:
            manifest_entries.append(
                ResourceEntry(
                    path=relative,
                    detectedType=None,
                    referencedByPage=absolute in page_refs,
                    referencedByBody=absolute in body_reference_paths,
                    decision="refused",
                    reasonCode="unsafe_path",
                    details="Symbolic links are not followed.",
                    sizeBytes=None,
                    sha256=None,
                )
            )
        elif absolute in body_paths:
            copied = any(source.absolute() == absolute for source, _ in asset_copies)
            manifest_entries.append(
                ResourceEntry(
                    path=relative,
                    detectedType=media_type,
                    referencedByPage=True,
                    referencedByBody=True,
                    decision="kept" if copied else "ignored",
                    reasonCode="article_body_image" if copied else "unsupported_resource_type",
                    details=None,
                    sizeBytes=size,
                    sha256=digest,
                )
            )
        else:
            decision, reason = _resource_reason(
                path,
                media_type,
                absolute in page_refs,
                absolute in page_script_paths,
                absolute in page_iframe_paths,
                absolute in page_stylesheet_paths,
                ancillary_roles.get(absolute),
            )
            manifest_entries.append(
                ResourceEntry(
                    path=relative,
                    detectedType=media_type,
                    referencedByPage=absolute in page_refs,
                    referencedByBody=absolute in body_reference_paths,
                    decision=decision,
                    reasonCode=reason,
                    details=None,
                    sizeBytes=size,
                    sha256=digest,
                )
            )

    referenced_missing = sorted(
        path
        for path in page_refs | body_reference_paths
        if not path.exists() and _is_within(path, resource_directory)
    )
    for path in referenced_missing:
        by_body = path in body_reference_paths
        manifest_entries.append(
            ResourceEntry(
                path=path.relative_to(html_path.parent).as_posix(),
                detectedType=None,
                referencedByPage=path in page_refs,
                referencedByBody=by_body,
                decision="missing",
                reasonCode="missing_resource",
                details="Referenced by body HTML but absent locally."
                if by_body
                else "Referenced by page HTML but absent locally.",
            )
        )
    manifest_entries.sort(key=lambda item: item.path)

    counts: dict[str, int] = {}
    for block in blocks:
        counts[f"blocks_{block.type}"] = counts.get(f"blocks_{block.type}", 0) + 1
    counts.update(
        {
            "blocks_total": len(blocks),
            "body_image_references": len(assets),
            "body_images_found": sum(asset.found for asset in assets),
            "body_images_missing": sum(not asset.found for asset in assets),
            "body_images_copied": sum(bool(asset.output_path) for asset in assets),
            "body_images_type_mismatch": sum(
                message.code == "declared_type_mismatch" for message in messages
            ),
            "resource_files": len(resource_paths),
            "resource_total_bytes": total_size,
            "external_page_references": external_count,
            "page_script_elements": script_count,
            "page_iframe_elements": iframe_count,
        }
    )
    if adapter.name == "zhihu_column_saved_html":
        counts.update(
            {
                "image_captions": sum(
                    bool(block.caption) for block in blocks if block.type == "image"
                ),
                "link_cards": sum(
                    bool(
                        span.source_href and urlparse(span.source_href).hostname == "link.zhihu.com"
                    )
                    for block in blocks
                    for span in (block.inlines or [])
                ),
                "embedded_videos": sum(
                    message.code == "unsupported_embedded_video" for message in messages
                ),
                "link_card_thumbnails": sum(
                    role == "link_card_thumbnail" for role in ancillary_roles.values()
                ),
                "video_posters": sum(role == "video_poster" for role in ancillary_roles.values()),
            }
        )
    status = (
        "completed_with_warnings"
        if any(message.level in {Severity.WARNING, Severity.ERROR} for message in messages)
        else "completed"
    )
    completeness: AssetCompleteness = asset_completeness(assets)
    content_sha256 = content_fingerprint(metadata, blocks)
    asset_sha256 = asset_fingerprint(assets)
    fingerprint_sha256 = revision_fingerprint(
        schema_version=adapter.schema_version,
        adapter=adapter.name,
        adapter_version=adapter.adapter_version,
        content_sha256=content_sha256,
        asset_sha256=asset_sha256,
    )
    revision = RevisionInfo(
        revisionId=f"revision-{fingerprint_sha256[:16]}",
        fingerprintSha256=fingerprint_sha256,
        contentFingerprintSha256=content_sha256,
        assetFingerprintSha256=asset_sha256,
        packageSha256=source_sample.archive_sha256 if source_sample else None,
        fingerprintVersion=FINGERPRINT_VERSION,
    )
    document = Document(
        schemaVersion=adapter.schema_version,
        documentId=document_id,
        sourceIdentity=source_identity,
        revision=revision,
        assetCompleteness=completeness,
        metadata=metadata,
        source=Source(
            htmlFileName=html_path.name,
            resourceDirectoryName=resource_directory.name,
            htmlSha256=html_sha256,
            htmlEncoding=encoding,
            archiveFileName=source_sample.archive_path.name if source_sample else None,
            archiveSha256=source_sample.archive_sha256 if source_sample else None,
            htmlArchivePath=source_sample.html_archive_path if source_sample else None,
            resourceArchivePath=source_sample.resource_archive_path if source_sample else None,
        ),
        blocks=blocks,
        assets=assets,
        processing=Processing(
            adapter=adapter.name,
            adapterVersion=adapter.adapter_version,
            status=status,
            statistics=counts,
            messages=messages,
            detectionEvidence=list(detection_evidence) or None,
        ),
    )
    manifest = ResourceManifest(
        schemaVersion=adapter.schema_version, documentId=document_id, resources=manifest_entries
    )
    locations = OutputLocations(
        documentJson="document.json",
        documentMarkdown="document.md",
        processingReport="processing-report.json",
        resourceManifest="resource-manifest.json",
        assetsDirectory="assets",
    )
    report = ProcessingReport(
        schemaVersion=adapter.schema_version,
        documentId=document_id,
        adapter=adapter.name,
        adapterVersion=adapter.adapter_version,
        status=status,
        inputSummary={
            "htmlFileName": html_path.name,
            "resourceDirectoryName": resource_directory.name,
            "htmlSha256": html_sha256,
            "htmlBytes": len(raw),
            "htmlEncoding": encoding,
            "resourceFiles": len(resource_paths),
            "resourceBytes": total_size,
            **(
                {
                    "archiveFileName": source_sample.archive_path.name,
                    "archiveSha256": source_sample.archive_sha256,
                }
                if source_sample
                else {}
            ),
        },
        metadata=metadata,
        statistics=counts,
        excludedRegions=adapter.excluded_regions(),
        messages=messages,
        outputs=locations,
        detectionEvidence=list(detection_evidence) or None,
        sourceIdentity=source_identity,
        revision=revision,
        assetCompleteness=completeness,
    )
    try:
        publication = publish_revision(
            output_directory,
            document,
            report,
            manifest,
            asset_copies,
        )
    except StorageError as error:
        raise NormalizationError(error.code, str(error)) from error
    except OSError as error:
        raise NormalizationError(
            "output_write_failed",
            f"The revision could not be published atomically: {type(error).__name__}.",
        ) from error
    return NormalizationResult(
        document=publication.document,
        report=publication.report,
        manifest=publication.manifest,
        output_directory=publication.revision_directory,
        document_directory=publication.document_directory,
        import_decision=publication.decision,
        current_revision_id=publication.current_revision_id,
        current_changed=publication.current_changed,
    )


def normalize_saved_article(
    html_path: Path,
    resource_directory: Path,
    output_directory: Path,
    *,
    limits: Limits | None = None,
    imported_at: str | None = None,
    source_sample: PreparedSample | None = None,
) -> NormalizationResult:
    return _normalize_article(
        html_path,
        resource_directory,
        output_directory,
        explicit_adapter=None,
        limits=limits,
        imported_at=imported_at,
        source_sample=source_sample,
    )


def normalize_wechat_article(
    html_path: Path,
    resource_directory: Path,
    output_directory: Path,
    *,
    limits: Limits | None = None,
    imported_at: str | None = None,
    source_sample: PreparedSample | None = None,
) -> NormalizationResult:
    return _normalize_article(
        html_path,
        resource_directory,
        output_directory,
        explicit_adapter=WeChatAdapter(),
        limits=limits,
        imported_at=imported_at,
        source_sample=source_sample,
    )


def normalize_zhihu_article(
    html_path: Path,
    resource_directory: Path,
    output_directory: Path,
    *,
    limits: Limits | None = None,
    imported_at: str | None = None,
    source_sample: PreparedSample | None = None,
) -> NormalizationResult:
    return _normalize_article(
        html_path,
        resource_directory,
        output_directory,
        explicit_adapter=ZhihuColumnAdapter(),
        limits=limits,
        imported_at=imported_at,
        source_sample=source_sample,
    )
