"""Compare legacy and compact embeddings with fixed attributes or synthetic catalog rows.

No external LLM calls. Synthetic labels from this same catalog are not independent gold.
Run with OLLAMA_URL and DATABASE_URL configured, from the backend environment.
"""

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend", type=Path, default=Path(__file__).resolve().parents[1] / "backend"
    )
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--items", type=Path)
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    sys.path.insert(0, str(options.backend))
    import numpy as np

    import api
    import confidence
    import embedder
    import review
    import search

    catalog = search.load_catalog(options.catalog, options.cache)
    if options.items:
        items = json.loads(options.items.read_text(encoding="utf-8"))[: options.count]
    else:
        active = np.flatnonzero(catalog.active)
        indices = active[np.linspace(0, len(active) - 1, options.count, dtype=int)]
        items = []
        for index in indices:
            name = catalog.names[index]
            details = [f"{key}: {values[0]}" for key, values in catalog.fixed[index] if values]
            text = ". ".join([name, *details])
            items.append(
                {
                    "row_number": len(items) + 1,
                    "raw_text": text,
                    "expected": catalog.codes[index],
                    "attributes": {"name": name, "search_text": text, "source": "llm"},
                }
            )
    report = {
        "independent_gold": False,
        "synthetic_gold": not bool(options.items),
        "scope": "fixed attributes; real local embeddings; no LLM/upload; cache cleared per mode",
        "runs": [],
    }
    for mode in ("legacy", "compact"):
        embedder._cache.clear()
        stages = Counter()
        rows = []
        started = time.monotonic()
        for offset in range(0, len(items), 100):
            batch = items[offset : offset + 100]
            attrs = [i["attributes"] for i in batch]
            parsed = [
                api.parser.Item(i["row_number"], i["raw_text"], a.get("columns", {}))
                for i, a in zip(batch, attrs, strict=True)
            ]
            queries = [a["search_text"] + ". " + r.text for a, r in zip(attrs, parsed, strict=True)]
            semantic = queries if mode == "legacy" else [a["search_text"] for a in attrs]
            tick = time.monotonic()
            vectors = embedder.embed(semantic)
            stages["embed"] += time.monotonic() - tick
            if vectors is None:
                raise RuntimeError("No actual embeddings; cannot report this as a valid benchmark")
            tick = time.monotonic()
            found = catalog.search(
                vectors,
                queries,
                units=[api.unit_hint(r, a) for r, a in zip(parsed, attrs, strict=True)],
                names=[api.product_name(r, a) for r, a in zip(parsed, attrs, strict=True)],
            )
            stages["search"] += time.monotonic() - tick
            for source, candidates in zip(batch, found, strict=True):
                rows.append(
                    {
                        "row": source["row_number"],
                        "expected": source.get("expected"),
                        "candidates": candidates,
                        **confidence.assess(candidates),
                    }
                )
        result = {
            "mode": mode,
            "seconds": time.monotonic() - started,
            "stages": dict(stages),
            "auto": sum(r["status"] == "auto" for r in rows),
            "manual": sum(r["status"] != "auto" for r in rows),
            "unique_embedding_queries": len(embedder._cache),
            "rows": rows,
        }
        if not options.items:
            result.update(
                {
                    "exact": sum(
                        bool(r["candidates"]) and r["candidates"][0]["code"] == r["expected"]
                        for r in rows
                    ),
                    "top5": sum(
                        any(c["code"] == r["expected"] for c in r["candidates"]) for r in rows
                    ),
                    "wrong_auto": sum(
                        r["status"] == "auto" and r["candidates"][0]["code"] != r["expected"]
                        for r in rows
                    ),
                }
            )
        else:
            from types import SimpleNamespace

            groups = Counter()
            for source, row in zip(items, rows, strict=True):
                if row["status"] != "auto":
                    key = review.signature(
                        SimpleNamespace(
                            attributes=source["attributes"], candidates=row["candidates"]
                        )
                    )
                    groups[key or str(source["row_number"])] += 1
            result["review_groups"] = len(groups)
        report["runs"].append(result)
        print(json.dumps({k: v for k, v in result.items() if k != "rows"}), flush=True)
    options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
