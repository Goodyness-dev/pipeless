from __future__ import annotations
import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()


def definition_hash(sql: str) -> str:
    """Computes SHA-256 of whitespace-normalized SQL."""
    normal = re.sub(r"\s+", " ", sql.strip().rstrip(";")).strip().lower()
    return hashlib.sha256(normal.encode("utf-8")).hexdigest()


class LedgerService:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_path = self.data_dir / "ledger.jsonl"

    def add(
        self,
        *,
        run_id: str,
        kind: str,
        name: str,
        sql: str,
        origin: str = "",
    ) -> dict:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "kind": kind,
            "name": name.lower(),
            "definition_hash": definition_hash(sql),
            "origin": origin,
        }
        with _LOCK, open(self.ledger_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        return entry

    def created_in_run(self, run_id: str) -> set[str]:
        created = set()
        if not self.ledger_path.exists():
            return created
        with _LOCK:
            for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    item = json.loads(line)
                    if item.get("run_id") == run_id:
                        created.add(item.get("name", "").lower())
        return created


class ModelRegistry:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.data_dir / "registry.json"
        self._ensure_file()

    def _ensure_file(self) -> None:
        with _LOCK:
            if not self.registry_path.exists():
                self.registry_path.write_text("{}", encoding="utf-8")

    def _read_all(self) -> dict[str, dict]:
        with _LOCK:
            if not self.registry_path.exists():
                return {}
            try:
                return json.loads(self.registry_path.read_text(encoding="utf-8"))
            except ValueError:
                return {}

    def _write_all(self, data: dict[str, dict]) -> None:
        with _LOCK:
            self.registry_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def get(self, fqn: str) -> dict | None:
        return self._read_all().get(fqn.lower())

    def put(self, fqn: str, model_info: dict) -> None:
        data = self._read_all()
        data[fqn.lower()] = model_info
        self._write_all(data)

    def list_models(self, verified_only: bool = False) -> list[dict]:
        data = self._read_all()
        models = list(data.values())
        if verified_only:
            models = [m for m in models if m.get("verified")]
        return models

    def set_verified(self, fqn: str, verified: bool = True, caveats: list[str] | None = None) -> None:
        data = self._read_all()
        key = fqn.lower()
        if key in data:
            data[key]["verified"] = verified
            if caveats:
                notes = data[key].setdefault("notes", [])
                for c in caveats:
                    if c not in notes:
                        notes.append(c)
            self._write_all(data)
