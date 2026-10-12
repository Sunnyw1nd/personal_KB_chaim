from __future__ import annotations

from enum import StrEnum
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class Message(StrictModel):
    code: str
    level: Severity
    message: str
    location: str | None = None
    suggestion: str | None = None


class Metadata(StrictModel):
    title: str
    account_name: str | None = Field(alias="accountName")
    author: str | None
    published_at: str | None = Field(alias="publishedAt")
    published_at_raw: str | None = Field(alias="publishedAtRaw")
    original_url: str | None = Field(alias="originalUrl")
    modified_at: str | None = Field(
        default=None, alias="modifiedAt", exclude_if=lambda value: value is None
    )
    modified_at_raw: str | None = Field(
        default=None, alias="modifiedAtRaw", exclude_if=lambda value: value is None
    )
    source_type: Literal["wechat_saved_html", "zhihu_column_saved_html"] = Field(alias="sourceType")
    imported_at: str = Field(alias="importedAt")


class SourceIdentity(StrictModel):
    source_type: Literal["wechat_saved_html", "zhihu_column_saved_html"] = Field(alias="sourceType")
    raw_value: str | None = Field(alias="rawValue")
    normalized_value: str = Field(alias="normalizedValue", min_length=1)
    native_id: str | None = Field(default=None, alias="nativeId")
    method: str = Field(min_length=1)
    normalization_version: str = Field(alias="normalizationVersion", min_length=1)
    confidence: Literal["high", "unresolved"]
    reliable: bool


class AssetCompleteness(StrictModel):
    expected: int = Field(ge=0)
    available: int = Field(ge=0)
    missing: int = Field(ge=0)
    invalid: int = Field(ge=0)
    completeness_rate: float = Field(alias="completenessRate", ge=0, le=1)
    missing_asset_ids: list[str] = Field(alias="missingAssetIds")
    invalid_asset_ids: list[str] = Field(alias="invalidAssetIds")

    @model_validator(mode="after")
    def validate_counts(self) -> AssetCompleteness:
        if self.available + self.missing + self.invalid != self.expected:
            raise ValueError("asset completeness counts must add up to expected")
        return self


class RevisionInfo(StrictModel):
    revision_id: str = Field(alias="revisionId", pattern=r"^revision-[0-9a-f]{16}$")
    fingerprint_sha256: str = Field(alias="fingerprintSha256", pattern=r"^[0-9a-f]{64}$")
    content_fingerprint_sha256: str = Field(
        alias="contentFingerprintSha256", pattern=r"^[0-9a-f]{64}$"
    )
    asset_fingerprint_sha256: str = Field(alias="assetFingerprintSha256", pattern=r"^[0-9a-f]{64}$")
    package_sha256: str | None = Field(
        default=None,
        alias="packageSha256",
        pattern=r"^[0-9a-f]{64}$",
    )
    fingerprint_version: str = Field(alias="fingerprintVersion", min_length=1)


class Source(StrictModel):
    html_file_name: str = Field(alias="htmlFileName")
    resource_directory_name: str = Field(alias="resourceDirectoryName")
    html_sha256: str = Field(alias="htmlSha256", pattern=r"^[0-9a-f]{64}$")
    html_encoding: str = Field(alias="htmlEncoding")
    archive_file_name: str | None = Field(
        default=None, alias="archiveFileName", exclude_if=lambda value: value is None
    )
    archive_sha256: str | None = Field(
        default=None,
        alias="archiveSha256",
        pattern=r"^[0-9a-f]{64}$",
        exclude_if=lambda value: value is None,
    )
    html_archive_path: str | None = Field(
        default=None, alias="htmlArchivePath", exclude_if=lambda value: value is None
    )
    resource_archive_path: str | None = Field(
        default=None, alias="resourceArchivePath", exclude_if=lambda value: value is None
    )


class InlineSpan(StrictModel):
    text: str
    href: str | None = None
    source_href: str | None = Field(
        default=None, alias="sourceHref", exclude_if=lambda value: value is None
    )
    marks: list[Literal["strong", "emphasis", "code"]] = Field(default_factory=list)


class ListItem(StrictModel):
    content: str
    inlines: list[InlineSpan] = Field(default_factory=list)


