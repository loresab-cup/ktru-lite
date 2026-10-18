"""Synthetic catalog self-retrieval diagnostic; never an end-to-end/model benchmark."""

import argparse
import cProfile
import hashlib
import json
import os
import platform
import sys
import time
from pathlib import Path


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument(
        "--backend", type=Path, default=Path(__file__).resolve().parents[1] / "backend"
    )
    args.add_argument("--catalog", type=Path, required=True)
    args.add_argument("--cache", type=Path, required=True)
    args.add_argument("--output", type=Path, required=True)
    args.add_argument("--count", type=int, default=1000)
    args.add_argument("--profile", type=Path)
    options = args.parse_args()
    sys.path.insert(0, str(options.backend.resolve()))
    import numpy as np

    import confidence
    import search

    started = time.perf_counter()
    catalog = search.load_catalog(options.catalog, options.cache)
    load_seconds = time.perf_counter() - started
    active = np.flatnonzero(catalog.active)
    indices = active[np.linspace(0, len(active) - 1, options.count, dtype=int)]
    names = [catalog.names[i] for i in indices]
    expected = [catalog.codes[i] for i in indices]
    vectors = catalog.vectors[indices]
    runs = []
    profiler = cProfile.Profile() if options.profile else None
    if profiler:
        profiler.enable()
    for _ in range(2):
        found = []
        started = time.perf_counter()
        for offset in range(0, len(names), 100):
            found.extend(
                catalog.search(
                    vectors[offset : offset + 100],
                    names[offset : offset + 100],
                    names=names[offset : offset + 100],
                )
            )
        elapsed = time.perf_counter() - started
        exact = top5 = auto = wrong_auto = manual = 0
        assigned = []
        for code, candidates in zip(expected, found, strict=True):
            best = candidates[0]["code"] if candidates else None
            assigned.append(best)
            exact += best == code
            top5 += any(c["code"] == code for c in candidates)
            verdict = confidence.assess(candidates)
            is_auto = verdict["status"] == "auto"
            auto += is_auto
            wrong_auto += is_auto and best != code
            manual += not is_auto
        runs.append(
            {
                "search_seconds": elapsed,
                "exact_full_code_count": exact,
                "top5_count": top5,
                "auto_count": auto,
                "wrong_auto_count": wrong_auto,
                "manual_review_count": manual,
                "count": len(names),
                "exact_full_code_rate": exact / len(names),
                "top5_rate": top5 / len(names),
                "wrong_auto_fraction_of_all": wrong_auto / len(names),
                "wrong_auto_fraction_of_auto": wrong_auto / auto if auto else None,
                "assignment_sha256": hashlib.sha256(json.dumps(assigned).encode()).hexdigest(),
            }
        )
        print(json.dumps(runs[-1]), flush=True)
    if profiler:
        profiler.disable()
        profiler.dump_stats(str(options.profile))
    report = {
        "scope": "search only; synthetic exact catalog names and catalog vectors",
        "independent_accuracy_evaluation": False,
        "live_llm_calls": 0,
        "new_embedding_calls": 0,
        "upload_to_result_seconds": None,
        "under_200_seconds_verified": False,
        "python": sys.version,
        "platform": platform.platform(),
        "cpu_logical": os.cpu_count(),
        "numpy": np.__version__,
        "catalog_codes": len(catalog.codes),
        "catalog_load_seconds": load_seconds,
        "batch_size": 100,
        "simultaneous_jobs": 1,
        "input_code_sha256": hashlib.sha256(json.dumps(expected).encode()).hexdigest(),
        "runs": runs,
    }
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
