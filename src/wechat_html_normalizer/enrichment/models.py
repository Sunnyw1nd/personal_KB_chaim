from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

SHA256_PATTERN = r"^[0-9a-f]{64}$"
REQUEST_SCHEMA_VERSION = "1.0.0"
RESULT_SCHEMA_VERSION = "1.0.0"
RUN_RECORD_SCHEMA_VERSION = "1.0.0"
QUALITY_RULES_PROMPT_VERSION = "image-enrichment-v2"
_CODE_LINE = re.compile(
    r"^\s*(?:from\s+\S+\s+import\b|import\s+|def\s+|class\s+|"
    r"with\s+|for\s+|while\s+|if\s+|elif\s+|else\s*:|try\s*:|"
    r"except\s+|return\b|print\s*\(|[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\s*(?:=|\())"
)


def _code_lines(text: str) -> set[str]:
    return {line.strip() for line in text.splitlines() if _CODE_LINE.match(line)}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ImageType(StrEnum):
    ARCHITECTURE_FLOW = "architecture_flow"
    TABLE = "table"
    CHART = "chart"
    CODE = "code"
    UI_SCREENSHOT = "ui_screenshot"
    TEXT_IMAGE = "text_image"
    DECORATIVE_TEXT = "decorative_text"
    QR_CODE = "qr_code"
    PHOTO_SCENE = "photo_scene"
    LOGO_MARK = "logo_mark"
    OTHER = "other"


