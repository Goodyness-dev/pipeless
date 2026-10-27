from __future__ import annotations
import json
from app.agents.base import BaseAgent
from app.agents.schemas import Plan, FinalAnswer, Draft, Verdict
from app.verify.evidence import EvidencePack

PLANNER_SYSTEM = """You are PlannerAgent inside Pipeless, an agent that other AI agents call to get verified answers from company data held in Zetaris.
Given a caller's request, produce a JSON plan. Rules:
- Use only the sources the caller provided (catalog recipe ids or REST URLs). Do not invent sources.
- If the question compares or combines records from two sources, set needs_join=true and propose join_hints with the likely key columns.
- Write 3-5 success_criteria that a skeptic could check (e.g. "every company in source A is accounted for", "row counts reconcile with the source").
- List caveats_to_watch (e.g. partial date coverage, string-typed numbers, rate-limited sources).
Output JSON with keys: objective, question, sources[{{id,kind,recipe_id,url,description,count_path}}], needs_join, join_hints[{{left,right,left_key,right_key}}], success_criteria[], caveats_to_watch[].
Relevant memories from earlier runs:
{memory_block}
"""

FINALISE_SYSTEM = """You are PlannerAgent finalising a verified answer for another AI agent.
Use ONLY the verified rows, the evidence pack and the Skeptic's findings.
State clearly:
1. What the answer is based on the data.
2. What was challenged by the Skeptic and how it was resolved (including any records that did not match).
3. What remains unverified (e.g. if DQ timed out, declare it unverified).
Never claim a check passed unless its status is pass.
Key matching proves only key coverage. It does not prove semantic compatibility or a one-to-one relationship.
Do not claim row preservation from a LEFT JOIN keyword. Use measured output-key retention instead.
Do not call the whole answer verified when the verdict or checks remain unresolved.
Output JSON:
{{
  "summary": "...",
  "findings": [{{"item": "...", "detail": "..."}}],
  "caveats": ["..."],
  "confidence": 0.0 - 1.0
}}
"""


class PlannerAgent(BaseAgent):
    name = "PlannerAgent"

    def plan(self, request) -> Plan:
        mem = self.memory_block(request.user_query, tags=["verification", "model"])
        sys_prompt = PLANNER_SYSTEM.format(memory_block=mem)
        user_prompt = f"User Query: {request.user_query}\nSources provided: {json.dumps([s.model_dump() for s in request.sources])}"

        data, _ = self.ctx.llm.chat_json(
            lane="fast",
            system=sys_prompt,
            user=user_prompt,
            agent_name=self.name,
            temperature=0.2,
        )
        plan = Plan(**data)
        # Planning must not rewrite away requested columns or expand source scope.
        plan.question = request.user_query
        plan.sources = list(request.sources)
        return plan

    def finalise(
        self,
        plan: Plan,
        draft: Draft,
        evidence: EvidencePack,
        verdict: Verdict | None,
    ) -> FinalAnswer:
        findings_json = json.dumps([f.model_dump() for f in (verdict.findings if verdict else [])])
        evidence_json = json.dumps(evidence.to_prompt_json())
        rows_sample = json.dumps(draft.rows[:10])

        user_prompt = (
            f"Question: {plan.question}\n"
            f"Investigator Summary: {draft.summary}\n"
            f"Draft SQL: {draft.sql}\n"
            f"Result Rows Sample (first 10): {rows_sample}\n"
            f"Result column names in row order: {json.dumps(draft.columns)}\n"
            f"Total result rows: {len(draft.rows)}\n"
            f"Skeptic Findings: {findings_json}\n"
            f"Evidence Pack: {evidence_json}\n"
            f"Stage Statuses: {json.dumps(evidence.statuses)}"
        )

        data, _ = self.ctx.llm.chat_json(
            lane="deep",
            system=FINALISE_SYSTEM,
            user=user_prompt,
            agent_name=self.name,
            temperature=0.2,
        )
        final = FinalAnswer(**data)
        for check in evidence.checks:
            if check.status in {"unverified", "skipped"}:
                caveat = f"{check.id}: {check.status}. {check.detail}"
                if caveat not in final.caveats:
                    final.caveats.append(caveat)
        if evidence.statuses.get("dq") != "pass":
            final.caveats.append("Data-quality checks have not been verified as passed.")
        if draft.join_spec:
            final.caveats.append("Join-key coverage alone does not verify semantic equivalence or one-to-one row cardinality.")
        return final
