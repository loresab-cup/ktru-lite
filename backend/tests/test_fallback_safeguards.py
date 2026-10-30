"""Fallback results must stay reviewable and must not classify non-product input."""

import uuid
from types import SimpleNamespace

import pytest

import confidence
import extractor
import parser
import search
from models import Item
from pipeline import apply_candidates, build_item


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
def test_rules_keep_product_brand_separate_from_compatible_device(text, brand, model):
    actual = extractor.rules(text)
    assert (actual["brand"], actual["model"]) == (brand, model)


def candidates():
    return [
        {"code": "a", "name": "Товар", "score": 0.99, "status": "ACTIVE"},
        {"code": "b", "name": "Другой", "score": 0.2, "status": "ACTIVE"},
    ]


@pytest.mark.parametrize("text", ["=1+1", "12345", "---", "1,25"])
def test_numeric_input_has_no_code_even_if_llm_returns_a_name(text):
    assert extractor.rules(text)["name"] == ""
    row = parser.Item(1, text)
    item = build_item(uuid.uuid4(), row, {"name": "Товар"}, candidates())
    assert item.ktru_code is None and item.candidates == []
    assert item.confidence == 0 and item.status == "needs_review"
    assert "не похожа" in item.summary


def test_words_only_cannot_become_auto_or_claim_semantic_search():
    item = Item(attributes={"name": "Товар", "words_only": True})
    apply_candidates(item, candidates())
    assert item.status == "needs_review" and item.confidence < confidence.HIGH
    assert "по словам" in item.summary and "по смыслу" not in item.summary
    assert confidence.for_code(item.candidates, "a") == item.confidence


def test_explanation_uses_catalog_spelling_instead_of_normalized_mixed_letters():
    catalog = SimpleNamespace(
        names=["Мышь компьютерная"],
        chars=[[["Интерфейс", ["USB"], ""]]],
    )
    assert search.Catalog._display_words(catalog, 0, {"usв", "мышь"}) == ["USB", "мышь"]
