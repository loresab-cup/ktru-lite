"""Measure a real deployed API job, including LLM coverage; explicit live opt-in required."""

import argparse
import json
import time
from collections import Counter
from pathlib import Path

import httpx


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--base", default="http://127.0.0.1:3000")
    args.add_argument("--file", type=Path, required=True)
    args.add_argument("--output", type=Path, required=True)
    args.add_argument("--timeout", type=float, default=420)
    args.add_argument("--allow-live", action="store_true")
    options = args.parse_args()
    if not options.allow_live:
        args.error("--allow-live required: uploading this file can make paid model requests")
    base = options.base.rstrip("/")
    requests = []
    with httpx.Client(timeout=30, trust_env=False) as client:

        def request(method, path, **kwargs):
            started = time.perf_counter()
            response = client.request(method, base + path, **kwargs)
            requests.append(
                {
                    "method": method,
                    "path": path,
                    "status": response.status_code,
                    "seconds": time.perf_counter() - started,
                }
            )
            response.raise_for_status()
            return response

        health = request("GET", "/api/health").json()
        if health.get("catalog") != "ready":
            raise SystemExit("Catalog is not ready; warm-up is excluded from the measurement")
        started = time.perf_counter()
        with options.file.open("rb") as source:
            response = request("POST", "/api/jobs", files={"file": (options.file.name, source)})
        job_id = response.json()["job_id"]
        if request("GET", f"/api/jobs/{job_id}").json()["status"] == "uploaded":
            request("POST", f"/api/jobs/{job_id}/start")
        last_print = 0
        while True:
            job = request("GET", f"/api/jobs/{job_id}").json()
            elapsed = time.perf_counter() - started
            if elapsed - last_print >= 10 or job["status"] in ("done", "failed"):
                print(
                    json.dumps(
                        {
                            "seconds": round(elapsed, 2),
                            "status": job["status"],
                            "processed": job["processed"],
                            "total": job["total"],
                            "fallback": job.get("fallback"),
                        }
                    ),
                    flush=True,
                )
                last_print = elapsed
            if job["status"] in ("done", "failed") or elapsed >= options.timeout:
                break
            time.sleep(1)
        full_seconds = time.perf_counter() - started
        items = []
        while True:
            page = request(
                "GET", f"/api/jobs/{job_id}/items", params={"limit": 200, "offset": len(items)}
            ).json()
            items.extend(page["items"])
            if not page["items"] or len(items) >= page["total"]:
                break
        report = {
            "live_provider": True,
            "base": base,
            "file": str(options.file),
            "job": job,
            "upload_to_terminal_seconds": full_seconds,
            "under_200_seconds": full_seconds < 200 and job["status"] == "done",
            "under_300_seconds": full_seconds < 300 and job["status"] == "done",
            "records": len(items),
            "unique_texts": len({i["raw_text"] for i in items}),
            "sources": dict(Counter((i.get("attributes") or {}).get("source") for i in items)),
            "fallback_reasons": dict(
                Counter(
                    i["attributes"].get("fallback")
                    for i in items
                    if i["attributes"].get("fallback")
                )
            ),
            "all_rows_from_llm": bool(items)
            and all(i["attributes"].get("source") == "llm" for i in items),
            "statuses": dict(Counter(i["status"] for i in items)),
            "health_before": health,
            "health_after": request("GET", "/api/health").json(),
            "requests": requests,
        }
        if job["status"] == "done":
            for extension in ("csv", "xlsx"):
                response = request(
                    "GET", f"/api/jobs/{job_id}/export", params={"format": extension}
                )
                report[f"export_{extension}_bytes"] = len(response.content)
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        options.output.with_suffix(".items.json").write_text(
            json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {k: v for k, v in report.items() if k not in ("job", "requests")}, ensure_ascii=True
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
