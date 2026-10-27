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
    (
        r"\b401\b|unauthori[sz]ed",
        "auth",
        False,
        0,
        "API key missing/wrong/expired. A HUMAN must create a new key (Management -> API Tokens). The agent must not create keys.",
    ),
    (
        r"Not Allowed|\b403\b",
        "permission_or_org",
        False,
        0,
        "Check ZETARIS_ORG_ID is the numeric ID, or the identity lacks the privilege (builder statements are Administrators only).",
    ),
    (
        r"\b400\b.*(uuid|X-Request-ID|queryId)",
        "bad_request_uuid",
        False,
        0,
        "X-Request-ID and queryId must be fresh UUIDs.",
    ),
    (
        r"\b429\b|OVER_RATE_LIMIT|rate limit",
        "rate_limited",
        True,
        60,
        "Rate limited. Send identifying User-Agent (app_name user@domain.tld), cache the raw table, wait 60s, retry that ONE statement.",
    ),
    (
        r"TTransportException|Multiple exceptions were thrown",
        "transport_cold_start",
        True,
        2,
        "Likely cold start on the source. Retry once immediately; do not redesign the schema first.",
    ),
    (
        r"\b502\b|Bad Gateway",
        "bad_gateway",
        True,
        3,
        "HTTP 502: cold start or upstream issue. Retry once. On a rate-limited source wait before another attempt.",
    ),
    (
        r"parent namespace : metastore is not existing",
        "namespace_parent_missing",
        False,
        0,
        "Run CREATE NAMESPACE IF NOT EXISTS lightning.metastore; first.",
    ),
    (
        r"already exists",
        "already_exists",
        False,
        0,
        "Object exists. Inspect its definition and reuse only if it matches. CREATE SCHEMASTORE CONTAINER has no IF NOT EXISTS.",
    ),
    (
        r"Description is invalid",
        "describe_by_invalid",
        False,
        0,
        "DESCRIBE BY accepts only letters, digits, spaces, _ . - and ,. Remove parentheses, slashes, colons.",
    ),
    (
        r"UNRESOLVED_COLUMN|cannot resolve",
        "unresolved_column",
        False,
        0,
        "Backtick mixed-case, reserved or hyphenated JSON keys (`entityName`, fact.`end`). Check the view's own SELECT list and the raw response keys.",
    ),
    (
        r"MISSING_ATTRIBUTES\.RESOLVED_ATTRIBUTE_APPEAR_IN_OPERATION",
        "repeated_exploded_view",
        False,
        0,
        "Same exploded UNION view read more than once. Rewrite with a window function (ROW_NUMBER/COUNT OVER) so the view is read once.",
    ),
    (
        r"DATATYPE_MISMATCH",
        "datatype_mismatch",
        False,
        0,
        "Dynamic-key objects: apply from_json(to_json(...), 'map<...>') to EVERY dynamic-key object exploded in the same query.",
    ),
    (
        r"Table not found|table or view not found",
        "table_not_found",
        False,
        0,
        "Use the qualified name <source>.<table> (or <container>.<view>); check SHOW LIGHTNING TABLES.",
    ),
    (
        r"is not activated",
        "usl_not_activated",
        False,
        0,
        "Run ACTIVATE USL TABLE for that table (UPDATE USL clears activation).",
    ),
    (
        r"nullable -> NOT NULL|nullable.*NOT NULL",
        "usl_nullability",
        False,
        0,
        "Use COALESCE in the activation query for NOT NULL columns; it does not validate keys.",
    ),
    (
        r"mismatched input .* expecting",
        "multi_statement",
        False,
        0,
        "Send ONE statement per call; keep a multi-table COMPILE USL whole.",
    ),
    (
        r"Connection refused|Max retries exceeded|Failed to establish",
        "unreachable",
        False,
        0,
        "Zetaris is not reachable at ZETARIS_REST_URL. Check Docker/containers and the URL (API base ends in /api/v1.0, port 8888 local).",
    ),
    (
        r"timed? ?out|Read timed out",
        "timeout",
        False,
        0,
        "Statement exceeded the timeout. For RUN DQ keep the result UNVERIFIED and do not relaunch while it may still run.",
    ),
]
_COMPILED = [(re.compile(p, re.I | re.S), k, r, w, h) for p, k, r, w, h in _PLAYBOOK]


def classify_error(http_status: int | None, body: str) -> ErrorInfo:
    haystack = f"{http_status or ''} {body}"
    for pattern, kind, retryable, wait, hint in _COMPILED:
        if pattern.search(haystack):
            return ErrorInfo(kind=kind, message=body[:400], retryable=retryable, wait_seconds=wait, hint=hint)
    return ErrorInfo(
        kind="unknown",
        message=body[:400],
        hint="Unclassified. Log the raw body and ask the Onboarder to repair from the message.",
    )
