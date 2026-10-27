"""Read-only authentication diagnostics. Never print credential values."""
import json
import os
from pathlib import Path

import requests
from dotenv import dotenv_values


def main():
    root = Path(__file__).resolve().parents[1]
    values = dotenv_values(root / ".env")
    token = values.get("METERLESS_RUNTIME_TOKEN") or ""
    inherited = os.environ.get("METERLESS_RUNTIME_TOKEN")
    example_token = dotenv_values(root / ".env.example").get("METERLESS_RUNTIME_TOKEN") or ""
    print(json.dumps({
        "token_present": bool(token),
        "token_length": len(token),
        "is_placeholder": token in {"your-nightshift-runtime-token", "your-nightshift-token", ""},
        "nvidia_key_in_runtime_field": token.startswith("nvapi-"),
        "includes_bearer_prefix": token.lower().startswith("bearer "),
        "has_whitespace": any(c.isspace() for c in token),
        "environment_override_present": inherited is not None,
        "override_matches_file": inherited == token if inherited is not None else None,
        "template_contains_nonplaceholder_token": bool(example_token) and example_token not in {"your-nightshift-runtime-token", "your-nightshift-token"},
    }), flush=True)
    base = (values.get("METERLESS_BASE_URL") or "http://127.0.0.1:8088/v1").rstrip("/")
    try:
        response = requests.get(base + "/models", headers={"Authorization": "Bearer " + token}, timeout=10)
        print(json.dumps({"file_token_http_status": response.status_code}), flush=True)
    except requests.RequestException:
        print("Model endpoint request failed (connection or timeout).", flush=True)


if __name__ == "__main__":
    main()
