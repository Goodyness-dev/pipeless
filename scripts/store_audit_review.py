"""Store an explicitly attributed independent review, not a fabricated agent lesson."""
from app.config import Config
from app.memory.hmem import HMem
from app.tracing import Tracer

cfg = Config.load()
run_id = 'independent_review_demo_audit_6342c2ed'
memory = HMem(cfg.data_dir, Tracer(run_id, cfg.log_dir, run_id=run_id))
existing = [m for m in memory.long.values() if m.run_id == run_id]
if existing:
    print('Independent review already stored.')
else:
    item = memory.add(
        'Independent implementation review of run demo_audit_6342c2ed: the pipeline originally approved '
        'the answer, but the semantic compatibility and one-to-one narrative were unsupported. '
        'Only 260 matched distinct keys were measured. For NY_TAXI.taxi_zones and NY_TAXI.zones, '
        'recheck output-key retention, repeated keys and label differences. Do not reuse the original '
        'approval as proof of semantic equivalence. Provenance: archived run quality_review field.',
        type='factual', layer='long_term', tags=['verification', 'join', 'model', 'independent-review'],
        entities=['NY_TAXI.taxi_zones', 'NY_TAXI.zones'], agent_id='IndependentReview',
        run_id=run_id, confidence=0.95, namespace='audit/independent-review')
    print(f'Independent review stored: {item.id}')