class Block(StrictModel):
    block_id: str = Field(alias="blockId", pattern=r"^block-[0-9]{4,}$")
    type: Literal["heading", "paragraph", "list", "quote", "code", "table", "image", "unknown"]
    order: int = Field(ge=1)
    source_locator: str = Field(alias="sourceLocator")
    content: str | None = None
    inlines: list[InlineSpan] | None = None
    level: int | None = Field(default=None, ge=1, le=6)
    ordered: bool | None = None
    items: list[ListItem] | None = None
    language: str | None = None
    rows: list[list[str]] | None = None
    asset_id: str | None = Field(default=None, alias="assetId")
    original_tag: str | None = Field(default=None, alias="originalTag")
    caption: str | None = Field(default=None, exclude_if=lambda value: value is None)
    caption_inlines: list[InlineSpan] | None = Field(
        default=None, alias="captionInlines", exclude_if=lambda value: value is None
    )
    source_url: str | None = Field(
        default=None, alias="sourceUrl", exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def validate_shape(self) -> Block:
        required = {
            "heading": (self.content is not None and self.level is not None),
            "paragraph": self.content is not None,
            "quote": self.content is not None,
            "code": self.content is not None,
            "list": self.items is not None and self.ordered is not None,
            "table": self.rows is not None,
            "image": self.asset_id is not None,
            "unknown": self.original_tag is not None,
        }[self.type]
        if not required:
            raise ValueError(f"missing fields required by block type {self.type!r}")
        return self


class Asset(StrictModel):
    asset_id: str = Field(alias="assetId", pattern=r"^asset-(?:[0-9]{4,}|[0-9a-f]{12})$")
    role: Literal["article_body_image"]
    source_path: str | None = Field(alias="sourcePath")
    output_path: str | None = Field(alias="outputPath")
    local_src: str | None = Field(alias="localSrc")
    original_url: str | None = Field(alias="originalUrl")
    declared_media_type: str | None = Field(alias="declaredMediaType")
    detected_media_type: str | None = Field(alias="detectedMediaType")
    original_file_name: str | None = Field(alias="originalFileName")
    size_bytes: int | None = Field(alias="sizeBytes", ge=0)
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    identity_key: str | None = Field(
        default=None, alias="identityKey", exclude_if=lambda value: value is None
    )
    identity_method: str | None = Field(
        default=None, alias="identityMethod", exclude_if=lambda value: value is None
    )
    found: bool
    description: None = None
    description_status: Literal["pending"] = Field(alias="descriptionStatus")
    description_method: None = Field(default=None, alias="descriptionMethod")
    ocr_text: None = Field(default=None, alias="ocrText")

    @model_validator(mode="after")
    def safe_paths(self) -> Asset:
        for value in (self.source_path, self.output_path):
            if value is None:
                continue
            path = PurePosixPath(value)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"unsafe relative path: {value!r}")
        if self.found and not all((self.source_path, self.sha256)):
            raise ValueError("found assets require a source path and hash")
        if self.output_path and not self.detected_media_type:
            raise ValueError("copied assets require a detected media type")
        return self


class Processing(StrictModel):
    adapter: Literal["wechat_saved_html", "zhihu_column_saved_html"]
    adapter_version: str = Field(alias="adapterVersion")
    status: Literal["completed", "completed_with_warnings", "failed"]
    statistics: dict[str, int]
    messages: list[Message]
    detection_evidence: list[str] | None = Field(
        default=None, alias="detectionEvidence", exclude_if=lambda value: value is None
    )


class Document(StrictModel):
    schema_version: Literal["1.0.0", "1.1.0", "2.0.0"] = Field(alias="schemaVersion")
    document_id: str = Field(alias="documentId", pattern=r"^(wechat|zhihu)-[0-9a-f]{16}$")
    source_identity: SourceIdentity | None = Field(
        default=None, alias="sourceIdentity", exclude_if=lambda value: value is None
    )
    revision: RevisionInfo | None = Field(default=None, exclude_if=lambda value: value is None)
    asset_completeness: AssetCompleteness | None = Field(
        default=None, alias="assetCompleteness", exclude_if=lambda value: value is None
    )
    metadata: Metadata
    source: Source
    blocks: list[Block]
    assets: list[Asset]
    processing: Processing

    @model_validator(mode="after")
    def validate_references(self) -> Document:
        if self.schema_version == "2.0.0" and not all(
            (self.source_identity, self.revision, self.asset_completeness)
        ):
            raise ValueError(
                "schema 2.0.0 requires sourceIdentity, revision, and assetCompleteness"
            )
        block_ids = [block.block_id for block in self.blocks]
        asset_ids = [asset.asset_id for asset in self.assets]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("blockId values must be unique")
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("assetId values must be unique")
        if [block.order for block in self.blocks] != list(range(1, len(self.blocks) + 1)):
            raise ValueError("block order must be consecutive, starting at 1")
        known_assets = set(asset_ids)
        dangling = [
            b.asset_id for b in self.blocks if b.type == "image" and b.asset_id not in known_assets
        ]
        if dangling:
            raise ValueError(f"image blocks reference missing assets: {dangling}")
        return self


