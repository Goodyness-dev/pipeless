from __future__ import annotations
import json
from typing import Any
from app.onboarding.ddl import preview_select, create_view
from app.adapters.zetaris import ZetarisError

REPAIR_SYSTEM = """You are OnboarderAgent repairing a failed Spark SQL SELECT view in Zetaris.
Your previous SELECT failed with an error. Use the error message, hint, and memories to fix it.
Rules:
- Backtick mixed-case, reserved (`end`, `start`), or hyphenated column names.
- Dynamic keys: from_json(to_json(...), 'map<...>') then explode.
- Do NOT self-join the exploded view.
Output JSON:
{{
  "select_sql": "...",
  "fix_summary": "...",
  "columns": [{{"name": "", "type": ""}}]
}}
"""


def attempt_repair(
    runner: Any,
    stmt: dict[str, Any],
    err: ZetarisError,
    ctx: Any,
    plan: Any,
    result: Any,
) -> bool:
    """Attempts self-repair of novel view SELECT queries up to 3 times."""
    hint = err.info.hint
    current_sql = stmt.get("select_sql", stmt.get("sql", ""))

    for attempt in range(1, 4):
        mem_block = "(none)"
        if ctx.memory:
            mem_payload = ctx.memory.retrieve(
                query=f"{err.info.kind} {hint}",
                agent_id="OnboarderAgent",
                run_id=ctx.run_id,
                tags=["onboarding", "repair"],
            )
            mem_block = mem_payload.render_block()

        user_prompt = (
            f"Failing SQL:\n{current_sql}\n\n"
            f"Error kind: {err.info.kind}\n"
            f"Error message: {err.info.message}\n"
            f"Playbook hint: {hint}\n\n"
            f"Memories from earlier runs:\n{mem_block}"
        )

        try:
            llm_res, _ = ctx.llm.chat_json(
                lane="fast",
                system=REPAIR_SYSTEM,
                user=user_prompt,
                agent_name="OnboarderAgent",
                temperature=0.1,
            )
        except Exception:
            break

        new_sql = llm_res.get("select_sql", "").strip()
        fix_summary = llm_res.get("fix_summary", "adjusted SQL syntax")

        ctx.tracer.emit(
            "OnboarderAgent",
            "repair_attempt",
            f"Attempt {attempt}: {err.info.kind}",
            fix_summary,
            target_agent="Zetaris",
            retry_count=attempt,
        )

        preview_stmt = preview_select(new_sql)
        try:
            runner.execute(preview_stmt, purpose=f"repair attempt {attempt}: preview view")
            # If preview succeeds, update statement for subsequent view creation
            stmt["sql"] = preview_stmt
            stmt["select_sql"] = new_sql
            result.repairs.append(
                {
                    "attempt": attempt,
                    "error_kind": err.info.kind,
                    "hint": hint,
                    "fix": fix_summary,
                }
            )
            if ctx.memory:
                ctx.memory.add(
                    content=f"Fixed {err.info.kind} error with: {fix_summary}",
                    type="procedural",
                    layer="short_term",
                    tags=["onboarding", "repair", err.info.kind],
                    entities=[err.info.kind],
                    agent_id="OnboarderAgent",
                    run_id=ctx.run_id,
                    confidence=0.85,
                )
            return True
        except ZetarisError as next_err:
            err = next_err
            current_sql = new_sql

    if ctx.memory:
        ctx.memory.add(
            content=f"Failed to fix {err.info.kind} after 3 attempts",
            type="procedural",
            layer="short_term",
            tags=["onboarding", "failed", err.info.kind],
            agent_id="OnboarderAgent",
            run_id=ctx.run_id,
            confidence=0.4,
        )
    return False
