"""Local read-only viewer for recorded live demonstration evidence."""
import json
import re
from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import FileResponse
from app.config import Config

router = APIRouter()


@router.get("/demo", include_in_schema=False)
def demo_page():
    return FileResponse(Path(__file__).parent / "static" / "demo.html")


@router.get("/demo/state", include_in_schema=False)
def demo_state():
    cfg = Config.load()
    directory = cfg.data_dir / "demo"
    result = {}
    for name in ["progress", "audit", "repair-evaluation", "followup"]:
        path = directory / f"{name}.json"
        if path.exists():
            try:
                result[name] = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                pass
    progress = result.get("progress", {})
    run_id = progress.get("run_id", "")
    if progress.get("status") == "running" and re.fullmatch(r"demo_[A-Za-z0-9_-]+", run_id):
        trace = cfg.log_dir / f"{run_id}.jsonl"
        try:
            lines = trace.read_text(encoding="utf-8").splitlines()
            latest = json.loads(lines[-1]) if lines else {}
            progress["latest_event"] = {key: latest.get(key) for key in ["agent_name", "action", "timestamp", "status"]}
        except (OSError, ValueError):
            pass
    return result
