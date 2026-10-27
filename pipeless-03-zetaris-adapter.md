# PIPELESS — 03 ZETARIS ADAPTER

> Prerequisites: files 01, 02. This is the most important integration. Every statement Pipeless sends to Zetaris goes through the code in this file. It must be real (no mocks), careful (one statement at a time), and honest about errors.

Files created here: `app/adapters/zetaris.py`, `app/zsql/splitter.py`, `app/zsql/classify.py`, `app/zsql/playbook.py`, `app/zsql/guard.py`, `scripts/probe_zetaris.py`.

---

## 1. Design principles

1. **Two identities.** `builder` (admin API key) creates things. `reader` (non-admin API key) reads. Both keys are created by a human in the Zetaris UI.
2. **One statement per call.** Never send two statements in one request. A multi-table `COMPILE USL ... DDL` is one statement and must stay intact.
3. **Serialise the builder.** A global lock around builder statements, plus an optional minimum gap (15 s if the User-Agent is not identified, 0 otherwise).
4. **Classify every error** with the playbook (section 5) so agents can repair from the message.
5. **Never fake a response.** If Zetaris is unreachable, raise a structured error.
6. **Strings everywhere.** Zetaris REST returns values as strings; convert explicitly.
7. **Do not trust a success status alone.** Registration success is not the same as readable rows.

---

## 2. `app/adapters/zetaris.py`

