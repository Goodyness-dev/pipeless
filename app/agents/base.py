from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from app.adapters.meterless import NightshiftLLM
from app.tracing import Tracer


@dataclass
class AgentContext:
    cfg: Any
    tracer: Tracer
    llm: NightshiftLLM
    memory: Any                 # HMem instance
    run_id: str
    caller_agent: str | None = None


class BaseAgent:
    name = "BaseAgent"

    def __init__(self, ctx: AgentContext):
        self.ctx = ctx

    def handoff(self, target: str, what: str) -> None:
        self.ctx.tracer.emit(self.name, "handoff", what, "", target_agent=target)

    def memory_block(self, query: str, tags: list[str] | None = None) -> str:
        """Retrieve top memories from H-MEM and render prompt block."""
        if not self.ctx.memory:
            return "(none)"
        payload = self.ctx.memory.retrieve(
            query=query,
            agent_id=self.name,
            run_id=self.ctx.run_id,
            tags=tags or [],
        )
        return payload.render_block()
