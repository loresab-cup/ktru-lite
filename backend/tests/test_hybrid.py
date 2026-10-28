"""Hybrid retrieval: BM25 over code names adds codes the vector shortlist missed."""

import numpy as np

import search


def catalog(names, vectors):
    n = len(names)
    return search.Catalog(
        vectors=np.asarray(vectors, dtype=np.float32),
        codes=[f"{i:02d}.00.00.000-00000001" for i in range(n)],
        statuses=["ACTIVE"] * n,
        names=names,
        okpd2=[""] * n,
        code_words=[" ".join(search.words(name)) for name in names],
        chars=[[] for _ in names],
        bounds=[[] for _ in names],
        fixed=[[] for _ in names],
        templates=[False] * n,
        units=[[] for _ in names],
    )


def test_name_match_outside_the_vector_shortlist_is_ranked(monkeypatch):
    monkeypatch.setattr(search, "VECTOR_LIMIT", 3)
    names = ["Скарификатор", "Скальпель", "Пинцет", "Кефир"]
    vectors = [[1.0, 0.0], [0.99, 0.14], [0.98, 0.2], [0.6, 0.8]]
    c = catalog(names, vectors)
    query = np.asarray([[1.0, 0.0]], dtype=np.float32)
    monkeypatch.setattr(search, "LEX_LIMIT", 0)
    assert "Кефир" not in [x["name"] for x in c.search(query, ["Кефир 2,5% 1 л"])[0]]
    monkeypatch.setattr(search, "LEX_LIMIT", 20)
    [found] = c.search(query, ["Кефир 2,5% 1 л"])
    kefir = next(x for x in found if x["name"] == "Кефир")
    assert kefir["cosine"] == 0.6  # its real similarity, not an invented one
    assert kefir["score"] < found[0]["score"]  # it only joins: no lexical bonus in the score


def test_lexical_part_skips_inactive_codes(monkeypatch):
    monkeypatch.setattr(search, "VECTOR_LIMIT", 1)
    c = catalog(["Пинцет", "Кефир"], [[1.0, 0.0], [0.0, 1.0]])
    c.active[1] = False
    query = np.asarray([[1.0, 0.0]], dtype=np.float32)
    assert [x["name"] for x in c.search(query, ["Кефир"])[0]] == ["Пинцет"]
    assert "Кефир" in [x["name"] for x in c.search(query, ["Кефир"], include_inactive=True)[0]]


# --- the product name written in the row (anchors) ----------------------------------------


def anchored(c, vector, text):
    vectors = None if vector is None else np.asarray([vector], dtype=np.float32)
    return c.search(vectors, [text], anchors=[text])[0]


def test_anchor_returns_the_named_product_when_characteristics_dominate(monkeypatch):
    monkeypatch.setattr(search, "VECTOR_LIMIT", 1)
    c = catalog(["Клавиатура", "Чай черный USB"], [[1.0, 0.0], [0.0, 1.0]])
    assert anchored(c, None, "Клавиатура проводная USB цвет черный")[0]["name"] == "Клавиатура"
    c.active[0] = False
    assert anchored(c, None, "Клавиатура USB черный")[0]["name"] == "Чай черный USB"


def test_anchor_keeps_the_real_cosine_of_a_generic_name():
    c = catalog(["Мыло туалетное", "Шампунь"], [[0.6, 0.8], [1.0, 0.0]])
    found = anchored(c, [1.0, 0.0], "Мыло")
    assert found[0]["name"] == "Мыло туалетное"
    assert found[0]["cosine"] == 0.6


def test_anchor_keeps_a_more_specific_name_and_a_synonym_with_the_qualifier():
    names = ["Анализатор биохимический", "Анализатор биохимический метаболического профиля"]
    c = catalog(names, [[0.6, 0.8], [1.0, 0.0]])
    assert anchored(c, [1.0, 0.0], "Анализатор биохимический")[0]["name"] == names[1]
    c = catalog(["Стул операционный", "Кресло офисное"], [[0.6, 0.8], [1.0, 0.0]])
    assert anchored(c, [1.0, 0.0], "Стул офисный")[0]["name"] == "Кресло офисное"


def test_anchor_takes_the_name_of_the_row_not_of_the_search_hint():
    """The LLM hint «Сухари» comes first in the query; the row says «Изделия сухарные»."""
    c = catalog(["Изделия сухарные", "Сухари"], [[1.0, 0.0], [0.6, 0.8]])
    row = "Изделия сухарные. Вид изделия: Сухари сдобные"
    query = f"Сухари. Вид: сдобные. {row}"
    vector = np.asarray([[1.0, 0.0]], dtype=np.float32)
    assert c.search(vector, [query], anchors=[row])[0][0]["name"] == "Изделия сухарные"
    assert c.search(vector, [query], anchors=[query])[0][0]["name"] == "Сухари"


def test_without_anchors_the_ranking_is_unchanged():
    c = catalog(["Мыло туалетное", "Шампунь"], [[0.6, 0.8], [1.0, 0.0]])
    vector = np.asarray([[1.0, 0.0]], dtype=np.float32)
    assert c.search(vector, ["Мыло"])[0][0]["name"] == "Шампунь"


def test_a_product_with_the_word_in_its_name_is_not_the_named_product():
    """«3D-сканер стационарный» outranked «Сканер»: it has the word but is another product."""
    c = catalog(["3D-сканер стационарный", "Сканер"], [[1.0, 0.0], [0.6, 0.8]])
    assert anchored(c, [1.0, 0.0], "Сканер. Формат A4")[0]["name"] == "Сканер"


def test_the_same_words_in_another_order_are_the_same_product():
    assert search.same_product("поручень опорный", "Опорный поручень")
    assert search.same_product("видеоскоп гибкий", "Видеоскоп гибкий, одноразовый")
    assert not search.same_product("сканер", "3D-сканер стационарный")
    c = catalog(["Опорный поручень", "Поручень"], [[1.0, 0.0], [0.6, 0.8]])
    assert (
        anchored(c, [1.0, 0.0], "Поручень опорный. Длина 600 мм")[0]["name"] == "Опорный поручень"
    )


def test_anchor_finds_the_name_written_in_another_word_order(monkeypatch):
    """292 modified, 28.10.2026: «Цифровой микроскоп» went to auto as «Микроскоп световой
    инвертированный», «Музыкальный микрофон» as «Микрофон компьютерный»."""
    monkeypatch.setattr(search, "VECTOR_LIMIT", 1)
    names = ["Микроскоп световой инвертированный", "Микроскоп цифровой"]
    c = catalog(names, [[1.0, 0.0], [0.9, 0.44]])
    text = "Цифровой микроскоп. Строение оптической схемы: Инвертированный"
    assert anchored(c, [1.0, 0.0], text)[0]["name"] == "Микроскоп цифровой"
    # one word is not a name in another order: «Микроскоп» alone keeps the ranking
    assert anchored(c, [1.0, 0.0], "Микроскоп инвертированный")[0]["name"] == names[0]
