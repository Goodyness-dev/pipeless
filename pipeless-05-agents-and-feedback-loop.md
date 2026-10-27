# PIPELESS — 05 AGENTS AND THE FEEDBACK LOOP

> Prerequisites: files 01–04. This file defines the four agents, their schemas, their prompts, and the orchestrator loop. The hackathon rules demand **real collaboration**: an evaluator critiques, the executor revises, and the logs show it.

Files created here: `app/agents/schemas.py`, `base.py`, `planner.py`, `onboarder.py`, `investigator.py`, `skeptic.py`, `orchestrator.py`.

---

## 1. The cast

| Agent | Lane | Responsibility | Output |
|---|---|---|---|
| **PlannerAgent** | fast (finalise: deep) | Understand the request, choose sources and strategy, define success criteria; at the end write the final answer from verified evidence | `Plan`, `FinalAnswer` |
| **OnboarderAgent** | fast | Get each source into Zetaris safely (catalog recipe or novel REST source); repair its own errors (file 06) | `OnboardResult` |
| **InvestigatorAgent** | fast | Write the answer SQL against the registered models; run it on the reader identity; revise when criticised | `Draft` |
| **SkepticAgent** | **deep** | Adversarially review the draft using the deterministic evidence; approve, request revision, or reject | `Verdict` |
| *VerifierService* (code, not an agent) | none | Deterministic checks (file 07) | `EvidencePack` |

The Verifier is plain code on purpose: **a model cannot talk its way past a failed count check.**

## 2. The loop (this is the feedback loop the rules require)

```
plan      = Planner.plan(request)                          # handoff: Planner -> Onboarder
onboard   = Onboarder.onboard(plan)                        # handoff: Onboarder -> Investigator
draft     = Investigator.answer(plan, onboard)             # handoff: Investigator -> Verifier
evidence  = Verifier.collect(plan, onboard, draft)
for round in 0..MAX_REVISIONS:
    verdict = Skeptic.review(plan, draft, evidence)        # handoff: Skeptic -> Investigator (or Planner)
    if verdict.verdict == "approve" and evidence.all_blocking_passed: break
    if verdict.verdict == "reject": mark failed; break
    draft    = Investigator.revise(plan, draft, verdict, evidence)   # revision_applied
    evidence = Verifier.collect(plan, onboard, draft)
final = Planner.finalise(plan, draft, evidence, verdict)
```

Hard rules:
1. **Deterministic failures are blocking.** If any `blocking` check failed, the Skeptic cannot produce `approve`; the orchestrator forces `revise` (or `reject` after the cap).
2. If the Skeptic says `revise` and names a **source-level** problem (for example, "the reference list is missing a key" or "a source view is wrong"), route the critique to the Onboarder, not only the Investigator.
3. The loop is capped by `MAX_REVISIONS` and `MAX_LLM_CALLS_PER_RUN`. On exhaustion, return `status="answered_with_caveats"` with the unresolved findings listed. **Never** loop forever.
4. Every handoff and every verdict is a trace event.

## 3. `app/agents/schemas.py`

```python
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


class SourceSpec(BaseModel):
    id: str
    kind: Literal["catalog", "rest_url"]
    recipe_id: str | None = None          # for kind == "catalog" (file 06 CATALOG keys)
    url: str | None = None                # for kind == "rest_url"
    description: str = ""
    count_path: str | None = None         # dot path to the array in the JSON, for source-vs-Zetaris count checks


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
    columns: list[dict] = Field(default_factory=list)   # [{name, type}] from DESCRIBE / LOAD USL
    row_grain: str = ""
    source_id: str = ""
    count_path: str | None = None
    source_row_count: int | None = None   # counted directly from the source API when available
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
    rows: list[list] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    truncated: bool = False
    revision: int = 0
    summary: str = ""                      # plain-language reading of the rows


class Finding(BaseModel):
    id: str
    severity: Literal["blocking", "major", "minor"]
    claim: str
    evidence_ref: str = ""                 # id of a CheckResult or "row N"
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
```

## 4. `app/agents/base.py`

```python
from __future__ import annotations
from dataclasses import dataclass
from app.adapters.meterless import NightshiftLLM
from app.memory.hmem import HMem
from app.tracing import Tracer


@dataclass
class AgentContext:
    cfg: object
    tracer: Tracer
    llm: NightshiftLLM
    memory: HMem
    run_id: str
    caller_agent: str | None = None


class BaseAgent:
    name = "BaseAgent"

    def __init__(self, ctx: AgentContext):
        self.ctx = ctx

    def handoff(self, target: str, what: str):
        self.ctx.tracer.emit(self.name, "handoff", what, "", target_agent=target)

    def memory_block(self, query: str, tags: list[str] | None = None) -> str:
        """Retrieve top memories from H-MEM and render a prompt block. Trace is logged inside HMem.retrieve."""
        payload = self.ctx.memory.retrieve(query=query, agent_id=self.name, run_id=self.ctx.run_id, tags=tags or [])
        return payload.render_block()
```

