# PIPELESS — 08 H-MEM MEMORY ENGINE (THE REQUIRED METERLESS ENGINE)

> Prerequisites: files 01–07. The hackathon requires **at least one Meterless Engine implemented in a working feature**, not merely mentioned. We implement a headless **H-MEM** (hierarchical memory) so Pipeless learns from onboarding and verification across runs. Meterless's engines are reference architectures: we implement the services; there is no package to install.

Files created here: `app/memory/hmem.py`, `scripts/three_session_demo.py`.

> **Human step (also satisfies the Build rule):** open `meterless.ai/engines.html`, choose **IMPLEMENT → Give my agent memory**, and paste that official prompt into Cursor's Agent chat alongside this file. Compare our implementation with the official contract/conformance checks it describes and fix any gaps. Keep a note of the result for the submission.

---

## 1. What memory does for Pipeless (concrete, demonstrable)

| Without memory | With H-MEM |
|---|---|
| Every run rediscovers that a source needs backticks on `end`, or that a cold start needs one retry | The lesson from the first run is retrieved and injected into the Onboarder's prompt on the second run |
| Skeptic repeats the same adversarial checks blindly | Skeptic is reminded which failure modes bit us before (for example, a join that dropped a key from this reference list) |
| No record of which models are safe to reuse | Lessons about caveats travel with the model |

**Do not pre-seed memory.** The playbook in file 03 holds *documentation knowledge about Zetaris* (allowed in code). H-MEM holds *lessons learned from real runs*, each with provenance (`run_id`, `agent_id`). Pre-seeding would look like hard-coding.

## 2. Scope (what to build, in order)

**P0 minimum (≈ what Meterless says pays for itself):** `MemoryStoreService` + `MemoryRetrievalService` + audit ledger.
**P1:** mining from runtime events, feedback adaptation, simple conflict handling.
**P2 (only if time):** dreaming/sleep. Skip otherwise and say so in the README.

## 3. Data model

```python
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
    entities: list[str] = field(default_factory=list)     # e.g. host names, source ids, error kinds
    related_to: list[str] = field(default_factory=list)
    supersedes: str | None = None
    derived_from: list[str] = field(default_factory=list)
    agent_id: str = ""
    run_id: str = ""
    task_id: str = ""
```

Tiers: `short_term` lives in process memory for the current run; `long_term` is persisted to `data/memory/long_term.jsonl`. A run starts by **hydrating** long-term memory (warm start). At the end of a successful run, promote short-term memories whose confidence ≥ 0.6 to long-term.

## 4. Store + audit ledger

```python
class HMem:
    def __init__(self, data_dir: Path, tracer, generator=None):
        self.dir = Path(data_dir) / "memory"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lt_path = self.dir / "long_term.jsonl"
        self.audit_path = self.dir / "audit.jsonl"
        self.tracer = tracer
        self.generator = generator            # injected LLM generator (file 04 as_generator); optional
        self._lock = threading.Lock()
        self.short: dict[str, Memory] = {}
        self.long: dict[str, Memory] = {}
        self._hydrate()

    def _hydrate(self) -> None:
        if self.lt_path.exists():
            for line in self.lt_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    m = Memory(**json.loads(line)); self.long[m.id] = m

    def _audit(self, action: str, mem_id: str, actor: str, details: dict | None = None) -> None:
        rec = {"ts": datetime.now(timezone.utc).isoformat(), "memoryId": mem_id, "action": action,
               "actor": actor, "details": details or {}}
        with self._lock, open(self.audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    def add(self, content, *, type="procedural", layer="short_term", tags=None, entities=None,
            agent_id="", run_id="", task_id="", confidence=0.6, namespace="onboarding/repair") -> Memory:
        now = datetime.now(timezone.utc).isoformat()
        m = Memory(id="mem_" + uuid.uuid4().hex[:10], content=content, type=type, layer=layer,
                   timestamp=now, last_accessed=now, tags=tags or [], entities=entities or [],
                   agent_id=agent_id, run_id=run_id, task_id=task_id, confidence=confidence, namespace=namespace)
        self._resolve_conflicts(m)
        (self.short if layer == "short_term" else self.long)[m.id] = m
        if layer == "long_term":
            self._persist(m)
        self._audit("create", m.id, agent_id, {"layer": layer, "tags": m.tags})
        self.tracer.emit("H-MEM", "memory_write", content, m.id, agent_id=agent_id, layer=layer)
        return m

    def _persist(self, m: Memory) -> None:
        with self._lock, open(self.lt_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(m)) + "\n")

    def promote_run(self, run_id: str) -> int:
        n = 0
        for m in list(self.short.values()):
            if m.run_id == run_id and m.confidence >= 0.6:
                m.layer = "long_term"; self.long[m.id] = m; self._persist(m); del self.short[m.id]
                self._audit("promote", m.id, "H-MEM"); n += 1
        return n
```

