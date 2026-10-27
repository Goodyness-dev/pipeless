# PIPELESS — 01 PROJECT OVERVIEW (READ THIS FIRST)

> You are the coding agent. These 10 files are your complete specification. Read all of them, in order, before writing any code. Where a file says **UNKNOWN**, do not guess: write the small probe script the file describes, run it against the real service, and record what you learn.

---

## 0. How to use these 10 files

| File | Content | Build priority |
|---|---|---|
| 01 | This file: what we build, rules, facts, unknowns | read first |
| 02 | Repo structure, config, `.env`, tracing | P0 |
| 03 | Zetaris adapter (REST client, statement runner, error playbook, SQL guard) | P0 |
| 04 | Meterless Nightshift adapter (OpenAI-compatible LLM client, lanes) | P0 |
| 05 | Agents: Planner, Onboarder, Investigator, Skeptic + feedback loop | P0 |
| 06 | Onboarding engine: catalog recipes, novel REST sources, policy/approval | P0 (catalog) / P1 (novel) |
| 07 | Verification: deterministic checks, USL lifecycle, status model | P0 |
| 08 | H-MEM memory engine (the required Meterless Engine) | P0 minimum, P1 full |
| 09 | HTTP API for other agents, identities, reader `/ask`, caller demo agent | P0 `/run`, P1 rest |
| 10 | Logs, tests, Docker, README, demo script, day-by-day plan | P0 |

**First message to give the coding agent:**
"Read the ten spec files in order. Implement in the order given by file 10, section 7. After each module, run it against the real Zetaris and Nightshift and show me the output. Never mock a sponsor service. Never hard-code an answer. Where a spec file says UNKNOWN, write the probe script first."

---

## 1. What Pipeless is, in plain words

Imagine a company running several AI agents: a support agent, a finance agent, a sales agent. Every one of them needs company data. Today each agent would need hand-written plumbing to reach that data, and nobody checks whether the answers are right.

**Pipeless is an agent that other agents call.** A calling agent sends one request: a data question plus the sources it cares about. Pipeless then:

1. **Plans** what is needed.
2. **Onboards** the sources into Zetaris (registers tables, flattens JSON into views, caches rate-limited sources), fixing its own mistakes using the error messages Zetaris returns. Everything it creates passes a dry-run and policy check first.
3. **Investigates**: writes and runs the SQL that answers the question, on a read-only identity.
4. **Verifies** with deterministic code checks (row counts against the source, joins that silently drop records, truncated results, numeric-looking strings, partial date coverage).
5. **Skeptic**: a separate agent on a separate Nemotron model lane challenges the answer using the evidence from step 4. If it finds a problem, the Investigator revises. This is a real feedback loop.
6. **Replies** with a structured answer plus receipts: the SQL, the checks, what the Skeptic challenged, and the status of each stage (kept separate on purpose).
7. **Leaves something behind**: a verified model registered in a model registry, with read access granted to a read-only role, so the next agent reuses it instead of starting over. **H-MEM** (Meterless memory engine) remembers what worked and what failed.

### The headline demo (grounded in Zetaris's own recorded test)
Zetaris's USL catalog records that joining EDGAR company profiles to the SIC reference list with an INNER JOIN silently drops IBM (SIC code 3570 is missing from the 1,005-row reference list), giving 6 rows instead of 7. A foreign-key data-quality rule catches it. Pipeless's demo: the first answer looks clean, the deterministic join-loss check notices 7 went in and 6 came out, the Skeptic confirms and names the missing record, the final answer says "6 matched; IBM's SIC code is not in the reference list." **This is historical output from the docs. Verify it on our own instance before relying on it.**

---

## 2. Non-negotiable rules (from the hackathon starter guide)

**Required in the submission:** Zetaris integrated for real; Meterless integrated for real (Nightshift as primary AI provider, plus at least one Meterless Engine in a working feature); project developed in Cursor; NVIDIA Nemotron through Nightshift if we want the NVIDIA award.

**Not allowed (disqualification risks):**
- Hard-coded outputs or fake agent responses
- A single prompt wrapped as several "agents"
- A linear A → B → C pipeline with no feedback loop
- Mocked Zetaris or Meterless integrations presented as real
- API keys committed to GitHub
- Manual setup steps judges cannot reproduce