## 5. Prompts (each agent has its OWN system prompt; this is what makes them real agents)

### 5.1 PlannerAgent — system prompt

```text
You are PlannerAgent inside Pipeless, an agent that other AI agents call to get verified answers from company data held in Zetaris.
Given a caller's request, produce a JSON plan. Rules:
- Use only the sources the caller provided (catalog recipe ids or REST URLs). Do not invent sources.
- If the question compares or combines records from two sources, set needs_join=true and propose join_hints with the likely key columns (they may be refined later).
- Write 3-5 success_criteria that a skeptic could check (for example: "every company in source A is accounted for", "row counts reconcile with the source").
- List caveats_to_watch (for example: partial date coverage, string-typed numbers, rate-limited sources).
Output JSON with keys: objective, question, sources[{id,kind,recipe_id,url,description,count_path}], needs_join, join_hints[{left,right,left_key,right_key}], success_criteria[], caveats_to_watch[].
Relevant memories from earlier runs (may be empty):
{memory_block}
```

### 5.2 OnboarderAgent
System prompts live in file 06 (they depend on the onboarding flow). The agent is responsible for calling the onboarding engine and, on failure, the repair loop.

### 5.3 InvestigatorAgent — system prompt

```text
You are InvestigatorAgent inside Pipeless. You write ONE read-only Spark SQL query (SELECT or WITH) that answers the caller's question using ONLY the registered models listed below.
Hard rules:
- One statement only, no semicolons inside it, no DDL, no DML.
- Use fully qualified names exactly as listed. Backtick any mixed-case, reserved or hyphenated column names.
- Zetaris returns every value as a string. Cast to DOUBLE/INT/DATE before numeric or date comparison, sorting or aggregation.
- Never read the same exploded/UNION view twice in one query; use window functions (ROW_NUMBER/COUNT OVER) instead of self-joins or repeated subqueries.
- If you JOIN, prefer a LEFT JOIN from the side whose records must all be accounted for, and select the join key from both sides so unmatched records are visible. Do not silently use INNER JOIN when a missing match would hide a record.
- Add a clear LIMIT only if the question asks for top-N; otherwise do not truncate.
Output JSON: {"sql": "...", "rationale": "...", "expected_grain": "one row per ...", "join_spec": {"left":"","right":"","left_key":"","right_key":""} | null}.
Models:
{models_json}
Plan success criteria:
{criteria}
Memories from earlier runs:
{memory_block}
```

Revision prompt (user message) includes: previous SQL, the Skeptic's findings (with severity/evidence refs), the evidence pack summary, and the instruction *"Fix every blocking and major finding. Keep what was correct. Explain what changed."*

### 5.3b Investigator reading the rows into a summary
After SQL runs, make a **second, small** fast-lane call: "Summarise these rows in 2–4 sentences for the caller. Only state what the rows show. Mention row count and any unmatched/missing records." This summary is what gets challenged.

### 5.4 SkepticAgent (deep lane) — system prompt

```text
You are SkepticAgent inside Pipeless. Your only job is to try to prove the Investigator's answer WRONG or MISLEADING, using the evidence provided. You are not helpful or polite; you are rigorous. You must not approve an answer that has an unexplained difference between input and output counts.
You receive: the question, the plan's success criteria, the SQL, a sample of rows, the plain-language summary, and an EvidencePack of deterministic checks (each with an id, status pass/fail/warn/unverified/skipped, and details).
Check, in this order:
1. Count reconciliation: do source counts, Zetaris counts and result counts agree? Any difference must be explained by the answer itself.
2. Silent record loss: did a JOIN drop records from the side that should be fully accounted for? Name the missing keys from the evidence.
3. Truncation: could a row limit have cut the result?
4. Type traps: numbers or dates handled as strings in ORDER BY / comparisons / aggregates.
5. Coverage: are the dates, regions or periods partial while the summary implies completeness?
6. Overclaiming: does the summary claim anything the rows do not show (for example "all", "every", "no discrepancies")?
7. Unverified evidence: any check with status unverified or skipped must be stated as unverified, never as passed.
Rules for your verdict:
- If any check with blocking=true has status fail, your verdict MUST be "revise" (or "reject" if unfixable).
- Every finding must cite an evidence_ref (a check id) or a specific row. No vague findings.
- Findings about the source data (for example, a reference list missing a code) have level "source".
- Approve only if all blocking checks pass, the summary matches the rows, and unverified items are disclosed.
Output JSON: {"verdict":"approve|revise|reject","confidence":0-1,"findings":[{"id","severity":"blocking|major|minor","claim","evidence_ref","suggested_fix","level":"answer|source"}],"required_checks":[]}.
Memories from earlier runs:
{memory_block}
```

