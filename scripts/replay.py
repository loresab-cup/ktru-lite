"""Search and confidence again on the saved LLM attributes of runs: test a ranking or
confidence change without a single LLM request.

    docker compose cp scripts/replay.py backend:/tmp/replay.py
    docker compose cp runs/ backend:/tmp/runs
    docker compose exec backend python /tmp/replay.py /tmp/runs /tmp/out [--code /tmp/code]
    docker compose cp backend:/tmp/out ./replayed

runs/ holds <set>.json files of scripts/bench.py. --code is a folder with changed backend
modules (search.py, confidence.py); without it the code of the container (/app) is used. A
search.py with another CACHE_VERSION builds its own catalog cache: set CACHE_DIR=/tmp/cache
for it (docker compose exec -e CACHE_DIR=/tmp/cache ...), the cache of the service stays.
The output has the same shape, so scripts/compare_runs.py compares it with the live run.
First replay the unchanged code and compare: it must give the live numbers row for row.
The second opinion is not asked again: its pick is kept when the candidates did not change.
"""

import argparse
import inspect
import json
import pathlib
import sys


def unit_hints(search, columns: dict, attributes: dict) -> tuple:
    column, written = None, False
    for title, value in columns.items():
        if not title.lower().startswith("ед"):
            continue
        written = written or bool(str(value).strip())
        column = search.unit_key(value)
        if column:
            break
    guessed = search.unit_key(attributes.get("measure") or "")
    return column, None if written and column is None else guessed


class FrozenEmbeddings:
    """Only frozen inputs; a missing query is an error, never an external API request."""

    def __init__(self, path):
        import numpy as np

        self.np = np
        self.by_query = None
        if path is not None:
            with np.load(path, allow_pickle=False) as stored:
                queries, vectors = stored["queries"].tolist(), stored["vectors"]
            if vectors.shape != (len(queries), 256) or not np.isfinite(vectors).all():
                raise ValueError("Invalid frozen query vectors")
            if len(set(queries)) != len(queries):
                raise ValueError("Duplicate frozen queries")
            self.by_query = dict(zip(queries, vectors, strict=True))

    def embed(self, queries):
        if self.by_query is None:
            return None
        return self.np.stack([self.by_query[query] for query in queries])


def replay(catalog, embedder, search, confidence, items: list[dict], rank_only=False) -> list[dict]:
    out = []
    for start in range(0, len(items), 20):
        batch = items[start : start + 20]
        attrs = [{k: v for k, v in i["attributes"].items() if k != "columns"} for i in batch]
        columns = [i["attributes"].get("columns") or {} for i in batch]
        queries = [
            f"{a['search_text']}. {i['raw_text']}" for a, i in zip(attrs, batch, strict=True)
        ]
        hints = [unit_hints(search, c, a) for c, a in zip(columns, attrs, strict=True)]
        extra = {}
        if "attributes" in inspect.signature(catalog.search).parameters:
            extra["attributes"] = [a.get("characteristics") or [] for a in attrs]
        found = catalog.search(
            embedder.embed(queries),
            queries,
            False,
            units=[c or g for c, g in hints],
            column_units=[c for c, _ in hints],
            anchors=[i["raw_text"] for i in batch],
            **extra,
        )
        for item, a, cands in zip(batch, attrs, found, strict=True):
            verdict = confidence.assess(cands, is_product=bool(a.get("name")))
            code = cands[0]["code"] if cands else None
            same = [c["code"] for c in cands] == [c["code"] for c in item["candidates"]]
            if not rank_only and same and item["summary"].startswith("Код выбран нейросетью"):
                code = item["ktru_code"]  # the second opinion would pick the same again
                selected = confidence.for_code(cands, code)
                verdict |= {
                    "confidence": selected,
                    "zone": confidence.zone_of(selected),
                    "status": "needs_review",
                }
            out.append(
                item
                | {
                    "candidates": cands,
                    "ktru_code": code,
                    "confidence": verdict["confidence"],
                    "zone": verdict["zone"],
                    "status": verdict["status"],
                    "replay_changed_candidates": not same,
                }
            )
    return out


def main() -> None:
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("runs", type=pathlib.Path)
    args.add_argument("out", type=pathlib.Path)
    args.add_argument("--code", default="/app")
    source = args.add_mutually_exclusive_group(required=True)
    source.add_argument("--vectors", type=pathlib.Path, help="frozen queries/vectors npz")
    source.add_argument("--words-only", action="store_true", help="explicit no-vector fallback")
    args.add_argument(
        "--rank-only", action="store_true", help="compare ranking before second opinion"
    )
    options = args.parse_args()
    sys.path.insert(0, options.code)
    import confidence
    import search

    embedder = FrozenEmbeddings(options.vectors)
    catalog = search.load_catalog()
    options.out.mkdir(parents=True, exist_ok=True)
    for path in sorted(options.runs.glob("*.json")):
        run = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(run, dict) or "items" not in run:
            continue
        items = sorted(run["items"], key=lambda i: i["id"])
        again = {
            "job": run["job"],
            "items": replay(catalog, embedder, search, confidence, items, options.rank_only),
        }
        (options.out / path.name).write_text(json.dumps(again, ensure_ascii=False), "utf-8")
        print(f"{path.stem}: {len(items)} rows", flush=True)


if __name__ == "__main__":
    main()
