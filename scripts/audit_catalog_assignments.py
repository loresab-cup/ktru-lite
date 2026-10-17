"""Read-only audit of saved job assignments against the application's catalog."""

import argparse
import json
import uuid
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import select, text

import api
import search


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--job", required=True)
    args.add_argument("--output", type=Path, required=True)
    options = args.parse_args()
    catalog = search.load_catalog()
    with api.SessionLocal() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        job = session.get(api.Job, uuid.UUID(options.job))
        if not job:
            raise ValueError("Job not found")
        items = session.scalars(
            select(api.Item)
            .where(api.Item.job_id == job.id)
            .order_by(api.Item.row_number)
        ).all()
        rows = []
        for item in items:
            idx = catalog.index.get(item.ktru_code)
            cols = item.attributes.get("columns") or {}
            source_title = cols.get("Наименование товара") or item.raw_text
            rows.append(
                {
                    "item_id": item.id,
                    "row": item.row_number,
                    "input_name": source_title,
                    "raw_text": item.raw_text,
                    "attributes": item.attributes,
                    "assigned_code": item.ktru_code,
                    "saved_name": item.ktru_name,
                    "app_status": item.status,
                    "confidence": item.confidence,
                    "catalog_present": idx is not None,
                    "catalog_name": catalog.names[idx] if idx is not None else None,
                    "catalog_status": catalog.statuses[idx]
                    if idx is not None
                    else None,
                    "catalog_units": catalog.units[idx] if idx is not None else [],
                    "catalog_characteristics": catalog.chars[idx]
                    if idx is not None
                    else [],
                    "catalog_fixed": catalog.fixed[idx] if idx is not None else [],
                    "is_template": bool(catalog.templates[idx])
                    if idx is not None
                    else None,
                    "candidates": [
                        {k: c.get(k) for k in ("code", "name", "status", "score")}
                        for c in item.candidates
                    ],
                }
            )
        grouped = defaultdict(list)
        for row in rows:
            grouped[row["input_name"]].append(row)
        groups = []
        for title, members in grouped.items():
            combos = Counter((r["assigned_code"], r["catalog_name"]) for r in members)
            groups.append(
                {
                    "input": title,
                    "count": len(members),
                    "auto": sum(r["app_status"] == "auto" for r in members),
                    "selected": [
                        {
                            "code": c,
                            "name": n,
                            "count": count,
                            "auto": sum(
                                r["app_status"] == "auto" and r["assigned_code"] == c
                                for r in members
                            ),
                        }
                        for (c, n), count in combos.most_common()
                    ],
                }
            )
        summary = {
            "job_id": str(job.id),
            "filename": job.filename,
            "job_status": job.status,
            "catalog_size": len(catalog.codes),
            "rows": len(rows),
            "distinct_selected_codes": len({r["assigned_code"] for r in rows}),
            "codes_present": sum(r["catalog_present"] for r in rows),
            "catalog_statuses": dict(Counter(r["catalog_status"] for r in rows)),
            "saved_name_mismatches": sum(
                r["saved_name"] != r["catalog_name"] for r in rows
            ),
            "template_assignments": sum(bool(r["is_template"]) for r in rows),
            "app_statuses": dict(Counter(r["app_status"] for r in rows)),
            "input_groups": len(groups),
        }
        result = {
            "summary": summary,
            "groups": groups,
            "rows": rows,
            "method": "read-only PostgreSQL assignments joined by full code to local catalog; existence is not semantic correctness",
        }
    options.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
