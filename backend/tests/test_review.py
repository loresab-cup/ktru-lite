"""Why a row needs review: the reason shown in the list and in the card."""

import confidence


def test_very_low_candidate_confidence_does_not_overflow():
    assert confidence.probability(0.5, -10) == 0.0
    assert 0 <= confidence.probability(0.5, -0.01) < confidence.HIGH


def cand(code, name="Кефир", score=0.9, chars=None, status="ACTIVE"):
    return {
        "code": code,
        "name": name,
        "score": score,
        "raw_score": score,
        "status": status,
        "characteristics": chars or [],
    }


def test_auto_row_has_no_reason():
    found = [cand("1", score=1.0), cand("2", name="Ряженка", score=0.7)]
    assert confidence.assess(found)["status"] == "auto"
    assert confidence.review_reason(found) == ""


def test_twins_that_differ_by_unit_name_the_unit():
    found = [cand("1", score=0.914), cand("2", score=0.909)]
    cards = [{"units": ["l"], "characteristics": []}, {"units": ["kg"], "characteristics": []}]
    reason = confidence.review_reason(found, True, cards)
    assert "в литрах и килограммах" in reason and "«Ед. изм.»" in reason


def test_twins_that_differ_by_a_characteristic_name_it():
    found = [cand("1", score=0.914), cand("2", score=0.909)]
    cards = [
        {"units": ["l"], "characteristics": [["Массовая доля жира", ["2.5"]], ["Вид", ["А"]]]},
        {"units": ["l"], "characteristics": [["Массовая доля жира", ["3.2"]], ["Вид", ["А"]]]},
    ]
    reason = confidence.review_reason(found, True, cards)
    assert "«Массовая доля жира»" in reason and "«Вид»" not in reason


def test_twins_the_catalog_cannot_tell_apart_say_so():
    found = [cand("1", score=0.914), cand("2", score=0.909)]
    assert "одинаковые характеристики" in confidence.review_reason(found)


def test_weak_match_not_a_product_and_inactive_code():
    weak = [cand("1", name="Шприц", score=0.6), cand("2", name="Игла", score=0.59)]
    assert "слабое" in confidence.review_reason(weak)
    assert "не похожа на товар" in confidence.review_reason(weak, is_product=False)
    stopped = [cand("1", score=1.0, status="TERMINATED")]
    assert "отменён" in confidence.review_reason(stopped)
    assert "не найдено" in confidence.review_reason([])