class ResourceEntry(StrictModel):
    path: str
    detected_type: str | None = Field(alias="detectedType")
    referenced_by_page: bool = Field(alias="referencedByPage")
    referenced_by_body: bool = Field(alias="referencedByBody")
    decision: Literal["kept", "ignored", "refused", "missing"]
    reason_code: str = Field(alias="reasonCode")
    details: str | None = None
    size_bytes: int | None = Field(default=None, alias="sizeBytes", ge=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ResourceManifest(StrictModel):
    schema_version: Literal["1.0.0", "1.1.0", "2.0.0"] = Field(alias="schemaVersion")
    document_id: str = Field(alias="documentId")
    resources: list[ResourceEntry]


class OutputLocations(StrictModel):
    document_json: str = Field(alias="documentJson")
    document_markdown: str = Field(alias="documentMarkdown")
    processing_report: str = Field(alias="processingReport")
    resource_manifest: str = Field(alias="resourceManifest")
    assets_directory: str = Field(alias="assetsDirectory")


class ProcessingReport(StrictModel):
    schema_version: Literal["1.0.0", "1.1.0", "2.0.0"] = Field(alias="schemaVersion")
    document_id: str = Field(alias="documentId")
    adapter: str
    adapter_version: str = Field(alias="adapterVersion")
    status: Literal["completed", "completed_with_warnings", "failed"]
    input_summary: dict[str, str | int | None] = Field(alias="inputSummary")
    metadata: Metadata
    statistics: dict[str, int]
    excluded_regions: list[dict[str, str]] = Field(alias="excludedRegions")
    messages: list[Message]
    outputs: OutputLocations
    detection_evidence: list[str] | None = Field(
        default=None, alias="detectionEvidence", exclude_if=lambda value: value is None
    )
    source_identity: SourceIdentity | None = Field(
        default=None, alias="sourceIdentity", exclude_if=lambda value: value is None
    )
    revision: RevisionInfo | None = Field(default=None, exclude_if=lambda value: value is None)
    asset_completeness: AssetCompleteness | None = Field(
        default=None, alias="assetCompleteness", exclude_if=lambda value: value is None
    )


class StoredRevision(StrictModel):
    revision_id: str = Field(alias="revisionId", pattern=r"^revision-[0-9a-f]{16}$")
    fingerprint_sha256: str = Field(alias="fingerprintSha256", pattern=r"^[0-9a-f]{64}$")
    content_fingerprint_sha256: str = Field(
        alias="contentFingerprintSha256", pattern=r"^[0-9a-f]{64}$"
    )
    asset_fingerprint_sha256: str = Field(alias="assetFingerprintSha256", pattern=r"^[0-9a-f]{64}$")
    package_sha256s: list[str] = Field(alias="packageSha256s")
    html_sha256s: list[str] = Field(alias="htmlSha256s")
    imported_at: str = Field(alias="importedAt")
    path: str
    asset_completeness: AssetCompleteness = Field(alias="assetCompleteness")
    decision_at_creation: str = Field(alias="decisionAtCreation")


class DocumentRecord(StrictModel):
    schema_version: Literal["1.0.0"] = Field(alias="schemaVersion")
    document_id: str = Field(alias="documentId", pattern=r"^(wechat|zhihu)-[0-9a-f]{16}$")
    source_identity: SourceIdentity = Field(alias="sourceIdentity")
    directory_name: str = Field(alias="directoryName")
    title_at_creation: str = Field(alias="titleAtCreation")
    current_revision_id: str = Field(alias="currentRevisionId", pattern=r"^revision-[0-9a-f]{16}$")
    revisions: list[StoredRevision]

    @model_validator(mode="after")
    def validate_current(self) -> DocumentRecord:
        ids = [item.revision_id for item in self.revisions]
        if len(ids) != len(set(ids)):
            raise ValueError("revisionId values must be unique")
        if self.current_revision_id not in ids:
            raise ValueError("currentRevisionId must reference a stored revision")
        return self
