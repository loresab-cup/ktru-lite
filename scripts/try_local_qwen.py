"""Run inside backend container; call the local Ollama only, never the paid provider.

Input contains synthetic fixture rows and the unchanged extraction prompt.
Example: python try_local_qwen.py --input /tmp/qwen-input.json --output /tmp/qwen-result.json
"""

import argparse
import json
import time
from collections import Counter
from pathlib import Path

import httpx

import extractor


def norm(text):
    return " ".join((text or "").lower().replace("ё", "е").split())


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--input", type=Path, required=True)
    args.add_argument("--output", type=Path, required=True)
    args.add_argument("--model", default="qwen3:1.7b")
    options = args.parse_args()
    fixture = json.loads(options.input.read_text(encoding="utf-8-sig"))
    events = []
    properties = {
        key: {"type": "string"} for key in ("name", "brand", "model", "search_text", "measure")
    }
    properties["id"] = {"type": "integer"}
    properties["characteristics"] = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {key: {"type": "string"} for key in ("name", "value", "unit")},
            "required": ["name", "value", "unit"],
            "additionalProperties": False,
        },
    }
    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }

    with httpx.Client(timeout=180, trust_env=False) as client:

        def extract(texts, label):
            payload = {
                "model": options.model,
                "stream": False,
                "think": False,
                "keep_alive": "10m",
                "format": schema,
                "options": {
                    "temperature": extractor.TEMPERATURE,
                    "seed": 42,
                    "num_ctx": 8192,
                    "num_predict": 4096,
                },
                "messages": [
                    {"role": "system", "content": fixture["system_prompt"]},
                    {
                        "role": "user",
                        "content": json.dumps(
                            [{"id": i, "text": text} for i, text in enumerate(texts)],
                            ensure_ascii=False,
                        ),
                    },
                ],
            }
            started = time.perf_counter()
            event = {"label": label, "rows": len(texts)}
            result = []
            try:
                response = client.post("http://ollama:11434/api/chat", json=payload)
                event["status"] = response.status_code
                response.raise_for_status()
                body = response.json()
                event.update(
                    {
                        key: body.get(key)
                        for key in (
                            "done_reason",
                            "total_duration",
                            "load_duration",
                            "prompt_eval_count",
                            "prompt_eval_duration",
                            "eval_count",
                            "eval_duration",
                        )
                    }
                )
                event["thinking_characters"] = len(body.get("message", {}).get("thinking") or "")
                event["content"] = body.get("message", {}).get("content") or ""
                by_id = extractor.answer_by_id(extractor.parse_json_answer(event["content"]))
                for index, text in enumerate(texts):
                    try:
                        attributes = (
                            extractor.accept(by_id[index], text)
                            if index in by_id
                            else extractor.rules(text, extractor.MISSING)
                        )
                    except (TypeError, ValueError):
                        attributes = extractor.rules(text, extractor.ERROR)
                    result.append(attributes)
            except (httpx.HTTPError, ValueError) as exc:
                event["error_type"] = type(exc).__name__
                result = [extractor.rules(text, extractor.ERROR) for text in texts]
            event["seconds"] = time.perf_counter() - started
            event["llm_rows"] = sum(a.get("source") == "llm" for a in result)
            events.append(event)
            print(json.dumps({k: v for k, v in event.items() if k != "content"}), flush=True)
            return result

        smoke = extract(["Бумага офисная TestBrand А4, 80 г/м2, 500 листов"], "smoke")
        answers = {}
        started = time.perf_counter()
        if smoke[0].get("source") == "llm":
            for offset in range(0, len(fixture["texts"]), 10):
                chunk = fixture["texts"][offset : offset + 10]
                answers.update(
                    zip(chunk, extract(chunk, f"fixture-{offset // 10 + 1}"), strict=True)
                )
        seconds = time.perf_counter() - started
    field_ok = {"name": 0, "brand": 0, "model": 0}
    results = []
    for case in fixture["cases"]:
        used = set()
        for product in case["products"]:
            anchors = product.get("match_anchors", []) + [product["name"]]
            candidates = [
                (i, text)
                for i, text in enumerate(case["texts"])
                if i not in used and any(norm(a) in norm(text) for a in anchors)
            ]
            selected = candidates[0] if candidates else None
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
                correct = {key: False for key in correct}
            for key, value in correct.items():
                field_ok[key] += value
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
        "model": options.model,
        "provider": "local Ollama; no GigaChat calls",
        "smoke": smoke,
        "fixture_seconds": seconds,
        "products": len(results),
        "unique_texts": len(fixture["texts"]),
        "fields_correct": field_ok,
        "aggregate_percent": 100 * sum(field_ok.values()) / (3 * len(results)),
        "sources": dict(Counter(a.get("source") for a in answers.values())),
        "events": events,
        "results": results,
        "answers_by_text": answers,
        "settings": {"think": False, "num_ctx": 8192, "num_predict": 4096, "batch_size": 10},
        "scope": "synthetic fixture extraction only, not a full 1000-position performance or accuracy test",
    }
    options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                k: v
                for k, v in report.items()
                if k not in ("smoke", "events", "results", "answers_by_text")
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
