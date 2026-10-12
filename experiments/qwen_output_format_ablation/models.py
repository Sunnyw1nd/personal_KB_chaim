from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CodeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    codeText: str = Field(min_length=1)
    language: str | None
    uncertainties: list[str]


class DiagramResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field(min_length=1)
    keyFacts: list[str]
    uncertainties: list[str]
