"""All labelled sets through the running service: one line of numbers per set, rows saved.

    python scripts/bench.py OUT_DIR [set ...] [--api http://127.0.0.1:8000] [--keep]

Sets: real eis eism 292 292m 1000 1000m human primer (all when none is given). For each set
the file is uploaded, the finished job and its items go to OUT_DIR/<set>.json (the input of
scripts/replay.py and scripts/compare_runs.py), and the job is deleted unless --keep.
Each run costs LLM requests: a full run of all sets is about 3 600 rows. The site has accounts:
set EVAL_LOGIN and EVAL_PASSWORD (a user of backend/users.py).
Metrics are the ones of scripts/evaluate.py; human_1000 counts only its 770 rows with a known
code, its incomplete and service rows only by how many went to auto.
"""

import argparse
import json
import pathlib

import evaluate
import httpx

ROOT = pathlib.Path(__file__).resolve().parent.parent
D = ROOT / "dataset"
SETS = {
    "real": ("real_produkty/produkty_без_КТРУ.xlsx", "real_produkty/produkty_reference.jsonl"),
    "eis": ("eis/ООЗ_мыло_крем_без_КТРУ.xlsx", "eis/ООЗ_мыло_крем_reference.jsonl"),
    "eism": (
        "modified/ООЗ_мыло_крем_изм_без_КТРУ.xlsx",
        "modified/ООЗ_мыло_крем_изм_reference.jsonl",
    ),
    "292": (
        "organizers/Спецификация_292_без_КТРУ.xlsx",
        "organizers/Спецификация_292_reference.jsonl",
    ),
    "292m": (
        "modified/Спецификация_292_изм_без_КТРУ.xlsx",
        "modified/Спецификация_292_изм_reference.jsonl",
    ),
    "1000": (
        "organizers/Спецификация_1000_без_КТРУ.xlsx",
        "organizers/Спецификация_1000_reference.jsonl",
    ),
    "1000m": (
        "modified/Спецификация_1000_изм_без_КТРУ.xlsx",
        "modified/Спецификация_1000_изм_reference.jsonl",
    ),
    "human": (
        "human_2026_10_28/human_1000.xlsx",
        "human_2026_10_28/human_1000.reference.jsonl",
    ),
    "primer": ("../frontend/public/primer.xlsx", "primer/primer_reference.jsonl"),
}


def reference(name: str) -> dict:
    lines = (D / SETS[name][1]).read_text(encoding="utf-8").splitlines()
    return {int(row["row_number"]): row for row in map(json.loads, filter(None, lines))}


def graded(row: dict) -> bool:
    return row.get("label_status", evaluate.SURE) == evaluate.SURE


def numbers(client: httpx.Client, name: str, job: dict, items: list[dict]) -> dict:
    ref = reference(name)
    result = evaluate.evaluate(client, items, {k: v for k, v in ref.items() if graded(v)}, False)
    line = {
        "set": name,
        "rows": result["rows"],
        "exact": result["code"],
        "meaning": result["meaning"],
        "top5": result["top5"],
        "auto": result["auto"],
        "wrong_in_auto": result["auto"] - result["auto_right"],
        "seconds": round((job.get("stage_seconds") or {}).get("total", 0)),
        "rules": job.get("fallback") or 0,
    }
    others = {k: v["label_status"] for k, v in ref.items() if not graded(v)}
    for status in sorted(set(others.values())):
        rows = [i for i in items if others.get(i["row_number"]) == status]
        line[f"{status}_in_auto"] = f"{sum(i['status'] == 'auto' for i in rows)}/{len(rows)}"
    return line


def main() -> None:
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("out", type=pathlib.Path)
    args.add_argument("sets", nargs="*", help=f"some of: {' '.join(SETS)}")
    args.add_argument("--api", default="http://127.0.0.1:8000")
    args.add_argument("--keep", action="store_true", help="do not delete the jobs")
    options = args.parse_args()
    unknown = set(options.sets) - set(SETS)
    if unknown:
        args.error(f"unknown sets: {', '.join(sorted(unknown))}")
    options.out.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=options.api, timeout=120) as client:
        evaluate.log_in(client)  # EVAL_LOGIN and EVAL_PASSWORD
        for name in options.sets or list(SETS):
            job_id = evaluate.upload(client, D / SETS[name][0])
            job, items = evaluate.run(client, job_id)
            saved = {"job": job, "items": items}
            (options.out / f"{name}.json").write_text(
                json.dumps(saved, ensure_ascii=False), encoding="utf-8"
            )
            print(json.dumps(numbers(client, name, job, items), ensure_ascii=False), flush=True)
            if not options.keep:
                client.delete(f"/api/jobs/{job_id}")


if __name__ == "__main__":
    main()
