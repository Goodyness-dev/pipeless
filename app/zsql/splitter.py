from __future__ import annotations
import re

_NEXT_CREATE_TABLE = re.compile(r"\s*CREATE\s+TABLE\b", re.I)


def split_statements(text: str) -> list[str]:
    """Splits a .sql script into single statements. Quote- and comment-aware.
    Preserves multi-table COMPILE USL ... DDL as one single statement."""
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
                buf.append(nxt)
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch == "-" and nxt == "-":                    # line comment: skip to end of line
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":                    # block comment
            j = text.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == ";":
            current = "".join(buf).strip()
            is_compile = bool(re.match(r"COMPILE\s+USL\b", current, re.I))
            if is_compile and _NEXT_CREATE_TABLE.match(text, i + 1):
                buf.append(ch)
                i += 1
                continue        # semicolon belongs to the COMPILE payload
            if current:
                statements.append(current)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements
