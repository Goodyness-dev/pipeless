from __future__ import annotations
import re

FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|GRANT|REVOKE|COMPILE|ACTIVATE|REMOVE|MATERIALIZE|"
    r"REGISTER|EXPORT|CACHE|UNCACHE|ASSIGN|TRUNCATE|MERGE|SET)\b",
    re.I,
)
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
