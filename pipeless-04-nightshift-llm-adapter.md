# PIPELESS — 04 METERLESS NIGHTSHIFT (LLM) ADAPTER

> Prerequisites: files 01–03. Every model call in Pipeless goes through this one module. This satisfies the **Power rule**: Nightshift is our primary AI provider, and the NVIDIA Nemotron lanes behind it supply the models.

Files created here: `app/adapters/meterless.py`, `scripts/check_nightshift.py`.

---

## 1. What Nightshift is (so you code against the right contract)

Nightshift is the Meterless "AI hotspot" running on the participant's computer. It exposes an **OpenAI-compatible** HTTP API. Our app only needs the Nightshift base URL and its runtime token (both shown in Nightshift's **Console**). **The NVIDIA API key is entered once inside Nightshift's Providers tab and must never appear in our code or `.env`.**

Contract (from the setup guide):
- `Authorization: Bearer <runtime token>`
- header `meterless-app: <our app name>`
- header `meterless-scope: user` for chat
- `GET /v1/models` for model discovery
- `POST /v1/chat/completions` (OpenAI-compatible)
- A working `/healthz` response is **not** proof a real model is connected. Always make a real chat call.
- `meterless/auto` is a routing mode, **not** an NVIDIA model id. It may route to a non-NVIDIA model. To make Nemotron evidence unambiguous, we **pin** specific model ids (`MODEL_FAST`, `MODEL_DEEP`) discovered from `GET /v1/models`.

## 2. Lanes (two models, two jobs)

| Lane | Env var | Used by | Why |
|---|---|---|---|
| `fast` | `MODEL_FAST` (example: `nvidia/nemotron-3.5-lightning-30b-a3b`) | Planner, Onboarder, Investigator, memory extraction | quick responses, tool-like JSON, first attempts |
| `deep` | `MODEL_DEEP` (example: `nvidia/nemotron-3.5-super-vl-120b-a12b`) | **Skeptic**, Planner finalise | complex reasoning, second opinions |

The Skeptic runs on a **different model** than the Investigator on purpose. A second model with adversarial instructions plus deterministic evidence is much less likely to simply agree with the first answer. The model ids are examples; the real ones come from `/v1/models`. If only one Nemotron model is available, use it for both lanes but keep different system prompts and **say so honestly** in the README.

## 3. `app/adapters/meterless.py`

```python
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

    def chat(self, *, lane: Lane, system: str, user: str, agent_name: str,
             temperature: float = 0.2, max_tokens: int | None = None,
             want_json: bool = False) -> LLMResult:
        if self.calls >= self.max_calls:
            raise LLMBudgetExceeded(f"LLM call budget of {self.max_calls} reached for this run")
        self.calls += 1
        model = self.model_for(lane)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        kwargs = dict(model=model, messages=messages, temperature=temperature)
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        t0 = time.time()
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            self.tracer.emit(agent_name, "llm_call", f"[{lane}] {user}", f"ERROR {exc}",
                             target_agent="Nightshift", status="error", lane=lane, model=model)
            raise LLMError(f"Nightshift call failed: {exc}") from exc
        text = (resp.choices[0].message.content or "") if resp.choices else ""
        usage = getattr(resp, "usage", None)
        result = LLMResult(
            text=text, lane=lane, model_requested=model,
            model_returned=getattr(resp, "model", model) or model,
            latency_ms=int((time.time() - t0) * 1000),
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
        )
        self.tracer.emit(agent_name, "llm_call", f"[{lane}] {user}", text, target_agent="Nightshift",
                         lane=lane, model_requested=model, model_returned=result.model_returned,
                         latency_ms=result.latency_ms, prompt_tokens=result.prompt_tokens,
                         completion_tokens=result.completion_tokens)
        return result

    def chat_json(self, *, lane: Lane, system: str, user: str, agent_name: str,
                  temperature: float = 0.1, retries: int = 1) -> tuple[dict, LLMResult]:
        """Ask for JSON; parse robustly; on failure re-ask once with the parse error."""
        sys_prompt = system + "\n\nReturn ONLY one JSON object. No prose, no markdown fences."
        last_err = ""
        for attempt in range(retries + 1):
            prompt = user if attempt == 0 else (
                user + f"\n\nYour previous reply was not valid JSON ({last_err}). Return ONLY the JSON object.")
            res = self.chat(lane=lane, system=sys_prompt, user=prompt, agent_name=agent_name, temperature=temperature)
            try:
                return extract_json(res.text), res
            except ValueError as exc:
                last_err = str(exc)
        raise LLMError(f"Model did not return valid JSON after {retries + 1} attempts: {last_err}")

    # Adapter for the Meterless engine "injected generator" contract (file 08).
    def as_generator(self, agent_name: str = "H-MEM"):
        def generator(prompt, attachments=None, system_prompt=None, on_stream=None, abort_signal=None):
            res = self.chat(lane="fast", system=system_prompt or "You are a precise assistant.",
                            user=prompt, agent_name=agent_name)
            return {"text": res.text}
        return generator


_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


def extract_json(text: str) -> dict:
    """Tolerant JSON extraction: strips <think> blocks and fences, finds the first balanced {...}."""
    cleaned = _THINK.sub("", text).strip()
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
                    return json.loads(cleaned[start:i + 1])
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSON: {exc}") from exc
    raise ValueError("unbalanced JSON object")
```

