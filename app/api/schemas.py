from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, Field
from app.agents.schemas import SourceSpec


class RunRequest(BaseModel):
    request_id: str
    user_query: str
    sources: list[SourceSpec]
    context: dict[str, Any] = Field(default_factory=dict)


class AskRequest(BaseModel):
    question: str
    model_fqns: list[str] = Field(default_factory=list)


class StageStatus(BaseModel):
    onboarding: str = "ok"
    registered: bool = False
    rows_readable: bool = False
    deterministic_checks: str = "pending"
    skeptic: str = "pending"
    dq: str = "not_run"


class RunResult(BaseModel):
    summary: str
    findings: list[dict[str, Any]] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    confidence: float = 0.5


class RunReceipts(BaseModel):
    sql: str
    checks: list[dict[str, Any]] = Field(default_factory=list)
    skeptic_findings: list[dict[str, Any]] = Field(default_factory=list)
    revisions: int = 0
    revision_history: list[dict[str, Any]] = Field(default_factory=list)
    repairs: list[dict[str, Any]] = Field(default_factory=list)
    created_objects: list[str] = Field(default_factory=list)
    reused_objects: list[str] = Field(default_factory=list)


class RunResponse(BaseModel):
    request_id: str
    trace_id: str
    status: Literal["verified", "answered_with_caveats", "needs_approval", "failed"]
    agents_used: list[str] = Field(default_factory=list)
    stage_status: StageStatus
    result: RunResult | None = None
    receipts: RunReceipts | None = None
    model_left_behind: dict[str, Any] | None = None
    memory: dict[str, Any] = Field(default_factory=dict)
    collaboration: dict[str, Any] = Field(default_factory=dict)
    llm: dict[str, Any] = Field(default_factory=dict)
    log_path: str = ""
