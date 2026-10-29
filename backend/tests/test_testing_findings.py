"""Regressions from the team's PDF: fallback attributes, invalid rows and missing catalog."""

import uuid
from types import SimpleNamespace

import numpy as np
import pytest

import app_lifecycle
import confidence
import extractor
import parser
import runtime as rt
import search
from models import Item
from spec_processing import apply_candidates, build_item


@pytest.mark.parametrize(
    "text,brand,model",
    [
        ("Картридж совместимый для HP LaserJet 1020", "", ""),
        ("Чехол для Apple iPad 10.2", "", ""),
        ("Кабель совместимый с Apple iPhone 15", "", ""),
        ("Картридж HP 85A для принтера LaserJet", "HP", "85A"),
        ("Ручка Erich Krause синяя", "Erich Krause", ""),
        ("Принтер HP 1020, лазерный", "HP", "1020"),
        ("Планшет Apple 10.2 дюйма", "Apple", ""),
        ("Ноутбук Lenovo E14, 16 ГБ", "Lenovo", "E14"),
    ],
)
def test_rules_do_not_confuse_compatible_device_and_product(text, brand, model):
    actual = extractor.rules(text)
    assert (actual["brand"], actual["model"]) == (brand, model)


@pytest.mark.parametrize("text", ["=1+1", "12345", "---", "1,25"])
def test_numeric_or_formula_input_has_no_code_even_if_llm_returns_a_name(text):
    assert extractor.rules(text)["name"] == ""
    row = parser.Item(row_number=1, text=text, columns={})
    item = build_item(uuid.uuid4(), row, {"name": "Товар"}, candidates())
    assert item.ktru_code is None and item.candidates == []
    assert item.confidence == 0 and item.status == "needs_review"
    assert "не похожа" in item.summary


def candidates():
    return [
        {"code": "a", "name": "Товар", "score": 0.99, "status": "ACTIVE"},
        {"code": "b", "name": "Другой", "score": 0.2, "status": "ACTIVE"},
    ]


def test_words_only_attribute_cannot_turn_into_auto_with_semantic_explanation():
    item = Item(attributes={"name": "Товар", "words_only": True})
    apply_candidates(item, candidates())
    assert item.status == "needs_review" and item.confidence < confidence.HIGH
    assert "по словам" in item.summary and "по смыслу" not in item.summary
    assert item.candidates[0]["words_only"]


def test_system_unit_is_not_a_medical_delivery_unit():
    assert not search.same_product("Системный блок", "Блок доставки оксида азота, системный")


def test_catalog_retries_until_available(monkeypatch):
    monkeypatch.setattr(rt.state, "catalog", None)
    attempts = []

    def load():
        attempts.append(1)
        if len(attempts) == 2:
            rt.state.catalog = object()

    waits = []
    stop = SimpleNamespace(
        is_set=lambda: False, wait=lambda seconds: waits.append(seconds) or False
    )
    monkeypatch.setattr(app_lifecycle, "load_catalog", load)
    app_lifecycle.load_catalog_when_available(stop)
    assert len(attempts) == 2 and waits == [15]


def test_catalog_retry_stops_on_shutdown(monkeypatch):
    monkeypatch.setattr(rt.state, "catalog", None)
    attempts = []
    monkeypatch.setattr(app_lifecycle, "load_catalog", lambda: attempts.append(1))
    stop = SimpleNamespace(is_set=lambda: False, wait=lambda seconds: True)
    app_lifecycle.load_catalog_when_available(stop)
    assert len(attempts) == 1


def test_successful_catalog_load_clears_old_error(monkeypatch):
    catalog = SimpleNamespace(vectors=np.zeros((1, app_lifecycle.embedder.DIM)))
    monkeypatch.setattr(rt.state, "catalog", None)
    monkeypatch.setattr(rt.state, "catalog_error", "FileNotFoundError")
    monkeypatch.setattr(app_lifecycle.search, "load_catalog", lambda: catalog)
    monkeypatch.setattr(
        app_lifecycle.db, "engine", SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
    )
    app_lifecycle.load_catalog()
    assert rt.state.catalog is catalog and rt.state.catalog_error is None