```python
from __future__ import annotations
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

import requests

from app.config import Config, ConfigError
from app.tracing import Tracer
from app.util import Stopwatch, summarize
from app.zsql.classify import classify_statement
from app.zsql.playbook import ErrorInfo, classify_error

Identity = Literal["builder", "reader"]

# Builder statements are serialised across the whole process. Reads may run concurrently.
_BUILDER_LOCK = threading.RLock()
_LAST_BUILDER_CALL = {"t": 0.0}


class ZetarisError(Exception):
    def __init__(self, info: ErrorInfo, sql: str, http_status: int | None, body: str):
        super().__init__(f"{info.kind}: {info.message}")
        self.info = info
        self.sql = sql
        self.http_status = http_status
        self.body = body


@dataclass
class ZResult:
    rows: list[list[Any]]
    raw: dict
    elapsed_ms: int
    limit: int
    truncated: bool          # True when len(rows) == limit (possible truncation)
    columns: list[str] | None = None   # UNKNOWN U1: fill if the response exposes them


class ZetarisClient:
    def __init__(self, cfg: Config, identity: Identity = "builder"):
        self.cfg = cfg
        self.identity = identity
        key = cfg.zetaris_builder_key if identity == "builder" else cfg.zetaris_reader_key
        if not key:
            raise ConfigError(f"No Zetaris API key configured for identity '{identity}'")
        self._key = key
        self._session = requests.Session()

    def _headers(self) -> dict:
        return {
            "Authorization": "Bearer " + self._key,
            "X-Org-ID": self.cfg.zetaris_org_id,
            "X-Request-ID": str(uuid.uuid4()),
            "Content-Type": "application/json",
        }

    def run_sql(self, sql: str, limit: int | None = None, timeout: int | None = None) -> ZResult:
        sql = normalise_sql(sql)
        limit = limit or self.cfg.query_limit
        url = self.cfg.zetaris_rest_url.rstrip("/") + "/sql-editor/sqls/run"
        payload = {"queryId": str(uuid.uuid4()), "sql": sql, "source": "SqlEditor", "limit": limit}
        sw = Stopwatch()
        try:
            resp = self._session.post(
                url, headers=self._headers(), json=payload,
                timeout=timeout or self.cfg.zetaris_timeout_s,
            )
        except requests.RequestException as exc:  # connection refused, timeout, DNS
            info = classify_error(None, str(exc))
            raise ZetarisError(info, sql, None, str(exc)) from exc

        body_text = resp.text
        if not resp.ok:
            raise ZetarisError(classify_error(resp.status_code, body_text), sql, resp.status_code, body_text)

        try:
            body = resp.json()
        except ValueError:
            raise ZetarisError(classify_error(resp.status_code, "non-JSON body: " + body_text[:200]),
                               sql, resp.status_code, body_text)

        # UNKNOWN U1: an error may arrive as HTTP 200 with an error field. Detect defensively.
        if isinstance(body, dict) and (body.get("error") or str(body.get("status", "")).lower() in ("error", "failed")):
            raise ZetarisError(classify_error(resp.status_code, json.dumps(body)), sql, resp.status_code, body_text)

        data = body.get("data") if isinstance(body, dict) else None
        rows = data if isinstance(data, list) else []
        return ZResult(rows=rows, raw=body, elapsed_ms=sw.ms(), limit=limit,
                       truncated=len(rows) >= limit)


def normalise_sql(sql: str) -> str:
    """Strip whitespace and ONE trailing semicolon (the starter smoke test sends 'SELECT 1' without one).
    Semicolons inside a COMPILE USL payload are left untouched."""
    s = sql.strip()
    if s.endswith(";"):
        s = s[:-1].rstrip()
    return s


class StatementRunner:
    """Executes ONE statement at a time with logging, pacing and a minimal, documented retry policy."""

    def __init__(self, client: ZetarisClient, cfg: Config, tracer: Tracer, agent_name: str):
        self.client = client
        self.cfg = cfg
        self.tracer = tracer
        self.agent = agent_name

    def execute(self, sql: str, *, purpose: str, rate_limited: bool = False,
                limit: int | None = None, timeout: int | None = None) -> ZResult:
        kind = classify_statement(sql)
        attempts = 0
        while True:
            attempts += 1
            try:
                result = self._call(sql, kind, limit, timeout, rate_limited)
                self.tracer.emit(self.agent, "zetaris_statement", f"[{purpose}] {sql}",
                                 f"{len(result.rows)} rows in {result.elapsed_ms} ms",
                                 target_agent="Zetaris", retry_count=attempts - 1, kind=kind,
                                 identity=self.client.identity, truncated=result.truncated)
                return result
            except ZetarisError as err:
                self.tracer.emit(self.agent, "zetaris_error", f"[{purpose}] {sql}",
                                 f"{err.info.kind}: {err.info.message}", target_agent="Zetaris",
                                 retry_count=attempts - 1, status="error",
                                 http_status=err.http_status, hint=err.info.hint)
                if attempts == 1 and err.info.retryable:
                    wait = err.info.wait_seconds
                    self.tracer.emit(self.agent, "zetaris_retry", sql, f"waiting {wait}s then retrying once")
                    time.sleep(wait)
                    continue
                raise

    def _call(self, sql, kind, limit, timeout, rate_limited) -> ZResult:
        if self.client.identity == "builder":
            with _BUILDER_LOCK:
                gap = self.cfg.statement_gap_s if rate_limited else 0.0
                wait = gap - (time.time() - _LAST_BUILDER_CALL["t"])
                if wait > 0:
                    time.sleep(wait)
                try:
                    return self.client.run_sql(sql, limit, timeout)
                finally:
                    _LAST_BUILDER_CALL["t"] = time.time()
        return self.client.run_sql(sql, limit, timeout)

    # Convenience helpers --------------------------------------------------------------
    def scalar(self, sql: str, *, purpose: str) -> str | None:
        r = self.execute(sql, purpose=purpose, limit=5)
        return r.rows[0][0] if r.rows and r.rows[0] else None

    def count(self, sql_from: str, *, purpose: str) -> int:
        v = self.scalar(f"SELECT COUNT(*) FROM {sql_from}", purpose=purpose)
        return int(float(v)) if v is not None else -1
```

Notes for the coding agent:
- `ZResult.columns` is **UNKNOWN U1**. The probe script (section 7) prints the full raw response for `SELECT 1 AS one`. If the response includes column names or a schema, fill `columns`; otherwise leave `None` and have callers rely on position plus `DESCRIBE`.
- The retry policy (one retry, flagged in the playbook) mirrors the docs: HTTP 429 → wait 60 s and retry that statement once; cold-start 502 / `TTransportException` → retry once immediately. **Never loop.**

---

## 3. `app/zsql/splitter.py` (used by the recipe loader in file 06)

Splits a `.sql` recipe file into single statements. Quote- and comment-aware. Keeps a multi-table `COMPILE USL ... DDL` whole.

