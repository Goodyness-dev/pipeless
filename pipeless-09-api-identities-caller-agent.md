# PIPELESS — 09 API FOR OTHER AGENTS, IDENTITIES, READER `/ask`, CALLER DEMO AGENT

> Prerequisites: files 01–08. This file turns Pipeless into an **agent for other agents**: a stable HTTP contract, two Zetaris identities (builder vs reader), a reusable model registry, access grants, and a small real calling agent for the demo.

Files created here: `app/api/main.py`, `app/api/schemas.py`, `app/api/grants.py`, `caller_demo/support_agent.py`, `run.py`.

---

## 1. Why an API, and what other agents get

The starter guide asks for a programmatic entry point (`POST /run`) so judges can call the project without a UI. Our API is that entry point and also the product surface for other agents:

| Endpoint | Purpose | Identity used against Zetaris |
|---|---|---|
| `POST /run` | Full pipeline: plan → onboard → investigate → verify → Skeptic loop → answer + receipts | builder (create) + reader (answer reads) |
| `POST /approve/{run_id}` | Approve a plan returned as `needs_approval` (manual mode) | builder |
| `GET /runs/{run_id}` | Status/result of a run, including trace path | none |
| `GET /models` | Verified models other agents can reuse (name, columns, caveats) | none (registry only) |
| `POST /ask` | A follow-up read-only question against **verified** models; no onboarding | reader |
| `GET /health` | Config sanity (no secrets): Zetaris reachable, Nightshift reachable, models pinned | both (SELECT 1, tiny chat) |

All endpoints except `/health` require `Authorization: Bearer <PIPELESS_API_TOKEN>`. Optional header `X-Caller-Agent: <name>` identifies the calling agent and is logged on every trace line (this is the agent-to-agent evidence judges will look for).

## 2. Two identities (the "governed" story, grounded in Zetaris's permissions guide)

Zetaris recommends least privilege: creating/updating/deleting stays with administrators; agents that only read use their own non-administrator user.

| Identity | Zetaris user | Key env var | Does |
|---|---|---|---|
| **builder** | administrator | `ZETARIS_BUILDER_API_KEY` | registers sources, schema store views, USL compile/activate, DQ, grants |
| **reader** | non-admin user | `ZETARIS_READER_API_KEY` | `SELECT` on assigned/granted models only |

### One-time **human** setup (the agent must not do these)
1. In the Zetaris UI as admin: **Management → Users** create `pipeless_reader` (non-admin). **Management → Roles** create `pipeless_readers`; assign the user to the role.
2. **Management → Permissions**: expand the role and tick **View** on the resources it needs (for example Data Catalog / Query Builder / Unified Semantic Layer). Leave **Execute** unticked. **Save.**
3. Sign in as `pipeless_reader` and create an API key under **Management → API Tokens** (shown once); name it `pipeless-reader`. Create the builder's key the same way as admin.
4. Put both keys in `.env`.

### Granting access to verified models (`app/api/grants.py`)
Uses the builder identity after a run reaches `verified`:

```python
class GrantService:
    def grant_read(self, runner, model, role="pipeless_readers") -> dict:
        """Record the outcome; never raise. U3 is UNKNOWN: a non-admin may or may not be able to SELECT after GRANT."""
        stmt = {
          "usl_table": f"GRANT SELECT ON USL TABLE {model.fqn} TO ROLE '{role}'",
          "schemastore_view": f"GRANT SELECT ON VIEW TABLE {model.fqn} TO ROLE '{role}'",
        }.get(model.kind)
        if not stmt:
            return {"status": "not_attempted", "reason": f"no grant form for kind {model.kind}"}
        try:
            runner.execute(stmt, purpose="grant read to reader role")
            check = runner_reader.execute(f"SELECT * FROM {model.fqn} LIMIT 1", purpose="verify reader access")
            return {"status": "granted_and_verified" if check.rows else "granted_unreadable", "statement": stmt}
        except ZetarisError as e:
            return {"status": "failed", "error_kind": e.info.kind, "message": e.info.message[:200]}
```
- Syntax comes from the Zetaris command reference: `GRANT {SELECT|INSERT|CACHE} ON {... | USL TABLE <name> | <object_type> TABLE <name>} TO {ROLE '<role>' | USER '<user>'}` and, for views, `VIEW TABLE <container>.<view>`.
- **Unknown U3:** whether the reader can actually read afterwards. Probe on the real instance. The `status` string is returned to callers; if it is `failed` or `granted_unreadable`, `/ask` falls back to the builder identity **with a logged warning and the SQL guard enforced** (file 03). Never hide this fallback in the response (`"identity_used": "builder_fallback"`).
- Policy: `GRANT` statements pass through `policy.evaluate` as `ACCESS` kind (file 06); in manual approval mode they need approval.
- Track the outcome in `registry.json` (`granted_roles`).
- Report with `SHOW GRANT USER '<reader email>' ON ALL` as optional evidence.

