"""Accuracy of the running service on a labelled file: codes, top-5, attributes, time.

    python scripts/evaluate.py spec.xlsx reference.jsonl [--api http://127.0.0.1:8000]

reference.jsonl - one object per row: {"row_number": 2, "ktru_code": "...", "name": "...",
"brand": "...", "model": "..."}; row_number is the row of the file (the header is row 1),
name/brand/model are optional. Only the standard library and httpx are needed.
"""

import argparse
import json
import pathlib
import time

import httpx


def norm(text) -> str:
    return " ".join(str(text or "").lower().replace("ё", "е").split())


def run(api: str, path: pathlib.Path) -> tuple[dict, list[dict], float]:
    started = time.monotonic()
    with path.open("rb") as f:
        job_id = httpx.post(f"{api}/api/jobs", files={"file": (path.name, f)}, timeout=120).json()[
            "job_id"
        ]
    while True:
        job = httpx.get(f"{api}/api/jobs/{job_id}", timeout=30).json()
        if job["status"] in ("done", "failed", "cancelled"):
            break
        time.sleep(1)
    seconds = time.monotonic() - started
    items, offset = [], 0
    while True:
        page = httpx.get(
            f"{api}/api/jobs/{job_id}/items", params={"limit": 200, "offset": offset}, timeout=60
        ).json()
        items += page["items"]
        offset += 200
        if offset >= page["total"]:
            return job, items, seconds


def main() -> None:
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("file", type=pathlib.Path)
    args.add_argument("reference", type=pathlib.Path)
    args.add_argument("--api", default="http://127.0.0.1:8000")
    options = args.parse_args()
    lines = options.reference.read_text(encoding="utf-8").splitlines()
    reference = {int(row["row_number"]): row for row in map(json.loads, filter(None, lines))}

    job, items, seconds = run(options.api, options.file)
    print(f"{options.file.name}: {job['status']}, {len(items)} rows in {seconds:.0f} s")
    counts = {"rows": 0, "code": 0, "top5": 0, "auto": 0, "auto_right": 0}
    fields = {"name": [0, 0], "brand": [0, 0], "model": [0, 0]}
    for item in items:
        expected = reference.get(item["row_number"])
        if not expected:
            continue
        right = item["ktru_code"] == expected["ktru_code"]
        counts["rows"] += 1
        counts["code"] += right
        counts["top5"] += any(c["code"] == expected["ktru_code"] for c in item["candidates"])
        if item["status"] == "auto":
            counts["auto"] += 1
            counts["auto_right"] += right
        for field, hits in fields.items():
            if field in expected:
                hits[0] += 1
                hits[1] += norm(item["attributes"].get(field)) == norm(expected[field])
    n = max(1, counts["rows"])
    print(f"exact code:  {counts['code']}/{counts['rows']} = {counts['code'] / n:.1%}")
    print(f"code in top-5: {counts['top5'] / n:.1%}")
    print(f"auto: {counts['auto']} rows, right {counts['auto_right']}")
    for field, (total, right) in fields.items():
        if total:
            print(f"{field}: {right}/{total} = {right / total:.1%}")


if __name__ == "__main__":
    main()
