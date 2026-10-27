# Pipeless

A local prototype that investigates data questions and returns SQL with an evidence trail. Built for Open Agent Hackathon 2026, track **The Agent That Can Explain Why**.

## Workflow

A Planner defines the question. A deterministic onboarding service inspects or registers allowed sources in Zetaris. An Investigator writes read-only SQL. Python checks collect evidence, then a separate Skeptic model challenges the answer and requests revisions. The Planner returns an answer with caveats. Local hierarchical memory stores outcomes and retrieves related lessons.

There are three model-driven roles: Planner, Investigator and Skeptic. The onboarding service is a tool service.

## Sponsor integration

- **Zetaris** executes data queries and audit probes. The live demonstration uses its installed `NY_TAXI.taxi_zones` and `NY_TAXI.zones` sample tables.
- **Meterless Nightshift** provides the local OpenAI-compatible model gateway. The NVIDIA provider key stays in Nightshift. Pipeless uses a Nightshift runtime token.
- **NVIDIA Nemotron** supplies the models. Tested IDs are `nvidia/nemotron-3.5-lightning-30b-a3b` and `nvidia/nemotron-3-ultra-550b-a55b`. Traces record each response's reported model.
- `app/memory/hmem.py` implements local hierarchical storage and retrieval inspired by H-MEM. This is a project implementation, not complete conformance to every Meterless reference capability.

## What the checks prove

The join audit measures distinct non-null source keys and matched keys. It checks actual query-output retention rather than trusting a `LEFT JOIN` keyword. Filters and limits can discard keys. Matching IDs does not establish semantic equivalence, row-level preservation or one-to-one cardinality.

Date-column discovery does **not** verify date coverage. Coverage remains `unverified` when date values, expected ranges and gaps have not been measured. Other checks cover result truncation, heuristic string-number risks and summary row-count discrepancies. Source reconciliation requires source JSON and a declared count path.

## Local setup

Requires Python 3.11+, running Zetaris and Meterless Nightshift with an enabled NVIDIA provider. Use the official distributions. Create Zetaris API tokens in its platform UI and obtain the Nightshift runtime token locally.

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Fill the private `.env` with endpoints and tokens. Never commit it. For the installed-sample operator demo, leave `ZETARIS_READER_API_KEY` empty to use the builder identity for read-only operations. This does not demonstrate least-privilege isolation. Verify a configured reader identity separately.

```powershell
.venv\Scripts\python.exe -m scripts.check_zetaris_ready
.venv\Scripts\python.exe -m scripts.check_nightshift
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000
```

The local evidence viewer is `http://127.0.0.1:8000/demo`. It displays saved run outputs. Refresh does not start an audit or call a model.

## Reproducing the demonstration

With the installed taxi samples available:

```powershell
.venv\Scripts\python.exe -m scripts.run_live_demo --mode audit
.venv\Scripts\python.exe -m scripts.run_live_demo --mode repair-evaluation
.venv\Scripts\python.exe -m scripts.run_live_demo --mode followup
```

These execute real queries and model calls and can take several minutes. Outputs go to private `data/demo/` and traces to `logs/`. The repair evaluation deliberately seeds a disclosed flawed query that drops LocationID 1 through a filter. The model repairs it and the verifier rechecks the output. Do not present the seeded query as a naturally generated model failure. The autonomous audit uses model-generated SQL throughout.

The local harness uses the existing builder identity for read-only SQL, performs no grants and makes no reader-role claim. The HTTP API exposes `/health`, `/run`, `/runs/{run_id}`, `/models` and `/ask`. Inspect `/docs` for schemas. Set `PIPELESS_API_TOKEN` and bind to loopback for local use.

## Current limits

The sample-data demo does not establish production value, complete data quality, semantic compatibility or a production incident. SEC/company-dns onboarding requires a real authorized contact email in the User-Agent and the official Zetaris getting-started recipe repository. The earlier illustrative IBM/SIC story is not a measured result of this installation.

Novel-source onboarding, reader-role isolation and all API paths have not yet received a complete live acceptance test. SQL validation and some type/summary checks are heuristics. A `verified` pipeline status means implemented blocking checks passed and the Skeptic approved, not that every natural-language claim has been proved. Inspect the saved evidence and caveats.

Keep private tokens, installation bundles, environment backups, logs and raw memory out of public repositories. Declare pre-existing code in the final submission according to the event rules.
