"""Measure canonical-name rescue on saved rows, using real local embeddings."""

import json
import re
from pathlib import Path

import embedder
import search


def canonical(text, names):
    first = re.split(r"\.\s|[,;:]", text, maxsplit=1)[0].lower().replace("ё", "е").split()
    for end in range(len(first), 0, -1):
        name = " ".join(first[:end])
        if name in names:
            return name
    return None


def main():
    catalog = search.load_catalog()
    names = {}
    for i, name in enumerate(catalog._names_lower):
        if catalog.active[i]:
            names.setdefault(name, []).append(i)
    report = []
    for folder, stem in [
        ("real_produkty", "produkty"),
        ("eis", "ООЗ_мыло_крем"),
        ("modified", "ООЗ_мыло_крем_изм"),
        ("organizers", "Спецификация_292"),
        ("modified", "Спецификация_292_изм"),
        ("organizers", "Спецификация_1000"),
        ("modified", "Спецификация_1000_изм"),
    ]:
        source = Path("/tmp/baseline") / f"{stem}_без_КТРУ.items.json"
        items = json.loads(source.read_text())
        reference = {
            r["row_number"]: r["ktru_code"]
            for r in map(
                json.loads,
                (Path("/tmp/dataset") / folder / f"{stem}_reference.jsonl")
                .read_text()
                .splitlines(),
            )
        }
        before = sum(i["ktru_code"] == reference[i["row_number"]] for i in items)
        result = {
            "file": stem,
            "rows": len(items),
            "before": before,
            "after": before,
            "changes": [],
        }
        for item in items:
            text = f"{item['attributes']['search_text']}. {item['raw_text']}"
            name = canonical(text, names)
            if name is None:
                first = re.search(r"[а-яА-ЯёЁa-zA-Z]{3,}", text)
                anchor = search.words(first.group()) if first else set()
                if not anchor or anchor & search.words(item.get("ktru_name") or ""):
                    continue
                head = re.split(r"\.\s|[,;:]", text, maxsplit=1)[0].lower().split()
                qualifiers = {
                    word[:5] for word in search.words(" ".join(head[1:]))
                    if re.search(r"[а-я]", word)
                }
                if qualifiers & search.stems(item.get("ktru_name") or ""):
                    continue
            elif search.words(name) <= search.words(item.get("ktru_name") or ""):
                continue
            vector = embedder.embed([text])
            assert vector is not None
            if name is None:
                found = catalog._anchor_words(
                    text, item["candidates"], False, None, None, vector[0]
                )
            else:
                pool = names[name]
                cosines = catalog.vectors[pool] @ vector[0]
                near = [(i, float(score)) for i, score in zip(pool, cosines)]
                found = catalog._rank(
                    search.words(text),
                    search.query_bounds(text),
                    search.stems(text),
                    near,
                    text=text,
                )
            actual = catalog._anchor_vectors(text, item["candidates"], vector[0], False, None, None)
            assert found == actual
            old = item["ktru_code"] == reference[item["row_number"]]
            new = found[0]["code"] == reference[item["row_number"]]
            result["after"] += int(new) - int(old)
            result["changes"].append(
                {
                    "row": item["row_number"],
                    "old": old,
                    "new": new,
                    "before": item["ktru_name"],
                    "after": found[0]["name"],
                }
            )
        report.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    Path("/tmp/vector-experiment.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
