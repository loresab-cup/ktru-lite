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
    return {item["row_number"]: item for item in items}


def counts(rows: dict, ref: dict) -> tuple[int, int, int]:
    right = auto = wrong = 0
    for number, item in rows.items():
        ok = item["ktru_code"] == ref[number]["ktru_code"]
        right += ok
        auto += item["status"] == "auto"
        wrong += item["status"] == "auto" and not ok
    return right, auto, wrong


def main() -> None:
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("before", type=pathlib.Path)
    args.add_argument("after", type=pathlib.Path)
    args.add_argument("--show", type=int, default=10, help="rows listed per set")
    options = args.parse_args()
    broken_any = False
    for name in SETS:
        a, b = options.before / f"{name}.json", options.after / f"{name}.json"
        if not (a.exists() and b.exists()):
            continue
        ref = {k: v for k, v in reference(name).items() if graded(v)}
        before = {k: v for k, v in load(a).items() if k in ref}
        after = {k: v for k, v in load(b).items() if k in ref}
        rows = before.keys() & after.keys()
        right = {k for k in rows if after[k]["ktru_code"] == ref[k]["ktru_code"]}
        was = {k for k in rows if before[k]["ktru_code"] == ref[k]["ktru_code"]}
        fixed, broken = sorted(right - was), sorted(was - right)
        (r1, a1, w1), (r2, a2, w2) = counts(before, ref), counts(after, ref)
        print(
            f"{name:6} exact {r1} -> {r2}, auto {a1} -> {a2}, wrong in auto {w1} -> {w2}, "
            f"fixed {len(fixed)} {fixed[: options.show]}, broken {len(broken)} "
            f"{broken[: options.show]}"
        )
        broken_any |= bool(broken) or w2 > w1
    sys.exit(1 if broken_any else 0)


if __name__ == "__main__":
    main()