**Therefore, in code:**
- Every agent must call the model (Nightshift) with its own role prompt and its own input. No agent returns canned text.
- The Skeptic-to-Investigator revision loop must actually run, branch, and be visible in logs.
- `SAMPLE_MODE=true` may only load sample **input** data (for example a saved JSON source response used for shape inference). It must never load prewritten final answers, and it must never pretend Zetaris or Nightshift were called. If credentials are missing, return a structured error.
- `output_examples/` is written by real runs and is never read by runtime code.
- Secrets live only in `.env`. `.env` is in `.gitignore`. Only `.env.example` is committed.

> **Tooling note for the human:** the rules say the project must be developed in Cursor. Other assistants may be used alongside it for coding help. Open this project in Cursor and use its Agent for at least the run/validate/audit steps in file 10, and confirm the Technical Execution Requirements and AI Usage Policy on HackOS before submitting.

---

## 3. Ground-truth facts from the Zetaris docs (treat as true, but verify on our instance)

1. Everyone runs their own Zetaris: local Docker (UI `http://localhost:3000`, REST `http://localhost:8888/api/v1.0`, JDBC `jdbc:zetaris:lightning@localhost:10000`) or a managed AWS cluster.
2. REST call: `POST {ZETARIS_REST_URL}/sql-editor/sqls/run` with headers `Authorization: Bearer <API key>`, `X-Org-ID: <numeric org id, 1 on local>`, `X-Request-ID: <fresh UUID>`; JSON body `{"queryId": "<uuid>", "sql": "...", "source": "SqlEditor", "limit": N}`; response JSON has a `data` array of rows. **REST returns values as strings; decimals may use exponent format.**
3. **One SQL statement per call.** A multi-table `COMPILE USL ... DDL` must be sent as one statement (semicolons inside belong to the payload).
4. API keys are created by a human in the UI (**Management → API Tokens**), shown once, carry that user's permissions. **Never** have the agent create or fetch keys. Up to three keys per user.
5. Permissions: registering sources, `COMPILE/ACTIVATE USL`, `REGISTER DQ`, `CREATE SCHEMASTORE CONTAINER`, drops are **Administrators only**. Creating schema store views: admins or Query Builder Write users. Reading: signed-in users, limited to assigned objects. Least-privilege recommendation: builder = admin identity; readers = their own non-admin users via a role with SELECT.
6. Safety rules in Zetaris's own `AGENTS.md`/client guide: start read-only (`SHOW`, `DESCRIBE`, `SELECT`); dry-run before creating; wait for go-ahead; never print credentials.
7. A Lightning REST table **re-fetches its endpoint on every uncached query**. Immediately run `CACHE TABLE <db>.<raw_table>` after creating a rate-limited REST table. A cached count query dropped from 49.7 s to about 1.2 s in the docs.
8. `company_dns` rate limit depends on User-Agent: identified callers (`app_name user@domain.tld`) get 6,000/min (burst 600); anonymous get 600/min (burst 60) in the event; service default is 5/min (burst 1). After HTTP 429: wait 60 s and retry **that one statement**.
9. `CREATE SCHEMASTORE CONTAINER` has **no** `IF NOT EXISTS`. `COMPILE USL ... IF NOT EXISTS` does not update an existing model. `ACTIVATE USL TABLE` is not verified as repeatable. `lightning.metastore` must exist before child namespaces.
10. `CREATE LIGHTNING DATABASE ... DESCRIBE BY "..."` accepts only letters, digits, spaces, `_ . - ,`.
11. JSON gotchas: backtick mixed-case / reserved / hyphenated field names; numeric-looking strings must be cast; dynamic-key objects need `from_json(to_json(x), 'map<string, struct<...>>')`; reading the same exploded UNION view twice in one query causes `MISSING_ATTRIBUTES.RESOLVED_ATTRIBUTE_APPEAR_IN_OPERATION` (use window functions).
12. Known to fail (never demo): PUDL, Foursquare, Overture analytical queries, Ookla, GBIF, Singapore PM2.5, NASA DONKI.
13. Treat "registered", "rows readable" and "DQ completed" as three separate outcomes. DQ run did not complete in the recorded EDGAR/PUDL/NOAA run; USL materialization is unverified.
14. Meterless Nightshift: OpenAI-compatible. `GET /v1/models`, `POST /v1/chat/completions`, `Authorization: Bearer <runtime token>`, headers `meterless-app: <name>` and `meterless-scope: user`. `meterless/auto` is a routing mode, not a Nemotron model id. The NVIDIA key lives only inside Nightshift's Providers tab, never in our app.
15. Meterless Engines (H-MEM, Markovian, World Model, Scout Intent) are **reference architectures**, not a pip package. We implement them. Official ready-made implementation prompts exist at `meterless.ai/engines.html`.

