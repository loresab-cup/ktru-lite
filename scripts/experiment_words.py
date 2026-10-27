"""Measure a name-anchored words-only retrieval proposal before changing production ranking."""

import json
import re
from pathlib import Path

from trace_search import PAIRS

import extractor
import parser
import search


def original(catalog, text):
    qw = search.words(text)
    near = catalog._nearest_by_words(qw, False)
    return catalog._rank(qw, search.query_bounds(text), search.stems(text), near, text=text)


def anchored(catalog, text):
    """Keep the leading product word when the catalog has matching names."""
    qw = search.words(text)
    first = re.search(r"[а-яА-ЯёЁa-zA-Z]{3,}", text)
    anchor = search.words(first.group()) if first else set()
    before = original(catalog, text)
    if before and anchor & search.words(before[0]["name"]):
        return before
    postings = catalog._name_postings()
    pool = set()
    for word in anchor:
        pool.update(int(i) for i in postings.get(word, []))
    pool = sorted(i for i in pool if catalog.active[i])
    if not pool:
        return before
    nearest = [
        (i, search.WORDS_ONLY_BASE * len(qw & search.words(catalog.names[i])) / max(1, len(qw)))
        for i in pool
    ]
    return catalog._rank(qw, search.query_bounds(text), search.stems(text), nearest, text=text)


def main():
    catalog = search.load_catalog()
    report = {"datasets": [], "pairs": []}
    root = Path("/tmp/dataset")
    for folder, name in [
        ("real_produkty", "produkty"),
        ("eis", "ООЗ_мыло_крем"),
        ("modified", "ООЗ_мыло_крем_изм"),
        ("organizers", "Спецификация_292"),
        ("modified", "Спецификация_292_изм"),
        ("organizers", "Спецификация_1000"),
        ("modified", "Спецификация_1000_изм"),
    ]:
        expected = {
            r["row_number"]: r["ktru_code"]
            for r in map(
                json.loads, (root / folder / f"{name}_reference.jsonl").read_text().splitlines()
            )
        }
        counts = {"file": name, "rows": 0, "before": 0, "after": 0, "lost": [], "gained": []}
        for row in parser.parse_file(root / folder / f"{name}_без_КТРУ.xlsx"):
            if row.row_number not in expected:
                continue
            text = f"{extractor.rules(row.text)['search_text']}. {row.text}"
            before = original(catalog, text)
            after = anchored(catalog, text)
            assert after == catalog.search(None, [text])[0]
            right = expected[row.row_number]
            b = bool(before and before[0]["code"] == right)
            a = bool(after and after[0]["code"] == right)
            counts["rows"] += 1
            counts["before"] += b
            counts["after"] += a
            if b and not a:
                counts["lost"].append(row.row_number)
            if a and not b:
                counts["gained"].append(row.row_number)
        report["datasets"].append(counts)
        print(json.dumps(counts, ensure_ascii=False), flush=True)
    for pair in PAIRS:
        for text in pair:
            report["pairs"].append(
                {
                    "text": text,
                    "before": original(catalog, text),
                    "after": anchored(catalog, text),
                }
            )
    Path("/tmp/ktru-results/words-experiment.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
