"""Fixed values: compared right after «name:», and a specific code beats a general one."""

import numpy as np

import search


def catalog(fixed: list[list]) -> search.Catalog:
    n = len(fixed)
    names = ["Интерактивная доска"] * n
    return search.Catalog(
        vectors=np.asarray([[1.0, 0.0]] * n, dtype=np.float32),
        codes=[f"26.20.17.110-0000000{i}" for i in range(n)],
        statuses=["ACTIVE"] * n,
        names=names,
        okpd2=[""] * n,
        code_words=[" ".join(search.words(name)) for name in names],
        chars=[[] for _ in names],
        bounds=[[[], []] for _ in names],
        fixed=fixed,
        templates=[False] * n,
        units=[[] for _ in names],
    )


def test_written_value_is_the_text_after_the_name_up_to_the_next_colon():
    text = "объем установленной памяти: ≥ 48 гигабайт объем увеличения памяти: 16 гигабайт"
    assert search.written_value(text, "объем установленной памяти") == {
        "48",
        "гигаб",
        "объем",
        "увели",
        "памят",
    }
    assert search.written_value(text, "цвет") is None
    assert search.written_value("подобъем памяти: 8", "объем памяти") is None  # not a whole word


def test_value_of_another_characteristic_does_not_count():
    c = catalog(
        [
            [["Объем установленной памяти", ["≥ 16"]]],
            [["Объем установленной памяти", ["≥ 48"]]],
        ]
    )
    row = "Сервер. Объем установленной памяти: ≥ 48 ГБ. Объем увеличения памяти: 16 ГБ"
    [found] = c.search(np.asarray([[1.0, 0.0]], dtype=np.float32), [row])
    assert found[0]["code"].endswith("1")


def test_specific_code_beats_a_general_code_that_also_allows_the_value():
    c = catalog(
        [
            [["Количество точек касания", ["≥ 1", "≥ 2", "≥ 4", "≥ 6", "≥ 8"]]],
            [["Количество точек касания", ["≥ 2"]]],
        ]
    )
    row = "Интерактивная доска. Количество точек касания: ≥ 2 Штука"
    [found] = c.search(np.asarray([[1.0, 0.0]], dtype=np.float32), [row])
    assert found[0]["code"].endswith("1")
