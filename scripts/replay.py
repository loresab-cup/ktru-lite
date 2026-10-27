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
import json
import pathlib
import sys


def unit_hints(search, columns: dict, attributes: dict) -> tuple:
    column = None
    for title, value in columns.items():
        column = search.unit_key(value) if title.lower().startswith("ед") else None
        if column:
            break
    return column, search.unit_key(attributes.get("measure") or "")


def replay(catalog, embedder, search, confidence, items: list[dict]) -> list[dict]:
    out = []
    for start in range(0, len(items), 20):
        batch = items[start : start + 20]
        attrs = [{k: v for k, v in i["attributes"].items() if k != "columns"} for i in batch]
        columns = [i["attributes"].get("columns") or {} for i in batch]
        queries = [
            f"{a['search_text']}. {i['raw_text']}" for a, i in zip(attrs, batch, strict=True)
        ]
        hints = [unit_hints(search, c, a) for c, a in zip(columns, attrs, strict=True)]
        found = catalog.search(
            embedder.embed(queries),
            queries,
            False,
            units=[c or g for c, g in hints],
            column_units=[c for c, _ in hints],
            anchors=[i["raw_text"] for i in batch],
        )
        for item, a, cands in zip(batch, attrs, found, strict=True):
            verdict = confidence.assess(cands, is_product=bool(a.get("name")))
            code = cands[0]["code"] if cands else None
            same = [c["code"] for c in cands] == [c["code"] for c in item["candidates"]]
            if same and item["summary"].startswith("Код выбран нейросетью"):
                code = item["ktru_code"]  # the second opinion would pick the same again
            out.append(
                item
                | {
                    "candidates": cands,
                    "ktru_code": code,
                    "confidence": verdict["confidence"],
                    "zone": verdict["zone"],
                    "status": verdict["status"],
                }
            )
    return out


def main() -> None:
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("runs", type=pathlib.Path)
    args.add_argument("out", type=pathlib.Path)
    args.add_argument("--code", default="/app")
    options = args.parse_args()
    sys.path.insert(0, options.code)
    import confidence
    import embedder
    import search

    catalog = search.load_catalog()
    options.out.mkdir(parents=True, exist_ok=True)
    for path in sorted(options.runs.glob("*.json")):
        run = json.loads(path.read_text(encoding="utf-8"))
        items = sorted(run["items"], key=lambda i: i["id"])
        again = {"job": run["job"], "items": replay(catalog, embedder, search, confidence, items)}
        (options.out / path.name).write_text(json.dumps(again, ensure_ascii=False), "utf-8")
        print(f"{path.stem}: {len(items)} rows", flush=True)


if __name__ == "__main__":
    main()
