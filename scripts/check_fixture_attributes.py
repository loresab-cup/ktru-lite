"""Small live synthetic fixture check; not a representative acceptance gold set."""

import argparse
import json
import os
import sys
import time
from pathlib import Path


def norm(text):
    return " ".join((text or "").lower().replace("ё", "е").split())


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--project", type=Path, required=True)
    args.add_argument("--fixtures", type=Path, required=True)
    args.add_argument("--output", type=Path, required=True)
    args.add_argument("--allow-live", action="store_true")
    options = args.parse_args()
    if not options.allow_live:
        args.error("--allow-live required")
    for line in (options.project / ".env").read_text(encoding="utf-8-sig").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            if key.strip().startswith("GIGACHAT_"):
                os.environ[key.strip()] = value.strip().strip('"').strip("'")
    sys.path.insert(0, str(options.project / "backend"))
    import extractor
    import parser

    manifest = json.loads((options.fixtures / "manifest.json").read_text(encoding="utf-8"))
    cases = []
    texts = []
    for case in manifest["cases"]:
        if case["id"] not in {
            "text_utf8",
            "csv_cp1251",
            "docx_table",
            "pdf_text",
            "xlsx_multisheet",
            "unknown_utf16",
            "duplicates_csv",
        }:
            continue
        expected = json.loads((options.fixtures / case["expected"]).read_text(encoding="utf-8"))
        rows = parser.parse_file(options.fixtures / case["file"])
        cases.append((case["id"], rows, expected["products"]))
        texts.extend(r.text for r in rows)
    started = time.perf_counter()
    job = extractor.JobExtractor(texts)
    answers = {text: job.get(text) for text in texts}
    results = []
    field_ok = {"name": 0, "brand": 0, "model": 0}
    for case, rows, expected in cases:
        used = set()
        for product in expected:
            anchors = product.get("match_anchors", []) + [product["name"]]
            candidates = [
                (index, row)
                for index, row in enumerate(rows)
                if index not in used and any(norm(anchor) in norm(row.text) for anchor in anchors)
            ]
            selected = candidates[0] if candidates else None
            attrs = answers[selected[1].text] if selected else {}
            if selected:
                used.add(selected[0])
            correct = {
                "name": norm(attrs.get("name"))
                in {norm(n) for n in product.get("accepted_names", [product["name"]])},
                "brand": norm(attrs.get("brand")) == norm(product.get("brand")),
                "model": norm(attrs.get("model")) == norm(product.get("model")),
            }
            if selected is None:
                correct = {key: False for key in correct}
            for key, value in correct.items():
                field_ok[key] += value
            results.append(
                {
                    "case": case,
                    "matched": bool(selected),
                    "expected": {k: product.get(k) for k in field_ok},
                    "actual": {k: attrs.get(k) for k in field_ok},
                    "source": attrs.get("source"),
                    "correct": correct,
                }
            )
    report = {
        "model": extractor.MODEL,
        "synthetic_fixtures_only": True,
        "representative_gold": False,
        "seconds": time.perf_counter() - started,
        "products": len(results),
        "unique_texts": len(answers),
        "fields_correct": field_ok,
        "accuracy_percent": {k: 100 * v / len(results) for k, v in field_ok.items()},
        "aggregate_percent": 100 * sum(field_ok.values()) / (3 * len(results)),
        "results": results,
        "answers_by_text": answers,
    }
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps({k: v for k, v in report.items() if k not in ("results", "answers_by_text")}),
        flush=True,
    )
    extractor._pool.shutdown(wait=True)
    extractor._client.close()


if __name__ == "__main__":
    main()
