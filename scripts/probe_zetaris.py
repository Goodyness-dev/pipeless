"""Probe script to test live Zetaris connection and resolve UNKNOWN U1 and U2.

Run with: python scripts/probe_zetaris.py
"""
from __future__ import annotations
import json
import sys
import uuid
from pathlib import Path
import requests

from app.config import Config, ConfigError


def run_probe_query(base_url: str, key: str, org_id: str, sql: str, timeout: int = 30) -> tuple[int, Any]:
    url = base_url.rstrip("/") + "/sql-editor/sqls/run"
    headers = {
        "Authorization": f"Bearer {key}",
        "X-Org-ID": str(org_id),
        "X-Request-ID": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }
    payload = {
        "queryId": str(uuid.uuid4()),
        "sql": sql,
        "source": "SqlEditor",
        "limit": 10,
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        try:
            body = resp.json()
        except ValueError:
            body = resp.text
        return resp.status_code, body
    except requests.RequestException as e:
        return 0, {"network_error": str(e)}


def main() -> int:
    try:
        cfg = Config.load()
    except Exception as e:
        print(f"[ERROR] Failed to load config: {e}")
        return 1

    if not cfg.zetaris_builder_key:
        print("[SKIP] ZETARIS_BUILDER_API_KEY is not set. Add it to .env when Zetaris is running.")
        return 0

    print(f"=== Probing Zetaris at {cfg.zetaris_rest_url} (Org ID: {cfg.zetaris_org_id}) ===")

    summary = {
        "trailing_semicolon_ok": False,
        "columns_in_response": False,
        "error_shape": "unknown",
        "reader_ok": False,
    }

    # 1. Test SELECT 1 without and with trailing semicolon
    print("\n--- 1. Testing 'SELECT 1 AS one' (without semicolon) ---")
    status_no_semi, body_no_semi = run_probe_query(cfg.zetaris_rest_url, cfg.zetaris_builder_key, cfg.zetaris_org_id, "SELECT 1 AS one")
    print(f"Status: {status_no_semi}\nBody: {json.dumps(body_no_semi, indent=2) if isinstance(body_no_semi, dict) else body_no_semi}")

    print("\n--- Testing 'SELECT 1 AS one;' (with semicolon) ---")
    status_semi, body_semi = run_probe_query(cfg.zetaris_rest_url, cfg.zetaris_builder_key, cfg.zetaris_org_id, "SELECT 1 AS one;")
    print(f"Status: {status_semi}\nBody: {json.dumps(body_semi, indent=2) if isinstance(body_semi, dict) else body_semi}")
    summary["trailing_semicolon_ok"] = (status_semi == 200 and not (isinstance(body_semi, dict) and body_semi.get("error")))

    # Check for columns in response
    if isinstance(body_no_semi, dict):
        if "columns" in body_no_semi or "schema" in body_no_semi:
            summary["columns_in_response"] = True

    # 2. SHOW LIGHTNING DATABASES
    print("\n--- 2. SHOW LIGHTNING DATABASES ---")
    st, body = run_probe_query(cfg.zetaris_rest_url, cfg.zetaris_builder_key, cfg.zetaris_org_id, "SHOW LIGHTNING DATABASES")
    print(f"Status: {st}\nBody: {json.dumps(body, indent=2) if isinstance(body, dict) else body}")

    # 3. SELECT * FROM NY_TAXI.taxi_zones LIMIT 5
    print("\n--- 3. Sample query NY_TAXI.taxi_zones ---")
    st, body = run_probe_query(cfg.zetaris_rest_url, cfg.zetaris_builder_key, cfg.zetaris_org_id, "SELECT * FROM NY_TAXI.taxi_zones LIMIT 5")
    print(f"Status: {st}\nBody: {json.dumps(body, indent=2) if isinstance(body, dict) else body}")
    if isinstance(body, dict) and ("columns" in body or "schema" in body):
        summary["columns_in_response"] = True

    # 4. Deliberately bad query
    print("\n--- 4. Bad query 'SELECT * FROM nope.nope' ---")
    st_bad, body_bad = run_probe_query(cfg.zetaris_rest_url, cfg.zetaris_builder_key, cfg.zetaris_org_id, "SELECT * FROM nope.nope")
    print(f"Status: {st_bad}\nBody: {json.dumps(body_bad, indent=2) if isinstance(body_bad, dict) else body_bad}")
    summary["error_shape"] = f"http_{st_bad}" if st_bad != 200 else "http_200_with_error_body"

    # 5. Reader identity test
    if cfg.zetaris_reader_key:
        print("\n--- 5. Reader query 'SELECT 1' ---")
        st_reader, body_reader = run_probe_query(cfg.zetaris_rest_url, cfg.zetaris_reader_key, cfg.zetaris_org_id, "SELECT 1")
        print(f"Status: {st_reader}\nBody: {json.dumps(body_reader, indent=2) if isinstance(body_reader, dict) else body_reader}")
        summary["reader_ok"] = (st_reader == 200 and not (isinstance(body_reader, dict) and body_reader.get("error")))
    else:
        print("\n--- 5. Reader identity not configured (optional for initial probe) ---")

    # 6. Save summary
    out_dir = Path(cfg.data_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    probe_file = out_dir / "zetaris_probe.json"
    probe_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n[DONE] Probe summary saved to {probe_file}:")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