### 5.5 Planner finalise — system prompt (deep lane)

```text
You are PlannerAgent finalising a verified answer for another AI agent. Use ONLY the verified rows, the evidence pack and the Skeptic's findings. State clearly: what the answer is, what was challenged and how it was resolved, what remains unverified, and any records that did not match. Never claim a check passed unless its status is pass. Output JSON: {"summary","findings":[{"item","detail"}],"caveats":[],"confidence":0-1}.
```

## 6. Orchestrator skeleton (`app/agents/orchestrator.py`)

```python
from __future__ import annotations
from app.agents.schemas import Plan, Verdict
from app.verify.evidence import EvidencePack


def run_pipeline(request, ctx, planner, onboarder, investigator, skeptic, verifier, grants=None):
    t = ctx.tracer
    plan = planner.plan(request)
    t.emit("PlannerAgent", "plan_created", request.user_query, plan.objective)
    planner.handoff("OnboarderAgent", "onboard sources")
    onboard = onboarder.onboard(plan)
    if onboard.status == "needs_approval":
        return {"status": "needs_approval", "plan": plan, "onboard": onboard}
    if onboard.status == "failed":
        return {"status": "failed", "stage": "onboarding", "plan": plan, "onboard": onboard}

    onboarder.handoff("InvestigatorAgent", "models ready")
    draft = investigator.answer(plan, onboard)
    investigator.handoff("VerifierService", "evidence request")
    evidence = verifier.collect(plan, onboard, draft)

    verdict, revisions = None, 0
    for _round in range(ctx.cfg.max_revisions + 1):
        verdict = skeptic.review(plan, draft, evidence)
        # Hard rule: a deterministic blocking failure can never be approved.
        if verdict.verdict == "approve" and evidence.has_blocking_failure():
            verdict.verdict = "revise"
            verdict.findings.append(evidence.as_blocking_finding())
        t.emit("SkepticAgent", "skeptic_verdict", plan.question, verdict.verdict,
               target_agent="InvestigatorAgent", confidence=verdict.confidence,
               findings=[f.model_dump() for f in verdict.findings])
        if verdict.verdict in ("approve", "reject") or _round == ctx.cfg.max_revisions:
            break
        revisions += 1
        t.emit("SkepticAgent", "revision_requested", plan.question, f"{len(verdict.findings)} findings",
               target_agent="InvestigatorAgent", retry_count=revisions)
        if any(f.level == "source" for f in verdict.findings):
            onboard = onboarder.reconsider(plan, onboard, verdict)      # source-level critique
        draft = investigator.revise(plan, draft, verdict, evidence, onboard)
        t.emit("InvestigatorAgent", "revision_applied", draft.sql, draft.summary,
               target_agent="SkepticAgent", retry_count=revisions)
        evidence = verifier.collect(plan, onboard, draft)

    final = planner.finalise(plan, draft, evidence, verdict)
    return {"status": _status(verdict, evidence, revisions, ctx), "plan": plan, "onboard": onboard,
            "draft": draft, "evidence": evidence, "verdict": verdict, "final": final,
            "revisions": revisions}


def _status(verdict: Verdict, evidence: EvidencePack, revisions: int, ctx) -> str:
    if verdict.verdict == "approve" and not evidence.has_blocking_failure():
        return "verified"
    if verdict.verdict == "reject":
        return "failed"
    return "answered_with_caveats"
```

Each agent method that calls the model must:
1. Retrieve memory first (`memory_block`) and include it in the system prompt.
2. Call `ctx.llm.chat_json(...)` with its own system prompt and lane.
3. Validate the returned JSON with the matching pydantic schema (reject and re-ask once on validation error).
4. Emit its trace events (`handoff`, `llm_call` is automatic, domain events as listed in file 02).
5. Write an H-MEM event after meaningful outcomes (file 08).

## 7. Anti-fake checklist for this file

- No agent returns a literal string as a final answer. Summaries come from rows + model.
- The Skeptic receives real evidence from the Verifier, not a description of it.
- The revision path must be testable: unit test a scenario where the first draft uses an INNER JOIN that drops a key, the Skeptic returns `revise`, the Investigator produces a LEFT JOIN, and the second evidence pack passes. (Use a recorded fixture for the **test only**, never in runtime.)
- `MAX_REVISIONS` and `MAX_LLM_CALLS_PER_RUN` are enforced.

## 8. Acceptance criteria

- [ ] On the IBM scenario (file 07), the log shows `skeptic_verdict=revise` then `revision_applied` then a second `skeptic_verdict`.
- [ ] Four distinct agent names appear in the logs with distinct prompts and lanes (fast vs deep visible in `llm_call` events).
- [ ] The returned `status` is `verified` only when blocking checks pass and the Skeptic approves.
- [ ] A forced failure (bad join key) yields `answered_with_caveats`, not a crash.
