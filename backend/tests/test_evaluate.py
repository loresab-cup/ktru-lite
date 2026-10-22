"""scripts/evaluate.py: values compared by meaning - units converted, ranges, words."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/evaluate.py"
spec = importlib.util.spec_from_file_location("evaluate", SCRIPT)
evaluate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluate)


def test_units_are_converted():
    assert evaluate.same_value("2000 мм", "2 м")
    assert evaluate.same_value("100 г", "0,1 кг")
    assert evaluate.same_value("100 Грамм", "0.1 Килограмм")
    assert evaluate.same_value("1,5 л", "1500 мл")
    assert evaluate.same_value("2 дм³", "2 л")
    assert evaluate.same_value("16 ГБ", "16384 Мегабайт")
    assert not evaluate.same_value("2 м", "2 мм")
    assert not evaluate.same_value("100 г", "100 мл")  # another dimension


def test_ranges():
    assert evaluate.same_value("не менее 5", "≥ 5")
    assert evaluate.same_value("от 10 до 20 мм", "≥ 1 см и ≤ 2 см")
    assert not evaluate.same_value("≥ 5", "≤ 5")
    allowed = ["< 90 Грамм", "≥ 90 и < 110 Грамм", "≥ 110 Грамм"]
    assert evaluate.value_fits("100 Грамм", allowed)
    assert evaluate.value_fits("0,1 кг", allowed)
    assert evaluate.value_fits("не менее 16 ГБ", ["≥ 8 Гигабайт"])
    assert evaluate.value_fits("110 г", ["≥ 90 и < 110 Грамм"]) is False  # 110 is excluded
    assert evaluate.value_fits("≥ 15.6 Дюйм (25,4 мм)", ["≥ 15.6 Дюйм (25,4 мм)"])
    assert evaluate.value_fits("100 мл", ["≥ 90 и < 110 Грамм"]) is None  # not comparable


def test_words_and_names():
    assert evaluate.same_name("Мыло туалетное твёрдое", "мыло твердое туалетное")
    assert evaluate.same_name("Крем", "Крем для рук")
    assert not evaluate.same_name("Мыло хозяйственное", "Мыло туалетное")
    assert evaluate.value_fits("да", ["Да", "Нет"])
    assert evaluate.value_fits("Ординарное (О)", ["Экстра (Э)"]) is False
    assert evaluate.same_char_name("Вес", "Масса нетто")
    assert not evaluate.same_char_name("Количество ядер", "Количество розеток")


def test_code_right_by_meaning():
    right = {"code": "20.41.31.110-00000002", "name": "Мыло туалетное твердое", "units": ["pcs"]}
    right["characteristics"] = [["Марка мыла", ["Ординарное (О)"]]]
    neighbour = {"code": "20.41.31.110-00000007", "name": "Мыло туалетное твердое"}
    neighbour |= {"units": ["pcs"], "characteristics": [["Масса нетто", ["≥ 90 и < 110 Грамм"]]]}
    row = {"name": "Мыло туалетное", "characteristics": [{"name": "Вес", "value": "100 г"}]}
    assert evaluate.right_by_meaning(neighbour, right, row)
    heavy = row | {"characteristics": [{"name": "Вес", "value": "0,2 кг"}]}
    assert not evaluate.right_by_meaning(neighbour, right, heavy)
    in_litres = neighbour | {"units": ["l"]}
    assert not evaluate.right_by_meaning(in_litres, right, row)
