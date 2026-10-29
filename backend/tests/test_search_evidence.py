"""Weak evidence and unrelated attributes must not become confident automatic codes."""

import numpy as np
import pytest

import confidence
import search


def catalog(names, characteristics=None):
    chars = characteristics or [[] for _ in names]
    return search.Catalog(
        vectors=np.eye(len(names), dtype=np.float32),
        codes=[str(i) for i in range(len(names))],
        statuses=["ACTIVE"] * len(names),
        names=names,
        okpd2=[""] * len(names),
        code_words=[" ".join(search.words(n)) for n in names],
        chars=[search.brief_chars(c)[0] for c in chars],
        bounds=[search.numeric_bounds(c, named=True) for c in chars],
        fixed=[[] for _ in names],
        templates=[False] * len(names),
        units=[[] for _ in names],
    )


@pytest.mark.parametrize(
    "evidence",
    [
        {"score": 0.4},
        {"score": 0.95, "words_only": True},
        {"score": 0.95, "characteristic_clashes": 1},
    ],
)
def test_large_margin_does_not_hide_weak_or_contradictory_evidence(evidence):
    first = {"code": "a", "name": "Товар", "status": "ACTIVE", **evidence}
    found = [first, {"code": "b", "score": 0.35}]
    verdict = confidence.assess(found)
    assert verdict["status"] == "needs_review" and verdict["confidence"] < confidence.HIGH
    assert verdict["confidence"] == confidence.with_confidences(found)[0]["confidence"]
    assert verdict["confidence"] == confidence.for_code(found, "a")


def test_ram_is_not_a_numeric_match_for_ssd():
    c = catalog(
        ["Ноутбук"], [[{"name": "Ёмкость SSD", "values": [{"min": 16, "max": 16, "unit": "ГБ"}]}]]
    )
    query = search.scoped_numbers(
        "оперативная память 16 ГБ", [{"name": "Оперативная память", "value": "16", "unit": "ГБ"}]
    )
    assert c._numeric_fit(0, query) == (0, 0)
    ssd = search.scoped_numbers(
        "Ёмкость SSD 16 ГБ", [{"name": "Ёмкость SSD", "value": "16", "unit": "ГБ"}]
    )
    assert c._numeric_fit(0, ssd) == (1, 0)


def test_literal_product_name_is_evidence_even_with_moderate_vector_similarity():
    found = [
        {"code": "paper", "name": "Бумага", "status": "ACTIVE", "score": 0.7, "name_match": True},
        {"code": "other", "score": 0.5},
    ]
    assert confidence.assess(found)["status"] == "auto"


def test_named_numeric_conflict_is_exposed():
    c = catalog(
        ["Ноутбук"],
        [[{"name": "Оперативная память", "values": [{"min": 8, "max": 8, "unit": "ГБ"}]}]],
    )
    query = search.scoped_numbers(
        "Оперативная память 16 ГБ", [{"name": "Оперативная память", "value": "16", "unit": "ГБ"}]
    )
    assert c._numeric_fit(0, query) == (0, 1)


@pytest.mark.parametrize("prefix", ["Для офиса:", "Поставка:", "Нам требуется:"])
def test_service_prefix_does_not_replace_semantic_candidates(prefix):
    c = catalog(["Ноутбук", "Средство для мытья посуды"])
    initial = [c.brief(0) | {"score": 0.9, "raw_score": 0.9, "cosine": 0.9}]
    found = c._anchored(
        prefix + " ноутбук, 16 ГБ",
        "Ноутбук. оперативная память: 16 ГБ",
        initial,
        np.array([1, 0], dtype=np.float32),
        None,
        None,
        False,
    )
    assert found[0]["code"] == "0"


def test_unnamed_same_unit_does_not_mix_different_attributes():
    c = catalog(
        ["Ноутбук"],
        [
            [
                {"name": name, "values": [{"min": 16, "max": 16, "unit": "ГБ"}]}
                for name in ["Оперативная память", "Ёмкость SSD"]
            ]
        ],
    )
    assert c._numeric_fit(0, search.scoped_numbers("16 ГБ")) == (0, 0)


@pytest.mark.parametrize(
    "short,full",
    [
        ("ОЗУ", "Общий объем установленной оперативной памяти"),
        ("Экран", "Диагональ экрана"),
        ("Ядер", "Количество ядер процессора"),
        ("Производительность", "Производительность, шкур/смена"),
    ],
)
def test_common_labels_and_units_in_headers_are_understood(short, full):
    assert search.same_characteristic(short, full)


def test_semantic_qualifier_after_comma_is_not_discarded():
    assert not search.same_characteristic("Диаметр, внутренний", "Диаметр, наружный")
