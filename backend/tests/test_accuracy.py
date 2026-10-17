"""Product identity must survive misleading embedding neighbors and accessory names."""

import numpy as np
import pytest

import confidence
import search


def catalog(titles, vectors=None, statuses=None):
    n = len(titles)
    return search.Catalog(
        vectors=np.asarray(vectors if vectors is not None else [[1.0, 0.0]] * n, dtype=np.float32),
        codes=[str(i) for i in range(n)],
        statuses=statuses or ["ACTIVE"] * n,
        names=titles,
        okpd2=[""] * n,
        code_words=[" ".join(search.words(name)) for name in titles],
        chars=[[] for _ in titles],
        bounds=[[[], []] for _ in titles],
        fixed=[[] for _ in titles],
        templates=[False] * n,
        units=[[] for _ in titles],
    )


def test_hybrid_recovers_product_outside_vector_shortlist(monkeypatch):
    c = catalog(
        ["Скарификатор лазерный"] * 55 + ["Картридж лазерный"],
        [[1.0, 0.0]] * 55 + [[0.85, 0.5268]],
    )
    found = c.search(
        np.array([[1.0, 0.0]], dtype=np.float32),
        ["Картридж лазерный HP 85A черный"],
        names=["Картридж лазерный"],
    )[0]
    assert found[0]["name"] == "Картридж лазерный"
    assert found[0]["cosine"] == 0.85
    # Lexical ranking must not fabricate a perfect confidence score.
    assert found[0]["score"] < found[0]["rank_score"]


def test_accessory_cannot_auto_accept_as_product():
    c = catalog(["Скобы для степлера"])
    found = c.search(np.array([[1.0, 0.0]], dtype=np.float32), ["Степлер 24/6"], names=["Степлер"])[
        0
    ]
    verdict = confidence.assess(found)
    assert verdict["status"] == "needs_review"
    assert found == []


def test_product_outranks_accessory_with_shared_word():
    c = catalog(["Скобы для степлера", "Степлер канцелярский"])
    found = c.search(None, ["Степлер канцелярский 24/6"], names=["Степлер канцелярский"])[0]
    assert found[0]["name"] == "Степлер канцелярский"


def test_unasked_specialization_needs_review():
    c = catalog(["Молоко питьевое для детского питания"])
    found = c.search(
        np.array([[1.0, 0.0]], dtype=np.float32),
        ["Молоко питьевое"],
        names=["Молоко питьевое"],
    )[0]
    assert confidence.assess(found)["status"] == "needs_review"
    assert any("детское" in reason for reason in found[0]["review_reasons"])


def test_name_retrieval_obeys_inactive_filter():
    c = catalog(["Степлер", "Скарификатор"], statuses=["TERMINATED", "ACTIVE"])
    assert all(
        r["status"] == "ACTIVE" for r in c.search(None, ["Степлер лазерный"], names=["Степлер"])[0]
    )


def test_names_length_validated():
    c = catalog(["Степлер"])
    with pytest.raises(ValueError):
        c.search(None, ["Степлер"], names=[])


@pytest.mark.parametrize(
    ("source", "wrong"),
    [
        ("Перекись водорода", "Перекладина гимнастическая"),
        ("Антисептик для рук", "Антисыворотка отрицательная ИВД"),
        ("Мешки для мусора", "Пакет текстильный для упаковки готовых изделий"),
        ("Бинт марлевый", "Бинт трубчатый"),
        ("Шкаф металлический", "Шкаф деревянный"),
    ],
)
def test_no_code_is_safer_than_incompatible_product(source, wrong):
    c = catalog([wrong])
    assert c.search(None, [source], names=[source])[0] == []


def test_product_head_handles_inflection_and_leading_adjectives():
    assert search.product_head("Вода питьевая") == search.product_head("Воды питьевые")
    assert search.product_head("Туалетная бумага") == search.product_head("Бумага туалетная")
    assert search.product_head("Термометр медицинский") != search.product_head("Термостат")
