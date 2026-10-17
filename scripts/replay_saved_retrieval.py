"""Replay current search against saved Yandex attributes, without model calls or DB writes.

Run inside backend with /app on PYTHONPATH:
  python replay_saved_retrieval.py --audit before.json --output after.json
"""

import argparse
import json
import time
from pathlib import Path

import api
import embedder
import parser


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument('--audit', type=Path, required=True)
    args.add_argument('--output', type=Path, required=True)
    options = args.parse_args()
    audit = json.loads(options.audit.read_text(encoding='utf-8'))
    catalog = api.search.load_catalog()
    results = []
    started = time.monotonic()
    for offset in range(0, len(audit['rows']), api.BATCH):
        batch = audit['rows'][offset:offset + api.BATCH]
        rows = [parser.Item(r['row'], r['raw_text'], r['attributes'].get('columns', {})) for r in batch]
        attributes = [r['attributes'] for r in batch]
        queries = [f"{a['search_text']}. {r.text}" for a, r in zip(attributes, rows, strict=True)]
        vectors = embedder.embed(queries)
        if vectors is None:
            raise RuntimeError('Embeddings unavailable: refusing a non-comparable fallback run')
        candidates = catalog.search(vectors, queries,
            units=[api.unit_hint(r, a) for r, a in zip(rows, attributes, strict=True)],
            names=[api.product_name(r, a) for r, a in zip(rows, attributes, strict=True)])
        for source, row, attrs, found in zip(batch, rows, attributes, candidates, strict=True):
            verdict = api.confidence.assess(found, bool(attrs.get('name')))
            best = found[0] if found else {}
            results.append({'row': row.row_number, 'input_name': source['input_name'],
                'before_code': source['assigned_code'], 'before_name': source['catalog_name'],
                'assigned_code': best.get('code'), 'catalog_name': best.get('name'),
                'candidates': found, **verdict})
    report = {'rows': results, 'elapsed_seconds': time.monotonic() - started,
              'new_llm_calls': 0, 'db_writes': 0,
              'timing_scope': 'search and confidence only, with saved extraction'}
    options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'rows': len(results), 'elapsed_seconds': report['elapsed_seconds']}))


if __name__ == '__main__':
    main()
