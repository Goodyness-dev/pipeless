# PIPELESS — 10 LOGS, TESTS, DOCKER, README, DEMO, SUBMISSION, DAY PLAN

> Prerequisites: files 01–09. This file covers everything that makes the project **scoreable and reproducible**: evidence in `logs/`, tests, the anti-fake audit, Docker, README, demo script, submission checklist, and the build order.

---

## 1. Timeline (be careful with time zones)

- Build window: 22–27 October 2026. Today (24 October) is Day 3. The guide says: submit **well before 23:45 UTC on 27 October**.
- Lagos is UTC+1, so 23:45 UTC on the 27th is **00:45 on the 28th in Lagos**. Treat **the evening of 26 October (Lagos)** as the real internal deadline for the final audit, and submit on the 27th with a large safety margin.

## 2. What the logs must show (judges verify real work from these)

Each run writes `logs/<trace_id>.jsonl` (file 02 `Tracer`). A scoreable trace contains, in order:
1. `plan_created` (PlannerAgent) and a `handoff` to OnboarderAgent.
2. `memory_retrieve` events (with item ids and scores).
3. `policy_decision` events, one per statement, and `approval_requested` in manual mode.
4. `zetaris_statement` per statement (one per call) and `zetaris_error` / `zetaris_retry` / `repair_attempt` when something fails.
5. `llm_call` for every model call, with `lane`, `model_requested`, `model_returned`.
6. `check_result` per deterministic check.
7. `skeptic_verdict` (with findings), `revision_requested`, `revision_applied`, then a second `skeptic_verdict`.
8. `final_answer` with the stage statuses.
Weak pattern to avoid: "Agent1 ran / Agent2 ran / Done".

Also keep `data/memory/audit.jsonl` and `data/ledger.jsonl`. Copy representative JSONL files from a real run into the submission (they are evidence; they must contain **no secrets**).

## 3. Tests (`tests/`, run with `pytest`)

Tests may use small recorded fixtures **for testing only**; runtime code must never read them.

| Test file | What it proves |
|---|---|
| `test_splitter.py` | `COMPILE USL` with three `CREATE TABLE`s stays one statement; `;` inside quotes does not split; `--` and `/* */` comments dropped; trailing `;` handling |
| `test_classify.py` | statement kinds and targets (`CREATE LIGHTNING REST TABLE a FROM b`, `CACHE TABLE db.t`, `ACTIVATE USL TABLE ...`) |
| `test_playbook.py` | each playbook entry classifies a representative error text (429, 502, `UNRESOLVED_COLUMN`, `MISSING_ATTRIBUTES...`, `already exists`) |
| `test_guard.py` | rejects `DROP`, multi-statement, non-SELECT, tables outside the allowlist; accepts a safe SELECT with a string literal containing `;` |
| `test_extract_json.py` | fenced JSON, `<think>` prefix, trailing prose, braces inside strings |
| `test_tracing.py` | `redact` hides key-like fields; JSONL lines are valid |
| `test_policy.py` | destructive statement on a non-ledger object blocked; allowlisted create allowed; manual mode needs approval |
| `test_ddl.py` | `safe_desc` strips `( ) / :`; unsafe identifiers raise |
| `test_lock.py` | two threads' builder statements never overlap (use a fake client **in the test only**) |
| `test_verifier_logic.py` | join-loss decision table: `left_total != matched` and draft hides unmatched → fail; draft surfaces unmatched → warn |
| `test_loop.py` | with fake agents in the test, the orchestrator performs revise → second verdict and enforces `MAX_REVISIONS` and the blocking-failure rule |
| `test_hmem.py` | add → promote → restart → retrieve returns the lesson; no memory exists before a run; audit lines written |
| `test_api_contract.py` | 401 without token; invalid body → 422; `/ask` with a `DROP` → 400 |

Live (not unit) checks, run manually and saved as screenshots/logs for the README: `probe_zetaris.py`, `check_nightshift.py`, an end-to-end IBM run, a novel-source run, the three-session demo.

## 4. Anti-fake audit (`scripts/audit_hardcoding.sh`)

```bash
#!/usr/bin/env bash
set -e
echo "== suspicious returns =="; grep -Rn "return {" app/ run.py || true
echo "== output_examples referenced at runtime =="; grep -Rn "output_examples" app/ run.py && echo "FAIL" || echo "ok"
echo "== hard-coded demo values =="; grep -RniE "ibm|3570|0000051143" app/ run.py && echo "REVIEW: only allowed in docs/tests" || echo "ok"
echo "== secrets =="; grep -RniE "(api[_-]?key|token|password)\s*=\s*['\"][A-Za-z0-9_\-]{12,}" app/ run.py scripts/ caller_demo/ && echo "FAIL" || echo "ok"
```

