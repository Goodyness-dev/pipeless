from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, Field


class SourceSpec(BaseModel):
    id: str
    kind: Literal["catalog", "rest_url"]
    recipe_id: str | None = None          # for kind == "catalog"
    url: str | None = None                # for kind == "rest_url"
    description: str = ""
    count_path: str | None = None         # dot path to array in JSON for source reconciliation


class JoinSpec(BaseModel):
    left: str                             # fully qualified table/view
    right: str
    left_key: str
    right_key: str


class Plan(BaseModel):
    objective: str
    question: str
    sources: list[SourceSpec]
    needs_join: bool = False
    join_hints: list[JoinSpec] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    caveats_to_watch: list[str] = Field(default_factory=list)


class ModelInfo(BaseModel):
    fqn: str                              # fully qualified name usable in FROM
    kind: Literal["usl_table", "schemastore_view", "lightning_table"]
    columns: list[dict] = Field(default_factory=list)   # [{name, type}]
    row_grain: str = ""
    source_id: str = ""
    count_path: str | None = None
    source_row_count: int | None = None
    notes: list[str] = Field(default_factory=list)


class OnboardResult(BaseModel):
    status: Literal["ok", "partial", "failed", "needs_approval"]
    models: list[ModelInfo] = Field(default_factory=list)
    created_objects: list[str] = Field(default_factory=list)
    reused_objects: list[str] = Field(default_factory=list)
    attempts: int = 0
    repairs: list[dict] = Field(default_factory=list)
    plan_statements: list[dict] = Field(default_factory=list)   # for approval / dry run
    errors: list[str] = Field(default_factory=list)


class Draft(BaseModel):
    sql: str
    rationale: str = ""
    expected_grain: str = ""
    join_spec: JoinSpec | None = None
    rows: list[list[Any]] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    truncated: bool = False
    revision: int = 0
    summary: str = ""                      # plain-language reading of the rows


class Finding(BaseModel):
    id: str
    severity: Literal["blocking", "major", "minor"]
    claim: str
    evidence_ref: str = ""                 # id of a CheckResult or row reference
    suggested_fix: str = ""
    level: Literal["answer", "source"] = "answer"


class Verdict(BaseModel):
    verdict: Literal["approve", "revise", "reject"]
    confidence: float = 0.5
    findings: list[Finding] = Field(default_factory=list)
    required_checks: list[str] = Field(default_factory=list)


class FinalAnswer(BaseModel):
    summary: str
    findings: list[dict] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    confidence: float = 0.5
