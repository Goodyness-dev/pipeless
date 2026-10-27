from __future__ import annotations
import json
import re
import time
from dataclasses import dataclass
from typing import Literal

from openai import OpenAI

from app.config import Config
from app.tracing import Tracer
from app.util import summarize

Lane = Literal["fast", "deep"]


class LLMBudgetExceeded(RuntimeError):
    pass


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResult:
    text: str
    lane: Lane
    model_requested: str
    model_returned: str      # what Nightshift reports it actually used (evidence for the NVIDIA award)
    latency_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None


class NightshiftLLM:
    def __init__(self, cfg: Config, tracer: Tracer, max_calls: int | None = None):
        cfg.require_llm()
        self.cfg = cfg
        self.tracer = tracer
        self.max_calls = max_calls or cfg.max_llm_calls_per_run
        self.calls = 0
        self._client = OpenAI(
            base_url=cfg.llm_base_url,
            api_key=cfg.llm_api_key,
            timeout=180.0,
            max_retries=1,
            default_headers={"meterless-app": cfg.llm_app_name, "meterless-scope": "user"},
        )

    def model_for(self, lane: Lane) -> str:
        return self.cfg.model_fast if lane == "fast" else self.cfg.model_deep

    def discover_models(self) -> list[str]:
        """GET /v1/models. Used at startup and by scripts/check_nightshift.py."""
        listing = self._client.models.list()
        return [m.id for m in listing.data]

    def chat(
        self,
        *,
        lane: Lane,
        system: str,
        user: str,
        agent_name: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        want_json: bool = False,
    ) -> LLMResult:
        if self.calls >= self.max_calls:
            raise LLMBudgetExceeded(f"LLM call budget of {self.max_calls} reached for this run")
        self.calls += 1
        model = self.model_for(lane)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        kwargs = dict(model=model, messages=messages, temperature=temperature)
        if want_json and model.startswith("nvidia/nemotron-"):
            # Supported by Nightshift's shipped NVIDIA adapter. Keep structured
            # responses within their output cap instead of spending it on reasoning.
            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        t0 = time.time()
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            self.tracer.emit(
                agent_name,
                "llm_call",
                f"[{lane}] {user}",
                f"ERROR {exc}",
                target_agent="Nightshift",
                status="error",
                lane=lane,
                model=model,
            )
            raise LLMError(f"Nightshift call failed: {exc}") from exc

        text = (resp.choices[0].message.content or "") if resp.choices else ""
        usage = getattr(resp, "usage", None)
        result = LLMResult(
            text=text,
            lane=lane,
            model_requested=model,
            model_returned=getattr(resp, "model", None) or "unreported",
            latency_ms=int((time.time() - t0) * 1000),
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
        )
        self.tracer.emit(
            agent_name,
            "llm_call",
            f"[{lane}] {user}",
            text,
            target_agent="Nightshift",
            lane=lane,
            model_requested=model,
            model_returned=result.model_returned,
            latency_ms=result.latency_ms,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
        )
        return result

    def chat_json(
        self,
        *,
        lane: Lane,
        system: str,
        user: str,
        agent_name: str,
        temperature: float = 0.1,
        retries: int = 1,
    ) -> tuple[dict, LLMResult]:
        """Ask for JSON; parse robustly; on failure re-ask once with the parse error."""
        sys_prompt = system + "\n\nReturn ONLY one JSON object. No prose, no markdown fences."
        last_err = ""
        for attempt in range(retries + 1):
            prompt = (
                user
                if attempt == 0
                else (user + f"\n\nYour previous reply was not valid JSON ({last_err}). Return ONLY the JSON object.")
            )
            res = self.chat(lane=lane, system=sys_prompt, user=prompt, agent_name=agent_name, temperature=temperature, max_tokens=4096, want_json=True)
            try:
                return extract_json(res.text), res
            except ValueError as exc:
                last_err = str(exc)
                self.tracer.emit(agent_name, "json_parse_error", "structured response", last_err, status="error", retry_count=attempt)
        raise LLMError(f"Model did not return valid JSON after {retries + 1} attempts: {last_err}")

    # Adapter for the Meterless engine "injected generator" contract (file 08).
    def as_generator(self, agent_name: str = "H-MEM"):
        def generator(prompt, attachments=None, system_prompt=None, on_stream=None, abort_signal=None):
            res = self.chat(
                lane="fast",
                system=system_prompt or "You are a precise assistant.",
                user=prompt,
                agent_name=agent_name,
            )
            return {"text": res.text}

        return generator


_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


def extract_json(text: str) -> dict:
    """Tolerant JSON extraction: strips <think> blocks and fences, finds the first balanced {...}."""
    cleaned = _THINK.sub("", text).strip()
    # Some gateways omit the opening reasoning tag but retain its closing tag.
    if "</think>" in cleaned:
        cleaned = cleaned.rsplit("</think>", 1)[1].strip()
    cleaned = _FENCE.sub("", cleaned).strip()
    start = cleaned.find("{")
    if start == -1:
        raise ValueError("no JSON object found")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(cleaned)):
        c = cleaned[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(cleaned[start : i + 1])
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSON: {exc}") from exc
    raise ValueError("unbalanced JSON object")