## 3. Request / response contract (`app/api/schemas.py`)

Based on the starter guide's sample, extended with our separate statuses.

### `POST /run` request
```json
{
  "request_id": "example-001",
  "user_query": "Which companies have an industry description in EDGAR that disagrees with the official SIC reference list?",
  "sources": [
    {"id": "sic_edgar", "kind": "catalog", "recipe_id": "sic_edgar_usl"}
  ],
  "context": {"track": "The Agent That Can Explain Why"}
}
```
Novel source example: `{"id": "mysrc", "kind": "rest_url", "url": "https://api.example.com/v1/items", "description": "item list", "count_path": "data.items"}`.

### `POST /run` response
```json
{
  "request_id": "example-001",
  "trace_id": "trace-20261025-001",
  "status": "verified",
  "agents_used": ["PlannerAgent", "OnboarderAgent", "InvestigatorAgent", "SkepticAgent"],
  "stage_status": {
    "onboarding": "ok",
    "registered": true,
    "rows_readable": true,
    "deterministic_checks": "pass_with_warnings",
    "skeptic": "approve",
    "dq": "completed"
  },
  "result": {
    "summary": "7 companies were compared; 6 matched the reference list ...",
    "findings": [{"item": "...", "detail": "..."}],
    "rows": [["..."]],
    "caveats": ["..."],
    "confidence": 0.82
  },
  "receipts": {
    "sql": "SELECT ...",
    "checks": [{"id": "join_loss", "status": "warn", "detail": "left_total=7 matched=6", "evidence": {}}],
    "skeptic_findings": [{"id": "f1", "severity": "blocking", "claim": "...", "evidence_ref": "join_loss"}],
    "revisions": 1,
    "repairs": [],
    "created_objects": [], "reused_objects": []
  },
  "model_left_behind": {"fqn": "lightning.metastore....", "verified": true, "access": {"status": "granted_and_verified", "role": "pipeless_readers"}},
  "memory": {"retrieved": [{"id": "mem_...", "score": 0.61}], "written": 2},
  "collaboration": {"feedback_loop_completed": true, "revision_count": 1},
  "llm": {"calls": 9, "models_used": ["<model ids actually returned by Nightshift>"]},
  "log_path": "logs/trace-20261025-001.jsonl"
}
```
Possible `status` values: `verified`, `answered_with_caveats`, `needs_approval`, `failed`. Errors are structured: `{"status":"failed","error":{"stage":"onboarding","kind":"auth","message":"...","hint":"..."}}` with a proper HTTP code (never a bare 500 with a stack trace).

## 4. `app/api/main.py` (FastAPI skeleton)

```python
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from fastapi import Depends, FastAPI, Header, HTTPException

app = FastAPI(title="Pipeless")
_BUILD_POOL = ThreadPoolExecutor(max_workers=1)     # builder runs are serialised: Zetaris wants one statement at a time
RUNS: dict[str, dict] = {}                          # run_id -> state (also persisted to data/runs/<id>.json)


def auth(authorization: str = Header(None)):
    expected = CONFIG.api_token
    if not expected or authorization != f"Bearer {expected}":
        raise HTTPException(401, "invalid or missing bearer token")


@app.post("/run")
def run(req: RunRequest, x_caller_agent: str | None = Header(None), _=Depends(auth)):
    ctx = build_context(req.request_id, caller_agent=x_caller_agent)       # Tracer, LLM, HMem, agents
    ctx.tracer.emit("API", "handoff", req.user_query, "run accepted", target_agent="PlannerAgent",
                    caller_agent=x_caller_agent)
    future = _BUILD_POOL.submit(run_pipeline_safely, req, ctx)             # queue: one builder run at a time
    result = future.result(timeout=900)                                    # synchronous contract for the demo; add async later
    return render_response(req, ctx, result)
```

Concurrency rules:
- Builder runs go through a single-worker pool (and the `_BUILDER_LOCK` from file 03). Several calling agents may queue; each gets its own trace id. Reads (`/ask`) may run concurrently.
- Cap queue depth (return `429` with `Retry-After` if more than N waiting; N=5) — protects the Nightshift budget.
- Wrap the pipeline so **any exception** becomes a structured error response and a `final_answer` trace event with `status="error"`.

