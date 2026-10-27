# PIPELESS — 02 REPO STRUCTURE, CONFIG, ENV, TRACING

> Prerequisite: file 01. This file defines the skeleton every other file plugs into. Create it first.

---

## 1. Folder structure (create exactly this)

```
pipeless/
├── run.py                          # entry point: starts FastAPI on :8000
├── requirements.txt
├── Dockerfile
├── .env.example                    # placeholders only (committed)
├── .gitignore                      # includes .env, data/, logs/*.jsonl
├── README.md
├── app/
│   ├── __init__.py
│   ├── config.py                   # this file
│   ├── tracing.py                  # this file
│   ├── util.py                     # this file
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── zetaris.py              # file 03: REST client + statement runner
│   │   └── meterless.py            # file 04: Nightshift LLM client
│   ├── zsql/
│   │   ├── __init__.py
│   │   ├── splitter.py             # file 03
│   │   ├── classify.py             # file 03
│   │   ├── playbook.py             # file 03
│   │   └── guard.py                # file 03
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── schemas.py              # file 05
│   │   ├── base.py                 # file 05
│   │   ├── planner.py
│   │   ├── onboarder.py
│   │   ├── investigator.py
│   │   ├── skeptic.py
│   │   └── orchestrator.py         # file 05
│   ├── onboarding/
│   │   ├── __init__.py
│   │   ├── catalog.py              # file 06
│   │   ├── recipe_loader.py
│   │   ├── policy.py
│   │   ├── ddl.py
│   │   ├── novel_rest.py
│   │   ├── repair.py
│   │   └── registry.py
│   ├── verify/
│   │   ├── __init__.py
│   │   ├── checks.py               # file 07
│   │   ├── usl.py
│   │   └── evidence.py
│   ├── memory/
│   │   ├── __init__.py
│   │   └── hmem.py                 # file 08
│   └── api/
│       ├── __init__.py
│       ├── main.py                 # file 09
│       ├── schemas.py
│       └── grants.py
├── caller_demo/
│   └── support_agent.py            # file 09: a real calling agent for the demo
├── scripts/
│   ├── probe_zetaris.py            # file 03
│   ├── check_nightshift.py         # file 04
│   ├── audit_hardcoding.sh         # file 10
│   └── three_session_demo.py       # file 08/10
├── tests/                          # file 10
├── data/                           # gitignored: memory/, registry.json, ledger.jsonl, recipes_sha.txt
├── logs/                           # JSONL traces (judges read these)
├── input_examples/                 # at least 3
└── output_examples/                # at least 3 REAL outputs (never read by runtime code)
```

Python 3.11. Use type hints everywhere. Keep modules small and readable.

---

## 2. `requirements.txt`

```text
fastapi>=0.110
uvicorn[standard]>=0.29
pydantic>=2.6
requests>=2.31
openai>=1.30
python-dotenv>=1.0
pytest>=8.0
```

(Do not add heavy frameworks. No LangChain. We implement agents ourselves so judges can read the collaboration in the logs.)

---

## 3. `.env.example` (placeholders only)

```bash
# ---------- Zetaris ----------
ZETARIS_REST_URL=http://localhost:8888/api/v1.0
ZETARIS_ORG_ID=1
# Created by the HUMAN in the Zetaris UI: Management -> API Tokens. Never ask an agent to create these.
ZETARIS_BUILDER_API_KEY=your-admin-user-api-key
ZETARIS_READER_API_KEY=your-non-admin-user-api-key
# Required form: app_name user@domain.tld  (a contact email is mandatory or the strict rate limit applies)
ZETARIS_USER_AGENT=pipeless you@example.com
ZETARIS_QUERY_LIMIT=1000
ZETARIS_TIMEOUT_SECONDS=120
# Local clone of https://github.com/zetaris/getting-started (recipes are read from here)
ZETARIS_REPO_PATH=../getting-started

# ---------- Meterless Nightshift (primary AI provider) ----------
METERLESS_BASE_URL=http://127.0.0.1:8088/v1
METERLESS_RUNTIME_TOKEN=your-nightshift-runtime-token
METERLESS_APP_NAME=pipeless
# Pin real model ids discovered via GET /v1/models (see scripts/check_nightshift.py)
MODEL_FAST=nvidia/nemotron-3.5-lightning-30b-a3b
MODEL_DEEP=nvidia/nemotron-3.5-super-vl-120b-a12b

# ---------- Pipeless runtime ----------
PIPELESS_API_TOKEN=choose-a-long-random-string
APPROVAL_MODE=auto_policy        # auto_policy | manual
MAX_REVISIONS=2
MAX_LLM_CALLS_PER_RUN=40
SAMPLE_MODE=false
LOG_DIR=logs
DATA_DIR=data
```