---

## 4. UNKNOWN items (write probes, do not guess)

| # | Unknown | Probe |
|---|---|---|
| U1 | Exact JSON shape of Zetaris REST success response (column names? schema key?) and error responses (HTTP status vs 200 with error body) | `scripts/probe_zetaris.py` (file 03) |
| U2 | Whether a trailing `;` is accepted | probe sends both forms |
| U3 | Whether a non-admin reader user can `SELECT` a USL table / schemastore view after `GRANT` | `GrantService` records outcome (file 09) |
| U4 | Whether `ACTIVATE USL TABLE` works from V4 `company_dns` data | not needed for P0; do not claim |
| U5 | Which Nemotron model IDs our Nightshift exposes | `scripts/check_nightshift.py` (file 04) |
| U6 | Whether Nightshift honors `response_format` JSON mode | client falls back to robust JSON extraction |
| U7 | Required schema of `metadata.json` | download "Metadata json Template" from HackOS; never invent the schema |
| U8 | Whether `RUN DQ` needs a rule name | verifier tries both forms (file 07) |
| U9 | Whether `LEFT JOIN ... IS NULL` anti-join works across USL tables | verifier test; fall back to `NOT EXISTS`/`NOT IN` |

---

## 5. Scoring map (so effort goes where points are)

Technical 20, Innovation 15, Demo 15, Impact 30, Sponsor Tech 10 (per the Meterless index doc). We earn:
- **Technical:** real two-identity Zetaris integration, deterministic verification, real feedback loop, JSONL traces.
- **Innovation:** Skeptic on a separate Nemotron lane; self-repairing onboarding; agent-to-agent API.
- **Demo:** the IBM moment; three-session H-MEM learning curve; caller agent asking Pipeless.
- **Impact:** a clear user (a team running multiple agents on shared company data); measured time-to-onboard and correctness checks.
- **Sponsor tech:** README and `metadata.json` list exactly where Zetaris (adapter, onboarding, verification), Meterless Nightshift (all LLM calls) and Meterless H-MEM (memory injected into agent prompts) sit.

---

## 6. Glossary

- **Lightning database:** container for file/REST tables (`CREATE LIGHTNING DATABASE`).
- **REST table / filestore table:** a registered API response / file.
- **SchemaStore container + view:** folder + flattening view over raw tables.
- **USL (Unified Semantic Layer):** declared tables, keys and relationships; lifecycle: namespace → compile → activate → query → DQ.
- **DQ:** data-quality rules; PK/FK constraints generate rules.
- **Builder identity:** admin API key. **Reader identity:** non-admin API key.
- **Lane:** a model assignment in Nightshift (fast worker, deep worker, judge).

---

## 7. Definition of done for the whole project

- [ ] `python run.py` starts the API; `POST /run` works end to end on the catalog IBM scenario and on one held-out source never rehearsed.
- [ ] Logs show all four agents, at least one Skeptic critique and one revision.
- [ ] Every LLM call goes through Nightshift; the model actually used is logged per call.
- [ ] H-MEM retrieval trace appears in prompts for Onboarder/Investigator/Skeptic; three-session demo reproducible.
- [ ] Separate statuses returned: `onboarding`, `rows_readable`, `deterministic_checks`, `skeptic`, `dq`.
- [ ] Docker build works; README matches the code; three real input/output examples saved.
