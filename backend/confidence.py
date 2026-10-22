"""Top-5 candidates -> confidence %, zone, needs_review, item status and a short explanation."""

import math

# confidence = 1 / (1 + exp(-(W_SCORE * top1 + W_MARGIN * margin + BIAS)))
#   top1   - score of the best candidate (cosine + word bonus, 0..1);
#   margin - top1 minus the score of the second candidate (0 when it is alone).
# How to fit: run a labeled specification through the service, collect (top1, margin,
# code is right) of every row and fit a logistic regression on the two features; its
# coefficients go here. Then check the high zone: at least ~95 % of its codes must be right,
# otherwise raise BIAS a little.
# Fit of 22.10.2026: logistic regression of "the exact code is right" on (top1, margin) over
# 2 000 rows (two runs of the 1000-row organizers' file), neural networks on, bge-m3 on a GPU.
# The older fit (14.5 / 41.5 / -12.2) was too careful: the rows it sent to review were right
# in 98 % of the cases by meaning, and the mean confidence 86 % was below the real 95 %.
# On 596 other rows (292 rows, their modified copy, the real specification): log loss 0.100
# instead of 0.241, 93 % of rows in the high zone with 98.9 % exact codes (99.8 % by meaning);
# on the training rows 95 % in the high zone with 96.2 % exact (98.6 % by meaning).
# Adding the cosine, the numeric matches or the source of the attributes changed nothing.
W_SCORE = 11.05
W_MARGIN = 76.0
BIAS = -8.28

HIGH = 85.0  # >= HIGH: zone high, the code is set automatically
MEDIUM = 60.0  # MEDIUM..HIGH: medium, below: low; both need a human

STATUS_LABELS = {"TERMINATED": "код отменён", "INACTIVE": "код недействующий"}


def probability(top1: float, margin: float) -> float:
    z = W_SCORE * top1 + W_MARGIN * margin + BIAS
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
    margin = best["score"] - candidates[1]["score"] if len(candidates) > 1 else 0.0
    confidence = round(probability(best["score"], margin), 1)
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
