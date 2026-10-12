"""Versioned compact model contract, deterministic unpacking and explicit formal mapping."""

from __future__ import annotations

import json
import re
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from .models import EnrichmentResult

PROFILE = "compact-json-v1"
MAPPING_VERSION = "compact-mapping-v1"
VALIDATION_VERSION = "compact-validation-v1"
RECOVERY_VERSION = "compact-recovery-v1"


class CompactBase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    knowledgeValue: Literal["none", "low", "medium", "high", "context_dependent"]
    uncertainties: list[str]


class CompactCode(CompactBase):
    imageType: Literal["code"]
    codeText: str = Field(min_length=1)
    language: str | None


class CompactDescription(CompactBase):
    imageType: Literal["architecture_flow", "ui_screenshot", "photo_scene"]
    description: str = Field(min_length=1)
    keyFacts: list[str]


class CompactTable(CompactBase):
    imageType: Literal["table"]
    headers: list[str] = Field(min_length=1)
    rows: list[list[str]] = Field(min_length=1)
    mergedCells: list[str]

    @model_validator(mode="after")
    def equal_columns(self) -> Self:
        if any(len(row) != len(self.headers) for row in self.rows):
            raise ValueError("every table row must match the header column count")
        return self


class CompactAxis(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str
    unit: str | None


class CompactChart(CompactBase):
    imageType: Literal["chart"]
    description: str = Field(min_length=1)
    axes: list[CompactAxis]
    legends: list[str]
    series: list[str]
    trends: list[str]
    keyValues: list[str]
    keyFacts: list[str]


class CompactText(CompactBase):
    imageType: Literal["text_image", "decorative_text", "logo_mark"]
    extractedText: str
    description: str
    retrievalDecision: Literal["include", "exclude", "manual_review"]


class CompactUncertain(CompactBase):
    imageType: Literal["uncertain", "other", "qr_code"]
    reason: str = Field(min_length=1)
    uncertainties: list[str] = Field(min_length=1)


CompactResult = Annotated[
    CompactCode | CompactDescription | CompactTable | CompactChart | CompactText | CompactUncertain,
    Field(discriminator="imageType"),
]
COMPACT_ADAPTER = TypeAdapter(CompactResult)


class CompactOutputError(ValueError):
    def __init__(self, code: str, issues: list[dict], *, parsed: dict | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.issues = issues
        self.parsed = parsed


def unwrap_content(content: str) -> tuple[str, list[str]]:
    """Remove only complete external wrappers; never alter any JSON string value."""
    steps = []
    text = content.strip()
    if text != content:
        steps.append("outer_whitespace")
    if text.startswith("\ufeff"):
        text = text[1:].strip()
        steps.append("leading_bom")
    match = re.fullmatch(
        r"(?P<fence>`{3,})(?:json|JSON)?[ \t]*\r?\n(?P<body>[\s\S]*?)\r?\n(?P=fence)", text
    )
    if match:
        text = match["body"].strip()
        steps.append("single_complete_json_fence")
    return text, steps


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def parse_compact(content: str) -> tuple[BaseModel, EnrichmentResult, dict]:
    text, steps = unwrap_content(content)
    try:
        parsed = json.loads(text, object_pairs_hook=_unique_pairs)
    except json.JSONDecodeError as exc:
        raise CompactOutputError(
            "json_syntax_error",
            [{"path": [], "message": exc.msg, "line": exc.lineno, "column": exc.colno}],
        ) from None
    except ValueError as exc:
        raise CompactOutputError("json_syntax_error", [{"path": [], "message": str(exc)}]) from None
    try:
        compact = COMPACT_ADAPTER.validate_python(parsed, strict=True)
    except ValidationError as exc:
        raise CompactOutputError(
            "compact_schema_error",
            _issues(exc),
            parsed=parsed if isinstance(parsed, dict) else None,
        ) from None
    try:
        result = map_result(compact)
    except ValidationError as exc:
        raise CompactOutputError("business_validation_error", _issues(exc), parsed=parsed) from None
    return (
        compact,
        result,
        {
            "unwrapSteps": steps,
            "parsed": parsed,
            "schemaStatus": "passed",
            "businessStatus": "passed",
        },
    )


def _issues(exc: ValidationError) -> list[dict]:
    return [
        {"path": list(issue["loc"]), "message": issue["msg"], "type": issue["type"]}
        for issue in exc.errors(include_url=False, include_input=False)
    ]


def map_result(compact: BaseModel) -> EnrichmentResult:
    data = compact.model_dump()
    image_type = data["imageType"]
    values: dict = {
        "schemaVersion": "1.0.0",
        "imageType": image_type,
        "knowledgeValue": data["knowledgeValue"],
        "retrievalDecision": "include",
        "uncertainties": data["uncertainties"],
    }
    if isinstance(compact, CompactCode):
        values.update(
            action="transcribe_code", code={"text": compact.codeText, "language": compact.language}
        )
    elif isinstance(compact, CompactDescription):
        values.update(action="describe", description=compact.description, keyFacts=compact.keyFacts)
    elif isinstance(compact, CompactTable):
        representation = "\n".join("\t".join(row) for row in [compact.headers, *compact.rows])
        values.update(
            action="extract_table",
            table={
                "headers": compact.headers,
                "rows": compact.rows,
                "mergedCells": compact.mergedCells,
                "textRepresentation": representation,
            },
        )
    elif isinstance(compact, CompactChart):
        values.update(
            action="understand_chart",
            description=compact.description,
            keyFacts=compact.keyFacts,
            chart={
                "axes": [axis.model_dump() for axis in compact.axes],
                "legends": compact.legends,
                "series": compact.series,
                "trends": compact.trends,
                "keyValues": compact.keyValues,
                "directObservations": compact.keyFacts,
            },
        )
    elif isinstance(compact, CompactText):
        decision = compact.retrievalDecision
        action = {
            "exclude": "exclude_from_retrieval",
            "include": "transcribe_text",
            "manual_review": "manual_review",
        }[decision]
        values.update(
            action=action,
            retrievalDecision=decision,
            extractedText=compact.extractedText,
            description=compact.description,
        )
    else:
        assert isinstance(compact, CompactUncertain)
        values.update(
            imageType="qr_code" if image_type == "qr_code" else "other",
            action="manual_review",
            retrievalDecision="manual_review",
            description=compact.reason,
        )
    return EnrichmentResult.model_validate(values, context={"enforce_current_policy": True})


def correction_differences(old: dict | None, new: dict) -> dict:
    if old is None:
        return {"comparison": "not_provable_original_unparseable", "lossless": None}
    common = {
        key
        for key in old.keys() & new.keys()
        if key
        in {"codeText", "description", "keyFacts", "extractedText", "headers", "rows", "keyValues"}
    }
    changed = {
        key: {"before": old[key], "after": new[key]}
        for key in sorted(common)
        if old[key] != new[key]
    }
    content_keys = {
        "codeText",
        "description",
        "keyFacts",
        "extractedText",
        "headers",
        "rows",
        "keyValues",
    }
    content_missing = (set(old) - set(new)) & content_keys
    content_added = (set(new) - set(old)) & content_keys
    type_changed = old.get("imageType") != new.get("imageType")
    return {
        "comparison": "common_content_fields",
        "lossless": not changed and not content_missing and not content_added and not type_changed,
        "changedFields": changed,
        "missingFields": sorted(set(old) - set(new)),
        "addedFields": sorted(set(new) - set(old)),
        "typeChanged": type_changed,
    }
