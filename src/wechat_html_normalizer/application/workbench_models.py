"""Independent local task and human-edit contracts; no Document/Asset migration."""

from typing import Literal

from pydantic import Field

from ..models import StrictModel


class WorkbenchTask(StrictModel):
    schema_version: Literal["1.0.0"] = Field(default="1.0.0", alias="schemaVersion")
    task_id: str = Field(alias="taskId", pattern=r"^[0-9a-f]{32}$")
    operation: Literal["import", "enrichment"]
    scope_fingerprint: str = Field(alias="scopeFingerprint", pattern=r"^[0-9a-f]{64}$")
    request_id: str = Field(alias="requestId", max_length=100)
    state: Literal[
        "queued",
        "running",
        "completed",
        "completed_with_errors",
        "blocked",
        "interrupted",
        "recovered",
    ]
    created_at: str = Field(alias="createdAt")
    scope: dict
    call_budget: int = Field(alias="callBudget", ge=0)
    items: list[dict] = Field(default_factory=list)
    run_ids: list[str] = Field(default_factory=list, alias="runIds")
    error: str | None = None
    updated_at: str = Field(alias="updatedAt")


class HumanRevision(StrictModel):
    schema_version: Literal["1.0.0"] = Field(default="1.0.0", alias="schemaVersion")
    revision_id: str = Field(alias="revisionId", pattern=r"^[0-9a-f]{32}$")
    parent_revision_id: str | None = Field(default=None, alias="parentRevisionId")
    binding: dict
    original_result_sha256: str = Field(alias="originalResultSha256", pattern=r"^[0-9a-f]{64}$")
    description: str | None = Field(max_length=100000)
    key_facts: list[str] = Field(alias="keyFacts", max_length=1000)
    code_text: str | None = Field(alias="codeText", max_length=500000)
    created_at: str = Field(alias="createdAt")
    actor: Literal["user", "codex_test"]
    review_status: Literal["draft"] = Field(default="draft", alias="reviewStatus")
