"""Structured specs skip paid extraction, while ambiguous rows and cancellation stay safe."""

import threading

import pytest

import extractor


def test_structured_fields_preserve_text_values_and_never_invent_brand():
    text = "Ноутбук. Тип накопителя: SSD Разрешение экрана: Full HD Единица измерения товара: Штука"
    row = extractor.structured(text)
    assert row["name"] == "Ноутбук" and row["brand"] == row["model"] == ""
    assert row["measure"] == "Штука" and row["source"] == "structured"
    assert row["search_text"] == "Ноутбук"
    assert "fallback" not in row and "provider" not in row
    assert row["characteristics"] == [
        {"name": "Тип накопителя", "value": "SSD", "unit": ""},
        {"name": "Разрешение экрана", "value": "Full HD", "unit": ""},
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Ноутбук Lenovo ThinkPad 16 ГБ",
        "Ноутбук Lenovo. Память: 16 ГБ",
        "Ноутбук. Бренд: Леново Память: 16 ГБ",
        "Ноутбук. Модель: Т14 Память: 16 ГБ",
        "Мыло. Товарный знак: Ладога Масса: 100 г",
        "Бумага плотность: 80 г/м2",
        "Товар. Характеристика:",
        "Товар. Характеристика: 12 В : потерянное имя",
    ],
)
def test_ambiguous_or_branded_rows_keep_llm_extraction(text):
    assert extractor.structured(text) is None


def test_unknown_product_name_cannot_bypass_llm_with_catalog_gate():
    names = {extractor.norm("Ноутбук")}
    assert extractor.structured("Ноутбук Леново. Память: 16 ГБ", names) is None
    assert extractor.structured("Ноутбук. Память: 16 ГБ", names)["source"] == "structured"


def test_mixed_job_only_submits_free_text_once_and_keeps_row_order(monkeypatch):
    monkeypatch.setattr(extractor, "LOCAL_STRUCTURED", True)
    calls = []

    def extract(texts, *args):
        calls.extend(texts)
        return [{"name": text, "source": "llm"} for text in texts]

    monkeypatch.setattr(extractor, "extract_chunk", extract)
    local = "Бумага. Формат: А4 Единица измерения товара: Штука"
    free = "Ноутбук Lenovo ThinkPad"
    job = extractor.JobExtractor([local, free, local, free])
    assert [job.get(t)["source"] for t in [local, free, local, free]] == [
        "structured",
        "llm",
        "structured",
        "llm",
    ]
    assert calls == [free]


def test_fully_structured_job_sends_no_requests_and_honors_cancellation(monkeypatch):
    monkeypatch.setattr(extractor, "LOCAL_STRUCTURED", True)

    def forbidden(*args):
        pytest.fail("a structured job must not call the LLM extractor")

    monkeypatch.setattr(extractor, "extract_chunk", forbidden)
    stopped = threading.Event()
    text = "Мыло. Масса: 100 г"
    job = extractor.JobExtractor([text], cancelled=stopped)
    assert job.get(text)["source"] == "structured"
    assert not job._futures
    job.cancel()
    with pytest.raises(extractor.Cancelled):
        job.get(text)
