from __future__ import annotations
import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class Memory:
    id: str
    content: str
    type: str                       # procedural | factual | preference | general
    layer: str                      # short_term | working | long_term
    timestamp: str
    last_accessed: str
    access_count: int = 0
    tags: list[str] = field(default_factory=list)
    confidence: float = 0.6
    source: str = "event"           # event | feedback | consolidation
    domain: str = "zetaris-onboarding"
    namespace: str = "onboarding/repair"
    entities: list[str] = field(default_factory=list)
    related_to: list[str] = field(default_factory=list)
    supersedes: str | None = None
    derived_from: list[str] = field(default_factory=list)
    agent_id: str = ""
    run_id: str = ""
    task_id: str = ""


_WORD = re.compile(r"[a-z0-9_]{3,}")


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


LAYER_WEIGHT = {"long_term": 1.0, "working": 0.85, "short_term": 0.7}


@dataclass
class Retrieved:
    memory: Memory
    score: float
    reason: str


@dataclass
class RetrievalPayload:
    query: str
    strategy: str                   # minimal | task_focused | comprehensive
    items: list[Retrieved]

    def render_block(self) -> str:
        if not self.items:
            return "(none)"
        return "\n".join(
            f"- [{r.memory.id} conf={r.memory.confidence:.2f} score={r.score:.2f}] {r.memory.content}"
            for r in self.items
        )


class HMem:
    def __init__(self, data_dir: Path, tracer, generator=None):
        self.dir = Path(data_dir) / "memory"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lt_path = self.dir / "long_term.jsonl"
        self.audit_path = self.dir / "audit.jsonl"
        self.tracer = tracer
        self.generator = generator            # optional LLM generator
        self._lock = threading.Lock()
        self.short: dict[str, Memory] = {}
        self.long: dict[str, Memory] = {}
        self._hydrate()

    def _hydrate(self) -> None:
        if self.lt_path.exists():
            for line in self.lt_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        m = Memory(**json.loads(line))
                        self.long[m.id] = m
                    except Exception:
                        pass

    def _audit(self, action: str, mem_id: str, actor: str, details: dict | None = None) -> None:
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "memoryId": mem_id,
            "action": action,
            "actor": actor,
            "details": details or {},
        }
        with self._lock, open(self.audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    def _persist(self, m: Memory) -> None:
        with self._lock, open(self.lt_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(m)) + "\n")

    def _resolve_conflicts(self, new_m: Memory) -> None:
        for old in self.long.values():
            if old.supersedes is None:
                common_entities = set(new_m.entities) & set(old.entities)
                if len(common_entities) >= 2 and new_m.confidence > old.confidence:
                    new_m.supersedes = old.id
                    self._audit("conflict_resolved", new_m.id, "H-MEM", {"superseded": old.id})
                    break

    def add(
        self,
        content: str,
        *,
        type: str = "procedural",
        layer: str = "short_term",
        tags: list[str] | None = None,
        entities: list[str] | None = None,
        agent_id: str = "",
        run_id: str = "",
        task_id: str = "",
        confidence: float = 0.6,
        namespace: str = "onboarding/repair",
    ) -> Memory:
        now = datetime.now(timezone.utc).isoformat()
        m = Memory(
            id="mem_" + uuid.uuid4().hex[:10],
            content=content,
            type=type,
            layer=layer,
            timestamp=now,
            last_accessed=now,
            tags=tags or [],
            entities=entities or [],
            agent_id=agent_id,
            run_id=run_id,
            task_id=task_id,
            confidence=confidence,
            namespace=namespace,
        )
        self._resolve_conflicts(m)
        (self.short if layer == "short_term" else self.long)[m.id] = m
        if layer == "long_term":
            self._persist(m)
        self._audit("create", m.id, agent_id, {"layer": layer, "tags": m.tags})
        if self.tracer:
            self.tracer.emit("H-MEM", "memory_write", content, m.id, agent_id=agent_id, layer=layer)
        return m

    def promote_run(self, run_id: str) -> int:
        n = 0
        for m in list(self.short.values()):
            if m.run_id == run_id and m.confidence >= 0.6:
                m.layer = "long_term"
                self.long[m.id] = m
                self._persist(m)
                del self.short[m.id]
                self._audit("promote", m.id, "H-MEM")
                n += 1
        return n

    def retrieve(
        self,
        *,
        query: str,
        agent_id: str,
        run_id: str,
        tags: list[str] | None = None,
        top_n: int = 5,
        threshold: float = 0.35,
    ) -> RetrievalPayload:
        q = _tokens(query)
        tagset = set(tags or [])
        scored: list[Retrieved] = []
        now = time.time()

        all_memories = list(self.long.values()) + list(self.short.values())
        for m in all_memories:
            if m.supersedes is None and any(o.supersedes == m.id for o in self.long.values()):
                continue

            mt = _tokens(m.content) | {e.lower() for e in m.entities}
            kw = len(q & mt) / max(1, len(q | mt)) if (q | mt) else 0.0
            tg = len(tagset & set(m.tags)) / max(1, len(tagset)) if tagset else 0.0
            ent = 1.0 if (q & {e.lower() for e in m.entities}) else 0.0

            try:
                age_days = max(0.0, (now - datetime.fromisoformat(m.timestamp).timestamp()) / 86400)
            except Exception:
                age_days = 0.0
            recency = 1.0 / (1.0 + age_days)
            scope = 0.15 if m.run_id == run_id else 0.0

            score = (
                (0.45 * kw + 0.2 * tg + 0.15 * ent + 0.1 * recency + scope)
                * LAYER_WEIGHT.get(m.layer, 0.7)
                * (0.5 + 0.5 * m.confidence)
            )

            if score >= threshold * 0.6:
                scored.append(Retrieved(m, score, f"kw={kw:.2f} tag={tg:.2f} ent={ent:.0f}"))

        scored.sort(key=lambda r: r.score, reverse=True)
        items = scored[:top_n]
        for r in items:
            r.memory.access_count += 1
            r.memory.last_accessed = datetime.now(timezone.utc).isoformat()

        payload = RetrievalPayload(query=query, strategy="task_focused", items=items)
        if self.tracer:
            self.tracer.emit(
                "H-MEM",
                "memory_retrieve",
                query,
                f"{len(items)} memories",
                target_agent=agent_id,
                items=[{"id": r.memory.id, "score": round(r.score, 3), "reason": r.reason} for r in items],
            )
        return payload
