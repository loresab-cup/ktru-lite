"""Synthetic fixture benchmark for Yandex AI Studio; does not change the application.

Run on the host with the backend dependencies installed. Credentials are read only
from the specified local JSON file, never included in reports or command arguments.
"""

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import extractor


def norm(text):
    return " ".join((text or "").lower().replace("ё", "е").split())


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--input", type=Path, required=True)
    args.add_argument("--output", type=Path, required=True)
    args.add_argument("--model", default="qwen3.6-35b-a3b")
    args.add_argument("--credentials", type=Path, required=True)
    options = args.parse_args()
    credentials = json.loads(options.credentials.read_text(encoding="utf-8-sig"))
    key = credentials.get("YANDEX_API_KEY", "").strip()
    folder = credentials.get("YANDEX_FOLDER_ID", "").strip()
    if not key or not folder:
        args.error(
            "Fill YANDEX_API_KEY and YANDEX_FOLDER_ID in the local credentials file; no requests sent"
        )
    fixture = json.loads(options.input.read_text(encoding="utf-8-sig"))
    model_uri = f"gpt://{folder}/{options.model}"
    headers = {"Authorization": f"Bearer {key}", "OpenAI-Project": folder}
    events = []

    with httpx.Client(timeout=180, trust_env=False) as client:

        def extract(texts, label):
            payload = {
                "model": model_uri,
                "stream": False,
                "temperature": extractor.TEMPERATURE,
                "max_tokens": 4096,
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
            if options.model.startswith("qwen"):
                payload["reasoning_effort"] = "none"
            started = time.perf_counter()
            event = {"label": label, "rows": len(texts)}
            result = []
            try:
                response = client.post(
                    "https://ai.api.cloud.yandex.net/v1/chat/completions",
                    json=payload,
                    headers=headers,
                )
                event["status"] = response.status_code
                response.raise_for_status()
                body = response.json()
                choice = body["choices"][0]
                event["finish_reason"] = choice.get("finish_reason")
                event["usage"] = body.get("usage", {})
                event["content"] = choice.get("message", {}).get("content") or ""
                if event["finish_reason"] != "stop":
                    raise ValueError("Incomplete model answer")
                by_id = extractor.answer_by_id(
                    extractor.parse_json_answer(event["content"])
                )
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
            except (
                httpx.HTTPError,
                ValueError,
                KeyError,
                IndexError,
                TypeError,
            ) as exc:
                event["error_type"] = type(exc).__name__
                result = [extractor.rules(text, extractor.ERROR) for text in texts]
            event["seconds"] = time.perf_counter() - started
            event["llm_rows"] = sum(a.get("source") == "llm" for a in result)
            events.append(event)
            print(
                json.dumps({k: v for k, v in event.items() if k != "content"}),
                flush=True,
            )
            return result

        smoke = extract(["Бумага офисная TestBrand А4, 80 г/м2, 500 листов"], "smoke")
        answers = {}
        started = time.perf_counter()
        if smoke[0].get("source") == "llm":
            for offset in range(0, len(fixture["texts"]), 10):
                chunk = fixture["texts"][offset : offset + 10]
                answers.update(
                    zip(
                        chunk,
                        extract(chunk, f"fixture-{offset // 10 + 1}"),
                        strict=True,
                    )
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
        "provider": "Yandex AI Studio; no GigaChat calls",
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
        "settings": {
            "reasoning_effort": "none" if options.model.startswith("qwen") else None,
            "max_tokens": 4096,
            "batch_size": 10,
            "structured_output": "prompt only",
        },
        "scope": "synthetic fixture extraction only, not a full 1000-position performance or accuracy test",
    }
    report["status"] = (
        "complete"
        if len(answers) == len(fixture["texts"])
        and all(a.get("source") == "llm" for a in answers.values())
        else "incomplete"
    )
    if not answers:
        report["fields_correct"] = None
        report["aggregate_percent"] = None
    options.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
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
