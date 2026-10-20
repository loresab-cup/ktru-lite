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
        bounds=[[[], []] for _ in names],
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
