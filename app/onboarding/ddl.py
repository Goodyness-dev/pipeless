from __future__ import annotations
import re

_DESC_BAD = re.compile(r"[^A-Za-z0-9 _.,\-]")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def safe_desc(text: str) -> str:
    """DESCRIBE BY accepts only letters, digits, spaces, _ . - and ,"""
    cleaned = _DESC_BAD.sub(" ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:100] or "Pipeless source"


def ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"unsafe identifier: {name!r}")
    return name


def create_database(db: str, description: str) -> str:
    return f'CREATE LIGHTNING DATABASE {ident(db)} DESCRIBE BY "{safe_desc(description)}"'


def create_rest_table(raw: str, db: str, endpoint: str, user_agent: str | None) -> str:
    header = f'    user-agent "{user_agent}"\n' if user_agent else ""
    return (
        f"CREATE LIGHTNING REST TABLE {ident(raw)} FROM {ident(db)} REQUEST(\n"
        f'    endpoint "{endpoint}",\n'
        f'    method "get",\n'
        f'    response_type "json",\n'
        f'    http_encoding "URLENCODED"\n'
        f") HEADER (\n{header}) BODY ()"
    )


def cache_table(db: str, raw: str) -> str:
    return f"CACHE TABLE {ident(db)}.{ident(raw)}"


def create_container(container: str) -> str:
    return f"CREATE SCHEMASTORE CONTAINER {ident(container)}"


def create_view(view: str, container: str, select_sql: str) -> str:
    return f"CREATE SCHEMASTORE VIEW {ident(view)} WITH CONTAINER {ident(container)} AS\n{select_sql.strip().rstrip(';')}"


def preview_select(select_sql: str) -> str:
    return f"PREVIEW SCHEMASTORE SQL AS {select_sql.strip().rstrip(';')}"
