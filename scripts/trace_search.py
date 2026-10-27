"""Trace candidate retrieval on the real catalog, with local Ollama and without embeddings."""

import argparse
import json
from pathlib import Path

import embedder
import extractor
import search

PAIRS = [
    ("Клавиатура", "Клавиатура проводная USB, цвет черный"),
    ("Ноутбук", "Ноутбук экран 15,6 дюйма, оперативная память 16 ГБ"),
    ("Мышь", "Мышь проводная USB, длина провода 1,5 м"),
    ("Бумага", "Бумага офисная А4, плотность 80 г/м2, 500 листов"),
    ("Мыло", "Мыло туалетное твердое, масса 100 г"),
    ("Молоко", "Молоко питьевое пастеризованное, жирность 2,5%, 1 л"),
    ("Стул", "Стул офисный, цвет черный, высота 800 мм"),
    ("Лампа", "Лампа светодиодная LED E27, 10 Вт, 4000 К"),
]


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--output", type=Path, default=Path("/tmp/ktru-results/trace.json"))
    options = args.parse_args()
    catalog = search.load_catalog()
    texts = [text for pair in PAIRS for text in pair]
    attributes = [extractor.rules(text) for text in texts]
    queries = [f"{attrs['search_text']}. {text}" for attrs, text in zip(attributes, texts)]
    vectors = embedder.embed(queries)
    report = {"catalog_size": len(catalog.codes), "embeddings": vectors is not None, "rows": []}
    for i, query in enumerate(queries):
        qw = search.words(query)
        near = catalog._nearest_by_words(qw, False)
        row = {
            "text": texts[i],
            "attributes": attributes[i],
            "words_nearest": [catalog.codes[j] for j, _ in near],
            "words_top5": catalog.search(None, [query])[0],
        }
        if vectors is not None:
            vector = vectors[i : i + 1]
            row["vectors_nearest"] = [
                catalog.codes[j] for j, _ in catalog._nearest_by_vectors(vector, False)[0]
            ]
            row["lexical"] = [
                catalog.codes[j] for j in catalog._lexical(search.stems(query), False)
            ]
            row["vectors_top5"] = catalog.search(vector, [query])[0]
        report["rows"].append(row)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for row in report["rows"]:
        print(row["text"], flush=True)
        for mode in ("words_top5", "vectors_top5"):
            print(mode, [(c["name"], c["raw_score"]) for c in row.get(mode, [])], flush=True)


if __name__ == "__main__":
    main()
