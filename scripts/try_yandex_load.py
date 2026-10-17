"""Isolated live extraction load test; leaves application configuration unchanged."""

import argparse
import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import extractor
import parser


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--credentials", type=Path, required=True)
    args.add_argument("--file", type=Path, required=True)
    args.add_argument("--output", type=Path, required=True)
    args.add_argument("--workers", type=int, default=4, choices=range(1, 5))
    options = args.parse_args()
    credentials = json.loads(options.credentials.read_text(encoding="utf-8-sig"))
    key = credentials.get("YANDEX_API_KEY", "").strip()
    folder = credentials.get("YANDEX_FOLDER_ID", "").strip()
    if not key or not folder:
        args.error("Missing credentials; no requests sent")
    started = time.perf_counter()
    rows = parser.parse_file(options.file)
    parse_seconds = time.perf_counter() - started
    texts = list(dict.fromkeys(row.text for row in rows))
    chunks = [texts[i : i + 10] for i in range(0, len(texts), 10)]
    headers = {"Authorization": f"Bearer {key}", "OpenAI-Project": folder}
    events = []
    answers = {}
    with httpx.Client(timeout=45, trust_env=False) as client:

        def extract(task):
            index, chunk = task
            begin = time.perf_counter()
            event = {"batch": index, "rows": len(chunk)}
            result = {text: extractor.rules(text, extractor.ERROR) for text in chunk}
            try:
                response = client.post(
                    "https://ai.api.cloud.yandex.net/v1/chat/completions",
                    headers=headers,
                    json={
                        "model": f"gpt://{folder}/qwen3.6-35b-a3b",
                        "reasoning_effort": "none",
                        "temperature": extractor.TEMPERATURE,
                        "max_tokens": 4096,
                        "stream": False,
                        "messages": [
                            {"role": "system", "content": extractor.SYSTEM_PROMPT},
                            {
                                "role": "user",
                                "content": json.dumps(
                                    [{"id": i, "text": t} for i, t in enumerate(chunk)],
                                    ensure_ascii=False,
                                ),
                            },
                        ],
                    },
                )
                event["status"] = response.status_code
                response.raise_for_status()
                body = response.json()
                choice = body["choices"][0]
                event["usage"] = body.get("usage", {})
                event["finish_reason"] = choice.get("finish_reason")
                if event["finish_reason"] != "stop":
                    raise ValueError("Incomplete model answer")
                by_id = extractor.answer_by_id(
                    extractor.parse_json_answer(choice["message"].get("content") or "")
                )
                for i, text in enumerate(chunk):
                    try:
                        result[text] = (
                            extractor.accept(by_id[i], text)
                            if i in by_id
                            else extractor.rules(text, extractor.MISSING)
                        )
                    except (TypeError, ValueError):
                        pass
            except (
                httpx.HTTPError,
                ValueError,
                KeyError,
                IndexError,
                TypeError,
            ) as exc:
                event["error_type"] = type(exc).__name__
            event["seconds"] = time.perf_counter() - begin
            event["llm_rows"] = sum(a.get("source") == "llm" for a in result.values())
            return event, result

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=options.workers) as pool:
            for event, result in pool.map(extract, enumerate(chunks)):
                events.append(event)
                answers.update(result)
                if len(events) % 10 == 0:
                    print(
                        json.dumps(
                            {
                                "batches_done": len(events),
                                "total": len(chunks),
                                "elapsed": time.perf_counter() - started,
                            }
                        ),
                        flush=True,
                    )
        seconds = time.perf_counter() - started
    attributes = [answers[row.text] for row in rows]
    usage = Counter()
    for event in events:
        usage.update(
            {k: v for k, v in event.get("usage", {}).items() if isinstance(v, int)}
        )
    report = {
        "provider": "Yandex AI Studio",
        "model": "qwen3.6-35b-a3b",
        "records": len(rows),
        "unique_texts": len(texts),
        "parse_seconds": parse_seconds,
        "extraction_seconds": seconds,
        "workers": options.workers,
        "batch_size": 10,
        "reasoning_effort": "none",
        "sources": dict(Counter(a.get("source") for a in attributes)),
        "fallback_reasons": dict(
            Counter(a.get("fallback") for a in attributes if a.get("fallback"))
        ),
        "usage": dict(usage),
        "scope": "extraction only; excludes embeddings, KTRU search, queue, HTTP upload, persistence; no representative accuracy gold",
        "events": events,
        "attributes": attributes,
    }
    options.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in ("events", "attributes")}
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
