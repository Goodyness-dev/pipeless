"""Demonstrates the H-MEM 3-session learning curve across consecutive Pipeless runs."""
from __future__ import annotations
import json
import os
import time
import requests

PIPELESS_URL = os.getenv("PIPELESS_URL", "http://localhost:8000")
TOKEN = os.getenv("PIPELESS_API_TOKEN", "choose-a-long-random-string")

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
    "X-Caller-Agent": "ThreeSessionDemo",
}


def run_session(session_num: int, payload: dict) -> dict:
    print(f"\n==================== SESSION {session_num} ====================")
    t0 = time.time()
    try:
        resp = requests.post(f"{PIPELESS_URL}/run", headers=HEADERS, json=payload, timeout=900)
        data = resp.json()
        elapsed = round(time.time() - t0, 2)
        receipts = data.get("receipts", {})
        mem = data.get("memory", {})
        print(f"Session {session_num} completed in {elapsed}s with status: {data.get('status')}")
        return {
            "session": session_num,
            "status": data.get("status"),
            "repairs": len(receipts.get("repairs", [])),
            "revisions": receipts.get("revisions", 0),
            "memories_written": mem.get("written", 0),
            "seconds": elapsed,
        }
    except Exception as e:
        print(f"Session {session_num} failed: {e}")
        return {
            "session": session_num,
            "status": "error",
            "repairs": 0,
            "revisions": 0,
            "memories_written": 0,
            "seconds": round(time.time() - t0, 2),
        }


def main():
    print("=== PIPELESS: H-MEM Three-Session Learning Curve Demo ===")

    # Session 1: First onboarding scenario
    s1_req = {
        "request_id": "session-1-demo",
        "user_query": "Compare EDGAR company profiles against SIC codes hierarchy.",
        "sources": [{"id": "sic_edgar", "kind": "catalog", "recipe_id": "sic_edgar_usl"}],
    }
    r1 = run_session(1, s1_req)

    # Session 2: Repetition / related query (H-MEM memories retrieved)
    s2_req = {
        "request_id": "session-2-demo",
        "user_query": "Identify SIC code mismatches for SEC EDGAR filers.",
        "sources": [{"id": "sic_edgar", "kind": "catalog", "recipe_id": "sic_edgar_usl"}],
    }
    r2 = run_session(2, s2_req)

    # Session 3: Follow-up query leveraging left-behind verified model
    s3_req = {
        "request_id": "session-3-demo",
        "user_query": "Which filers have discrepancies in description?",
        "sources": [{"id": "sic_edgar", "kind": "catalog", "recipe_id": "sic_edgar_usl"}],
    }
    r3 = run_session(3, s3_req)

    print("\n================== SUMMARY RESULTS TABLE ==================")
    print(f"{'Session':<10} | {'Status':<22} | {'Repairs':<8} | {'Revisions':<10} | {'Memories':<10} | {'Seconds':<8}")
    print("-" * 80)
    for r in [r1, r2, r3]:
        print(f"{r['session']:<10} | {str(r['status']):<22} | {r['repairs']:<8} | {r['revisions']:<10} | {r['memories_written']:<10} | {r['seconds']:<8}")


if __name__ == "__main__":
    main()