> Because `long_term.jsonl` is append-only, an updated memory is written as a new line with the same `id`; on hydrate, later lines win. Keep it simple.

## 5. Retrieval (the operational heart)

Hybrid scoring without embeddings (embeddings are optional later through Nightshift if available; **UNKNOWN**: do not assume an embeddings endpoint):

```python
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
        return "\n".join(f"- [{r.memory.id} conf={r.memory.confidence:.2f} score={r.score:.2f}] {r.memory.content}" for r in self.items)
```

```python
    def retrieve(self, *, query: str, agent_id: str, run_id: str, tags: list[str] | None = None,
                 top_n: int = 5, threshold: float = 0.35) -> RetrievalPayload:
        q = _tokens(query); tagset = set(tags or [])
        scored: list[Retrieved] = []
        now = time.time()
        for m in list(self.long.values()) + list(self.short.values()):
            if m.supersedes is None and any(o.supersedes == m.id for o in self.long.values()):
                continue                                         # superseded: skip
            mt = _tokens(m.content) | {e.lower() for e in m.entities}
            kw = len(q & mt) / max(1, len(q | mt))               # Jaccard keyword overlap
            tg = len(tagset & set(m.tags)) / max(1, len(tagset)) if tagset else 0.0
            ent = 1.0 if (q & {e.lower() for e in m.entities}) else 0.0
            age_days = max(0.0, (now - datetime.fromisoformat(m.timestamp).timestamp()) / 86400)
            recency = 1.0 / (1.0 + age_days)
            scope = 0.15 if m.run_id == run_id else 0.0
            score = (0.45 * kw + 0.2 * tg + 0.15 * ent + 0.1 * recency + scope) * LAYER_WEIGHT.get(m.layer, 0.7) \
                    * (0.5 + 0.5 * m.confidence)
            if score >= threshold * 0.6:                         # tune on real data; log all scores
                scored.append(Retrieved(m, score, f"kw={kw:.2f} tag={tg:.2f} ent={ent:.0f}"))
        scored.sort(key=lambda r: r.score, reverse=True)
        items = scored[:top_n]
        for r in items:
            r.memory.access_count += 1; r.memory.last_accessed = datetime.now(timezone.utc).isoformat()
        payload = RetrievalPayload(query=query, strategy="task_focused", items=items)
        self.tracer.emit("H-MEM", "memory_retrieve", query, f"{len(items)} memories",
                         target_agent=agent_id, items=[{"id": r.memory.id, "score": round(r.score, 3), "reason": r.reason} for r in items])
        return payload
```

Notes:
- Tune the threshold against real memories; log every returned item's score so the judge sees retrieval working.
- The payload is **structured** (not a pre-rendered string); `render_block()` is one consumer. Trace metadata (`retrievalReason`, per-memory score, strategy label) is the evidence that an agent reasoned over remembered state.

## 6. Mining: what events become memories

Hook these inside the agents (file 05/06). Each call includes `agent_id`, `run_id`, `task_id`.

