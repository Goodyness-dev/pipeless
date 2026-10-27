"""SupportAgent: A real calling agent that queries Pipeless via HTTP API."""
from __future__ import annotations
import json
import os
from pathlib import Path
import requests

from app.adapters.meterless import NightshiftLLM
from app.config import Config
from app.tracing import Tracer

PIPELESS_URL = os.getenv("PIPELESS_URL", "http://localhost:8000")
TOKEN = os.getenv("PIPELESS_API_TOKEN", "choose-a-long-random-string")

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "X-Caller-Agent": "SupportAgent",
    "Content-Type": "application/json",
}

SYSTEM = """You are SupportAgent, a customer-support assistant. You do not have direct access to company data.
When a customer question needs company data, reply with JSON:
{{"action": "ask_pipeless", "user_query": "...", "sources": [...]}}
using only the data sources listed below.
When you have an answer from Pipeless, reply with JSON:
{{"action": "reply", "message": "..."}}
and never claim more than the verified result and caveats say.
Available sources:
{sources_json}
"""


def run_support_agent(customer_question: str, sources: list[dict]):
    cfg = Config.load()
    tracer = Tracer("trace_support_agent", Path(cfg.log_dir))
    llm = NightshiftLLM(cfg, tracer)

    print(f"\n[SupportAgent] Customer asks: '{customer_question}'")
    step1, _ = llm.chat_json(
        lane="fast",
        system=SYSTEM.format(sources_json=json.dumps(sources, indent=2)),
        user=customer_question,
        agent_name="SupportAgent",
    )

    if step1.get("action") == "ask_pipeless":
        print("[SupportAgent] Delegating data query to Pipeless (POST /run)...")
        payload = {
            "request_id": f"support_req_{os.urandom(4).hex()}",
            "user_query": step1.get("user_query", customer_question),
            "sources": step1.get("sources", sources),
        }
        resp = requests.post(f"{PIPELESS_URL}/run", headers=HEADERS, json=payload, timeout=900)
        verified_data = resp.json()

        print(f"[SupportAgent] Pipeless replied with status: {verified_data.get('status')}")
        step2, _ = llm.chat_json(
            lane="fast",
            system=SYSTEM.format(sources_json="[]"),
            user=f"Pipeless returned: {json.dumps(verified_data)[:6000]}",
            agent_name="SupportAgent",
        )
        final_message = step2.get("message", "")
        print(f"[SupportAgent] Final answer to customer: {final_message}")
        return final_message, verified_data

    return step1.get("message", "I cannot help with this question."), None


if __name__ == "__main__":
    sources_sample = [
        {"id": "sic_edgar", "kind": "catalog", "recipe_id": "sic_edgar_usl"}
    ]
    q = "Can you check if any company's EDGAR industry description does not match the official SIC reference list?"
    run_support_agent(q, sources_sample)
