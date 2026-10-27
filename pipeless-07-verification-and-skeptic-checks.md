# PIPELESS — 07 VERIFICATION: DETERMINISTIC CHECKS, USL LIFECYCLE, STATUS MODEL

> Prerequisites: files 01–06. This is the "receipts" engine. Everything here is plain code, not a model. The Skeptic (file 05) reads this evidence and cannot approve over a failed blocking check.

Files created here: `app/verify/evidence.py`, `app/verify/checks.py`, `app/verify/usl.py`.

---

## 1. Principles

1. **Verify against the source, not against ourselves.** Count rows directly from the source API and compare with Zetaris. A lower Zetaris count may mean truncation, the source's own selection, or a connector problem; investigate before relying on it (Zetaris docs).
2. **Keep outcomes separate.** Report these as independent statuses and never merge them: `registered` (statement accepted), `rows_readable` (a SELECT returns rows), `deterministic_checks`, `skeptic`, `dq` (completed / timed_out / unverified / not_run).
3. **Unverified is a first-class result.** `RUN DQ` did not complete in Zetaris's recorded EDGAR/PUDL/NOAA run, and USL materialization is unverified. Never display "passed" without rule results.
4. **A check has a severity.** `blocking` checks stop approval. `warn` checks must be disclosed in the final answer.

## 2. `app/verify/evidence.py`

```python
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
        return any(c.blocking and c.status == "fail" for c in self.checks)

    def as_blocking_finding(self):
        from app.agents.schemas import Finding
        failed = [c for c in self.checks if c.blocking and c.status == "fail"]
        return Finding(id="blocking_check_failed", severity="blocking",
                       claim="; ".join(f"{c.id}: {c.detail}" for c in failed),
                       evidence_ref=failed[0].id if failed else "")

    def to_prompt_json(self) -> list[dict]:
        return [asdict(c) for c in self.checks]
```

## 3. `app/verify/checks.py` — the checks

Every check gets a `StatementRunner` (reader identity for reads, builder as documented fallback) and writes a `check_result` trace event.

### 3.1 `count_source_vs_zetaris` (blocking)
Compare the count of records in the **source** with the count in the Zetaris view.

```python
def count_at_path(obj, dotted: str) -> int:
    """Count items at a dot path. list -> len(list); dict -> number of keys (dynamic-key objects)."""
    cur = obj
    for part in dotted.split("."):
        cur = cur[part]
    return len(cur)

def check_source_vs_zetaris(runner, model, source_json, count_path) -> CheckResult:
    src = count_at_path(source_json, count_path)
    zet = runner.count(model.fqn, purpose="source reconciliation")
    status = "pass" if src == zet else "fail"
    return CheckResult("count_source_vs_zetaris", "Source count equals Zetaris count", status, True,
                       f"source={src} zetaris={zet}", {"source": src, "zetaris": zet, "path": count_path})
```
- The source JSON is the sample we already fetched (file 06, 9.1), or a fresh single fetch for a catalog recipe (`Recipe.source_count`). One fetch per source per run; cache it in the run context.
- A view that explodes a **nested** array can legitimately have more rows than top-level items. The Onboarder's `count_path` and `row_grain` must describe what is being counted; if they differ in grain, mark the check `warn` and explain rather than `fail`.
- If the source cannot be counted directly, status `skipped` with the reason.

### 3.2 `join_loss` (blocking) — the IBM check
Detects silent record loss in a JOIN. Run whenever `Draft.join_spec` is set.

```python
def join_loss_sqls(left: str, right: str, lkey: str, rkey: str) -> dict:
    return {
      "left_total":   f"SELECT COUNT(DISTINCT l.{lkey}) FROM {left} l",
      "matched":      f"SELECT COUNT(DISTINCT l.{lkey}) FROM {left} l INNER JOIN {right} r ON l.{lkey} = r.{rkey}",
      "unmatched":    f"SELECT l.{lkey} FROM {left} l LEFT JOIN {right} r ON l.{lkey} = r.{rkey} WHERE r.{rkey} IS NULL LIMIT 50",
    }
```
- `status = "fail"` if `left_total != matched` **and** the draft's SQL does not show the unmatched records (inspect the draft SQL: INNER JOIN without a branch that surfaces unmatched keys, or a result row count equal to `matched`). If the draft already surfaces the unmatched records (LEFT JOIN selecting both keys), `status = "warn"` with the unmatched keys listed in `evidence`.
- Include the unmatched keys in `evidence["unmatched_keys"]` so the Skeptic can name them.
- **UNKNOWN U9:** confirm the `LEFT JOIN ... IS NULL` form works across USL tables. If it errors, retry with `NOT EXISTS`, then a window-function formulation. Zetaris's documented limitation concerns reading the **same exploded UNION view twice**; these queries read two distinct tables, so they should be fine, but verify.
- Identifiers: validate keys against `^[A-Za-z_][A-Za-z0-9_]*$` or backtick them.

