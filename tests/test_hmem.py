from __future__ import annotations
import json
from pathlib import Path
from app.memory.hmem import HMem


def test_hmem_lifecycle_and_persistence(tmp_path: Path):
    tracer = None
    hmem1 = HMem(tmp_path, tracer)

    # 1. Add short-term memory
    m1 = hmem1.add(
        content="Host company-dns requires backticks on `end` column",
        type="procedural",
        layer="short_term",
        tags=["onboarding", "repair", "backticks"],
        entities=["company-dns", "unresolved_column"],
        run_id="run_001",
        confidence=0.8,
    )
    assert m1.id.startswith("mem_")
    assert m1.id in hmem1.short

    # 2. Promote run memories to long_term
    promoted = hmem1.promote_run("run_001")
    assert promoted == 1
    assert m1.id not in hmem1.short
    assert m1.id in hmem1.long

    # 3. Simulate process restart by instantiating new HMem
    hmem2 = HMem(tmp_path, tracer)
    assert m1.id in hmem2.long
    assert hmem2.long[m1.id].content == m1.content

    # 4. Retrieve memory matching query
    payload = hmem2.retrieve(
        query="error resolving end column in company-dns",
        agent_id="OnboarderAgent",
        run_id="run_002",
        tags=["onboarding", "repair"],
    )
    assert len(payload.items) > 0
    assert payload.items[0].memory.id == m1.id
    assert "backticks" in payload.items[0].memory.content

    # 5. Check audit trail
    audit_file = tmp_path / "memory" / "audit.jsonl"
    assert audit_file.exists()
    audit_lines = audit_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(audit_lines) >= 2  # create + promote
    assert any("create" in l for l in audit_lines)
    assert any("promote" in l for l in audit_lines)
