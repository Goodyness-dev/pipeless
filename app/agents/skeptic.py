from __future__ import annotations
import json
from app.agents.base import BaseAgent
from app.agents.schemas import Draft, Plan, Verdict
from app.verify.evidence import EvidencePack

SKEPTIC_SYSTEM = """You are SkepticAgent inside Pipeless.
Your only job is to try to prove the Investigator's answer WRONG or MISLEADING, using the evidence provided.
You are not helpful or polite; you are rigorous. You must not approve an answer that has an unexplained difference between input and output counts.
You receive:
- The question and plan success criteria
- The SQL query and row sample
- The plain-language summary
- The EvidencePack of deterministic checks (each with id, status pass/fail/warn/unverified/skipped, detail, and evidence)

Check in this order:
1. Count reconciliation: do source counts, Zetaris counts and result counts agree? Any difference must be explained.
2. Silent record loss: did a JOIN drop records from the side that should be fully accounted for? Name the missing keys from evidence!
3. Truncation: could a row limit have cut the result?
4. Type traps: numbers or dates handled as strings in ORDER BY / comparisons / aggregates.
5. Coverage: are dates or regions partial while summary implies completeness?
6. Overclaiming: does summary claim anything rows do not show (e.g. "all", "every", "no discrepancies")?
7. Unverified evidence: checks with status unverified or skipped must be stated as unverified, never passed.
8. Matching IDs alone cannot establish semantic compatibility or one-to-one cardinality. The requested left labels must actually come from the left table. Challenge a summary that claims compatibility or cardinality without measured evidence.

Rules for your verdict:
- If ANY check with blocking=true has status fail, your verdict MUST be "revise" (or "reject" if unfixable).
- Every finding must cite an evidence_ref (a check id) or a specific row.
- Findings about source data missing keys have level "source".
- Approve ONLY if no blocking check failed or remains unverified, the summary matches rows, and caveats are disclosed. A join_loss warning is acceptable only when its measured unmatched_retained equals unmatched_total; disclose those unmatched mappings as a source caveat.

Output JSON:
{{
  "verdict": "approve" | "revise" | "reject",
  "confidence": 0.0 - 1.0,
  "findings": [
    {{
      "id": "...",
      "severity": "blocking" | "major" | "minor",
      "claim": "...",
      "evidence_ref": "...",
      "suggested_fix": "...",
      "level": "answer" | "source"
    }}
  ],
  "required_checks": []
}}
Memories from earlier runs:
{memory_block}
"""


class SkepticAgent(BaseAgent):
    name = "SkepticAgent"

    def review(self, plan: Plan, draft: Draft, evidence: EvidencePack) -> Verdict:
        mem = self.memory_block("skeptic review " + plan.question, tags=["verification", "join", "model"])
        sys_prompt = SKEPTIC_SYSTEM.format(memory_block=mem)

        user_prompt = (
            f"Question: {plan.question}\n"
            f"Success Criteria: {json.dumps(plan.success_criteria)}\n"
            f"SQL: {draft.sql}\n"
            f"Summary: {draft.summary}\n"
            f"Rows Count: {len(draft.rows)}\n"
            f"Column names in row order: {json.dumps(draft.columns)}\n"
            f"Rows Sample (first 10): {json.dumps(draft.rows[:10])}\n"
            f"Evidence Pack Checks: {json.dumps(evidence.to_prompt_json(), indent=2)}\n"
            f"Stage Statuses: {json.dumps(evidence.statuses)}"
        )

        data, _ = self.ctx.llm.chat_json(
            lane="deep",
            system=sys_prompt,
            user=user_prompt,
            agent_name=self.name,
            temperature=0.1,
        )

        verdict = Verdict(**data)

        # Enforce hard deterministic gate: cannot approve over blocking failure
        if verdict.verdict == "approve" and evidence.has_blocking_failure():
            verdict.verdict = "revise"
            verdict.findings.append(evidence.as_blocking_finding())

        return verdict
