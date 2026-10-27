from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Literal

Status = Literal["pass", "fail", "warn", "unverified", "skipped"]


@dataclass
class CheckResult:
    id: str                      # stable id the Skeptic cites, e.g. "count_source_vs_zetaris"
    name: str
    status: Status
    blocking: bool
    detail: str
    evidence: dict = field(default_factory=dict)


@dataclass
class EvidencePack:
    checks: list[CheckResult] = field(default_factory=list)
    statuses: dict = field(default_factory=dict)   # registered, rows_readable, dq, ...

    def add(self, c: CheckResult) -> None:
        self.checks.append(c)

    def has_blocking_failure(self) -> bool:
        return any(c.blocking and c.status not in {"pass", "warn"} for c in self.checks)

    def as_blocking_finding(self):
        from app.agents.schemas import Finding
        failed = [c for c in self.checks if c.blocking and c.status not in {"pass", "warn"}]
        return Finding(
            id="blocking_check_failed",
            severity="blocking",
            claim="; ".join(f"{c.id}: {c.detail}" for c in failed),
            evidence_ref=failed[0].id if failed else "",
            suggested_fix="Revise query/data to address failed blocking check(s).",
            level="answer",
        )

    def to_prompt_json(self) -> list[dict]:
        return [asdict(c) for c in self.checks]
