from __future__ import annotations
import sys
import uvicorn
from pathlib import Path
from app.config import Config, ConfigError


def startup_check() -> None:
    print("=== Pipeless Startup Checklist ===")
    try:
        cfg = Config.load()
        print(f"[OK] Configuration loaded: {cfg}")
    except ConfigError as ce:
        print(f"[WARN] Config warning: {ce}")

    if not cfg.user_agent_valid:
        print("[WARNING] ZETARIS_USER_AGENT does not match 'app_name user@domain.tld'. Strict rate limits may apply.")

    # Check data directories
    (cfg.data_dir / "memory").mkdir(parents=True, exist_ok=True)
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    print(f"[OK] Directories verified: data={cfg.data_dir}, logs={cfg.log_dir}")


if __name__ == "__main__":
    startup_check()
    uvicorn.run("app.api.main:app", host="0.0.0.0", port=8000, log_level="info")