| Event | When | Memory content (template) | Tags / entities |
|---|---|---|---|
| `repair_success` | Onboarder fixed a view after an error | "Source host `{host}` (shape `{shape}`): error `{error_kind}` fixed by {fix_summary}." | tags `onboarding,repair,{error_kind}`; entities `[host, shape, error_kind]` |
| `repair_failed` | 3 failed attempts | "Source `{host}` shape `{shape}`: could not fix `{error_kind}` after 3 attempts; last error: {msg}." | tags `onboarding,failed` |
| `source_behaviour` | rate-limit hit, cold start, cache suspect | "Source `{host}` returned `{kind}`; handled by {action}." | tags `source,rate-limit` |
| `skeptic_finding_resolved` | a finding led to a revision that passed | "For questions joining `{left}`→`{right}`: Skeptic found `{claim}`; resolved by {fix}." | tags `verification,join` |
| `verification_caveat` | warn-level check on a model | "Model `{fqn}`: {caveat}." | tags `model,caveat` |
| `run_outcome` | end of run | "Run outcome `{status}` after `{revisions}` revisions; onboarding attempts `{attempts}`." | tags `outcome` |

**No-model fallback (required):** templates above are filled by code, so mining works without any LLM call. Optionally, a fast-lane LLM call (via `generator`) can rewrite the lesson more generally; if that call fails, keep the template text.

## 7. Feedback adaptation (events, not thumbs)

After a run, for each memory that was retrieved into a prompt:
- Run reached `verified` and the memory was relevant → `helpful`: `access_count += 1`, `confidence = min(1.0, confidence + 0.05)`.
- Run `answered_with_caveats` due to the same error kind the memory claimed to prevent → `wrong`: `confidence -= 0.2`, add tag `review`.
- Otherwise → `not_helpful`: `confidence -= 0.05`.
Audit each change.

## 8. Conflict handling (minimal)

`_resolve_conflicts(new)`: if an existing memory shares ≥ 2 entities **and** the same error kind but a different fix text, mark the new one as `supersedes = old.id` when the new one's run ended `verified` and the old one's confidence is lower. Audit `conflict_resolved`. Do not attempt anything fancier.

## 9. Injection sites (this is what makes memory visible)

- `PlannerAgent`: `memory_block(user_query, tags=["verification","model"])`
- `OnboarderAgent`: first thing in `onboard()` and again inside each repair attempt (file 06)
- `InvestigatorAgent` and `SkepticAgent`: tags `["verification","join","model"]`

Every injection logs a `memory_retrieve` event with item ids and scores, and the prompt text actually contains the block, so a judge can compare run 1 (empty) vs run 2 (populated).

## 10. The three-session demo (Meterless: "the strongest demos show three sessions")

`scripts/three_session_demo.py` runs real `POST /run` requests (no fakes) and prints a comparison table from the logs:

1. **Session 1:** onboard a novel source whose JSON has a trap (for example a key needing backticks or a numeric-string field). Memory is empty. Record: onboarding attempts, errors, repairs. `repair_success` lessons are written.
2. **Session 2:** a *different but related* source (same host family or same trap type). Retrieval returns the Session 1 lesson (log shows ids and scores). Record attempts/errors/repairs.
3. **Session 3:** a question that joins models from earlier runs; Skeptic is reminded of the earlier join finding. Record revisions needed.

Output: a small table `session | attempts | repairs | errors | memories_retrieved | seconds`. **Report honestly** whatever the numbers are; if memory did not reduce attempts, say so and show the retrieval trace anyway. Judges reward honest measurement.

## 11. Acceptance criteria

- [ ] `HMem.add` + `retrieve` + `promote_run` work and persist across process restarts (restart test).
- [ ] After a real repair in Session 1, `data/memory/long_term.jsonl` contains a lesson with `run_id`/`agent_id`.
- [ ] In Session 2, the Onboarder's prompt (visible via the logged `llm_call` input or a debug flag) contains the retrieved lesson, and a `memory_retrieve` event lists its id and score.
- [ ] No memory exists before the first run (no seeding). `audit.jsonl` shows every mutation.
- [ ] `three_session_demo.py` produces the comparison table from real logs.
- [ ] The README states which Meterless engine is mounted (H-MEM), where (agents' prompts), and what was *not* built (dreaming/sleep, unless done).
