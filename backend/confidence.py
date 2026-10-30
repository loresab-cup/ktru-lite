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
# Fit of 28.10.2026 for the Yandex embeddings: replayed search on the saved LLM answers of three
# runs, fitted on the 1000-row file and its modified copy of one run, BIAS lowered by 1.0 for the
# high zone. The cosine hardly tells a right code from a wrong one (medians 0.706 and 0.689), the
# margin does. Wrong in auto per 1000 rows 2 and 6-7, auto rows 823 and 795-797. The margin is
# taken from the raw scores: with scores capped at 1.0 two neighbour codes that both passed 1.0
# got margin 0 and the same confidence whatever the real gap (fit of 27.10.2026).
W_SCORE, W_MARGIN, BIAS = 0.80, 167.2, -1.38
MAX_MARGIN = 0.2  # a bigger gap says nothing more: the bonuses of one row add up past it

HIGH = 85.0  # >= HIGH: zone high, the code is set automatically
MEDIUM = 60.0  # MEDIUM..HIGH: medium, below: low; both need a human

STATUS_LABELS = {"TERMINATED": "код отменён", "INACTIVE": "код недействующий"}


def probability(top1: float, margin: float) -> float:
    z = W_SCORE * min(top1, 1.0) + W_MARGIN * min(margin, MAX_MARGIN) + BIAS
    # Lower candidates can have a large negative margin: avoid exp(-z) overflow.
    if z >= 0:
        return 100.0 / (1.0 + math.exp(-z))
    exp_z = math.exp(z)
    return 100.0 * exp_z / (1.0 + exp_z)


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
    if best.get("words_only"):
        parts.append("Поиск выполнен только по словам")
    elif not best.get("shared_words"):
        parts.append(f"совпадение по смыслу {best.get('cosine', best['score']):.2f}")
    if best.get("status") in STATUS_LABELS:
        parts.append(STATUS_LABELS[best["status"]])
    text = "; ".join(parts)
    return text[:1].upper() + text[1:]


TWIN_GAP = 0.02  # first and second code of one name this close: the row cannot tell them apart
WEAK_SCORE = 0.8  # below it the best code is far from the row
MAX_NAMED = 3  # characteristics named in the reason
# Unit keys of search.unit_key in words: codes of one name often differ only by the unit
UNIT_WORDS = {
    "l": "литрах",
    "ml": "миллилитрах",
    "kg": "килограммах",
    "g": "граммах",
    "t": "тоннах",
    "pcs": "штуках",
    "pack": "упаковках",
    "set": "комплектах",
    "pair": "парах",
    "m": "метрах",
    "m2": "квадратных метрах",
    "m3": "кубических метрах",
    "manhour": "человеко-часах",
    "unit": "условных единицах",
}


def differing_names(first: dict, second: dict) -> list[str]:
    """Characteristics the two codes have with different values (as far as the card shows)."""
    a = {c[0]: tuple(c[1]) for c in first.get("characteristics") or []}
    b = {c[0]: tuple(c[1]) for c in second.get("characteristics") or []}
    return sorted(name for name in a.keys() | b.keys() if a.get(name) != b.get(name))


def review_reason(
    candidates: list[dict], is_product: bool = True, cards: list[dict] | None = None
) -> str:
    """Why a person has to look at the row, in plain words; "" when the code goes to auto.

    cards: full catalog cards of the first two candidates; the candidates keep only six
    characteristics, and codes of one name usually differ further down."""
    if not is_product:
        return "Строка не похожа на товар, работу или услугу"
    if not candidates:
        return "Подходящих кодов КТРУ не найдено"
    best = candidates[0]
    if best.get("words_only"):
        return "Векторный поиск недоступен: код найден только по словам, проверьте выбор"
    if best.get("status") in STATUS_LABELS:
        return f"Лучший код {STATUS_LABELS[best['status']]}"
    if assess(candidates, is_product)["status"] == "auto":
        return ""
    second = candidates[1] if len(candidates) > 1 else None
    if second and second["name"] == best["name"] and raw(best) - raw(second) < TWIN_GAP:
        first_card, second_card = cards if cards and len(cards) == 2 else (best, second)
        units = [card.get("units") or [] for card in (first_card, second_card)]
        if all(units) and units[0] != units[1]:
            listed = " и ".join(UNIT_WORDS.get(u[0], u[0]) for u in units)
            return (
                f"Два кода «{best['name']}» различаются единицей закупки: в {listed}. "
                "Укажите единицу в колонке «Ед. изм.» или выберите код"
            )
        names = differing_names(first_card, second_card)
        if names:
            listed = ", ".join(f"«{n}»" for n in names[:MAX_NAMED])
            return (
                f"Два кода «{best['name']}» почти равны. Они различаются характеристиками "
                f"{listed}: уточните их в строке или выберите код"
            )
        return f"Два кода «{best['name']}» почти равны, в каталоге у них одинаковые характеристики"
    if min(raw(best), 1.0) < WEAK_SCORE:
        return "Совпадение с каталогом слабое: проверьте наименование и характеристики"
    if second:
        return f"Второй кандидат {second['code']} «{second['name']}» почти так же близок"
    return "Уверенность ниже порога автоматического выбора"


def raw(candidate: dict) -> float:
    """The score before the cap at 1.0; rows stored before 27.10.2026 have only the capped one."""
    return candidate.get("raw_score", candidate["score"])


def supported_probability(candidate: dict, margin: float) -> float:
    """A word-only search cannot justify automatic classification by its score gap."""
    if candidate.get("words_only"):
        margin = min(margin, 0.0)
    return probability(raw(candidate), margin)


def with_confidences(candidates: list[dict]) -> list[dict]:
    """The candidates with the confidence each of them would have as the pick: its margin is to
    the best of the others, so the first one gets the confidence of the item and the rest get low."""
    scores = [raw(c) for c in candidates]
    result = []
    for i, candidate in enumerate(candidates):
        others = scores[:i] + scores[i + 1 :]
        margin = scores[i] - max(others) if others else 0.0
        result.append(
            candidate | {"confidence": round(supported_probability(candidate, margin), 1)}
        )
    return result


def for_code(candidates: list[dict], code: str) -> float | None:
    """Search support for this code against the strongest alternative, not an LLM self-rating.

    Reuse the candidate-card formula, including a negative margin when search prefers another
    code. An unknown code has no search evidence and must not receive an invented percentage.
    """
    return next(
        (c["confidence"] for c in with_confidences(candidates) if c.get("code") == code), None
    )


def assess(candidates: list[dict], is_product: bool = True) -> dict:
    if not candidates:
        return {
            "confidence": 0.0,
            "zone": "low",
            "needs_review": True,
            "status": "needs_review",
            "summary": "Подходящих кодов КТРУ не найдено"
            if is_product
            else "Строка не похожа на товар, работу или услугу",
        }
    best = candidates[0]
    margin = raw(best) - raw(candidates[1]) if len(candidates) > 1 else 0.0
    confidence = round(supported_probability(best, margin), 1)
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
