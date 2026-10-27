from __future__ import annotations
import json
from app.adapters.zetaris import StatementRunner, ZetarisClient
from app.agents.base import BaseAgent
from app.agents.schemas import Draft, Plan, OnboardResult, Verdict, JoinSpec
from app.verify.evidence import EvidencePack
from app.zsql.guard import validate_read_only

INVESTIGATOR_SYSTEM = """You are InvestigatorAgent inside Pipeless.
You write ONE read-only Spark SQL query (SELECT or WITH) that answers the caller's question using ONLY the registered models listed below.
Hard rules:
- One statement only, no semicolons inside it, no DDL, no DML.
- For this interface, use a SELECT over the named registered tables directly; avoid WITH/CTE aliases, which are not registered sources in the strict SQL allowlist. Use GROUP BY for per-key aggregation.
- Use fully qualified names exactly as listed. Backtick any mixed-case, reserved or hyphenated column names.
- Zetaris returns every value as a string. Cast to DOUBLE/INT/DATE before numeric or date comparison, sorting or aggregation.
- Never read the same exploded/UNION view twice in one query; use window functions (ROW_NUMBER/COUNT OVER) instead of self-joins or repeated subqueries.
- If you JOIN, prefer a LEFT JOIN from the side whose records must all be accounted for, and select the join key from both sides so unmatched records are visible. Do not silently use INNER JOIN when a missing match would hide a record.
- Project the left join key exactly once using its original column name as the output alias; give the right key a different alias. Preserve unmatched rows through WHERE/HAVING filters so the verifier can measure their presence in the final query output.
- Add a clear LIMIT only if the question asks for top-N; otherwise do not truncate.
Output JSON:
{{
  "sql": "...",
  "rationale": "...",
  "expected_grain": "one row per ...",
  "join_spec": {{"left": "...", "right": "...", "left_key": "...", "right_key": "..."}} | null
}}
Models:
{models_json}
Plan success criteria:
{criteria}
Memories from earlier runs:
{memory_block}
"""

REVISE_PROMPT_TEMPLATE = """Your previous SQL had issues flagged by the Skeptic:
Original question: {question}
Previous SQL: {prev_sql}
Previous Summary: {prev_summary}
Skeptic Findings:
{findings}
Evidence Pack:
{evidence}
Fix every blocking and major finding. If the join dropped records, use a LEFT JOIN and remove or adjust filters that discard unmatched rows. Project the left join key exactly once under its original column name, and give the right key a different alias. A LEFT JOIN keyword alone is not proof of preservation. Keep what was correct. Explain what changed.
Keep every requested output column and requirement from the original question while fixing the flagged issue.
Output JSON with keys: sql, rationale, expected_grain, join_spec.
"""

class InvestigatorAgent(BaseAgent):
    name = "InvestigatorAgent"

    def answer(self, plan: Plan, onboard: OnboardResult) -> Draft:
        mem = self.memory_block("investigate " + plan.question, tags=["verification", "join", "model"])
        models_json = json.dumps([m.model_dump() for m in onboard.models], indent=2)
        criteria = "\n".join(f"- {c}" for c in plan.success_criteria)

        sys_prompt = INVESTIGATOR_SYSTEM.format(
            models_json=models_json,
            criteria=criteria,
            memory_block=mem,
        )
        user_prompt = f"Question: {plan.question}\nObjective: {plan.objective}"

        data, _ = self.ctx.llm.chat_json(
            lane="fast",
            system=sys_prompt,
            user=user_prompt,
            agent_name=self.name,
            temperature=0.2,
        )
        return self._build_and_execute_draft(data, onboard, plan, revision=0)

    def revise(
        self,
        plan: Plan,
        prev_draft: Draft,
        verdict: Verdict,
        evidence: EvidencePack,
        onboard: OnboardResult,
    ) -> Draft:
        mem = self.memory_block("revise " + plan.question, tags=["verification", "join"])
        models_json = json.dumps([m.model_dump() for m in onboard.models], indent=2)
        criteria = "\n".join(f"- {c}" for c in plan.success_criteria)

        sys_prompt = INVESTIGATOR_SYSTEM.format(
            models_json=models_json,
            criteria=criteria,
            memory_block=mem,
        )
        user_prompt = REVISE_PROMPT_TEMPLATE.format(
            question=plan.question,
            prev_sql=prev_draft.sql,
            prev_summary=prev_draft.summary,
            findings=json.dumps([f.model_dump() for f in verdict.findings], indent=2),
            evidence=json.dumps(evidence.to_prompt_json(), indent=2),
        )

        data, _ = self.ctx.llm.chat_json(
            lane="fast",
            system=sys_prompt,
            user=user_prompt,
            agent_name=self.name,
            temperature=0.2,
        )
        return self._build_and_execute_draft(data, onboard, plan, revision=prev_draft.revision + 1)

    def _build_and_execute_draft(
        self,
        data: dict,
        onboard: OnboardResult,
        plan: Plan,
        revision: int,
    ) -> Draft:
        raw_sql = data.get("sql", "").strip()
        allowed_tables = {m.fqn for m in onboard.models}

        # SQL Guard validation
        valid_sql = validate_read_only(raw_sql, allowed_tables=allowed_tables)

        js_data = data.get("join_spec")
        join_spec = JoinSpec(**js_data) if js_data and isinstance(js_data, dict) else None

        # Execute using reader identity if configured, else builder fallback
        identity = "reader" if self.ctx.cfg.zetaris_reader_key else "builder"
        client = ZetarisClient(self.ctx.cfg, identity=identity)
        runner = StatementRunner(client, self.ctx.cfg, self.ctx.tracer, self.name)

        zres = runner.execute(valid_sql, purpose="investigate: run draft query")

        draft = Draft(
            sql=valid_sql,
            rationale=data.get("rationale", ""),
            expected_grain=data.get("expected_grain", ""),
            join_spec=join_spec,
            rows=zres.rows,
            columns=zres.columns or [],
            truncated=zres.truncated,
            revision=revision,
            summary="",
        )

        draft.summary = self._summarize_rows(draft, plan)
        return draft

    def _summarize_rows(self, draft: Draft, plan: Plan) -> str:
        # Counts are measured facts. A model summary previously invented counts
        # from a ten-row sample and repeated a filter removed in a revision.
        summary = f"The executed query returned {len(draft.rows)} rows with {len(draft.columns)} columns. "
        if draft.truncated:
            summary += "The response may be truncated. "
        summary += ("Output rows may aggregate source records. Source-key retention, semantic compatibility "
                    "and date coverage require separate evidence checks.")
        return summary
