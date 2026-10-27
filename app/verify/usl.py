from __future__ import annotations
import concurrent.futures
import json
import time
from typing import Any
from app.adapters.zetaris import StatementRunner, ZetarisError


_ACTIVE_DQ_THREADS: dict[str, concurrent.futures.Future] = {}


def run_dq(
    runner: StatementRunner,
    usl_ns: str,
    usl: str,
    table: str,
    wait_s: int = 180,
) -> dict[str, Any]:
    """Execute DQ rules with a bounded wait. Never relaunches if a command is already in flight."""
    key = f"{usl_ns}.{usl}.{table}"
    global _ACTIVE_DQ_THREADS
    if key in _ACTIVE_DQ_THREADS and not _ACTIVE_DQ_THREADS[key].done():
        return {
            "status": "timed_out",
            "message": "DQ check is still in progress from a previous request. Not relaunching.",
            "rules": [],
            "invalid_sample": [],
            "elapsed_s": 0,
            "form_used": "in_flight",
        }

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_execute_dq_internal, runner, usl_ns, usl, table)
    _ACTIVE_DQ_THREADS[key] = future

    t0 = time.time()
    try:
        res = future.result(timeout=wait_s)
        res["elapsed_s"] = int(time.time() - t0)
        return res
    except concurrent.futures.TimeoutError:
        return {
            "status": "timed_out",
            "message": f"DQ check exceeded wait time of {wait_s}s. Status remains unverified.",
            "rules": [],
            "invalid_sample": [],
            "elapsed_s": int(time.time() - t0),
            "form_used": "unknown",
        }


def _execute_dq_internal(runner: StatementRunner, usl_ns: str, usl: str, table: str) -> dict[str, Any]:
    full_table = f"{usl_ns}.{usl}.{table}"
    form_used = "named_rule"
    rules: list[str] = []

    # 1. LIST DQ USL
    try:
        list_res = runner.execute(f"LIST DQ USL {usl_ns}.{usl}", purpose="list DQ rules")
        for row in list_res.rows:
            if row:
                rules.append(str(row[0]))
    except Exception:
        pass

    # 2. RUN DQ - UNKNOWN U8: try named rule first, fall back to table-level
    run_succeeded = False
    if rules:
        for r in rules:
            try:
                runner.execute(f"RUN DQ {r} TABLE {full_table}", purpose=f"run DQ rule {r}")
                run_succeeded = True
            except ZetarisError:
                break

    if not run_succeeded:
        # Fall back to RUN DQ TABLE without rule name
        try:
            form_used = "table_level"
            runner.execute(f"RUN DQ TABLE {full_table}", purpose="run DQ table without rule name")
            run_succeeded = True
        except Exception as e:
            return {
                "status": "failed",
                "message": f"DQ execution failed: {e}",
                "rules": rules,
                "invalid_sample": [],
                "form_used": form_used,
            }

    # 3. SHOW DQ ALL INVALID TABLE
    invalid_sample: list[dict] = []
    try:
        show_res = runner.execute(f"SHOW DQ ALL INVALID TABLE {full_table}", purpose="fetch invalid DQ records")
        for row in show_res.rows[:20]:
            if row:
                try:
                    parsed = json.loads(str(row[0])) if isinstance(row[0], str) else row[0]
                    invalid_sample.append(parsed if isinstance(parsed, dict) else {"record": row[0]})
                except Exception:
                    invalid_sample.append({"raw": str(row[0])})
    except Exception:
        pass

    status = "completed"
    return {
        "status": status,
        "rules": rules,
        "invalid_count": len(invalid_sample),
        "invalid_sample": invalid_sample,
        "form_used": form_used,
    }
