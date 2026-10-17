import confidence


def test_hybrid_margin_uses_actual_ordering_without_inflating_base_score():
    rows = [
        {'score': .78, 'rank_score': .96, 'status': 'ACTIVE'},
        {'score': .80, 'rank_score': .94, 'status': 'ACTIVE'},
    ]
    result = confidence.assess(rows)
    assert result['confidence'] == round(confidence.probability(.78, .02), 1)
    assert result['status'] == 'needs_review'


def test_conflicting_characteristics_still_block_high_confidence_auto():
    result = confidence.assess([
        {'score': .98, 'rank_score': 1.15, 'status': 'ACTIVE',
         'review_reasons': ['Характеристики требуют проверки']},
        {'score': .80, 'rank_score': .90, 'status': 'ACTIVE'},
    ])
    assert result['confidence'] > 85
    assert result['status'] == 'needs_review'


def test_large_ranking_bonus_does_not_raise_low_base_evidence_to_high():
    result = confidence.assess([
        {'score': .45, 'rank_score': .70, 'status': 'ACTIVE'},
        {'score': .44, 'rank_score': .69, 'status': 'ACTIVE'},
    ])
    assert result['confidence'] < 1
    assert result['status'] == 'needs_review'