```python
from __future__ import annotations
import re

_NEXT_CREATE_TABLE = re.compile(r"\s*CREATE\s+TABLE\b", re.I)


def split_statements(text: str) -> list[str]:
    statements: list[str] = []
    buf: list[str] = []
    i, n = 0, len(text)
    quote: str | None = None          # ', ", or `
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if quote:
            buf.append(ch)
            if ch == "\\" and quote != "`" and nxt:
                buf.append(nxt); i += 2; continue
            if ch == quote:
                quote = None
            i += 1; continue
        if ch == "-" and nxt == "-":                    # line comment: skip to end of line
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":                    # block comment
            j = text.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if ch in ("'", '"', "`"):
            quote = ch; buf.append(ch); i += 1; continue
        if ch == ";":
            current = "".join(buf).strip()
            is_compile = bool(re.match(r"COMPILE\s+USL\b", current, re.I))
            if is_compile and _NEXT_CREATE_TABLE.match(text, i + 1):
                buf.append(ch); i += 1; continue        # semicolon belongs to the COMPILE payload
            if current:
                statements.append(current)
            buf = []; i += 1; continue
        buf.append(ch); i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements
```

Required unit tests (file 10): a COMPILE USL with three CREATE TABLEs stays one statement; a `;` inside a quoted string does not split; `--` comments are dropped; the sic_usl compile in the docs (ends with `);`) stays whole.

---

## 4. `app/zsql/classify.py`

```python
from __future__ import annotations
import re

READ = "READ"          # SELECT, SHOW, DESCRIBE, LIST, PREVIEW, EXPLAIN, TEST, LOAD, RUN DQ
CREATE = "CREATE"      # CREATE, COMPILE, ACTIVATE, REGISTER
CACHE = "CACHE"
DESTRUCTIVE = "DESTRUCTIVE"   # DROP, REMOVE, UNCACHE, REVOKE
ACCESS = "ACCESS"      # GRANT, REVOKE, ASSIGN, CREATE ROLE
EXPORT = "EXPORT"      # MATERIALIZE, RUN EXPORT
OTHER = "OTHER"

_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^\s*(SELECT|WITH|SHOW|DESCRIBE|LIST|PREVIEW|EXPLAIN|TEST|LOAD)\b", re.I), READ),
    (re.compile(r"^\s*RUN\s+DQ\b", re.I), READ),
    (re.compile(r"^\s*RUN\s+EXPORT\b", re.I), EXPORT),
    (re.compile(r"^\s*MATERIALIZE\b", re.I), EXPORT),
    (re.compile(r"^\s*(CREATE\s+ROLE|GRANT|ASSIGN|REVOKE)\b", re.I), ACCESS),
    (re.compile(r"^\s*(DROP|REMOVE|UNCACHE|OFFSITE\s+UNCACHE)\b", re.I), DESTRUCTIVE),
    (re.compile(r"^\s*CACHE\s+TABLE\b", re.I), CACHE),
    (re.compile(r"^\s*(CREATE|COMPILE|ACTIVATE|REGISTER|UPDATE)\b", re.I), CREATE),
]


def classify_statement(sql: str) -> str:
    for pattern, label in _RULES:
        if pattern.match(sql):
            return label
    return OTHER


