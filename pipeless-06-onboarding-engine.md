# PIPELESS — 06 ONBOARDING ENGINE

> Prerequisites: files 01–05. This is where Pipeless gets sources into Zetaris. There are two paths: **catalog recipes** (Zetaris's own verified scripts, run by us one statement at a time) and **novel REST sources** (the Onboarder agent inspects a real response and generates the flattening view, then repairs its own errors). Both paths pass through the same **policy gate** and **dry-run** before anything is created.

Files created here: `app/onboarding/catalog.py`, `recipe_loader.py`, `policy.py`, `ddl.py`, `novel_rest.py`, `repair.py`, `registry.py`, and `app/agents/onboarder.py` (the agent that drives them).

---

## 1. Why two paths

- **Catalog path (P0).** Zetaris publishes verified recipes in `getting-started/open_data/`. They are the safest way to demonstrate the IBM scenario. Pipeless reads the `.sql` files from a local clone and executes each statement through our own adapter (so every statement is logged by us, with real Zetaris responses).
- **Novel path (P1, but required for the "unseen input" test).** Judges use their own inputs. The starter guide warns that a system that only works on the demo fails. The novel path onboards a REST source that is **not** in the catalog. The agent fetches a real sample, classifies its JSON shape, asks the model for a flattening `SELECT`, previews it in Zetaris, repairs on error, and only then creates the view.

USL generation by the model is **out of scope** (stretch). USL models come from catalog recipes only.

## 2. Hard rules (mirroring Zetaris's own safety guidance)

1. Start read-only: `SHOW LIGHTNING DATABASES`, `SHOW LIGHTNING TABLES <db>`, `DESCRIBE DATASOURCE TABLE <db>.<table>`.
2. Build a **plan** (list of statements with kind, target, purpose). Run it through `policy.evaluate`.
3. In `auto_policy` mode, execute only if every statement passes policy. In `manual` mode, return `status="needs_approval"` with the plan; execution resumes after `POST /approve/{run_id}`.
4. One statement per call. Wait for completion before the next dependent statement.
5. Never drop or replace an object that was not created by this run (check the ledger). Never touch objects outside the allowlist.
6. Reuse an existing object only if it matches the recipe definition (see section 7).
7. Never print credentials. Replace `YOUR_APP_NAME YOUR_CONTACT_EMAIL` placeholders in scripts with `ZETARIS_USER_AGENT` (must be `app_name user@domain.tld`).
8. Record every created object in `data/ledger.jsonl` (object kind, name, run id, definition hash, timestamp).

---

## 3. `app/onboarding/catalog.py`

A recipe is data: which repo files to read, which statements to include, how rate-limited the source is, and how to count the source directly for verification.

```python
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RecipePart:
    path: str                                  # relative to ZETARIS_REPO_PATH
    include_kinds: tuple[str, ...] | None = None   # e.g. ("LIGHTNING_DATABASE", "REST_TABLE", "CACHE_TABLE")
    stop_after_contains: str | None = None     # stop after the statement containing this text (inclusive)
    rate_limited: bool = False


@dataclass(frozen=True)
class Recipe:
    id: str
    description: str
    parts: tuple[RecipePart, ...]
    allowed_objects: tuple[str, ...]           # lowercase names this recipe may create (policy allowlist)
    needs_user_agent: bool = True
    source_count: dict | None = None           # how to count the source directly (see file 07)
    notes: tuple[str, ...] = ()


CATALOG: dict[str, Recipe] = {
    # Zetaris docs: Steps 0-2 of 12_company_dns_create.sql (through CACHE TABLE company_dns.sic_codes_raw)
    "company_dns_v3_raw": Recipe(
        id="company_dns_v3_raw",
        description="SIC reference hierarchy (V3 raw REST table, cached). Expect 1005 SIC codes.",
        parts=(RecipePart(
            path="open_data/rest_apis/sql/rate_limited/12_company_dns_create.sql",
            stop_after_contains="CACHE TABLE company_dns.sic_codes_raw",
            rate_limited=True),),
        allowed_objects=("company_dns", "sic_codes_raw"),
        source_count={"url": "https://company-dns.mediumroast.io/V3.0/sic", "count_path": "data.sics"},  # CONFIRM exact URL from the script's endpoint line
        notes=("V3 is deprecated; the /V3.0 path still answered on 2026-10-19 and may go away.",),
    ),
    # Zetaris docs: Step 0 and the seven *_submissions_raw REST tables of 11_edgar_company_profiles_create.sql
    "edgar_submissions_raw": Recipe(
        id="edgar_submissions_raw",
        description="SEC EDGAR submissions for 7 companies (raw REST tables).",
        parts=(RecipePart(
            path="open_data/rest_apis/sql/rate_limited/11_edgar_company_profiles_create.sql",
            include_kinds=("LIGHTNING_DATABASE", "REST_TABLE"),
            rate_limited=True),),
        allowed_objects=("edgar",),   # CONFIRM the database name from the script; add each *_submissions_raw table name
        notes=("EDGAR requires a real User-Agent and allows ~10 requests/second.",),
    ),
    # Zetaris docs: open_data/usl/sql/02_sic_edgar_usl_create.sql (namespace, compile, activate x2, DQ)
    "sic_edgar_usl": Recipe(
        id="sic_edgar_usl",
        description="Two-table USL (company, sic_code) with an FK from company.sic to sic_code.",
        parts=(RecipePart(path="open_data/usl/sql/02_sic_edgar_usl_create.sql"),),
        allowed_objects=("lightning.metastore", "lightning.metastore.usl_demo", "sic_edgar_usl"),
        notes=("Historical result: FK rule finds 1 invalid of 7 (IBM, cik 0000051143, sic 3570).",),
    ),
}

# Dependency order for each catalog target (from the docs: sic_edgar_usl needs company_dns raw + edgar raw tables).
DEPENDENCIES: dict[str, list[str]] = {
    "sic_edgar_usl": ["company_dns_v3_raw", "edgar_submissions_raw", "sic_edgar_usl"],
    "company_dns_v3_raw": ["company_dns_v3_raw"],
    "edgar_submissions_raw": ["edgar_submissions_raw"],
}
```

> The coding agent MUST open the real `.sql` files in the cloned repo and (a) confirm the exact database/table names used in each script and fill `allowed_objects` from them, (b) confirm the count URL in `source_count` from the script's `endpoint` line. Record the repo commit hash (`git rev-parse HEAD`) in `data/recipes_sha.txt` and in every run's logs for reproducibility. Do not copy SQL out of the docs into our code; always read the repo's files so Zetaris's current versions are used.

## 4. `app/onboarding/recipe_loader.py`

```python
from __future__ import annotations
from pathlib import Path
from app.config import Config
from app.onboarding.catalog import Recipe, RecipePart
from app.zsql.classify import classify_statement, statement_target
from app.zsql.splitter import split_statements

PLACEHOLDER = "YOUR_APP_NAME YOUR_CONTACT_EMAIL"


def load_statements(cfg: Config, recipe: Recipe) -> list[dict]:
    """Return ordered statements: {sql, kind, target, rate_limited, source_file}."""
    out: list[dict] = []
    for part in recipe.parts:
        text = (cfg.repo_path / part.path).read_text(encoding="utf-8")
        if PLACEHOLDER in text:
            if not cfg.user_agent_valid:
                raise ValueError("ZETARIS_USER_AGENT must be 'app_name user@domain.tld' for this recipe")
            text = text.replace(PLACEHOLDER, cfg.user_agent)
        for sql in split_statements(text):
            target = statement_target(sql)
            label = target[0] if target else None
            if part.include_kinds and label not in part.include_kinds:
                continue
            out.append({"sql": sql, "kind": classify_statement(sql), "target": target,
                        "rate_limited": part.rate_limited, "source_file": part.path})
            if part.stop_after_contains and part.stop_after_contains.lower() in sql.lower():
                break
    return out
```

Notes:
- Commented-out statements (teardown, optional materialization, select examples) are dropped by the splitter. Do **not** execute `*_select.sql` queries; the Investigator writes its own SQL.
- The loader must refuse recipes that contain `DROP` / `REMOVE` statements (these only appear commented out; if a live one shows up, raise).

---

## 5. `app/onboarding/policy.py`

```python
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class PolicyDecision:
    allowed: bool
    reason: str
    needs_human: bool = False


def evaluate(statement: dict, *, allowed_objects: set[str], run_created: set[str],
             approval_mode: str) -> PolicyDecision:
    kind = statement["kind"]
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
```

Allowlist for the novel path: names starting with `pl_` (our own prefix). Allowlist for catalog recipes: `Recipe.allowed_objects`. In `manual` mode every create returns `needs_human=True`, so the whole plan is returned for approval. Every decision emits a `policy_decision` trace event.

---

## 6. `app/onboarding/ddl.py` (DDL templates built by code; the model supplies only the SELECT body)

```python
from __future__ import annotations
import re

_DESC_BAD = re.compile(r"[^A-Za-z0-9 _.,\-]")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def safe_desc(text: str) -> str:
    """DESCRIBE BY accepts only letters, digits, spaces, _ . - and ,"""
    return _DESC_BAD.sub(" ", text).strip()[:100] or "Pipeless source"


def ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"unsafe identifier: {name!r}")
    return name


def create_database(db: str, description: str) -> str:
    return f'CREATE LIGHTNING DATABASE {ident(db)} DESCRIBE BY "{safe_desc(description)}"'


def create_rest_table(raw: str, db: str, endpoint: str, user_agent: str | None) -> str:
    header = f'    user-agent "{user_agent}"\n' if user_agent else ""
    return (
        f"CREATE LIGHTNING REST TABLE {ident(raw)} FROM {ident(db)} REQUEST(\n"
        f'    endpoint "{endpoint}",\n'
        f'    method "get",\n'
        f'    response_type "json",\n'
        f'    http_encoding "URLENCODED"\n'
        f") HEADER (\n{header}) BODY ()"
    )


def cache_table(db: str, raw: str) -> str:
    return f"CACHE TABLE {ident(db)}.{ident(raw)}"


def create_container(container: str) -> str:
    return f"CREATE SCHEMASTORE CONTAINER {ident(container)}"


def create_view(view: str, container: str, select_sql: str) -> str:
    return f"CREATE SCHEMASTORE VIEW {ident(view)} WITH CONTAINER {ident(container)} AS\n{select_sql.strip().rstrip(';')}"


def preview_select(select_sql: str) -> str:
    return f"PREVIEW SCHEMASTORE SQL AS {select_sql.strip().rstrip(';')}"
```

Object naming for novel sources: `pl_<slug>_<runid6>` for database, container, view, and `pl_<slug>_raw` for the raw table, all lowercase. A fresh suffix avoids "already exists" clashes because `CREATE SCHEMASTORE CONTAINER` has no `IF NOT EXISTS`, and `IF NOT EXISTS` elsewhere does not update.

---

## 7. `app/onboarding/registry.py` (reuse + ledger + model registry)

Two JSON stores under `data/`:

- `ledger.jsonl` — append-only: every object this process created `{ts, run_id, kind, name, definition_hash, source_file_or_origin}`.
- `registry.json` — verified models other agents can reuse: `{ "<fqn>": {kind, columns, row_grain, source_id, count_path, source_row_count, created_run, verified, notes[], granted_roles[] } }`.

Functions: `ledger_add`, `created_in_run(run_id)`, `registry_get/put/list`, `definition_hash(sql)` (SHA-256 of the whitespace-normalised statement).

**Reuse rule.** Before creating an object, check it exists:
- Lightning database: `SHOW LIGHTNING DATABASES` contains the name.
- REST/file table: `SHOW LIGHTNING TABLES <db>` contains the table.
- Container: no list command is documented; attempt the create and treat `already_exists` as a skip, but **log `reuse_unverified`** and run `SELECT COUNT(*)` on the dependent view to confirm it works.
- USL table: `SELECT COUNT(*) FROM lightning.metastore.<ns>.<usl>.<table>` succeeds ⇒ already activated ⇒ skip `ACTIVATE USL TABLE` (the docs say activation is not verified as repeatable).
- If the registry has the object and the stored `definition_hash` matches ⇒ reuse. If it exists but the hash differs or is unknown ⇒ do **not** modify; create a new versioned name (novel path) or stop and report (catalog path).

---

## 8. The onboarding executor (`app/agents/onboarder.py` core)

```python
from __future__ import annotations
from app.onboarding.recipe_loader import load_statements
from app.onboarding.catalog import CATALOG, DEPENDENCIES
from app.onboarding import policy
from app.zsql.classify import statement_target


class OnboarderAgent(BaseAgent):          # BaseAgent from file 05
    name = "OnboarderAgent"

    def onboard(self, plan) -> "OnboardResult":
        mem = self.memory_block("onboarding " + " ".join(s.description or s.url or s.recipe_id or "" for s in plan.sources),
                                tags=["onboarding", "zetaris"])
        result = OnboardResult(status="ok")
        statements: list[dict] = []
        for src in plan.sources:
            if src.kind == "catalog":
                for rid in DEPENDENCIES.get(src.recipe_id, [src.recipe_id]):
                    statements += self._tag(load_statements(self.ctx.cfg, CATALOG[rid]), CATALOG[rid])
            else:
                statements += self._plan_novel(src, mem, result)       # builds DDL, sample-fetch, LLM flatten (section 9)
        result.plan_statements = [{"sql": s["sql"], "kind": s["kind"], "purpose": s.get("purpose", "")} for s in statements]

        # Dry-run + policy gate
        decisions = [policy.evaluate(s, allowed_objects=self._allowed(plan), run_created=self._created(),
                                     approval_mode=self.ctx.cfg.approval_mode) for s in statements]
        for s, d in zip(statements, decisions):
            self.ctx.tracer.emit(self.name, "policy_decision", s["sql"], d.reason,
                                 status="success" if d.allowed else "blocked")
        if any(not d.allowed for d in decisions):
            result.status = "failed"; result.errors = [d.reason for d in decisions if not d.allowed]
            return result
        if any(d.needs_human for d in decisions):
            self.ctx.tracer.emit(self.name, "approval_requested", f"{len(statements)} statements", "waiting for POST /approve")
            result.status = "needs_approval"
            return result

        return self._execute(statements, plan, result)

    def _execute(self, statements, plan, result):
        runner = StatementRunner(ZetarisClient(self.ctx.cfg, "builder"), self.ctx.cfg, self.ctx.tracer, self.name)
        for s in statements:
            if self._exists_and_matches(runner, s):                   # reuse rule, section 7
                result.reused_objects.append(self._label(s)); continue
            try:
                runner.execute(s["sql"], purpose=s.get("purpose", "onboard"), rate_limited=s["rate_limited"])
                self._ledger(s); result.created_objects.append(self._label(s))
            except ZetarisError as err:
                fixed = self._repair(runner, s, err, plan, result)   # section 10; only for novel-path views
                if not fixed:
                    result.status = "failed"; result.errors.append(f"{err.info.kind}: {err.info.message}")
                    return result
        result.models = self._describe_models(runner, plan)           # DESCRIBE / LOAD USL (section 11)
        return result
```

Rules:
- Statement execution order is the order in the plan. Catalog dependencies are already ordered by `DEPENDENCIES`.
- After a `CREATE LIGHTNING REST TABLE` on a rate-limited source, the next statement **must** be `CACHE TABLE` (the catalog scripts already do this; the novel path adds it).
- After `CACHE TABLE`, time the next read of that table. About 1 second means the cache works; much longer means it called the API again, and the Onboarder must log `cache_suspect`.
- `reconsider(plan, onboard, verdict)` (called when the Skeptic raises a source-level finding) re-inspects the relevant model with `DESCRIBE`/`SELECT` probes and records the finding in `ModelInfo.notes` (for example "reference list lacks sic 3570"). It does **not** rebuild shared objects.

---

## 9. Novel REST path (`app/onboarding/novel_rest.py`)

Inputs: `SourceSpec(kind="rest_url", url=..., description=...)`.

### 9.1 Fetch a real sample ourselves (not through Zetaris)
```python
def fetch_sample(url: str, cfg, max_bytes: int = 200_000) -> tuple[dict | list, int]:
    """GET the endpoint once with our User-Agent. Return (parsed_json, byte_length).
    In SAMPLE_MODE the sample comes from input_examples/samples/<slug>.json instead (INPUT data only)."""
```
Respect rate limits: one fetch per source per run; never loop. Send `User-Agent: ZETARIS_USER_AGENT`.

### 9.2 Classify the shape (code, not the model) — five shapes from the Zetaris docs
```python
def classify_shape(obj) -> dict:
    # returns {"shape": "...", "array_paths": [...], "dynamic_key_paths": [...], "notes": [...]}
```
Shapes: (1) top-level object with array-of-structs field(s) → `LATERAL VIEW explode(<field>) AS item`; (2) array nested under a wrapper key (`data.items`); (3) top-level JSON array (registration may fail; untested; log it); (4) flat struct, no array — plain dot access, **no explode**; (5) dynamic-key object (object whose keys are codes/ids with uniform struct values) → `from_json(to_json(<path>), 'map<string, struct<...>>')` then explode. Heuristic for dynamic keys: an object with ≥ 20 keys whose values are all dicts with identical key sets.

Also detect: keys needing backticks (mixed case, reserved words such as `end`, `start`, `date`, hyphens); numeric-looking strings; nested arrays (a single `explode` only reaches the top level).

### 9.3 Ask the model for the flattening SELECT (Onboarder system prompt)

```text
You are OnboarderAgent inside Pipeless. You write ONE Spark SQL SELECT that flattens a REST JSON response registered in Zetaris as the table {db}.{raw}.
You receive: a truncated sample of the real response, the detected shape and array/dynamic-key paths, and the Zetaris rules below.
Rules (from Zetaris documentation):
- Arrays of structs: LATERAL VIEW explode(<array_field>) AS item, then plain dot access (item.field, item.sub.field).
- Do NOT use explode on something that is not an array; flat structs use plain dot access.
- Dynamic-key objects: from_json(to_json(<path>), 'map<string, struct<...>>') then explode, and apply that to EVERY dynamic-key object exploded in the same query.
- Backtick keys with mixed case, reserved words or hyphens: `entityName`, item.`end`, product.nutriments.`energy-kcal_100g`.
- JSON may encode numbers as strings: CAST(... AS DOUBLE/INT/BIGINT) in this view when the column is numeric.
- Read the raw table once per query. Do not self-join an exploded view.
- Select explicit, readable, snake_case aliases. Include an id/key column if one exists.
Output JSON: {"select_sql": "...", "row_grain": "one row per ...", "count_path": "dot.path.to.the.array.used.in.explode", "columns": [{"name":"","type":""}], "notes": []}.
Memories from earlier onboarding (may include fixes that worked before):
{memory_block}
```

User message: the truncated sample (≤ 12 KB, with a "[truncated]" marker), shape classification, and `FROM {db}.{raw}` to use.

### 9.4 Plan assembly (code)
```
1. SHOW LIGHTNING DATABASES                      (READ)
2. CREATE LIGHTNING DATABASE pl_<slug>_<run6> DESCRIBE BY "..."
3. CREATE LIGHTNING REST TABLE pl_<slug>_raw FROM pl_<slug>_<run6> REQUEST(...)
4. CACHE TABLE pl_<slug>_<run6>.pl_<slug>_raw
5. PREVIEW SCHEMASTORE SQL AS <select_sql>       (READ, returns up to 10 rows; validates the SQL)
6. CREATE SCHEMASTORE CONTAINER pl_<slug>_<run6>_c
7. CREATE SCHEMASTORE VIEW pl_<slug>_view WITH CONTAINER pl_<slug>_<run6>_c AS <select_sql>
8. SELECT COUNT(*) FROM <container>.<view>       (READ; compare with source count, file 07)
```
The `select_sql` in step 5/7 is provisional; the repair loop may revise it within the approved object names. Policy keys on **object names and statement kinds**, not exact SELECT text.

---

## 10. Repair loop (`app/onboarding/repair.py`)

Triggered when step 5 (PREVIEW) or step 7 (CREATE VIEW) raises `ZetarisError`.

```
for attempt in 1..3:
    hint = err.info.hint                                  # from the playbook (file 03)
    memory = H-MEM retrieval for "<err.kind> <shape>"
    ask Onboarder (fast lane) with: failing SQL, raw error text, playbook hint, shape classification, memory, the sample
        -> revised select_sql (same JSON schema as 9.3)
    emit repair_attempt {attempt, error_kind, hint, new_sql_summary}
    re-run PREVIEW; on success continue to CREATE VIEW
on success: H-MEM event "repair_success" {error_kind, shape, fix_summary}
on 3 failures: result.status="failed"; H-MEM event "repair_failed"
```
Rules:
- Max 3 attempts. Each attempt is an LLM call (counts toward the run budget).
- Never change object names or kinds during repair (policy already approved them).
- `rate_limited` / `transport_cold_start` / `bad_gateway` are handled by `StatementRunner`'s single retry (file 03), not by the repair loop.
- Record `result.repairs.append({"attempt","error_kind","hint","fix"})` so the final answer can show self-repair as part of the receipts.

---

## 11. Describing models after onboarding

For each created/reused queryable object:
- Schemastore view / lightning table: `DESCRIBE DATASOURCE TABLE <db>.<table>` → columns + types. (For a schemastore view use the name form `<container>.<view>`; if `DESCRIBE` rejects it, fall back to `SELECT * FROM <container>.<view> LIMIT 1` and infer from the response. **UNKNOWN** — probe it.)
- USL table: `LOAD USL <usl> NAMESPACE <namespace>` returns JSON (tables, ordered columns, keys). Parse it; do not guess column names.
- Count: `SELECT COUNT(*) FROM <fqn>`.
- Build `ModelInfo` (file 05) and register it in `registry.json` with `verified=false`; the Verifier flips it to `true` only after all blocking checks pass.

## 12. Catalog scenario: the sic_edgar_usl flow (what the Onboarder should execute)

1. `company_dns_v3_raw`: `CREATE LIGHTNING DATABASE COMPANY_DNS`, create the V3 REST table, `CACHE TABLE company_dns.sic_codes_raw`.
2. `edgar_submissions_raw`: the database plus the seven `*_submissions_raw` REST tables from script 11 (rate limited: identified User-Agent required).
3. `sic_edgar_usl`: `CREATE NAMESPACE IF NOT EXISTS lightning.metastore`, then the child namespace, the multi-table `COMPILE USL` (one statement), then the two `ACTIVATE USL TABLE` statements, as the repo script defines.
4. `LOAD USL sic_edgar_usl NAMESPACE lightning.metastore.usl_demo` to learn the real columns, then `SELECT COUNT(*)` on both tables.
5. Hand over to the Investigator.

Never run the `RUN DQ` here; the Verifier runs it later with a bounded wait (file 07).

## 13. Acceptance criteria

- [ ] Catalog path: `POST /run` with `recipe_id="sic_edgar_usl"` onboards from a clean instance, statement by statement, with one `zetaris_statement` trace event per statement and no multi-statement call.
- [ ] Re-running the same request reuses existing objects (logged as `reused_objects`) and does not error or duplicate.
- [ ] Policy: a plan containing `DROP LIGHTNING DATABASE x` for an object not in the ledger is blocked (unit test).
- [ ] `APPROVAL_MODE=manual` returns `needs_approval` with the full statement list and creates nothing.
- [ ] Novel path: onboarding a held-out public JSON REST API (not in the catalog) produces a working view; at least one deliberately imperfect first attempt is repaired, with `repair_attempt` events.
- [ ] Every created object appears in `data/ledger.jsonl`.
- [ ] `DESCRIBE BY` text is sanitised; unsafe identifiers raise.