class KnowledgeValue(StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CONTEXT_DEPENDENT = "context_dependent"


class EnrichmentAction(StrEnum):
    EXCLUDE = "exclude_from_retrieval"
    DESCRIBE = "describe"
    TRANSCRIBE_TEXT = "transcribe_text"
    TRANSCRIBE_CODE = "transcribe_code"
    EXTRACT_TABLE = "extract_table"
    UNDERSTAND_CHART = "understand_chart"
    OCR_AND_VISION = "ocr_and_vision"
    DECODE_QR = "decode_qr"
    MANUAL_REVIEW = "manual_review"


class RetrievalDecision(StrEnum):
    INCLUDE = "include"
    EXCLUDE = "exclude"
    MANUAL_REVIEW = "manual_review"


class RunStatus(StrEnum):
    SUCCESS = "success"
    CACHED = "cached"
    DRY_RUN = "dry_run"
    POLICY_REJECTED = "policy_rejected"
    SKIPPED = "skipped"
    INVALID_OUTPUT = "invalid_output"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    AUTHENTICATION_FAILED = "authentication_failed"
    PROVIDER_ERROR = "provider_error"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class ImageIdentity(StrictModel):
    sha256: str = Field(pattern=SHA256_PATTERN)
    relative_path: str = Field(alias="relativePath", min_length=1)
    media_type: str = Field(alias="mediaType", pattern=r"^image/[a-z0-9.+-]+$")
    actual_format: str = Field(alias="actualFormat", min_length=1)
    size_bytes: int = Field(alias="sizeBytes", ge=1)
    width: int = Field(ge=1)
    height: int = Field(ge=1)


class AssetReference(StrictModel):
    document_id: str = Field(alias="documentId", min_length=1)
    revision_id: str | None = Field(default=None, alias="revisionId")
    asset_id: str = Field(alias="assetId", min_length=1)
    binary_sha256: str = Field(alias="binarySha256", pattern=SHA256_PATTERN)


class ImageContext(StrictModel):
    available: bool
    article_title: str | None = Field(default=None, alias="articleTitle")
    caption: str | None = None
    preceding_text: str | None = Field(default=None, alias="precedingText")
    following_text: str | None = Field(default=None, alias="followingText")

    @model_validator(mode="after")
    def validate_availability(self) -> ImageContext:
        values = (self.article_title, self.caption, self.preceding_text, self.following_text)
        if not self.available and any(values):
            raise ValueError("unavailable context must not contain invented text")
        if self.available and not any(values):
            raise ValueError("available context requires at least one context field")
        return self


class DataPolicy(StrictModel):
    allow_external: bool = Field(alias="allowExternal")
    local_only: bool = Field(alias="localOnly")
    allowed_backend_types: list[Literal["cloud", "local"]] = Field(alias="allowedBackendTypes")
    classification: Literal["external_test_sample", "formal_asset", "local_only"]
    policy_version: str = Field(alias="policyVersion", min_length=1)
    reason_code: str = Field(alias="reasonCode", min_length=1)

    @model_validator(mode="after")
    def validate_policy(self) -> DataPolicy:
        if self.local_only and self.allow_external:
            raise ValueError("local-only data cannot allow external processing")
        return self


class EnrichmentRequest(StrictModel):
    schema_version: Literal["1.0.0"] = Field(alias="schemaVersion")
    image: ImageIdentity
    asset_references: list[AssetReference] = Field(alias="assetReferences", default_factory=list)
    context: ImageContext
    context_fingerprint_sha256: str = Field(
        alias="contextFingerprintSha256", pattern=SHA256_PATTERN
    )
    policy: DataPolicy
    prompt_version: str = Field(alias="promptVersion", min_length=1)
    result_schema_version: Literal["1.0.0"] = Field(alias="resultSchemaVersion")


class QualityReviewDecision(StrictModel):
    schema_version: Literal["1.0.0"] = Field(alias="schemaVersion")
    cache_key: str = Field(alias="cacheKey", pattern=SHA256_PATTERN)
    image_sha256: str = Field(alias="imageSha256", pattern=SHA256_PATTERN)
    review_status: ReviewStatus = Field(alias="reviewStatus")
    reason: str = Field(min_length=1)
    reviewer: Literal["user"]
    created_at: str = Field(alias="createdAt")
    event_id: str = Field(alias="eventId", pattern=r"^[0-9a-f]{32}$")


class CodeContent(StrictModel):
    text: str = Field(min_length=1)
    language: str | None = None


class TableContent(StrictModel):
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    merged_cells: list[str] = Field(alias="mergedCells", default_factory=list)
    text_representation: str = Field(alias="textRepresentation", min_length=1)


class ChartAxis(StrictModel):
    name: str
    unit: str | None = None


class ChartContent(StrictModel):
    axes: list[ChartAxis] = Field(default_factory=list)
    legends: list[str] = Field(default_factory=list)
    series: list[str] = Field(default_factory=list)
    trends: list[str] = Field(default_factory=list)
    key_values: list[str] = Field(alias="keyValues", default_factory=list)
    direct_observations: list[str] = Field(alias="directObservations", default_factory=list)
    textual_claims: list[str] = Field(alias="textualClaims", default_factory=list)
    inferences: list[str] = Field(default_factory=list)


class EnrichmentResult(StrictModel):
    schema_version: Literal["1.0.0"] = Field(alias="schemaVersion")
    image_type: ImageType = Field(alias="imageType")
    knowledge_value: KnowledgeValue = Field(alias="knowledgeValue")
    action: EnrichmentAction
    retrieval_decision: RetrievalDecision = Field(alias="retrievalDecision")
    extracted_text: str | None = Field(default=None, alias="extractedText")
    description: str | None = None
    code: CodeContent | None = None
    table: TableContent | None = None
    chart: ChartContent | None = None
    key_facts: list[str] = Field(alias="keyFacts", default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_action_payload(self, info: ValidationInfo) -> Self:
        required = {
            EnrichmentAction.DESCRIBE: bool(self.description),
            EnrichmentAction.TRANSCRIBE_TEXT: bool(self.extracted_text),
            EnrichmentAction.TRANSCRIBE_CODE: self.code is not None,
            EnrichmentAction.EXTRACT_TABLE: self.table is not None,
            EnrichmentAction.UNDERSTAND_CHART: self.chart is not None,
            EnrichmentAction.OCR_AND_VISION: bool(self.extracted_text or self.description),
            EnrichmentAction.DECODE_QR: bool(self.extracted_text or self.uncertainties),
            EnrichmentAction.EXCLUDE: True,
            EnrichmentAction.MANUAL_REVIEW: bool(self.uncertainties or self.warnings),
        }[self.action]
        if not required:
            raise ValueError(f"action {self.action!s} is missing its required content")
        if self.action == EnrichmentAction.EXCLUDE:
            if self.retrieval_decision != RetrievalDecision.EXCLUDE:
                raise ValueError("exclude action requires retrievalDecision=exclude")
            if self.knowledge_value == KnowledgeValue.HIGH:
                raise ValueError("high-value content cannot be excluded")
        elif self.retrieval_decision == RetrievalDecision.EXCLUDE:
            raise ValueError("retrievalDecision=exclude requires exclude action")
        if (
            self.action == EnrichmentAction.MANUAL_REVIEW
            and self.retrieval_decision != RetrievalDecision.MANUAL_REVIEW
        ):
            raise ValueError("manual review action requires retrievalDecision=manual_review")
        if not isinstance(info.context, dict) or not (
            info.context.get("enforce_current_policy")
            or info.context.get("prompt_version") == QUALITY_RULES_PROMPT_VERSION
        ):
            return self

        if self.action == EnrichmentAction.TRANSCRIBE_CODE:
            if self.image_type != ImageType.CODE:
                raise ValueError("code transcription action requires code image type")
            assert self.code is not None
            if (
                self.code.language
                and self.code.text.strip().casefold() == self.code.language.strip().casefold()
            ):
                raise ValueError("code.text must contain code, not only the language name")
            if self.extracted_text:
                extracted_lines = _code_lines(self.extracted_text)
                formal_lines = _code_lines(self.code.text)
                # Compare visibly code-shaped lines, not total OCR length: menus and line numbers
                # need not be copied into the formal code field.
                if (
                    formal_lines
                    and formal_lines <= extracted_lines
                    and len(extracted_lines - formal_lines) >= 3
                ):
                    raise ValueError("code.text omits multiple code lines found in extractedText")
        if self.action == EnrichmentAction.EXTRACT_TABLE and self.image_type != ImageType.TABLE:
            raise ValueError("table extraction action requires table image type")
        if self.action == EnrichmentAction.UNDERSTAND_CHART and self.image_type != ImageType.CHART:
            raise ValueError("chart action requires chart image type")
        if self.action == EnrichmentAction.UNDERSTAND_CHART:
            assert self.chart is not None
            if not any(
                (self.chart.axes, self.chart.series, self.chart.trends, self.chart.key_values)
            ):
                raise ValueError("chart action requires axes, series, trends or key values")
        if self.chart is not None and self.action != EnrichmentAction.UNDERSTAND_CHART:
            raise ValueError("chart payload requires understand_chart action")
        if self.code is not None and self.action != EnrichmentAction.TRANSCRIBE_CODE:
            raise ValueError("code payload requires transcribe_code action")
        if self.table is not None and self.action != EnrichmentAction.EXTRACT_TABLE:
            raise ValueError("table payload requires extract_table action")
        if self.image_type == ImageType.ARCHITECTURE_FLOW:
            if self.action not in (
                EnrichmentAction.DESCRIBE,
                EnrichmentAction.OCR_AND_VISION,
                EnrichmentAction.MANUAL_REVIEW,
            ):
                raise ValueError("architecture_flow requires visual description or manual review")
            if self.action != EnrichmentAction.MANUAL_REVIEW and not self.description:
                raise ValueError("architecture_flow requires a relationship description")
        if (
            self.knowledge_value == KnowledgeValue.NONE
            and self.action == EnrichmentAction.EXCLUDE
            and self.key_facts
        ):
            raise ValueError("excluded no-value image must not claim high-confidence key facts")
        return self


def validate_current_result(result: EnrichmentResult) -> EnrichmentResult:
    """Recheck reusable results with today's business rules, without rewriting history."""
    return EnrichmentResult.model_validate(
        result.model_dump(mode="json", by_alias=True),
        context={"enforce_current_policy": True},
    )


class Usage(StrictModel):
    input_tokens: int | None = Field(default=None, alias="inputTokens", ge=0)
    output_tokens: int | None = Field(default=None, alias="outputTokens", ge=0)
    total_tokens: int | None = Field(default=None, alias="totalTokens", ge=0)
    image_tokens: int | None = Field(default=None, alias="imageTokens", ge=0)
    cached_input_tokens: int | None = Field(default=None, alias="cachedInputTokens", ge=0)


class EnrichmentAttemptRecord(StrictModel):
    """One actual provider call made for an image task."""

    attempt_number: int = Field(alias="attemptNumber", ge=1)
    structured_retry_number: int = Field(alias="structuredRetryNumber", ge=0)
    transport_retry_number: int = Field(alias="transportRetryNumber", ge=0)
    status: RunStatus
    completed_at: str | None = Field(default=None, alias="completedAt")
    elapsed_ms: int | None = Field(default=None, alias="elapsedMs", ge=0)
    provider_response_received: bool = Field(alias="providerResponseReceived")
    request_id: str | None = Field(default=None, alias="requestId")
    actual_model: str | None = Field(default=None, alias="actualModel")
    usage: Usage | None = None
    raw_response_path: str | None = Field(default=None, alias="rawResponsePath")
    error_code: str | None = Field(default=None, alias="errorCode")
    error_message: str | None = Field(default=None, alias="errorMessage")
    finish_reason: str | None = Field(default=None, alias="finishReason")
    recovery_mode: str | None = Field(default=None, alias="recoveryMode")
    validation_issues: list[dict] | None = Field(default=None, alias="validationIssues")
    validation_status: str | None = Field(default=None, alias="validationStatus")
    recovery_details_path: str | None = Field(default=None, alias="recoveryDetailsPath")


class EnrichmentRunRecord(StrictModel):
    schema_version: Literal["1.0.0", "1.1.0"] = Field(alias="schemaVersion")
    cache_key: str = Field(alias="cacheKey", pattern=SHA256_PATTERN)
    image_sha256: str = Field(alias="imageSha256", pattern=SHA256_PATTERN)
    status: RunStatus
    processor: str
    provider: str
    requested_model: str = Field(alias="requestedModel")
    actual_model: str | None = Field(default=None, alias="actualModel")
    prompt_version: str = Field(alias="promptVersion")
    prompt_sha256: str = Field(alias="promptSha256", pattern=SHA256_PATTERN)
    result_schema_version: str = Field(alias="resultSchemaVersion")
    processed_at: str = Field(alias="processedAt")
    request_id: str | None = Field(default=None, alias="requestId")
    context_fingerprint_sha256: str = Field(
        alias="contextFingerprintSha256", pattern=SHA256_PATTERN
    )
    elapsed_ms: int | None = Field(default=None, alias="elapsedMs", ge=0)
    attempts: int = Field(ge=0)
    retry_count: int = Field(alias="retryCount", ge=0)
    structured_retry_count: int = Field(default=0, alias="structuredRetryCount", ge=0)
    transport_retry_count: int = Field(default=0, alias="transportRetryCount", ge=0)
    attempt_records: list[EnrichmentAttemptRecord] = Field(
        default_factory=list, alias="attemptRecords"
    )
    enable_thinking: bool = Field(alias="enableThinking")
    usage: Usage | None = None
    task_usage: Usage | None = Field(default=None, alias="taskUsage")
    result: EnrichmentResult | None = None
    raw_response_path: str | None = Field(default=None, alias="rawResponsePath")
    error_code: str | None = Field(default=None, alias="errorCode")
    error_message: str | None = Field(default=None, alias="errorMessage")
    processing_profile: str | None = Field(default=None, alias="processingProfile")
    review_status: ReviewStatus | None = Field(default=None, alias="reviewStatus")
    validation_status: str | None = Field(default=None, alias="validationStatus")

    @model_validator(mode="after")
    def validate_record(self) -> EnrichmentRunRecord:
        if self.status in (RunStatus.SUCCESS, RunStatus.CACHED) and self.result is None:
            raise ValueError("successful records require a result")
        if self.status not in (RunStatus.SUCCESS, RunStatus.CACHED) and self.result is not None:
            raise ValueError("failed or skipped records cannot contain a validated result")
        if self.attempt_records:
            if self.attempts != len(self.attempt_records):
                raise ValueError("attempt count must match attemptRecords")
            numbers = [attempt.attempt_number for attempt in self.attempt_records]
            if numbers != list(range(1, len(numbers) + 1)):
                raise ValueError("attemptRecords must use consecutive 1-based attempt numbers")
        return self


class BatchRunItem(StrictModel):
    image_sha256: str = Field(alias="imageSha256", pattern=SHA256_PATTERN)
    relative_path: str = Field(alias="relativePath")
    cache_key: str = Field(alias="cacheKey", pattern=SHA256_PATTERN)
    status: RunStatus
    request_path: str = Field(alias="requestPath")
    record_path: str | None = Field(default=None, alias="recordPath")
    error_code: str | None = Field(default=None, alias="errorCode")
    review_status: ReviewStatus | None = Field(default=None, alias="reviewStatus")


class BatchRunMetrics(StrictModel):
    image_tasks: int = Field(alias="imageTasks", ge=0)
    api_calls: int = Field(alias="apiCalls", ge=0)
    first_attempt_successes: int = Field(alias="firstAttemptSuccesses", ge=0)
    retry_successes: int = Field(alias="retrySuccesses", ge=0)
    structured_retry_successes: int = Field(alias="structuredRetrySuccesses", ge=0)
    final_failures: int = Field(alias="finalFailures", ge=0)
    structured_output_failures: int = Field(alias="structuredOutputFailures", ge=0)
    cache_hits: int = Field(alias="cacheHits", ge=0)
    api_calls_with_usage: int = Field(alias="apiCallsWithUsage", ge=0)
    api_calls_without_usage: int = Field(alias="apiCallsWithoutUsage", ge=0)
    transport_recovery_attempts: int | None = Field(
        default=None, alias="transportRecoveryAttempts", ge=0
    )
    result_recovery_attempts: int | None = Field(default=None, alias="resultRecoveryAttempts", ge=0)
    pending_review: int | None = Field(default=None, alias="pendingReview", ge=0)


class BatchRunManifest(StrictModel):
    schema_version: Literal["1.0.0", "1.1.0"] = Field(alias="schemaVersion")
    run_id: str = Field(alias="runId")
    status: Literal["in_progress", "completed", "dry_run", "blocked", "failed"]
    created_at: str = Field(alias="createdAt")
    completed_at: str | None = Field(default=None, alias="completedAt")
    provider: str
    requested_model: str = Field(alias="requestedModel")
    actual_models: list[str] = Field(alias="actualModels", default_factory=list)
    endpoint: str | None = None
    region: str
    prompt_version: str = Field(alias="promptVersion")
    prompt_sha256: str = Field(alias="promptSha256", pattern=SHA256_PATTERN)
    result_schema_version: str = Field(alias="resultSchemaVersion")
    inventory_sha256: str = Field(alias="inventorySha256", pattern=SHA256_PATTERN)
    configuration_fingerprint_sha256: str = Field(
        alias="configurationFingerprintSha256", pattern=SHA256_PATTERN
    )
    enable_thinking: bool = Field(alias="enableThinking")
    force: bool
    dry_run: bool = Field(alias="dryRun")
    dataset: dict[str, int]
    summary: dict[str, int]
    metrics: BatchRunMetrics | None = None
    items: list[BatchRunItem]
    usage: Usage | None = None
    notes: list[str] = Field(default_factory=list)
    processing_profile: str | None = Field(default=None, alias="processingProfile")
    profile_configuration: dict | None = Field(default=None, alias="profileConfiguration")
