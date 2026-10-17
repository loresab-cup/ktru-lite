"""Run inside backend: search existing synthetic extraction results without DB writes."""

import argparse
import json
import time
import uuid
from collections import Counter
from pathlib import Path

import api
import embedder
import parser
import search


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--file", type=Path, required=True)
    args.add_argument("--extracted", type=Path, required=True)
    args.add_argument("--output", type=Path, required=True)
    options = args.parse_args()
    extracted = json.loads(options.extracted.read_text(encoding="utf-8"))
    rows = parser.parse_file(options.file)
    attributes = extracted["attributes"]
    if len(rows) != len(attributes):
        raise ValueError("Row count mismatch")
    started = time.perf_counter()
    catalog = search.load_catalog()
    catalog_seconds = time.perf_counter() - started
    items = []
    batches = []
    job_id = uuid.uuid4()
    started = time.perf_counter()
    for offset in range(0, len(rows), api.BATCH):
        begin = time.perf_counter()
        chunk = rows[offset : offset + api.BATCH]
        attrs = attributes[offset : offset + api.BATCH]
        queries = [
            f"{a['search_text']}. {r.text}" for r, a in zip(chunk, attrs, strict=True)
        ]
        units = [api.unit_hint(r, a) for r, a in zip(chunk, attrs, strict=True)]
        vectors = embedder.embed(queries)
        found = catalog.search(vectors, queries, False, units)
        for row, attr, candidates in zip(chunk, attrs, found, strict=True):
            item = api.build_item(job_id, row, attr, candidates)
            items.append(
                {
                    "row_number": row.row_number,
                    "raw_text": row.text,
                    "attributes": attr,
                    "status": item.status,
                    "ktru_code": item.ktru_code,
                    "ktru_name": item.ktru_name,
                    "confidence": item.confidence,
                    "summary": item.summary,
                }
            )
        event = {
            "processed": offset + len(chunk),
            "seconds": time.perf_counter() - begin,
            "semantic_vectors": vectors is not None,
        }
        batches.append(event)
        print(json.dumps(event), flush=True)
    seconds = time.perf_counter() - started
    report = {
        "model": extracted["model"],
        "records": len(items),
        "catalog_seconds": catalog_seconds,
        "search_and_confidence_seconds": seconds,
        "extraction_seconds": extracted["extraction_seconds"],
        "combined_compute_seconds": extracted["parse_seconds"]
        + extracted["extraction_seconds"]
        + seconds,
        "statuses": dict(Counter(i["status"] for i in items)),
        "all_batches_semantic": all(e["semantic_vectors"] for e in batches),
        "scope": "separately measured extraction + production embedding/search/confidence functions; excludes upload, queue, database writes and exports; not an end-to-end benchmark or accuracy acceptance",
        "batches": batches,
        "items": items,
    }
    options.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps({k: v for k, v in report.items() if k not in ("items", "batches")}),
        flush=True,
    )


if __name__ == "__main__":
    main()
