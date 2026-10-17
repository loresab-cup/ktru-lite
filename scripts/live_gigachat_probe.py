"""Explicitly authorized live smoke/extraction check; never writes credentials to reports."""

import argparse
import json
import logging
import os
import sys
import time
from collections import Counter
from pathlib import Path


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--project", type=Path, required=True)
    args.add_argument("--file", type=Path)
    args.add_argument("--output", type=Path, required=True)
    args.add_argument("--allow-live", action="store_true")
    options = args.parse_args()
    if not options.allow_live:
        args.error("--allow-live is required: this command makes real model requests")
    for line in (options.project / ".env").read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip().startswith("GIGACHAT_"):
            os.environ[key.strip()] = value.strip().strip('"').strip("'")
    sys.path.insert(0, str(options.project / "backend"))
    import extractor
    import parser

    logging.getLogger("httpx").setLevel(logging.WARNING)
    calls = []
    original = extractor._client.post

    def observed(url, **kwargs):
        begin = time.perf_counter()
        event = {"kind": "oauth" if url == extractor.AUTH_URL else "chat"}
        try:
            response = original(url, **kwargs)
            event["status"] = response.status_code
            try:
                usage = response.json().get("usage", {})
                event["usage"] = {k: v for k, v in usage.items() if isinstance(v, (int, float))}
            except (ValueError, AttributeError):
                pass
            return response
        except Exception as exc:
            event["error_type"] = type(exc).__name__
            raise
        finally:
            event["seconds"] = time.perf_counter() - begin
            calls.append(event)

    extractor._client.post = observed
    texts = (
        [row.text for row in parser.parse_file(options.file)]
        if options.file
        else ["Бумага офисная TestBrand А4, 80 г/м2, 500 листов"]
    )
    begin = time.perf_counter()
    job = extractor.JobExtractor(texts)
    attributes = [job.get(text) for text in texts]
    elapsed = time.perf_counter() - begin
    report = {
        "live_provider": True,
        "model": extractor.MODEL,
        "parallel_limit": extractor.PARALLEL_REQUESTS,
        "job_budget_seconds": extractor.JOB_BUDGET,
        "records": len(texts),
        "unique_texts": len(set(texts)),
        "seconds": elapsed,
        "sources": dict(Counter(a.get("source") for a in attributes)),
        "fallback_reasons": dict(
            Counter(a.get("fallback") for a in attributes if a.get("fallback"))
        ),
        "all_rows_from_llm": all(a.get("source") == "llm" for a in attributes),
        "under_200_seconds": elapsed < 200,
        "under_300_seconds": elapsed < 300,
        "scope": "real extraction only; excludes embeddings, search, queue, storage, HTTP upload",
        "calls": calls,
    }
    if not options.file:
        report["synthetic_smoke_attributes"] = attributes
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in ("calls", "synthetic_smoke_attributes")},
            ensure_ascii=True,
        ),
        flush=True,
    )
    extractor._pool.shutdown(wait=True)
    extractor._client.close()


if __name__ == "__main__":
    main()
