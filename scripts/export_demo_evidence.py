"""Export measured results without inventing missing runs or successful outcomes."""
import csv
import json
from pathlib import Path


def main():
    target = Path('submission/evidence')
    target.mkdir(parents=True, exist_ok=True)
    report = ['# Pipeless demonstration evidence', '',
              'These are recorded executions on Zetaris-provided sample data, not a production incident.',
              'The SQL identity was the existing builder identity used for read-only statements. Reader-role isolation is unverified.', '']
    for mode in ['audit', 'repair-evaluation', 'followup']:
        source = Path('data/demo') / f'{mode}.json'
        if not source.exists():
            report += [f'## {mode}', 'No completed record is available.', '']
            continue
        record = json.loads(source.read_text(encoding='utf-8'))
        result = record['result']
        (target / source.name).write_text(json.dumps(record, indent=2), encoding='utf-8')
        draft = result.get('draft', {})
        with (target / f'{mode}-rows.csv').open('w', newline='', encoding='utf-8-sig') as stream:
            writer = csv.writer(stream)
            writer.writerow(draft.get('columns', []))
            writer.writerows(draft.get('rows', []))
        report += [f'## {mode}', f"Run: {record['run_id']}",
                   f"Pipeline status: {result['status']}. Revisions: {result.get('revisions', 0)}.",
                   f"Initial query source: {record['initial_query_source']}", '']
        if record.get('quality_review'):
            report += ['**Independent review warning:** ' + record['quality_review'], '']
        report += ['### Executed query revisions', '']
        for revision in result.get('revision_history', []):
            report += [f"Revision {revision['revision']}: {revision['returned_rows']} returned rows.",
                       '```sql', revision['sql'], '```', '']
            for check in revision.get('checks', []):
                report += [f"- {check['id']}: {check['status']}. {check['detail']}"]
            report += ['']
        models = sorted({call['model_returned'] for call in record.get('model_calls', []) if call.get('model_returned')})
        report += ['Observed response model IDs: ' + ', '.join(models), '']
        memories = sorted({item['id'] for event in record.get('memory_retrievals', []) for item in event.get('items', []) if item.get('id')})
        report += ['Retrieved memory IDs: ' + (', '.join(memories) or 'none'), '']
    (target / 'REPORT.md').write_text('\n'.join(report), encoding='utf-8')
    print(json.dumps({'evidence_directory': str(target), 'report_saved': True}))


if __name__ == '__main__':
    main()