Run the Cursor audit prompt from the starter guide as well:

```text
Audit this codebase for hardcoded outputs or fake functionality.
Look for: final answers stored in code; sample outputs returned at runtime; functions that pretend to call a model, Zetaris or Meterless; agents that ignore their input; if/else branches matching known test prompts; logs not tied to actual agent execution.
Return a table with file, line, risk and fix.
```

Also: run two unrelated questions and confirm outputs differ meaningfully.

## 5. Docker

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /app/logs /app/data
EXPOSE 8000
CMD ["python", "run.py"]
```

```bash
docker build -t pipeless .
docker run --rm -p 8000:8000 --env-file .env -v "$PWD/logs:/app/logs" -v "$PWD/data:/app/data" pipeless
```

Container networking gotchas (write them in the README):
- Zetaris and Nightshift run on the **host**. Inside Docker, `localhost` is the container. Set `ZETARIS_REST_URL=http://host.docker.internal:8888/api/v1.0` and `METERLESS_BASE_URL=http://host.docker.internal:8088/v1` (on Linux add `--add-host=host.docker.internal:host-gateway`).
- The Zetaris repo clone (`ZETARIS_REPO_PATH`) must be mounted: `-v "$PWD/../getting-started:/getting-started:ro"` and `ZETARIS_REPO_PATH=/getting-started`.
- Never copy `.env` into the image (`.dockerignore` it).

## 6. README (use the HackOS "README Template"; fill it with truth)

Must include: track and problem; what Pipeless does; the four agents and the feedback loop; **where Zetaris sits** (adapter, onboarding, verification, identities); **where Meterless sits** (Nightshift for all LLM calls with the two lanes; H-MEM in agent prompts); how Cursor was used; NVIDIA Nemotron lanes and the evidence (`model_returned` in logs); setup; usage with request/response examples; log format; sample mode; limitations; future work.

**Honest limitations to list:** USL models come from catalog recipes only; USL materialization not used (unverified in docs); DQ may time out (reported as unverified); V3 `company_dns` is deprecated and may disappear; reader-access after `GRANT` was/was not confirmed (state which); H-MEM dreaming/sleep not built; model ids depend on the local Nightshift; DONKI/PUDL/etc. known-to-fail sources avoided.

Run the starter guide's README audit: *"Compare README.md against the actual codebase. Flag features claimed but not implemented, run commands that will fail, missing environment variables, inaccurate agent descriptions, and any sponsor integration that is described but not real. Return exact edits."*

**`metadata.json`:** download the "Metadata json Template" from HackOS and fill it exactly; do not invent the schema. Name the Meterless engine (H-MEM), where it appears, and where Zetaris sits, so judges can score Sponsor Tech.

## 7. Build order (give this to the coding agent)

| Order | Task | Files | Done when |
|---|---|---|---|
| 1 | Skeleton, config, tracing, tests for tracing | 02 | `Config.load()` ok; JSONL written |
| 2 | Zetaris probe + client + runner + splitter/classify/playbook/guard | 03 | `probe_zetaris.py` summary written; unit tests pass |
| 3 | Nightshift client + `check_nightshift.py` | 04 | PASS line with returned model ids |
| 4 | Recipe loader + catalog + policy + ledger/registry | 06 | `company_dns_v3_raw` onboarded statement by statement |
| 5 | Verifier checks + `usl.py` | 07 | `join_loss` returns real numbers on `sic_edgar_usl` |
| 6 | Agents + orchestrator (feedback loop) | 05 | IBM scenario shows revise → revision → approve |
| 7 | H-MEM minimum (store + retrieval + injection) | 08 | lesson written and retrieved across two runs |
| 8 | API `/run`, `/models`, `/health`, run.py | 09 | curl returns the contract |
| 9 | Novel REST path + repair loop | 06 | held-out source onboarded with a logged repair |
| 10 | `/ask`, reader identity, grants | 09 | follow-up answered; honest grant status |
| 11 | Caller demo agent; three-session script | 08/09 | real agent-to-agent trace |
| 12 | Docker, README, examples, audit, demo video | 10 | fresh-clone run works |

**Cut line:** if time runs short, drop in this order: dreaming/sleep (never started), `/approve`, grants and reader fallback polish, three-session script polish. **Never cut:** two-agent feedback loop, real Zetaris calls, Nightshift for all LLM calls, one working H-MEM feature, logs, README honesty.

