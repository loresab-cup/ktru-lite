"""Selected-code evidence and strict acceptance of automatically proposed percentages."""

import pytest

import confidence
from acceptance import check_confidence


def test_selected_code_uses_raw_scores_and_matches_its_candidate_card():
    candidates = [
        {"code": "first", "score": 1.0, "raw_score": 1.08},
        {"code": "selected", "score": 1.0, "raw_score": 1.02},
    ]
    selected = confidence.for_code(candidates, "selected")
    assert selected == round(confidence.probability(1.02, -0.06), 1)
    assert selected == confidence.with_confidences(candidates)[1]["confidence"]
    assert selected < confidence.assess(candidates)["confidence"]
    assert confidence.for_code(candidates, "first") == confidence.assess(candidates)["confidence"]


def test_unknown_code_gets_no_invented_search_percentage():
    assert confidence.for_code([], "unknown") is None
    assert confidence.for_code([{"code": "known", "score": 0.9}], "unknown") is None


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), -0.1, 100.1, True, "95"])
def test_acceptance_rejects_missing_or_invalid_proposal_confidence(value):
    result = check_confidence([{"status": "needs_review", "confidence": value}])
    assert not result["passed"] and result["unscored_or_invalid"] == 1


def test_acceptance_allows_zero_and_does_not_invent_scores_for_human_decisions():
    result = check_confidence(
        [
            {"status": "auto", "confidence": 95.0},
            {"status": "needs_review", "confidence": 0.0},
            {"status": "corrected", "confidence": None},
            {"status": "auto", "confidence": None, "attributes": {"remembered": "2026-10-28"}},
        ]
    )
    assert result["passed"] and result["scored"] == 2
    assert result["remembered_human_decisions"] == 1
    assert not check_confidence([])["passed"]
