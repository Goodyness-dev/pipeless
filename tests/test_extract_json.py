from __future__ import annotations
import pytest
from app.adapters.meterless import extract_json


def test_orphan_reasoning_close_uses_only_final_answer():
    assert extract_json('I considered {"wrong": true}.</think>{"ok": true}') == {"ok": True}


def test_extract_simple_json():
    text = '{"key": "value", "num": 42}'
    res = extract_json(text)
    assert res == {"key": "value", "num": 42}


def test_extract_fenced_json():
    text = """```json
    {
        "status": "ok",
        "nested": {"count": 10}
    }
    ```"""
    res = extract_json(text)
    assert res["status"] == "ok"
    assert res["nested"]["count"] == 10


def test_extract_with_think_block():
    text = """<think>
    I should carefully consider what the user asked.
    Let me construct the JSON output.
    </think>
    ```json
    {
        "plan": "proceed",
        "action": "run_sql"
    }
    ```
    """
    res = extract_json(text)
    assert res["plan"] == "proceed"
    assert res["action"] == "run_sql"


def test_extract_with_trailing_prose():
    text = 'Here is the response: {"items": [1, 2, 3]} I hope this helps!'
    res = extract_json(text)
    assert res == {"items": [1, 2, 3]}


def test_extract_with_nested_braces_in_strings():
    text = '{"message": "Look at this regex: {foo} and {bar}", "valid": true}'
    res = extract_json(text)
    assert res["valid"] is True
    assert "{foo}" in res["message"]


def test_extract_invalid_json():
    with pytest.raises(ValueError, match="no JSON object found"):
        extract_json("Just some plain text without any brackets")

    with pytest.raises(ValueError, match="unbalanced JSON object"):
        extract_json('{"unclosed": "brace"')
