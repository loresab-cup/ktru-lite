"""Measure extraction against a frozen reference. Runs paid configured LLM requests."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, '/app' if Path('/app/extractor.py').exists() else str(Path(__file__).resolve().parents[1] / 'backend'))
import extractor


def evaluate(reference, split='all'):
    rows = [json.loads(line) for line in reference.read_text(encoding='utf-8').splitlines() if line]
    rows = [r for r in rows if split == 'all' or r['split'] == split]
    started = time.monotonic()
    job = extractor.JobExtractor([r['text'] for r in rows])
    results = []
    for row in rows:
        got = job.get(row['text'])
        matches = {k: extractor.norm(got.get(k) or '') == extractor.norm(row[k]) for k in ('name', 'brand', 'model')}
        results.append({'row_number': row['row_number'], 'text': row['text'], 'expected': {k: row[k] for k in matches}, 'actual': got, 'matches': matches})
    fields = {}
    for key in ('name', 'brand', 'model'):
        present = [r for r in results if r['expected'][key]]
        absent = [r for r in results if not r['expected'][key]]
        fields[key] = {'correct': sum(r['matches'][key] for r in results), 'total': len(results), 'accuracy': sum(r['matches'][key] for r in results) / len(results), 'present_correct': sum(r['matches'][key] for r in present), 'present_total': len(present), 'absent_correct': sum(r['matches'][key] for r in absent), 'absent_total': len(absent)}
    return {'reference_sha256': hashlib.sha256(reference.read_bytes()).hexdigest(), 'split': split, 'seconds': round(time.monotonic()-started, 3), 'fields': fields, 'fallback': sum(bool(r['actual'].get('fallback')) for r in results), 'passed': all(f['accuracy'] >= .9 for f in fields.values()), 'results': results}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reference', type=Path)
    parser.add_argument('--split', choices=['all', 'development', 'holdout'], default='all')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.reference, args.split)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k != 'results'}, ensure_ascii=False))
