"""Probe script to verify Nightshift connectivity, model discovery, and Nemotron lanes.

Run with: python scripts/check_nightshift.py
"""
from __future__ import annotations
import sys
import json
from datetime import datetime, timezone
from pathlib import Path

from app.config import Config, ConfigError
from app.tracing import Tracer
from app.adapters.meterless import NightshiftLLM, extract_json


def main() -> int:
    try:
        cfg = Config.load()
    except Exception as e:
        print(f"[ERROR] Failed to load config: {e}")
        return 1

    if not cfg.llm_api_key:
        print("[SKIP] METERLESS_RUNTIME_TOKEN is not set. Add it to .env when Nightshift is running.")
        return 0

    tracer = Tracer("check_nightshift", Path(cfg.log_dir))

    print(f"=== Checking Nightshift at {cfg.llm_base_url} (App: {cfg.llm_app_name}) ===")
    try:
        llm = NightshiftLLM(cfg, tracer)
    except ConfigError as ce:
        print(f"[CONFIG ERROR] {ce}")
        return 1

    # 1. Discover models
    print("\n--- 1. Discovering Models via GET /v1/models ---")
    try:
        models = llm.discover_models()
    except Exception as exc:
        print(f"[ERROR] Could not connect to Nightshift: {exc}")
        return 1

    nemotron_found = []
    for m in models:
        is_nemotron = "nemotron" in m.lower()
        if is_nemotron:
            nemotron_found.append(m)
            print(f"  * {m}  [NVIDIA Nemotron]")
        else:
            print(f"    {m}")

    print(f"\nTotal models available: {len(models)} (Nemotron models found: {len(nemotron_found)})")

    # 2. Verify pinned models
    print("\n--- 2. Checking Pinned Model IDs ---")
    print(f"MODEL_FAST: {cfg.model_fast}")
    print(f"MODEL_DEEP: {cfg.model_deep}")

    missing = []
    if cfg.model_fast not in models:
        missing.append(("MODEL_FAST", cfg.model_fast))
    if cfg.model_deep not in models:
        missing.append(("MODEL_DEEP", cfg.model_deep))

    if missing:
        print("[WARNING] The following pinned models are not present in /v1/models:")
        for var, name in missing:
            print(f"  {var}: '{name}' not found.")
        print("\nAvailable model IDs you can pin:")
        for m in models:
            print(f"  - {m}")
        return 1

    # 3. Test chat completions on fast lane
    print("\n--- 3. Testing Fast Lane ---")
    fast_res = llm.chat(
        lane="fast",
        system="You are a helpful assistant.",
        user="Reply with the single word: ready",
        agent_name="ProbeScript",
        max_tokens=512,
    )
    print(f"Fast Lane response: {fast_res.text.strip()!r}")
    print(f"Model returned: {fast_res.model_returned} (Latency: {fast_res.latency_ms} ms)")

    # 4. Test chat completions on deep lane
    print("\n--- 4. Testing Deep Lane ---")
    deep_res = llm.chat(
        lane="deep",
        system="You are a helpful assistant.",
        user="Reply with the single word: ready",
        agent_name="ProbeScript",
        max_tokens=1024,
    )
    print(f"Deep Lane response: {deep_res.text.strip()!r}")
    print(f"Model returned: {deep_res.model_returned} (Latency: {deep_res.latency_ms} ms)")

    # 5. Test JSON extraction
    print("\n--- 5. Testing JSON Extraction ---")
    json_res = llm.chat(
        lane="fast",
        system="Return only one JSON object, without commentary or markdown.",
        user="Return JSON: {\"ok\": true}",
        agent_name="ProbeScript",
        max_tokens=1024,
    )
    json_data = extract_json(json_res.text)
    print(f"Parsed JSON: {json_data}")
    if not json_data.get("ok"):
        print("[FAIL] JSON extraction failed to extract expected field")
        return 1

    if not fast_res.text.strip() or not deep_res.text.strip():
        print("[FAIL] A lane returned no visible text.")
        return 1
    evidence_path = cfg.data_dir / "nightshift_application_verification.json"
    evidence_path.write_text(json.dumps({
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "base_url": cfg.llm_base_url,
        "model_count": len(models),
        "fast": {"requested": fast_res.model_requested, "returned": fast_res.model_returned,
                 "text": fast_res.text, "latency_ms": fast_res.latency_ms},
        "deep": {"requested": deep_res.model_requested, "returned": deep_res.model_returned,
                 "text": deep_res.text, "latency_ms": deep_res.latency_ms},
        "json_result": json_data,
    }, indent=2), encoding="utf-8")
    print(f"\nPASS Nightshift: fast={fast_res.model_returned} deep={deep_res.model_returned}")
    print(f"Saved sanitized evidence: {evidence_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