### Suggested days
- **Sat 24 (today):** steps 1–3, get Zetaris and Nightshift probes passing; read the Official Rules, Technical Execution Requirements and Disqualification Checklist on HackOS.
- **Sun 25:** steps 4–8; first end-to-end IBM run.
- **Mon 26:** steps 9–11; Docker; generate three real output examples (`curl ... > output_examples/example_N.json`); audit; README; record the demo.
- **Tue 27 (Lagos morning/afternoon):** fresh-clone test, secrets check, submit with a large margin before the UTC deadline.

## 8. Input and output examples

Create **three** inputs in `input_examples/`:
1. `example_1.json` — catalog scenario `sic_edgar_usl` (the IBM question).
2. `example_2.json` — a novel public REST source (held out; not in the catalog).
3. `example_3.json` — `/ask`-style follow-up or a second novel source with a different shape.

Generate outputs from **real runs** only:
```bash
curl -X POST http://localhost:8000/run -H "Authorization: Bearer $PIPELESS_API_TOKEN" \
  -H "Content-Type: application/json" -d @input_examples/example_1.json > output_examples/example_1.json
```
Runtime code must never read `output_examples/`.

## 9. Demo video script (1–4 minutes; use the HackOS Demo Script Template and Demo Video Guide)

| Time | Beat | Show on screen |
|---|---|---|
| 0:00–0:20 | Problem: agents give confident, unaudited data answers; each agent needs hand-written data plumbing | one slide, no jargon |
| 0:20–0:45 | Pipeless: an agent for other agents; Zetaris underneath, Nemotron through Nightshift | architecture slide; Nightshift lanes with Nemotron; keys hidden |
| 0:45–1:30 | SupportAgent asks a question; Pipeless onboards the sources statement by statement (dry-run + policy visible) | terminal/logs: `policy_decision`, `zetaris_statement` |
| 1:30–2:20 | **The IBM moment:** first answer looks clean → join-loss check fails (7 in, 6 out) → Skeptic names the missing record → revision → final answer with caveats | `check_result`, `skeptic_verdict`, `revision_applied` lines, final JSON |
| 2:20–2:50 | Memory: second source onboarded with fewer repairs; show the retrieved lesson id | `memory_retrieve` event; comparison table |
| 2:50–3:20 | A second agent question answered via `/ask` on the left-behind verified model (reader identity) | `/models`, `/ask` result with `identity_used` |
| 3:20–3:40 | Honest limits and what is next | slide |

Rules: hide all keys and tokens; show real output; do not narrate numbers that the logs do not show.

## 10. Final checklist (from the starter guide's local checklist, plus ours)

**Repository:** entry point documented; `requirements.txt`, `.env.example`, README; ≥ 3 inputs and ≥ 3 real outputs; no real `.env` or keys committed; repository **public**.
**Sponsor tech:** Zetaris integrated with a real role; Meterless integrated (Nightshift + H-MEM) with a real role; developed in Cursor (confirm against the Technical Execution Requirements and AI Usage Policy); NVIDIA Nemotron visible in logs.
**Agents:** ≥ 2 distinct agents (we have 4); ≥ 1 feedback loop; evaluator critiques another agent's work; output revised or approved based on the critique.
**Logs:** in `logs/` and stdout; agent names, actions, handoffs, statuses; tied to the actual request.
**Anti-hardcoding:** no prewritten final answers; `output_examples/` not read at runtime; `SAMPLE_MODE` uses sample inputs only; different inputs give meaningfully different outputs.
**Submission:** demo video 1–4 min; slide deck (HackOS Pitch Deck Template); sponsor-technology explanation; pre-existing components declared; fresh-clone test passes; secrets scan clean.

Final audit prompt (from the guide), run in Cursor:
```text
Perform a final Open Agent Hackathon readiness audit. Check: the app starts from a clean clone with documented commands; the entry point works; Zetaris and Meterless are integrated, not mocked; .env.example is present and no real keys are committed; logs show agent collaboration; no hardcoded outputs; at least 3 input and 3 output examples; README commands match the code; sample mode works with no credentials.
Return PASS or FAIL for each item, with the exact fix for each failure.
```

## 11. Hand-off message for the human to paste to the coding agent at each step

"Implement step N from file 10 section 7. Follow the matching spec file exactly. Run it against the real Zetaris and Nightshift. Show me the command, the real output and the log lines. If the spec says UNKNOWN, write the probe first. Do not mock, do not hard-code, do not touch other steps."
