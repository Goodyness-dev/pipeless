"""Preserve the model output while flagging an independently observed overclaim."""
import json
from pathlib import Path

path = Path('data/demo/audit.json')
record = json.loads(path.read_text(encoding='utf-8'))
if record['run_id'] == 'demo_audit_6342c2ed':
    record['quality_review'] = (
        'The pipeline originally approved this run, but its final narrative overclaims semantic compatibility '
        'and one-to-one cardinality. The original check established 260 matched distinct keys only. '
        'It did not measure full output retention, label equivalence or row multiplicity. '
        'This record remains visible as a diagnosed failure, not accepted proof of compatibility. '
        'The verifier now checks output retention even when all source keys match.'
    )
    path.write_text(json.dumps(record, indent=2), encoding='utf-8')
print('Recorded audit review saved without replacing the model response.')