# Extract the object a statement creates, for policy and registry use.
_TARGETS = [
    ("LIGHTNING_DATABASE", re.compile(r"CREATE\s+LIGHTNING\s+DATABASE\s+(\w+)", re.I)),
    ("REST_TABLE", re.compile(r"CREATE\s+LIGHTNING\s+REST\s+TABLE\s+(\w+)\s+FROM\s+(\w+)", re.I)),
    ("FILESTORE_TABLE", re.compile(r"CREATE\s+LIGHTNING\s+FILESTORE\s+TABLE\s+(\w+)\s+FROM\s+(\w+)", re.I)),
    ("CONTAINER", re.compile(r"CREATE\s+SCHEMASTORE\s+CONTAINER\s+(\w+)", re.I)),
    ("VIEW", re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?SCHEMASTORE\s+VIEW\s+(\w+)\s+WITH\s+CONTAINER\s+(\w+)", re.I)),
    ("NAMESPACE", re.compile(r"CREATE\s+NAMESPACE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w\.]+)", re.I)),
    ("USL", re.compile(r"COMPILE\s+USL\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s+(?:DEPLOY\s+)?NAMESPACE\s+([\w\.]+)", re.I)),
    ("USL_TABLE", re.compile(r"ACTIVATE\s+USL\s+TABLE\s+([\w\.]+)", re.I)),
    ("CACHE_TABLE", re.compile(r"CACHE\s+TABLE\s+([\w\.]+)", re.I)),
]


def statement_target(sql: str) -> tuple[str, tuple[str, ...]] | None:
    for label, pattern in _TARGETS:
        m = pattern.search(sql)
        if m:
            return label, m.groups()
    return None
```

---

## 5. `app/zsql/playbook.py` (error classification from the Zetaris troubleshooting docs)

Each entry maps an error pattern to a kind, a retry policy and a repair hint. The hints are fed to the Onboarder's repair prompt (file 06) and to H-MEM (file 08).

```python
from __future__ import annotations
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorInfo:
    kind: str
    message: str
    retryable: bool = False
    wait_seconds: int = 0
    hint: str = ""


# (regex over "<http_status> <body>", kind, retryable, wait_seconds, hint)
_PLAYBOOK = [
    (r"\b401\b|unauthori[sz]ed", "auth", False, 0,
     "API key missing/wrong/expired. A HUMAN must create a new key (Management -> API Tokens). The agent must not create keys."),
    (r"Not Allowed|\b403\b", "permission_or_org", False, 0,
     "Check ZETARIS_ORG_ID is the numeric ID, or the identity lacks the privilege (builder statements are Administrators only)."),
    (r"\b400\b.*(uuid|X-Request-ID|queryId)", "bad_request_uuid", False, 0,
     "X-Request-ID and queryId must be fresh UUIDs."),
    (r"\b429\b|OVER_RATE_LIMIT|rate limit", "rate_limited", True, 60,
     "Rate limited. Send identifying User-Agent (app_name user@domain.tld), cache the raw table, wait 60s, retry that ONE statement."),
    (r"TTransportException|Multiple exceptions were thrown", "transport_cold_start", True, 2,
     "Likely cold start on the source. Retry once immediately; do not redesign the schema first."),
    (r"\b502\b|Bad Gateway", "bad_gateway", True, 3,
     "HTTP 502: cold start or upstream issue. Retry once. On a rate-limited source wait before another attempt."),
    (r"parent namespace : metastore is not existing", "namespace_parent_missing", False, 0,
     "Run CREATE NAMESPACE IF NOT EXISTS lightning.metastore; first."),
    (r"already exists", "already_exists", False, 0,
     "Object exists. Inspect its definition and reuse only if it matches. CREATE SCHEMASTORE CONTAINER has no IF NOT EXISTS."),
    (r"Description is invalid", "describe_by_invalid", False, 0,
     "DESCRIBE BY accepts only letters, digits, spaces, _ . - and ,. Remove parentheses, slashes, colons."),
    (r"UNRESOLVED_COLUMN|cannot resolve", "unresolved_column", False, 0,
     "Backtick mixed-case, reserved or hyphenated JSON keys (`entityName`, fact.`end`). Check the view's own SELECT list and the raw response keys."),
    (r"MISSING_ATTRIBUTES\.RESOLVED_ATTRIBUTE_APPEAR_IN_OPERATION", "repeated_exploded_view", False, 0,
     "Same exploded UNION view read more than once. Rewrite with a window function (ROW_NUMBER/COUNT OVER) so the view is read once."),
    (r"DATATYPE_MISMATCH", "datatype_mismatch", False, 0,
     "Dynamic-key objects: apply from_json(to_json(...), 'map<...>') to EVERY dynamic-key object exploded in the same query."),
    (r"Table not found|table or view not found", "table_not_found", False, 0,
     "Use the qualified name <source>.<table> (or <container>.<view>); check SHOW LIGHTNING TABLES."),
    (r"is not activated", "usl_not_activated", False, 0,
     "Run ACTIVATE USL TABLE for that table (UPDATE USL clears activation)."),
    (r"nullable -> NOT NULL|nullable.*NOT NULL", "usl_nullability", False, 0,
     "Use COALESCE in the activation query for NOT NULL columns; it does not validate keys."),
    (r"mismatched input .* expecting", "multi_statement", False, 0,
     "Send ONE statement per call; keep a multi-table COMPILE USL whole."),
    (r"Connection refused|Max retries exceeded|Failed to establish", "unreachable", False, 0,
     "Zetaris is not reachable at ZETARIS_REST_URL. Check Docker/containers and the URL (API base ends in /api/v1.0, port 8888 local)."),
    (r"timed? ?out|Read timed out", "timeout", False, 0,
     "Statement exceeded the timeout. For RUN DQ keep the result UNVERIFIED and do not relaunch while it may still run."),
]
_COMPILED = [(re.compile(p, re.I | re.S), k, r, w, h) for p, k, r, w, h in _PLAYBOOK]


def classify_error(http_status: int | None, body: str) -> ErrorInfo:
    haystack = f"{http_status or ''} {body}"
    for pattern, kind, retryable, wait, hint in _COMPILED:
        if pattern.search(haystack):
            return ErrorInfo(kind=kind, message=body[:400], retryable=retryable, wait_seconds=wait, hint=hint)
    return ErrorInfo(kind="unknown", message=body[:400], hint="Unclassified. Log the raw body and ask the Onboarder to repair from the message.")
```

Rule: `ErrorInfo.hint` is **knowledge about Zetaris**, taken from the docs. It is not a canned answer to a user question, so it is allowed in code. The H-MEM store (file 08) holds *learned lessons from real runs* and is never pre-seeded.

---

## 6. `app/zsql/guard.py` (reader SQL guard; applies to LLM-written SQL on every identity)

```python
from __future__ import annotations
import re

FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|GRANT|REVOKE|COMPILE|ACTIVATE|REMOVE|MATERIALIZE|"
    r"REGISTER|EXPORT|CACHE|UNCACHE|ASSIGN|TRUNCATE|MERGE|SET)\b", re.I)
