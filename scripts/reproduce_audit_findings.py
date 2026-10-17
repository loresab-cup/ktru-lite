"""Reproduce uncalibrated automatic acceptance without embeddings; no network."""

import argparse
import json
import sys
from pathlib import Path


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--project", type=Path, required=True)
    args.add_argument("--output", type=Path, required=True)
    options = args.parse_args()
    sys.path.insert(0, str(options.project / "backend"))
    import numpy as np

    import confidence
    import search

    names = ["Кабель медный", "Кабель"]
    catalog = search.Catalog(
        vectors=np.zeros((2, search.DIM), dtype=np.float32),
        codes=["27.32.13.199-00000001", "27.32.13.199-00000002"],
        statuses=["ACTIVE"] * 2,
        names=names,
        okpd2=["27.32.13.199"] * 2,
        code_words=[" ".join(search.words(n)) for n in names],
        chars=[[], []],
        bounds=[search.numeric_bounds([]) for _ in names],
        fixed=[[], []],
        templates=[False] * 2,
        units=[[], []],
    )
    [found] = catalog.search(None, ["Кабель медный"])
    report = {
        "scope": "synthetic deterministic reproduction, no external calls",
        "embeddings": None,
        "candidates": [{k: c[k] for k in ("code", "score", "cosine")} for c in found],
        "verdict": confidence.assess(found),
    }
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))


if __name__ == "__main__":
    main()
