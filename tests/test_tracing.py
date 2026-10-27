from __future__ import annotations
import json
from pathlib import Path
from app.util import redact
from app.tracing import Tracer


def test_redact_hides_secrets():
    payload = {
        "api_key": "secret123",
        "nested": {
            "token": "tok_xyz",
            "safe_field": "hello world",
            "password_hash": "hash_abc",
        },
        "authorization": "Bearer xyz",
        "regular_list": [{"secret_key": "val1"}, {"ok": "val2"}],
    }
    redacted = redact(payload)
    assert redacted["api_key"] == "***"
    assert redacted["authorization"] == "***"
    assert redacted["nested"]["token"] == "***"
    assert redacted["nested"]["password_hash"] == "***"
    assert redacted["nested"]["safe_field"] == "hello world"
    assert redacted["regular_list"][0]["secret_key"] == "***"
    assert redacted["regular_list"][1]["ok"] == "val2"


def test_tracer_emit(tmp_path: Path):
    tracer = Tracer("test_trace_123", tmp_path, run_id="run_456")
    rec = tracer.emit(
        agent_name="PlannerAgent",
        action="plan_created",
        input_summary="user asked a question",
        output_summary="plan formulated",
        target_agent="OnboarderAgent",
        secret_token="do_not_leak_me",
        count=5,
    )

    assert rec["agent_name"] == "PlannerAgent"
    assert rec["action"] == "plan_created"
    assert rec["secret_token"] == "***"
    assert rec["count"] == 5

    # Check file contents
    log_file = tmp_path / "test_trace_123.jsonl"
    assert log_file.exists()
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["trace_id"] == "test_trace_123"
    assert parsed["run_id"] == "run_456"
    assert parsed["secret_token"] == "***"