_STRING = re.compile(r"'(?:[^'\\]|\\.)*'")
_TABLE_REF = re.compile(r"\b(?:FROM|JOIN)\s+([`\w\.\-]+)", re.I)


class SqlGuardError(ValueError):
    pass


def _mask_strings(sql: str) -> str:
    return _STRING.sub("''", sql)


def validate_read_only(sql: str, allowed_tables: set[str] | None = None) -> str:
    """Return normalised SQL or raise SqlGuardError. One SELECT/WITH statement only."""
    s = sql.strip().rstrip(";").strip()
    masked = _mask_strings(s)
    if ";" in masked:
        raise SqlGuardError("Only one statement is allowed.")
    if not re.match(r"^(SELECT|WITH)\b", masked, re.I):
        raise SqlGuardError("Only SELECT / WITH queries are allowed.")
    m = FORBIDDEN.search(masked)
    if m:
        raise SqlGuardError(f"Forbidden keyword in a read query: {m.group(1).upper()}")
    if allowed_tables is not None:
        allowed = {t.lower().replace("`", "") for t in allowed_tables}
        for ref in _TABLE_REF.findall(masked):
            name = ref.lower().replace("`", "")
            if name not in allowed:
                raise SqlGuardError(f"Table not in the verified model registry: {ref}")
    return s
```

Apply `validate_read_only` to **every** SQL string the Investigator produces, regardless of identity.

---

## 7. `scripts/probe_zetaris.py` (resolves UNKNOWN U1 and U2; run it first)

The script must:
1. Run `SELECT 1 AS one` with and without trailing `;` and print the full raw JSON response and HTTP status.
2. Run `SHOW LIGHTNING DATABASES` and `SHOW LIGHTNING TABLES NY_TAXI`, print raw JSON.
3. Run `SELECT * FROM NY_TAXI.taxi_zones LIMIT 5` and print raw JSON (look for column names).
4. Run a deliberately bad statement `SELECT * FROM nope.nope` and print the **error** response (is it HTTP 4xx/5xx or 200 with an error body?).
5. If `ZETARIS_READER_API_KEY` is set, repeat step 1 with the reader identity.
6. Write a `data/zetaris_probe.json` summary: `{ "trailing_semicolon_ok": bool, "columns_in_response": bool, "error_shape": "...", "reader_ok": bool }`.

The coding agent must then update `ZetarisClient.run_sql` so `ZResult.columns` and the error detection match reality. **Do not skip this probe.**

---

## 8. Acceptance criteria

- [ ] `probe_zetaris.py` runs against the real instance and writes the summary.
- [ ] `StatementRunner.execute("SELECT 1")` logs a `zetaris_statement` event; a bad statement logs `zetaris_error` with a classified kind.
- [ ] Two builder statements issued from two threads never overlap (lock test).
- [ ] `split_statements` unit tests pass (file 10).
- [ ] `validate_read_only` rejects `DROP`, multi-statement, and tables outside the allowed set.
- [ ] No secret appears in any log line.
