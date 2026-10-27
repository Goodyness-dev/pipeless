from __future__ import annotations
import re
from decimal import Decimal
from typing import Any
from app.verify.evidence import CheckResult, EvidencePack


def count_at_path(obj: Any, dotted: str) -> int:
    """Count items at a dot path. list -> len(list); dict -> number of keys."""
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part, [])
        else:
            return 0
    if isinstance(cur, list):
        return len(cur)
    if isinstance(cur, dict):
        return len(cur.keys())
    return 0


def join_loss_sqls(left: str, right: str, lkey: str, rkey: str) -> dict[str, str]:
    return {
        "left_total": f"SELECT COUNT(DISTINCT l.{lkey}) FROM {left} l",
        "matched": f"SELECT COUNT(DISTINCT l.{lkey}) FROM {left} l INNER JOIN {right} r ON l.{lkey} = r.{rkey}",
        "unmatched": f"SELECT l.{lkey} FROM {left} l LEFT JOIN {right} r ON l.{lkey} = r.{rkey} WHERE r.{rkey} IS NULL LIMIT 50",
    }


class VerifierService:
    def __init__(self, runner, cfg, tracer):
        self.runner = runner
        self.cfg = cfg
        self.tracer = tracer
        self._source_cache: dict[str, Any] = {}
        self._dq_cache: dict[str, str] = {}

    def check_source_vs_zetaris(self, model, source_json: Any, count_path: str) -> CheckResult:
        if not source_json or not count_path:
            return CheckResult(
                id="count_source_vs_zetaris",
                name="Source count equals Zetaris count",
                status="skipped",
                blocking=True,
                detail="Source JSON or count_path not provided",
            )
        try:
            src = count_at_path(source_json, count_path)
            zet = self.runner.count(model.fqn, purpose="source reconciliation")
            status = "pass" if src == zet else "fail"
            return CheckResult(
                id="count_source_vs_zetaris",
                name="Source count equals Zetaris count",
                status=status,
                blocking=True,
                detail=f"source={src} zetaris={zet}",
                evidence={"source": src, "zetaris": zet, "path": count_path},
            )
        except Exception as e:
            return CheckResult(
                id="count_source_vs_zetaris",
                name="Source count equals Zetaris count",
                status="fail",
                blocking=True,
                detail=f"Error executing count comparison: {e}",
            )

    def check_join_loss(self, draft) -> CheckResult:
        js = draft.join_spec
        if not js:
            return CheckResult(
                id="join_loss",
                name="Silent record loss across join",
                status="skipped",
                blocking=True,
                detail="No join in query",
            )
        try:
            sqls = join_loss_sqls(js.left, js.right, js.left_key, js.right_key)
            lt_str = self.runner.scalar(sqls["left_total"], purpose="join_loss: left total")
            m_str = self.runner.scalar(sqls["matched"], purpose="join_loss: matched")
            def validated_count(value):
                count = Decimal(str(value))
                if not count.is_finite() or count < 0 or count != count.to_integral_value():
                    raise ValueError("Count evidence must be a non-negative integer")
                return int(count)

            left_total = validated_count(lt_str)
            matched = validated_count(m_str)
            if matched > left_total:
                raise ValueError("Matched count exceeds left count; inconsistent audit evidence")

            unmatched_keys: list[str] = []
            if left_total != matched:
                unm_res = self.runner.execute(sqls["unmatched"], purpose="join_loss: unmatched keys", limit=50)
                unmatched_keys = [str(r[0]) for r in unm_res.rows if r]

            evidence = {"left_total": left_total, "matched": matched, "unmatched_keys": unmatched_keys}
            if left_total == matched:
                key_columns = [c for c in draft.columns if c.casefold() == js.left_key.casefold()]
                if len(key_columns) != 1 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_columns[0]):
                    status = "fail"
                    detail = f"Preservation not verified: project the left join key AS {js.left_key} exactly once"
                else:
                    query = draft.sql.strip().rstrip(";")
                    probe = (f"SELECT COUNT(DISTINCT l.{js.left_key}) FROM {js.left} l "
                             f"INNER JOIN ({query}) q ON l.{js.left_key} = q.{key_columns[0]}")
                    retained = validated_count(self.runner.scalar(probe, purpose="join_loss: source keys retained in result"))
                    if retained > left_total:
                        raise ValueError("Retained count exceeds source key count")
                    evidence.update({"source_keys_retained": retained, "preservation_sql": probe})
                    status = "pass" if retained == left_total else "fail"
                    detail = (f"left_total={left_total} matched={matched}; {retained}/{left_total} distinct non-null "
                              "source keys retained in actual output. Semantic equivalence and row multiplicity are unverified")
            else:
                missing = left_total - matched
                # Never infer preservation from SQL keywords. Probe the actual query
                # output, including its WHERE/HAVING/LIMIT, against unmatched source keys.
                key_columns = [c for c in draft.columns if c.casefold() == js.left_key.casefold()]
                if len(key_columns) != 1 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_columns[0]):
                    status = "fail"
                    detail = (
                        f"left_total={left_total} matched={matched}; {missing} unmatched keys. "
                        f"Preservation not verified: project the left join key AS {js.left_key} "
                        "exactly once in the result so the audit can measure it."
                    )
                else:
                    key_column = key_columns[0]
                    query = draft.sql.strip().rstrip(";")
                    probe = (
                        f"SELECT COUNT(DISTINCT l.{js.left_key}) FROM {js.left} l "
                        f"LEFT JOIN {js.right} r ON l.{js.left_key} = r.{js.right_key} "
                        f"INNER JOIN ({query}) q ON l.{js.left_key} = q.{key_column} "
                        f"WHERE r.{js.right_key} IS NULL"
                    )
                    retained = validated_count(self.runner.scalar(probe, purpose="join_loss: unmatched keys retained in result"))
                    if retained > missing:
                        raise ValueError("Retained count exceeds unmatched count; inconsistent audit evidence")
                    evidence.update({"unmatched_total": missing, "unmatched_retained": retained, "preservation_sql": probe})
                    status = "warn" if retained == missing else "fail"
                    detail = (
                        f"left_total={left_total} matched={matched}; "
                        f"{retained}/{missing} unmatched distinct keys retained in query output"
                    )
                    if retained < missing:
                        detail += f"; {missing - retained} silently dropped"

            return CheckResult(
                id="join_loss",
                name="Silent record loss across join",
                status=status,
                blocking=True,
                detail=detail,
                evidence=evidence,
            )
        except Exception as e:
            return CheckResult(
                id="join_loss",
                name="Silent record loss across join",
                status="fail",
                blocking=True,
                detail=f"Error in join_loss check: {e}",
            )

    def check_result_truncation(self, draft) -> CheckResult:
        if not draft.truncated:
            return CheckResult(
                id="result_truncation",
                name="Result set truncation check",
                status="pass",
                blocking=True,
                detail=f"Result row count ({len(draft.rows)}) under limit",
            )
        try:
            wrapped = f"({draft.sql.rstrip(';')}) AS _sub"
            actual_count = self.runner.count(wrapped, purpose="truncation check")
            if actual_count > len(draft.rows):
                return CheckResult(
                    id="result_truncation",
                    name="Result set truncation check",
                    status="fail",
                    blocking=True,
                    detail=f"Query returned {len(draft.rows)} rows but total matches are {actual_count}",
                    evidence={"rows": len(draft.rows), "actual_count": actual_count},
                )
            return CheckResult(
                id="result_truncation",
                name="Result set truncation check",
                status="pass",
                blocking=True,
                detail=f"Row count equals query count ({actual_count})",
            )
        except Exception as e:
            return CheckResult(
                id="result_truncation",
                name="Result set truncation check",
                status="warn",
                blocking=False,
                detail=f"Unable to verify truncation: {e}",
            )

    def check_string_typed_numbers(self, draft, onboard) -> CheckResult:
        sql = draft.sql
        warnings = []
        for model in onboard.models:
            for col in model.columns:
                cname = col.get("name", "")
                ctype = str(col.get("type", "")).lower()
                if ctype in ("string", "varchar", "text"):
                    # Check if sorted or aggregated without CAST
                    pattern = rf"\b(ORDER\s+BY|SUM|AVG|MAX|MIN)\s+.*?\b{cname}\b"
                    if re.search(pattern, sql, re.I) and "CAST" not in sql.upper():
                        warnings.append(f"Column '{cname}' in {model.fqn} is string-typed but used in sorting/aggregation without CAST")
        if warnings:
            return CheckResult(
                id="string_typed_numbers",
                name="String typed numbers without CAST",
                status="warn",
                blocking=False,
                detail="; ".join(warnings),
            )
        return CheckResult(
            id="string_typed_numbers",
            name="String typed numbers without CAST",
            status="pass",
            blocking=False,
            detail="No uncast numeric-string operations detected",
        )

    def check_coverage_period(self, draft, onboard) -> CheckResult:
        # Check if date/period columns are present
        date_cols = []
        for model in onboard.models:
            for col in model.columns:
                cname = col.get("name", "").lower()
                if any(k in cname for k in ("date", "year", "month", "period", "timestamp")) or cname in {"created_at", "updated_at", "deleted_at"}:
                    date_cols.append((model.fqn, col.get("name")))
        if not date_cols:
            return CheckResult(
                id="coverage_period",
                name="Coverage period completeness",
                status="skipped",
                blocking=False,
                detail="No date or period columns in registered models",
            )
        return CheckResult(
            id="coverage_period",
            name="Coverage period completeness",
            status="unverified",
            blocking=False,
            detail="Temporal columns found, but date values, expected range and gaps have not been checked",
            evidence={"temporal_columns": [{"model": model, "column": col} for model, col in date_cols]},
        )

    def check_summary_matches_rows(self, draft) -> CheckResult:
        summary = draft.summary
        row_count = len(draft.rows)
        # Search for patterns like '6 companies', '7 records', '5 rows'
        matches = re.findall(r"\b(\d+)\s+(?:companies|rows|records|items|results)\b", summary, re.I)
        for num_str in matches:
            claimed = int(num_str)
            # If claimed count differs from rows, flag warning
            if claimed != row_count and not (draft.join_spec and claimed in (row_count - 1, row_count + 1)):
                return CheckResult(
                    id="summary_matches_rows",
                    name="Summary text reconciles with returned rows",
                    status="warn",
                    blocking=False,
                    detail=f"Summary mentions {claimed} records, but query returned {row_count} rows",
                    evidence={"claimed": claimed, "returned": row_count},
                )
        return CheckResult(
            id="summary_matches_rows",
            name="Summary text reconciles with returned rows",
            status="pass",
            blocking=False,
            detail=f"No conflicting explicit result-count claim detected for {row_count} returned rows; semantic accuracy is unverified",
        )

    def check_rows_readable(self, onboard) -> bool:
        if not onboard.models:
            return False
        all_readable = True
        for model in onboard.models:
            try:
                res = self.runner.execute(f"SELECT 1 FROM {model.fqn} LIMIT 1", purpose="rows_readable check", limit=1)
                if not res.rows:
                    all_readable = False
            except Exception:
                all_readable = False
        return all_readable

    def collect(self, plan, onboard, draft) -> EvidencePack:
        pack = EvidencePack()
        pack.statuses["registered"] = onboard.status in ("ok", "partial")
        pack.statuses["rows_readable"] = self.check_rows_readable(onboard)
        pack.add(CheckResult(id="source_readability", name="Registered sources are readable",
                             status="pass" if pack.statuses["registered"] and pack.statuses["rows_readable"] else "fail",
                             blocking=True, detail=f"registered={pack.statuses['registered']} rows_readable={pack.statuses['rows_readable']}"))

        # Source reconciliation
        for model in onboard.models:
            if model.count_path and model.source_row_count is not None:
                src_data = self._source_cache.get(model.source_id, {})
                c_res = self.check_source_vs_zetaris(model, src_data, model.count_path)
                pack.add(c_res)
                self.tracer.emit("VerifierService", "check_result", c_res.name, c_res.detail, status=c_res.status)

        # Join loss check
        if draft.join_spec:
            jl_res = self.check_join_loss(draft)
            pack.add(jl_res)
            self.tracer.emit("VerifierService", "check_result", jl_res.name, jl_res.detail, status=jl_res.status)

        # Truncation check
        tr_res = self.check_result_truncation(draft)
        pack.add(tr_res)
        self.tracer.emit("VerifierService", "check_result", tr_res.name, tr_res.detail, status=tr_res.status)

        # String numbers check
        sn_res = self.check_string_typed_numbers(draft, onboard)
        pack.add(sn_res)
        self.tracer.emit("VerifierService", "check_result", sn_res.name, sn_res.detail, status=sn_res.status)

        # Coverage period check
        cp_res = self.check_coverage_period(draft, onboard)
        pack.add(cp_res)
        self.tracer.emit("VerifierService", "check_result", cp_res.name, cp_res.detail, status=cp_res.status)

        # Summary vs rows check
        sm_res = self.check_summary_matches_rows(draft)
        pack.add(sm_res)
        self.tracer.emit("VerifierService", "check_result", sm_res.name, sm_res.detail, status=sm_res.status)

        # Data quality status (cached or unverified)
        pack.statuses["dq"] = self._dq_cache.get("default", "not_run")
        return pack
