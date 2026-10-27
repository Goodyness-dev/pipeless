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
    columns: list[str] | None = None   # UNKNOWN U1: filled defensively if response exposes them


def normalise_sql(sql: str) -> str:
    """Strip whitespace and ONE trailing semicolon.
    Semicolons inside a COMPILE USL payload are left untouched."""
    s = sql.strip()
    if s.endswith(";"):
        s = s[:-1].rstrip()
    return s


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
        headers = {
            "Authorization": "Bearer " + self._key,
            "X-Org-ID": str(self.cfg.zetaris_org_id),
            "X-Request-ID": str(uuid.uuid4()),
            "Content-Type": "application/json",
        }
        if self.cfg.user_agent:
            headers["User-Agent"] = self.cfg.user_agent
        return headers

    def run_sql(self, sql: str, limit: int | None = None, timeout: int | None = None) -> ZResult:
        sql = normalise_sql(sql)
        limit = limit or self.cfg.query_limit
        url = self.cfg.zetaris_rest_url.rstrip("/") + "/sql-editor/sqls/run"
        payload = {"queryId": str(uuid.uuid4()), "sql": sql, "source": "SqlEditor", "limit": limit}
        sw = Stopwatch()
        try:
            resp = self._session.post(
                url,
                headers=self._headers(),
                json=payload,
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
            raise ZetarisError(
                classify_error(resp.status_code, "non-JSON body: " + body_text[:200]),
                sql,
                resp.status_code,
                body_text,
            )

        # UNKNOWN U1: an error may arrive as HTTP 200 with an error field. Detect defensively.
        if isinstance(body, dict) and (body.get("error") or str(body.get("status", "")).lower() in ("error", "failed")):
            raise ZetarisError(classify_error(resp.status_code, json.dumps(body)), sql, resp.status_code, body_text)

        data = body.get("data") if isinstance(body, dict) else None
        rows = data if isinstance(data, list) else []

        # Defensive extraction of column names if provided in body
        columns = None
        if isinstance(body, dict):
            if "columns" in body and isinstance(body["columns"], list):
                columns = [str(c) for c in body["columns"]]
            elif "headers" in body and isinstance(body["headers"], list):
                columns = [str(c) for c in body["headers"]]
            elif "schema" in body and isinstance(body["schema"], list):
                columns = [str(c.get("name", c)) if isinstance(c, dict) else str(c) for c in body["schema"]]

        return ZResult(
            rows=rows,
            raw=body if isinstance(body, dict) else {},
            elapsed_ms=sw.ms(),
            limit=limit,
            truncated=len(rows) >= limit,
            columns=columns,
        )


class StatementRunner:
    """Executes ONE statement at a time with logging, pacing and a minimal, documented retry policy."""

    def __init__(self, client: ZetarisClient, cfg: Config, tracer: Tracer, agent_name: str):
        self.client = client
        self.cfg = cfg
        self.tracer = tracer
        self.agent = agent_name

    def execute(
        self,
        sql: str,
        *,
        purpose: str,
        rate_limited: bool = False,
        limit: int | None = None,
        timeout: int | None = None,
    ) -> ZResult:
        kind = classify_statement(sql)
        attempts = 0
        while True:
            attempts += 1
            try:
                result = self._call(sql, kind, limit, timeout, rate_limited)
                self.tracer.emit(
                    self.agent,
                    "zetaris_statement",
                    f"[{purpose}] {sql}",
                    f"{len(result.rows)} rows in {result.elapsed_ms} ms",
                    target_agent="Zetaris",
                    retry_count=attempts - 1,
                    kind=kind,
                    identity=self.client.identity,
                    truncated=result.truncated,
                )
                return result
            except ZetarisError as err:
                self.tracer.emit(
                    self.agent,
                    "zetaris_error",
                    f"[{purpose}] {sql}",
                    f"{err.info.kind}: {err.info.message}",
                    target_agent="Zetaris",
                    retry_count=attempts - 1,
                    status="error",
                    http_status=err.http_status,
                    hint=err.info.hint,
                )
                if attempts == 1 and err.info.retryable:
                    wait = err.info.wait_seconds
                    self.tracer.emit(
                        self.agent,
                        "zetaris_retry",
                        sql,
                        f"waiting {wait}s then retrying once",
                    )
                    time.sleep(wait)
                    continue
                raise

    def _call(self, sql: str, kind: str, limit: int | None, timeout: int | None, rate_limited: bool) -> ZResult:
        if self.client.identity == "builder":
            global _LAST_BUILDER_CALL
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

    # Convenience helpers
    def scalar(self, sql: str, *, purpose: str) -> str | None:
        r = self.execute(sql, purpose=purpose, limit=5)
        return str(r.rows[0][0]) if r.rows and r.rows[0] else None

    def count(self, sql_from: str, *, purpose: str) -> int:
        v = self.scalar(f"SELECT COUNT(*) FROM {sql_from}", purpose=purpose)
        if v is None:
            return -1
        try:
            return int(float(v))
        except (ValueError, TypeError):
            return -1