### `/ask` (read-only reuse)
1. Look up `registry.json` for models with `verified=true` matching `model_fqns` (or all verified models if omitted).
2. Investigator (fast lane) writes a SELECT using only those models.
3. `validate_read_only(sql, allowed_tables=<verified fqns>)` (file 03) — mandatory.
4. Run on the **reader** identity (or the logged builder fallback).
5. Run the draft-dependent checks (truncation, string-typed numbers, summary-vs-rows); skip expensive ones; Skeptic on the **deep** lane reviews once.
6. Return `{answer, rows, receipts, identity_used, caveats_from_registry}`. Include the model's stored caveats (for example "1 record unmatched in reference list") so the caveats travel with the model.

### `/models`
Returns verified models: `fqn, kind, columns, row_grain, source_id, notes (caveats), granted_roles, created_run`. This is what lets another agent discover reusable data without re-onboarding.

## 5. `run.py`

```python
import uvicorn
from app.config import Config

if __name__ == "__main__":
    cfg = Config.load()
    uvicorn.run("app.api.main:app", host="0.0.0.0", port=8000, log_level="info")
```

Startup checklist (log each, fail fast with a clear message):
1. Config valid; User-Agent format warning if invalid.
2. Zetaris `SELECT 1` on builder (and reader if configured).
3. Nightshift `discover_models()`; pinned `MODEL_FAST` / `MODEL_DEEP` present.
4. `ZETARIS_REPO_PATH` exists; record `git rev-parse HEAD` into `data/recipes_sha.txt`.
5. H-MEM hydrated; log how many long-term memories loaded.

## 6. `caller_demo/support_agent.py` — a REAL calling agent (agent-to-agent evidence)

A tiny agent whose job is to answer a support question that needs company data. It decides on its own to call Pipeless. It must use a real model call (Nightshift fast lane) to decide, and make real HTTP calls.

```python
"""A support agent that needs verified company data. It asks Pipeless instead of wiring data access itself."""
import json, os, requests
from app.adapters.meterless import NightshiftLLM, extract_json
# (build cfg/tracer as in run.py; use its own Tracer with agent name 'SupportAgent')

PIPELESS = os.getenv("PIPELESS_URL", "http://localhost:8000")
HEADERS = {"Authorization": f"Bearer {os.environ['PIPELESS_API_TOKEN']}", "X-Caller-Agent": "SupportAgent"}

SYSTEM = """You are SupportAgent, a customer-support assistant. You do not have direct access to company data.
When a customer question needs company data, reply with JSON: {"action":"ask_pipeless","user_query":"...","sources":[...]} using only the data sources listed below.
When you have an answer from Pipeless, reply with JSON: {"action":"reply","message":"..."} and never claim more than the verified result and caveats say.
Available sources: {sources_json}"""

def handle(customer_question: str, sources: list[dict]):
    step1 = llm.chat_json(lane="fast", system=SYSTEM.format(sources_json=json.dumps(sources)), user=customer_question, agent_name="SupportAgent")[0]
    if step1["action"] == "ask_pipeless":
        r = requests.post(f"{PIPELESS}/run", headers=HEADERS, json={"request_id": "demo-001", "user_query": step1["user_query"], "sources": step1["sources"]}, timeout=900)
        verified = r.json()
        step2 = llm.chat_json(lane="fast", system=SYSTEM.format(sources_json="[]"), user="Pipeless returned: " + json.dumps(verified)[:6000], agent_name="SupportAgent")[0]
        return step2["message"], verified
```

Demo flow with the caller:
1. SupportAgent receives a question → decides to call Pipeless (`POST /run`).
2. Pipeless runs the full pipeline (IBM moment).
3. SupportAgent replies using the answer **and its caveats**.
4. A second SupportAgent question reuses the left-behind model through `POST /ask` (no onboarding; the reader identity does the read).

Logs will show `caller_agent=SupportAgent` on API events and the agent-to-agent traffic. Do not fake the caller: it makes real calls.

## 7. SAMPLE_MODE for the API

`SAMPLE_MODE=true` means: novel-source JSON samples are read from `input_examples/samples/` instead of fetched from the internet. It does **not** skip Zetaris or Nightshift and never returns saved answers. If credentials are missing the API returns a structured `failed` response, not an invented result.

## 8. Acceptance criteria

- [ ] `curl -X POST /run` with the IBM scenario returns the contract above with all stage statuses.
- [ ] Two concurrent `/run` calls do not interleave Zetaris statements (trace timestamps prove serial builder execution).
- [ ] `/models` lists the verified model; `/ask` answers a follow-up using the reader identity (or reports `builder_fallback`) and a rejected SQL (`DROP`, other tables) returns 400 with the guard message.
- [ ] `GrantService` records an honest outcome; the response shows it.
- [ ] Missing/invalid bearer token → 401. Zetaris down → structured `failed` with kind `unreachable`.
- [ ] `caller_demo/support_agent.py` runs end to end and its calls show up in Pipeless's logs with `caller_agent`.
