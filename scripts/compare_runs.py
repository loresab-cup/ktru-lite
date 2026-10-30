"""Two runs of the same sets, row by row: what got fixed, what got broken.

    python scripts/compare_runs.py BEFORE_DIR AFTER_DIR [--show 10]

Both folders hold <set>.json of scripts/bench.py or scripts/replay.py. For every set in both:
exact codes, rows in auto and wrong codes among them before -> after, and the rows whose code
became right or wrong. Exit code 1 when a row got broken, so a regression is not missed.
A change of ranking or confidence is accepted only when no set gets worse (CLAUDE.md).
"""

import argparse
import json
import pathlib
import sys

from bench import SETS, graded, reference


def load(path: pathlib.Path) -> dict[int, dict]:
    items = json.loads(path.read_text(encoding="utf-8"))["items"]
    rows = {item["row_number"]: item for item in items}
    if len(rows) != len(items):
        raise ValueError(f"Duplicate row numbers: {path}")
    return rows


def counts(rows: dict, ref: dict) -> tuple[int, int, int]:
    right = auto = wrong = 0
    for number, item in rows.items():
        ok = item["ktru_code"] == ref[number]["ktru_code"]
        right += ok
        auto += item["status"] == "auto"
        wrong += item["status"] == "auto" and not ok
    return right, auto, wrong


def compare(before: dict, after: dict, ref: dict) -> dict:
    if before.keys() != after.keys() or before.keys() != ref.keys():
        raise ValueError("Before, after and reference must contain exactly the same rows")
    ref = {k: v for k, v in ref.items() if graded(v)}
    before = {k: v for k, v in before.items() if k in ref}
    after = {k: v for k, v in after.items() if k in ref}
    right = {k for k in ref if after[k]["ktru_code"] == ref[k]["ktru_code"]}
    was = {k for k in ref if before[k]["ktru_code"] == ref[k]["ktru_code"]}
    changed = [
        {
            "row_number": k,
            "expected": ref[k]["ktru_code"],
            "before": before[k]["ktru_code"],
            "after": after[k]["ktru_code"],
            "status_before": before[k]["status"],
            "status_after": after[k]["status"],
        }
        for k in sorted(ref)
        if (before[k]["ktru_code"], before[k]["status"])
        != (after[k]["ktru_code"], after[k]["status"])
    ]
    return {
        "before": counts(before, ref),
        "after": counts(after, ref),
        "fixed": sorted(right - was),
        "broken": sorted(was - right),
        "changed": changed,
    }


def main() -> None:
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("before", type=pathlib.Path)
    args.add_argument("after", type=pathlib.Path)
    args.add_argument("--show", type=int, default=10, help="rows listed per set")
    args.add_argument("--sets", nargs="+", choices=tuple(SETS), default=list(SETS))
    args.add_argument(
        "--out", type=pathlib.Path, help="save every changed row, including regressions"
    )
    options = args.parse_args()
    broken_any = False
    report = {}
    for name in options.sets:
        a, b = options.before / f"{name}.json", options.after / f"{name}.json"
        if not (a.exists() and b.exists()):
            args.error(f"Missing run for {name}; use --sets only for an intentional partial check")
        result = compare(load(a), load(b), reference(name))
        report[name] = result
        fixed, broken = result["fixed"], result["broken"]
        (r1, a1, w1), (r2, a2, w2) = result["before"], result["after"]
        print(
            f"{name:6} exact {r1} -> {r2}, auto {a1} -> {a2}, wrong in auto {w1} -> {w2}, "
            f"fixed {len(fixed)} {fixed[: options.show]}, broken {len(broken)} "
            f"{broken[: options.show]}"
        )
        broken_any |= bool(broken) or w2 > w1
    if options.out:
        options.out.parent.mkdir(parents=True, exist_ok=True)
        options.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    sys.exit(1 if broken_any else 0)


if __name__ == "__main__":
    main()
