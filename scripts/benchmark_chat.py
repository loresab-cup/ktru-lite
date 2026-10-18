"""Offline bounded load through real extraction and HTTP API; never calls a paid API.

Run: python scripts/benchmark_chat.py --output reports/benchmark-after.json
Use --backend-root PATH for an untouched baseline; --catalog PATH for the real dump.
Provider timings below measure a synthetic transport, not model inference.
"""

import argparse
import csv
import ctypes
import io
import json
import logging
import os
import statistics
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from generate_chat_data import ROOT, generate


def peak_memory_mb():
    if os.name != "nt":
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

    class Memory(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong)] + [
            (name, ctypes.c_size_t)
            for name in (
                "peak",
                "working",
                "peak_paged",
                "paged",
                "peak_nonpaged",
                "nonpaged",
                "pagefile",
                "peak_pagefile",
            )
        ]

    memory = Memory()
    memory.cb = ctypes.sizeof(memory)
    handle = ctypes.windll.kernel32.GetCurrentProcess
    handle.restype = ctypes.c_void_p
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulong,
    ]
    if ctypes.windll.psapi.GetProcessMemoryInfo(handle(), ctypes.byref(memory), memory.cb):
        return memory.peak / 1024**2
    return None


def summary(samples, elapsed, cpu):
    times = sorted(s[0] for s in samples)

    def percentile(p):
        return times[min(len(times) - 1, int((len(times) - 1) * p))]

    return {
        "requests": len(samples),
        "success": sum(s[1] for s in samples),
        "failed": sum(not s[1] for s in samples),
        "timeouts": sum(s[2] for s in samples),
        "wall_seconds": elapsed,
        "mean_seconds": statistics.mean(times),
        "median_seconds": statistics.median(times),
        "p95_seconds": percentile(0.95) if len(times) >= 20 else None,
        "p99_seconds": percentile(0.99) if len(times) >= 100 else None,
        "requests_per_second": len(samples) / elapsed,
        "cpu_seconds": cpu,
        "cpu_percent_one_core": 100 * cpu / elapsed,
        "process_peak_memory_mb": peak_memory_mb(),
    }


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--backend-root", type=Path, default=ROOT / "backend")
    args.add_argument("--catalog", type=Path)
    args.add_argument("--output", type=Path, default=ROOT / "reports/benchmark.json")
    options = args.parse_args()
    sys.path.insert(0, str(options.backend_root.resolve()))
    started = time.perf_counter()
    rows = generate(ROOT / "eval")
    generation_seconds = time.perf_counter() - started
    # Do not read .env or expose real credentials. All external transports are replaced.
    os.environ["GIGACHAT_CREDENTIALS"] = "synthetic-not-a-secret"
    os.environ["DATABASE_URL"] = "sqlite:///unused-benchmark.db"
    import httpx
    import numpy as np
    from fastapi.testclient import TestClient

    import embedder
    import extractor
    import search

    logging.getLogger("extractor").setLevel(logging.ERROR)
    lock = threading.Lock()
    timings, transport_calls = [], []

    def provider(request):
        begin = time.perf_counter()
        if str(request.url) == extractor.AUTH_URL:
            result = {"access_token": "synthetic-token", "expires_at": (time.time() + 3600) * 1000}
            kind = "oauth"
        else:
            kind = "chat"
            payload = json.loads(request.content)
            source = json.loads(payload["messages"][1]["content"])
            time.sleep(0.005)
            answer = [
                {
                    "id": row["id"],
                    "name": row["text"].split(".")[0],
                    "characteristics": [],
                    "search_text": row["text"][:400],
                }
                for row in source
            ]
            result = {"choices": [{"message": {"content": json.dumps(answer, ensure_ascii=False)}}]}
        with lock:
            transport_calls.append(kind)
            if kind == "chat":
                timings.append(time.perf_counter() - begin)
        return httpx.Response(200, json=result)

    original_client = extractor._client
    mock = httpx.Client(transport=httpx.MockTransport(provider))
    extractor._client = mock
    extractor.CREDENTIALS = "synthetic-not-a-secret"
    extractor._token = {"value": "", "expires": 0}
    extractor._down_until = 0
    embedder.embed = lambda texts, **kwargs: None  # explicitly not embedding performance
    report = {
        "provider": "httpx.MockTransport; 5 ms synthetic service delay; no real inference",
        "model_configuration": extractor.MODEL,
        "provider_parallel_limit": extractor.PARALLEL_REQUESTS,
        "dataset_records": len(rows),
        "generation_seconds": generation_seconds,
        "catalog": str(options.catalog) if options.catalog else "8 synthetic codes",
        "embedding": "disabled: real word-search fallback exercised",
        "extraction": {},
        "api_jobs": {},
    }
    try:
        for mode, count, concurrency, batch in [("A", 20, 1, 1), ("B", 1000, 2, 10)] + [
            (f"C-{n}", 200, n, 10) for n in (1, 2, 4, 8)
        ]:
            texts = [r["text"] for r in rows[:count]]
            chunks = [texts[i : i + batch] for i in range(0, len(texts), batch)]
            before = len(transport_calls)

            def extract(chunk):
                begin = time.perf_counter()
                result = extractor.extract_chunk(chunk, time.monotonic() + extractor.JOB_BUDGET)
                good = all(r.get("source") == "llm" for r in result)
                timeout = any(
                    r.get("fallback") in (extractor.BUDGET, extractor.UNAVAILABLE) for r in result
                )
                return time.perf_counter() - begin, good, timeout

            begin, cpu = time.perf_counter(), time.process_time()
            with ThreadPoolExecutor(concurrency) as pool:
                samples = list(pool.map(extract, chunks))
            result = summary(samples, time.perf_counter() - begin, time.process_time() - cpu)
            result.update(
                records=count,
                concurrency=concurrency,
                batch_size=batch,
                transport_calls=len(transport_calls) - before,
            )
            report["extraction"][mode] = result

        with tempfile.TemporaryDirectory(prefix="ktru-benchmark-") as temporary:
            temporary = Path(temporary)
            os.environ.update(
                DATABASE_URL=f"sqlite:///{temporary / 'jobs.db'}",
                UPLOAD_DIR=str(temporary / "uploads"),
                WORKERS="2",
            )
            begin = time.perf_counter()
            if options.catalog:
                catalog = search.load_catalog(options.catalog, temporary / "cache")
            else:
                names = [r["text"].split(".")[0] for r in rows[3:11]]
                catalog = search.Catalog(
                    vectors=np.zeros((8, search.DIM), dtype=np.float32),
                    codes=[f"01.11.11.111-{i:08d}" for i in range(8)],
                    statuses=["ACTIVE"] * 8,
                    names=names,
                    okpd2=["01.11.11.111"] * 8,
                    code_words=[" ".join(search.words(n)) for n in names],
                    chars=[[] for _ in names],
                    bounds=[search.numeric_bounds([]) for _ in names],
                    fixed=[[] for _ in names],
                    templates=[False] * 8,
                    units=[[] for _ in names],
                )
            report["catalog_load_seconds"] = time.perf_counter() - begin
            report["catalog_codes"] = len(catalog.codes)
            search.load_catalog = lambda: catalog
            import api

            with TestClient(api.app) as client:
                for _ in range(100):
                    if client.get("/api/health").json()["catalog"] == "ready":
                        break
                    time.sleep(0.01)

                def upload_job(count):
                    buffer = io.StringIO()
                    writer = csv.writer(buffer, delimiter=";")
                    writer.writerow(["Наименование", "Кол-во"])
                    writer.writerows([r["text"], "1"] for r in rows[:count])
                    begin = time.perf_counter()
                    response = client.post(
                        "/api/jobs",
                        files={"file": ("synthetic.csv", buffer.getvalue().encode(), "text/csv")},
                    )
                    if response.status_code != 202:
                        return time.perf_counter() - begin, False, False
                    url = f"/api/jobs/{response.json()['job_id']}"
                    client.post(f"{url}/start").raise_for_status()
                    while time.perf_counter() - begin < 180:
                        job = client.get(url).json()
                        if job["status"] in ("done", "failed"):
                            good = (
                                job["status"] == "done"
                                and job["processed"] == count
                                and job["fallback"] == 0
                            )
                            if good:
                                good = client.get(url + "/export?format=csv").status_code == 200
                            return time.perf_counter() - begin, good, False
                        time.sleep(0.05)
                    return time.perf_counter() - begin, False, True

                for mode, counts, concurrency in [("A", [1] * 5, 1), ("B", [1000], 1)] + [
                    (f"C-{n}", [10] * n, n) for n in (1, 2, 4, 8)
                ]:
                    begin, cpu = time.perf_counter(), time.process_time()
                    with ThreadPoolExecutor(concurrency) as pool:
                        samples = list(pool.map(upload_job, counts))
                    result = summary(
                        samples, time.perf_counter() - begin, time.process_time() - cpu
                    )
                    result.update(
                        records=sum(counts),
                        concurrency=concurrency,
                        request_unit="complete upload-to-done job including CSV export and 50 ms polling",
                    )
                    report["api_jobs"][mode] = result
            api.engine.dispose()
        report["synthetic_provider_calls"] = {
            kind: transport_calls.count(kind) for kind in ("oauth", "chat")
        }
        report["synthetic_service_seconds"] = {
            "total": sum(timings),
            "mean": statistics.mean(timings),
        }
        report["provider_retries"] = 0
        report["provider_rate_limits"] = 0
    finally:
        extractor._client = original_client
        mock.close()
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(options.output)


if __name__ == "__main__":
    main()