Design notes:
- `UNKNOWN U6`: we do not rely on `response_format`. JSON is requested in the prompt and parsed tolerantly. If the coding agent confirms Nightshift supports JSON mode, it may add it, but the fallback must stay.
- Nemotron reasoning models may emit `<think>...</think>`; `extract_json` strips it.
- `LLMResult.model_returned` is logged on **every** call. For the NVIDIA award, the logs must visibly show a Nemotron model handled the requests, and Nightshift's own run details can corroborate.
- `max_calls` caps each run: a loop against a paid or rate-limited provider must never run unbounded.

## 4. `scripts/check_nightshift.py` (resolves UNKNOWN U5; run before anything else)

The script must:
1. Build `NightshiftLLM` from `.env`.
2. Print `discover_models()`. Highlight ids containing `nemotron` (case-insensitive).
3. Verify `MODEL_FAST` and `MODEL_DEEP` appear in the list; if not, print the closest matches and exit non-zero.
4. Make one real `chat` call per lane with the prompt `Reply with the single word: ready`, print `model_returned` and latency.
5. Make one `chat_json` call asking for `{"ok": true}` to prove JSON extraction works.
6. Print a final line: `PASS Nightshift: fast=<model_returned> deep=<model_returned>`.

Remind the human to also open Nightshift's run details/logs and confirm the requests were served by a Nemotron lane (for the NVIDIA award evidence).

## 5. Prompting guidance shared by all agents

- Keep system prompts **role-specific** and **adversarial where needed**. A single prompt wrapped as several agents is disqualifying: each agent has its own system prompt, its own inputs and its own output schema (file 05).
- Always pass the **evidence** the agent needs inline (SQL, rows, checks). Do not ask models to remember anything across calls; memory comes from H-MEM injection (file 08).
- Keep temperature low (0.1–0.2) for JSON/SQL tasks.
- Truncate big JSON samples to a configured byte budget before sending (default 12 KB) and say it was truncated.

## 6. Acceptance criteria

- [ ] `scripts/check_nightshift.py` prints PASS and shows the returned model id for both lanes.
- [ ] `extract_json` unit tests: fenced JSON, `<think>` prefix, trailing prose, nested braces inside strings.
- [ ] A run with `MAX_LLM_CALLS_PER_RUN=1` raises `LLMBudgetExceeded` on the second call.
- [ ] Every LLM call emits an `llm_call` trace event containing `model_returned`.
- [ ] No NVIDIA key anywhere in the repo or `.env.example`.