### 3.3 `result_truncation` (blocking if triggered)
`Draft.truncated` is true when `len(rows) >= limit`. If true, run `SELECT COUNT(*)` over the same query (wrap the draft SQL as a subquery) and compare with `len(rows)`. `fail` if the count is larger; `pass` otherwise.

### 3.4 `string_typed_numbers` (warn)
Zetaris REST returns strings; some JSON fields are quoted numbers. For each `ORDER BY`, `MAX`, `MIN`, `SUM`, `AVG` or comparison in the draft SQL on a column whose declared type is `string` and whose sample values parse as numbers/dates, and that is not wrapped in `CAST(...)` → `warn` (lexicographic sorting danger, per the NASA NeoWs example in the docs). Use `ModelInfo.columns` types from `DESCRIBE`.

### 3.5 `coverage_period` (warn)
If any column named like a date/period/year appears in the result or model, compute `MIN`/`MAX`; if the plan specified a period (or the summary implies "full year"/"all"), compare and `warn` on partial coverage. (The docs' EDGAR/PUDL/NOAA example had 37 observed days, not a full year.)

### 3.6 `summary_matches_rows` (warn)
Deterministic sanity: the numbers the Investigator's summary mentions for row counts must equal `len(rows)`. Extract integers followed by words like "rows", "companies", "records" and compare.

### 3.7 `rows_readable` (status, not a check)
`SELECT * FROM <fqn> LIMIT 5` returns ≥1 row for each model. Sets `statuses["rows_readable"]` to `true/false`. This is separate from `registered`.

### 3.8 DQ (`app/verify/usl.py`) — see section 5.

### 3.9 Collector
```python
class Verifier:
    def collect(self, plan, onboard, draft) -> EvidencePack:
        pack = EvidencePack()
        pack.statuses["registered"] = onboard.status in ("ok", "partial")
        pack.statuses["rows_readable"] = self._rows_readable(onboard)
        for model in onboard.models:
            if model.count_path and model.source_row_count is not None:
                pack.add(...count check...)
        if draft.join_spec:
            pack.add(self.join_loss(draft))
        pack.add(self.truncation(draft)); pack.add(self.string_numbers(draft, onboard)); pack.add(self.coverage(draft, onboard))
        pack.add(self.summary_rows(draft))
        pack.statuses["dq"] = self.dq_status_cached(onboard)    # computed once per onboarding, not per revision round
        return pack
```
Run expensive checks (source fetch, DQ) once per run and reuse across revision rounds. Only the draft-dependent checks re-run each round.

---

## 4. The IBM scenario (historical result from Zetaris's docs — verify on your instance)

Recorded in the USL catalog and HOWTO:
- `sic_edgar_usl` has two tables, `company` and `sic_code`, with `company.sic` → `sic_code.sic_code`.
- Auto-generated PK rule on `cik`: 7 total, 7 valid, 0 invalid. Auto-generated FK rule on `sic`: **7 total, 6 valid, 1 invalid**.
- The invalid record is **IBM: cik `0000051143`, sic `3570`**, EDGAR description "Computer & office Equipment". The reference list (1,005 rows) has no code 3570.
- The plain description-agreement query is an INNER JOIN returning 6 rows. It silently excludes IBM and returns 5 wording-only differences (Ford and Tesla "&" vs "and"; Oracle, Target and Walmart with EDGAR's "Retail-"/"Services-" prefix).
- Conclusion in the docs: the FK rule and the plain query are **complementary**, not interchangeable.

**What Pipeless must demonstrate:**
1. Question (from a calling agent): "Which companies have an industry description in EDGAR that disagrees with the official SIC reference list?"
2. First draft (Investigator): INNER JOIN, 6 rows, summary says "5 companies differ".
3. Verifier: `join_loss` → `left_total=7`, `matched=6`, `unmatched_keys=["3570" (or the company key)]` → **fail (blocking)**.
4. Skeptic: `revise`, finding cites `join_loss`, names the missing record, level `source` (the reference list lacks the code).
5. Revision: LEFT JOIN selecting both keys; result shows 5 wording differences + 1 unmatched record (IBM).
6. Final answer: states 7 companies in, 6 matched, IBM's SIC code 3570 not in the reference list, plus the 5 wording differences; DQ status reported separately.

The Investigator and Skeptic must **discover** this through the checks. Do not write IBM, 3570 or any of these numbers into any prompt, test oracle used at runtime, or output example. (Unit tests may assert on a recorded fixture, clearly labelled as a test fixture.)

## 5. USL lifecycle and DQ (`app/verify/usl.py`)

Lifecycle statements (all via the builder `StatementRunner`, one at a time):
```
CREATE NAMESPACE IF NOT EXISTS lightning.metastore
CREATE NAMESPACE IF NOT EXISTS lightning.metastore.<ns>
COMPILE USL IF NOT EXISTS <usl> DEPLOY NAMESPACE lightning.metastore.<ns> DDL <CREATE TABLE ...;...>   -- ONE statement
ACTIVATE USL TABLE lightning.metastore.<ns>.<usl>.<table> AS <SELECT ...>
SELECT COUNT(*) FROM lightning.metastore.<ns>.<usl>.<table>
LIST DQ USL lightning.metastore.<ns>.<usl>
RUN DQ [<rule>] TABLE lightning.metastore.<ns>.<usl>.<table>
SHOW DQ ALL INVALID TABLE lightning.metastore.<ns>.<usl>.<table>
```
(For catalog recipes the repo script performs the first four; Pipeless only reads and verifies.)

### DQ procedure with a bounded wait
```python
def run_dq(runner_builder, usl_ns: str, usl: str, table: str, wait_s: int = 180) -> dict:
    """Returns {status: completed|timed_out|failed|not_run, rules: [...], invalid_sample: [...], elapsed_s, form_used}."""
```
1. `LIST DQ USL ...` to get rule names (constraint rules are named after the column; do not guess generated names).
2. **UNKNOWN U8:** the command reference says a rule name is required; the EDGAR guide runs `RUN DQ TABLE ...` without one. Try `RUN DQ <rule> TABLE ...` per rule first; if that errors, try the no-name form; record `form_used`.
3. Execute in a worker thread with `wait_s` timeout. On timeout set `status="timed_out"` (**unverified**), keep the thread's result if it arrives later, and **do not relaunch DQ while the earlier command may still be running** (Zetaris doc warning).
4. `SHOW DQ ALL INVALID TABLE ...` returns rows in a JSON `records` column (per the SQL companion); parse defensively and put a sample (≤ 20 records) into the evidence.
5. Result mapping: `completed` with all invalid counts 0 → `pass`; invalid > 0 → `warn` with the sample (a data-quality finding, not an error); `timed_out` → `unverified`.

`dq` is **not blocking** for approval unless the plan's success criteria require it, but it is always reported with its own status.

## 6. Model registry flip

After a run reaches `status="verified"`, set `registry[fqn].verified = true`, store the caveats (`warn` checks, unmatched keys) in `notes`, and emit `memory_write` events. A model that finished `answered_with_caveats` stays `verified=false` and is **not** offered to other agents through `/ask` (file 09).

## 7. Acceptance criteria

- [ ] On the sic_edgar_usl scenario the Verifier's `join_loss` result shows `left_total`, `matched` and the unmatched key(s) from real queries.
- [ ] `count_source_vs_zetaris` compares a real source count with a real `COUNT(*)`.
- [ ] A truncated result (force `limit=5`) is caught by `result_truncation`.
- [ ] `run_dq` returns `timed_out` rather than hanging, and never starts a second DQ command while the first may be running.
- [ ] `statuses` always contains `registered`, `rows_readable`, `dq` as separate values.
- [ ] No check reads a hard-coded expected number.
