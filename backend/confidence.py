"""Top-5 candidates -> confidence %, zone, needs_review, item status and a short explanation."""

import math

# confidence = 1 / (1 + exp(-(W_SCORE * top1 + W_MARGIN * margin + BIAS)))
#   top1   - score of the best candidate (cosine + word bonus), capped at 1.0;
#   margin - raw score of the best minus raw score of the second (0 when it is alone), capped
#            at MAX_MARGIN.
# How to fit: run a labeled specification through the service, collect (top1, margin,
# code is right) of every row and fit a logistic regression on the two features; its
# coefficients go here. Then check the high zone: at least ~95 % of its codes must be right,
# otherwise raise BIAS a little.
# Fit of 27.10.2026 on 4 000 rows (the 1000-row organizers' file and its modified copy, with
# the rules and with the neural networks). The margin used to be taken from the scores capped
# at 1.0: two neighbour codes that both passed 1.0 had margin 0 and got 94 % whatever the real
# gap, and 6 % of such rows had a wrong code. With the raw margin, on the training rows the
# wrong codes in auto went 36-41 -> 9-11 per 1000 (auto rows 938-951 -> 811-837); on the
# other rows (292 rows, its modified copy, the real file, the EIS form) 3-4 -> 0 wrong in auto,
# log loss 0.128 -> 0.098. The price: about one row in nine more goes to a person.
W_SCORE = 8.8
W_MARGIN = 123.7
BIAS = -7.78
MAX_MARGIN = 0.2  # a bigger gap says nothing more: the bonuses of one row add up past it

HIGH = 85.0  # >= HIGH: zone high, the code is set automatically
MEDIUM = 60.0  # MEDIUM..HIGH: medium, below: low; both need a human

STATUS_LABELS = {"TERMINATED": "код отменён", "INACTIVE": "код недействующий"}


def probability(top1: float, margin: float) -> float:
    z = W_SCORE * min(top1, 1.0) + W_MARGIN * min(margin, MAX_MARGIN) + BIAS
    return 100.0 / (1.0 + math.exp(-z))


def zone_of(confidence: float) -> str:
    if confidence >= HIGH:
        return "high"
    return "medium" if confidence >= MEDIUM else "low"


def explain(best: dict, is_product: bool) -> str:
    """What matched, in plain words; no network."""
    parts = []
    if not is_product:
        parts.append("Похоже, строка не описывает товар")
    if best.get("shared_words"):
        parts.append("совпали слова: " + ", ".join(best["shared_words"][:8]))
    if best.get("matched_characteristics"):
        parts.append("характеристики: " + "; ".join(best["matched_characteristics"][:4]))
    if best.get("numeric_matches"):
        parts.append(f"совпало числовых границ: {best['numeric_matches']}")
    if not best.get("shared_words"):
        parts.append(f"совпадение по смыслу {best.get('cosine', best['score']):.2f}")
    if best.get("status") in STATUS_LABELS:
        parts.append(STATUS_LABELS[best["status"]])
    text = "; ".join(parts)
    return text[:1].upper() + text[1:]


def raw(candidate: dict) -> float:
    """The score before the cap at 1.0; rows stored before 27.10.2026 have only the capped one."""
    return candidate.get("raw_score", candidate["score"])


def with_confidences(candidates: list[dict]) -> list[dict]:
    """The candidates with the confidence each of them would have as the pick: its margin is to
    the best of the others, so the first one gets the confidence of the item and the rest get low."""
    scores = [raw(c) for c in candidates]
    result = []
    for i, candidate in enumerate(candidates):
        others = scores[:i] + scores[i + 1 :]
        margin = scores[i] - max(others) if others else 0.0
        result.append(candidate | {"confidence": round(probability(scores[i], margin), 1)})
    return result


def assess(candidates: list[dict], is_product: bool = True) -> dict:
    if not candidates:
        return {
            "confidence": 0.0,
            "zone": "low",
            "needs_review": True,
            "status": "needs_review",
            "summary": "Подходящих кодов КТРУ не найдено",
        }
    best = candidates[0]
    margin = raw(best) - raw(candidates[1]) if len(candidates) > 1 else 0.0
    confidence = round(probability(raw(best), margin), 1)
    zone = zone_of(confidence)
    # A terminated code and a row that is not a product never go to auto
    auto = zone == "high" and best.get("status") == "ACTIVE" and is_product
    return {
        "confidence": confidence,
        "zone": zone,
        "needs_review": not auto,
        "status": "auto" if auto else "needs_review",
        "summary": explain(best, is_product),
    }
