"""Names must not repeat extracted suffixes or lose their product identity."""

import extractor


def test_confirmed_brand_and_model_are_separate_from_name():
    source = "Монитор Samsung S24R350, 24 дюйма"
    proposed = {
        "name": source,
        "brand": "Samsung",
        "model": "S24R350",
        "search_text": "Монитор 24 дюйма",
        "characteristics": [{"name": "диагональ", "value": "24", "unit": "дюйма"}],
    }
    result = extractor.accept(proposed, source)
    assert result["name"] == "Монитор"
    assert result["brand"] == "Samsung" and result["model"] == "S24R350"
    assert result["search_text"] == proposed["search_text"]


def test_comma_without_attribute_evidence_is_preserved():
    assert extractor.product_name("Кабель, провод", "", "", []) == "Кабель, провод"


def test_empty_heading_and_brand_first_name_stay_safe():
    assert extractor.product_name("", "", "", []) == ""
    assert extractor.product_name("HP принтер", "HP", "", []) == "HP принтер"
    assert extractor.product_name("Молоко для детей", "", "", []) == "Молоко для детей"


def test_format_suffix_is_not_product_name():
    assert (
        extractor.product_name("Бумага А4", "", "", [{"name": "формат", "value": "А4"}]) == "Бумага"
    )


def test_unknown_brand_cannot_cut_a_source_name():
    result = extractor.accept({"name": "Ноутбук учебный", "brand": "Lenovo"}, "Ноутбук учебный")
    assert result["name"] == "Ноутбук учебный" and result["brand"] == ""


def test_device_an_accessory_fits_is_not_its_name():
    """development set of 29.10.2026: three accessories kept the device in their names."""
    assert (
        extractor.product_name("Чехол для планшета Apple iPad Air", "", "", [])
        == "Чехол для планшета"
    )
    assert extractor.product_name("Блок питания для ноутбука Lenovo ThinkPad T14", "", "", []) == (
        "Блок питания для ноутбука"
    )
    # one Latin word is a property, not a device; a Cyrillic brand is left to the model
    assert (
        extractor.product_name("Кабель для подключения USB", "", "", [])
        == "Кабель для подключения USB"
    )
    assert (
        extractor.product_name("Фильтр для воды Аквафор B100", "", "", [])
        == "Фильтр для воды Аквафор B100"
    )


def test_a_confirmed_number_at_the_end_is_a_characteristic():
    volume = [{"name": "объём", "value": "120", "unit": "л"}]
    assert extractor.product_name("Пакеты для мусора 120 л", "", "", volume) == "Пакеты для мусора"
    assert extractor.product_name("Ящик 120 л", "", "", []) == "Ящик 120 л"  # nothing confirms it