> The model ids above are examples from the setup guide, not guaranteed. The real ids come from `GET /v1/models`.

`.gitignore` must contain: `.env`, `data/`, `logs/*.jsonl`, `__pycache__/`, `.venv/`.

---

## 4. `app/config.py`

```python
from __future__ import annotations
import os
import re
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# "app_name user@domain.tld" -- a bare app name or no contact email gets the strict rate limit.
UA_PATTERN = re.compile(r"^\S+\s+[^@\s]+@[^@\s]+\.[^@\s]+$")


class ConfigError(RuntimeError):
    pass


def _env(name: str, default: str | None = None, required: bool = False) -> str | None:
    value = os.getenv(name, default)
    if required and not value:
        raise ConfigError(f"{name} is missing from .env")
    return value


@dataclass(frozen=True)
class Config:
    # Zetaris
    zetaris_rest_url: str
    zetaris_org_id: str
    zetaris_builder_key: str | None
    zetaris_reader_key: str | None
    user_agent: str
    user_agent_valid: bool
    zetaris_timeout_s: int
    query_limit: int
    statement_gap_s: float          # 0 when identified, 15 when not (docs)
    repo_path: Path
    # Nightshift
    llm_base_url: str
    llm_api_key: str
    llm_app_name: str
    model_fast: str
    model_deep: str
    # Runtime
    api_token: str | None
    approval_mode: str
    max_revisions: int
    max_llm_calls_per_run: int
    sample_mode: bool
    log_dir: Path
    data_dir: Path

    @staticmethod
    def load() -> "Config":
        ua = _env("ZETARIS_USER_AGENT", "", False) or ""
        ua_valid = bool(UA_PATTERN.match(ua))
        cfg = Config(
            zetaris_rest_url=_env("ZETARIS_REST_URL", "http://localhost:8888/api/v1.0"),
            zetaris_org_id=_env("ZETARIS_ORG_ID", "1"),
            zetaris_builder_key=_env("ZETARIS_BUILDER_API_KEY"),
            zetaris_reader_key=_env("ZETARIS_READER_API_KEY"),
            user_agent=ua,
            user_agent_valid=ua_valid,
            zetaris_timeout_s=int(_env("ZETARIS_TIMEOUT_SECONDS", "120")),
            query_limit=int(_env("ZETARIS_QUERY_LIMIT", "1000")),
            statement_gap_s=0.0 if ua_valid else 15.0,
            repo_path=Path(_env("ZETARIS_REPO_PATH", "../getting-started")).expanduser(),
            llm_base_url=_env("METERLESS_BASE_URL", "http://127.0.0.1:8088/v1"),
            llm_api_key=_env("METERLESS_RUNTIME_TOKEN", ""),
            llm_app_name=_env("METERLESS_APP_NAME", "pipeless"),
            model_fast=_env("MODEL_FAST", ""),
            model_deep=_env("MODEL_DEEP", ""),
            api_token=_env("PIPELESS_API_TOKEN"),
            approval_mode=_env("APPROVAL_MODE", "auto_policy"),
            max_revisions=int(_env("MAX_REVISIONS", "2")),
            max_llm_calls_per_run=int(_env("MAX_LLM_CALLS_PER_RUN", "40")),
            sample_mode=(_env("SAMPLE_MODE", "false") or "false").lower() == "true",
            log_dir=Path(_env("LOG_DIR", "logs")),
            data_dir=Path(_env("DATA_DIR", "data")),
        )
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        (cfg.data_dir / "memory").mkdir(parents=True, exist_ok=True)
        return cfg

    def require_builder(self) -> str:
        if not self.zetaris_builder_key:
            raise ConfigError("ZETARIS_BUILDER_API_KEY is missing from .env")
        return self.zetaris_builder_key

    def require_llm(self) -> None:
        if not self.llm_api_key:
            raise ConfigError("METERLESS_RUNTIME_TOKEN is missing from .env")
        if not (self.model_fast and self.model_deep):
            raise ConfigError("MODEL_FAST and MODEL_DEEP must be set (see scripts/check_nightshift.py)")
```

