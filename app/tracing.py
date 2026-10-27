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
        self.path.parent.mkdir(parents=True, exist_ok=True)

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
    ) -> dict:
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
        return record
