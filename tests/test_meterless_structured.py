from types import SimpleNamespace
from unittest.mock import MagicMock
from app.adapters.meterless import NightshiftLLM


def test_nemotron_json_call_disables_thinking_without_budget_parameter():
    llm = NightshiftLLM.__new__(NightshiftLLM)
    llm.cfg = SimpleNamespace(model_fast="nvidia/nemotron-3.5-lightning-30b-a3b", model_deep="other")
    llm.calls, llm.max_calls = 0, 3
    llm.tracer = MagicMock()
    llm._client = MagicMock()
    llm._client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))], model=llm.cfg.model_fast, usage=None)
    value, _ = llm.chat_json(lane="fast", system="JSON", user="test", agent_name="Test")
    assert value == {"ok": True}
    sent = llm._client.chat.completions.create.call_args.kwargs
    assert sent["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert "reasoning_budget" not in sent
