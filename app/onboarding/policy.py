from __future__ import annotations
from dataclasses import dataclass


@dataclass
class PolicyDecision:
    allowed: bool
    reason: str
    needs_human: bool = False


def evaluate(
    statement: dict,
    *,
    allowed_objects: set[str],
    run_created: set[str],
    approval_mode: str,
) -> PolicyDecision:
    kind = statement.get("kind", "")
    target = statement.get("target")
    name_parts = {p.lower() for p in (target[1] if target else ())}

    if kind in ("READ",):
        return PolicyDecision(True, "read-only statement")
    if kind == "DESTRUCTIVE":
        # only objects THIS RUN created, never recipe/shared objects
        if name_parts and name_parts.issubset(run_created):
            return PolicyDecision(True, "drops an object created by this run")
        return PolicyDecision(False, "destructive statement outside this run's own objects", needs_human=True)
    if kind in ("EXPORT",):
        return PolicyDecision(False, "export/materialization is not verified and not allowed by Pipeless", needs_human=True)
    if kind == "ACCESS":
        return PolicyDecision(True, "access grant on a Pipeless-verified model", needs_human=(approval_mode == "manual"))
    if kind in ("CREATE", "CACHE"):
        if not name_parts:
            return PolicyDecision(False, "cannot determine the object being created", needs_human=True)
        ok = all(any(p == a or p.startswith(a) or a.startswith(p) for a in allowed_objects) for p in name_parts)
        if ok:
            return PolicyDecision(True, "object is in the allowlist", needs_human=(approval_mode == "manual"))
        return PolicyDecision(False, f"object {sorted(name_parts)} is outside the allowlist", needs_human=True)
    return PolicyDecision(False, f"unclassified statement kind {kind}", needs_human=True)
