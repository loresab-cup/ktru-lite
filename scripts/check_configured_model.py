"""Run inside backend with --allow-live; uses the selected provider, never prints keys."""

import argparse
import json
import time
from collections import Counter
from pathlib import Path

import extractor


def norm(text):
    return " ".join((text or "").lower().replace("ё", "е").split())


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--input", type=Path, required=True)
    args.add_argument("--output", type=Path, required=True)
    args.add_argument("--allow-live", action="store_true")
    options = args.parse_args()
    if not options.allow_live:
        args.error("--allow-live required: this test makes real API calls")
    if extractor.llm_state() != "on":
        args.error("Selected provider is not configured or is temporarily unavailable")
    fixture = json.loads(options.input.read_text(encoding="utf-8-sig"))
    calls = []
    original = extractor._client.post

    def observed(url, **kwargs):
        started = time.perf_counter()
        event = {}
        try:
            response = original(url, **kwargs)
            event["status"] = response.status_code
            try:
                body = response.json()
                event["usage"] = body.get("usage", {})
                event["stop_reason"] = body.get("stop_reason")
            except (ValueError, AttributeError):
                pass
            return response
        except Exception as exc:
            event["error_type"] = type(exc).__name__
            raise
        finally:
            event["seconds"] = time.perf_counter() - started
            calls.append(event)
            print(json.dumps(event), flush=True)

    extractor._client.post = observed
    try:
        smoke = extractor.extract_chunk(
            ["Бумага офисная TestBrand А4, 80 г/м2, 500 листов"], time.monotonic() + 90
        )
        answers = {}
        started = time.perf_counter()
        if smoke[0].get("source") == "llm":
            for offset in range(0, len(fixture["texts"]), 10):
                chunk = fixture["texts"][offset : offset + 10]
                attrs = extractor.extract_chunk(chunk, time.monotonic() + 90)
                answers.update(zip(chunk, attrs, strict=True))
        seconds = time.perf_counter() - started
    finally:
        extractor._client.post = original
    field_ok = {"name": 0, "brand": 0, "model": 0}
    results = []
    for case in fixture["cases"]:
        used = set()
        for product in case["products"]:
            anchors = product.get("match_anchors", []) + [product["name"]]
            selected = next(
                (
                    (i, text)
                    for i, text in enumerate(case["texts"])
                    if i not in used and any(norm(a) in norm(text) for a in anchors)
                ),
                None,
            )
            attrs = answers.get(selected[1], {}) if selected else {}
            if selected:
                used.add(selected[0])
            correct = {
                "name": norm(attrs.get("name"))
                in {norm(n) for n in product.get("accepted_names", [product["name"]])},
                "brand": norm(attrs.get("brand")) == norm(product.get("brand")),
                "model": norm(attrs.get("model")) == norm(product.get("model")),
            }
            if not attrs:
                correct = {k: False for k in correct}
            for k, value in correct.items():
                field_ok[k] += value
            results.append(
                {
                    "case": case["id"],
                    "expected": {k: product.get(k) for k in field_ok},
                    "actual": {k: attrs.get(k) for k in field_ok},
                    "correct": correct,
                    "source": attrs.get("source"),
                }
            )
    report = {
        "provider": extractor.PROVIDER,
        "model": extractor.MODEL,
        "smoke": smoke,
        "fixture_seconds": seconds,
        "products": len(results),
        "unique_texts": len(fixture["texts"]),
        "fields_correct": field_ok if answers else None,
        "aggregate_percent": 100 * sum(field_ok.values()) / (3 * len(results))
        if answers
        else None,
        "sources": dict(Counter(a.get("source") for a in answers.values())),
        "calls": calls,
        "results": results,
        "answers_by_text": answers,
        "status": "complete"
        if len(answers) == len(fixture["texts"])
        and all(a.get("source") == "llm" for a in answers.values())
        else "incomplete",
        "scope": "synthetic fixture extraction only; not full KTRU accuracy or 1000-row performance acceptance",
    }
    options.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                k: v
                for k, v in report.items()
                if k not in ("smoke", "calls", "results", "answers_by_text")
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
