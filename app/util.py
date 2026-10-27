from __future__ import annotations
import hashlib
import json
import re
import time
import uuid
from typing import Any

_SECRET_KEYS = re.compile(r"(key|token|password|secret|authorization)", re.I)


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def summarize(value: Any, limit: int = 300) -> str:
    """Short, log-safe summary of any value."""
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def redact(obj: Any) -> Any:
    """Recursively replace secret-looking dict values with '***'."""
    if isinstance(obj, dict):
        return {k: ("***" if _SECRET_KEYS.search(str(k)) else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


def to_number(text: Any) -> float | None:
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
