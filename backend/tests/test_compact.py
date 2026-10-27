"""The cheaper wire format must preserve guarded attributes and the extraction contract."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

import attribute_format
import extractor


def test_compact_and_original_keep_the_same_source_attributes():
    text = "Бумага офисная SvetoCopy Classic 80 г/м2 формат А4"
    compact = {
        "id": 0,
        "n": "Бумага офисная",
        "b": "SvetoCopy",
        "m": "Classic",
        "c": [["Плотность", "80", "г/м2"], ["Формат", "А4", ""]],
        "s": "Бумага для офисной техники",
        "u": "упак",
    }
    [result] = extractor.to_attributes(json.dumps([compact]), [text], "yandex2")
    assert (result["name"], result["brand"], result["model"]) == (
        "Бумага офисная",
        "SvetoCopy",
        "Classic",
    )
    assert result["source"] == "llm" and result["provider"] == "yandex2"
    assert result["characteristics"][0] == {"name": "Плотность", "value": "80", "unit": "г/м2"}
    assert "Бумага для офисной техники" in result["search_text"]
    assert "Плотность: 80 г/м2" in result["search_text"] and "Формат: А4" in result["search_text"]
    assert "_category" not in result


def test_compact_hallucinations_are_removed_from_attributes_and_search_hint():
    answer = [
        {
            "id": 0,
            "n": "Бумага",
            "b": "InventedBrand",
            "m": "X100",
            "c": [["Плотность", "200", "г/м2"], ["Формат", "А4", "выдумка"]],
        }
    ]
    [result] = extractor.to_attributes(json.dumps(answer), ["Бумага А4"], "x")
    assert result["brand"] == result["model"] == ""
    assert result["characteristics"] == [{"name": "Формат", "value": "А4", "unit": ""}]
    assert "200" not in result["search_text"] and "выдумка" not in result["search_text"]


def test_generic_category_preserves_explicit_qualifications_and_purchase_unit():
    name = "Двутавр стальной горячекатаный с параллельными гранями полок"
    assert attribute_format.search_text("Двутавр", name, []) == name
    answer = [{"id": 0, "n": "Услуги связи", "u": "шт"}]
    [result] = extractor.to_attributes(
        json.dumps(answer), ["Услуги связи. Единица измерения товара: Месяц"], "x"
    )
    assert result["measure"] == "Месяц"


@pytest.mark.parametrize("chars", [42, "80", [["Плотность"]], [["Вес", 80, "г"]]])
def test_bad_compact_rows_fall_back_individually(chars):
    answer = [{"id": 0, "n": "Бумага", "c": chars}, {"id": 1, "n": "Мыло"}]
    first, second = extractor.to_attributes(json.dumps(answer), ["Бумага", "Мыло"], "x")
    assert first["fallback"] == extractor.ERROR
    assert second["source"] == "llm"


def test_duplicate_rows_share_one_request_and_keep_original_order(monkeypatch):
    calls = []
    monkeypatch.setattr(extractor, "_pool", ThreadPoolExecutor(1))

    def extract(texts, *args):
        calls.extend(texts)
        return [{"name": text, "source": "llm"} for text in texts]

    monkeypatch.setattr(extractor, "extract_chunk", extract)
    texts = ["Бумага", "Мыло", "Бумага", "Мыло"]
    job = extractor.JobExtractor(texts)
    assert [job.get(t)["name"] for t in texts] == texts
    assert calls == ["Бумага", "Мыло"]