Rules for the coding agent:
- Validate config at startup and log a **redacted** summary (never print keys).
- If `user_agent_valid` is false, log a loud warning: the strict rate limit will apply and statement spacing falls back to 15 s.

---

## 5. `app/util.py`

```python
from __future__ import annotations
import hashlib
import json
import re
import time
import uuid

_SECRET_KEYS = re.compile(r"(key|token|password|secret|authorization)", re.I)


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def summarize(value, limit: int = 300) -> str:
    """Short, log-safe summary of any value."""
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = re.sub(r"\s+", " ", text)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def redact(obj):
    """Recursively replace secret-looking dict values with '***'."""
    if isinstance(obj, dict):
        return {k: ("***" if _SECRET_KEYS.search(str(k)) else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


def to_number(text):
    """Zetaris REST returns strings; decimals may be in exponent form."""
    if text is None:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


class Stopwatch:
    def __init__(self):
        self.t0 = time.time()

    def ms(self) -> int:
        return int((time.time() - self.t0) * 1000)
```

---

## 6. `app/tracing.py` (the evidence judges read)

Every agent action, every Zetaris statement, every LLM call, every memory retrieval and every handoff is one JSON line in `logs/<trace_id>.jsonl` **and** printed to stdout. Fields follow the starter guide's recommended schema, plus extras.

```python
from __future__ import annotations
import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from app.util import redact, summarize


class Tracer:
    def __init__(self, trace_id: str, log_dir: Path, run_id: str | None = None):
        self.trace_id = trace_id
        self.run_id = run_id or trace_id
        self.path = Path(log_dir) / f"{trace_id}.jsonl"
        self._lock = threading.Lock()

    def emit(
        self,
        agent_name: str,
        action: str,
        input_summary: str = "",
        output_summary: str = "",
        target_agent: str | None = None,
        confidence: float | None = None,
        retry_count: int = 0,
        status: str = "success",
        **extra,
    ) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "agent_name": agent_name,
            "action": action,
            "input_summary": summarize(input_summary),
            "output_summary": summarize(output_summary),
            "target_agent": target_agent,
            "confidence": confidence,
            "retry_count": retry_count,
            "status": status,
        }
        record.update(redact(extra))
        line = json.dumps(record, default=str)
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            print(line, file=sys.stdout, flush=True)
```

### Standard action names (use these so logs are searchable)
`plan_created`, `handoff`, `zetaris_statement`, `zetaris_error`, `zetaris_retry`, `llm_call`, `memory_retrieve`, `memory_write`, `policy_decision`, `approval_requested`, `onboard_step`, `repair_attempt`, `check_result`, `skeptic_verdict`, `revision_requested`, `revision_applied`, `final_answer`, `grant_attempt`.

### Handoff logging pattern (the weak vs strong pattern in the starter guide)
Whenever one agent passes work to another, emit a `handoff` event with `agent_name=<sender>`, `target_agent=<receiver>`, `input_summary=<what was asked>`. A strong log reads like:

```
PlannerAgent -> OnboarderAgent: onboard sources
OnboarderAgent -> Zetaris: statements (zetaris_statement events)
OnboarderAgent -> InvestigatorAgent: models ready
InvestigatorAgent -> VerifierService: evidence request
SkepticAgent -> InvestigatorAgent: critique (revision_requested)
InvestigatorAgent -> SkepticAgent: revised answer
SkepticAgent -> PlannerAgent: approve
PlannerAgent -> caller: final answer
```

---

## 7. Acceptance criteria for this file

- [ ] `python -c "from app.config import Config; print(Config.load())"` prints config with keys hidden (override `__repr__` or print a redacted dict).
- [ ] `Tracer.emit` writes valid JSONL and never contains secrets (unit test passes a fake key field and checks `***`).
- [ ] Folder tree exists with empty `__init__.py` files.
- [ ] `.gitignore` excludes `.env`.
