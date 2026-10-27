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
    ("USL_METADATA", re.compile(r"^\s*UPDATE\s+USL\s+([\w\.]+)", re.I)),
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
