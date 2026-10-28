"""Catalog characteristics, quantities and unit conversion; no ranking or I/O."""

import re

BRIEF_CHARS, BRIEF_VALUES = 20, 40


MAX_FIXED_VALUES = 30


_UNIT_PREFIXES = (
    ("квадратный метр", "m2"),
    ("кубический метр", "m3"),
    ("кубический дециметр", "l"),
    ("миллилитр", "ml"),
    ("литр", "l"),
    ("килограмм", "kg"),
    ("грамм", "g"),
    ("тонн", "t"),
    ("штук", "pcs"),
    ("упаков", "pack"),
    ("комплект", "set"),
    ("пар", "pair"),
    ("человеко-час", "manhour"),
    ("условн", "unit"),
    ("метр", "m"),
)


_UNIT_SHORT = {
    "кг": "kg",
    "г": "g",
    "гр": "g",
    "т": "t",
    "л": "l",
    "мл": "ml",
    "дм3": "l",
    "шт": "pcs",
    "упак": "pack",
    "уп": "pack",
    "компл": "set",
    "к-т": "set",
    "пара": "pair",
    "м": "m",
    "м2": "m2",
    "м3": "m3",
    "чел.-ч": "manhour",
    "чел.-час": "manhour",
    "усл. ед": "unit",
    "усл.ед": "unit",
}


_WORD = re.compile(r"\d+(?:[.,]\d+)+|[a-zа-я0-9]+")


_NUM = r"(\d+(?:[.,]\d+)?)"


_LOWER = re.compile(
    rf"(?<!\w)(?:не\s+менее|не\s+меньше|(?<!не )более|(?<!не )больше|свыше|от|≥|>=|>)\s*{_NUM}"
)


_UPPER = re.compile(
    rf"(?<!\w)(?:не\s+более|не\s+больше|(?<!не )менее|(?<!не )меньше|до|≤|<=|<)\s*{_NUM}"
)


_PLAIN = re.compile(rf"(?<![\w.,]){_NUM}")


_SCALE_PREFIXES = (
    ("кубический сантиметр", 1.0, "volume"),
    ("кубический дециметр", 1000.0, "volume"),
    ("кубический метр", 1e6, "volume"),
    ("миллиметр", 1.0, "length"),
    ("сантиметр", 10.0, "length"),
    ("километр", 1e6, "length"),
    ("метр", 1000.0, "length"),
    ("миллиграмм", 1e-6, "mass"),
    ("килограмм", 1.0, "mass"),
    ("грамм", 0.001, "mass"),
    ("тонн", 1000.0, "mass"),
    ("миллилитр", 1.0, "volume"),
    ("литр", 1000.0, "volume"),
    ("мегабайт", 1 / 1024, "bytes"),
    ("гигабайт", 1.0, "bytes"),
    ("терабайт", 1024.0, "bytes"),
    ("штук", 1.0, "count"),
    ("процент", 1.0, "percent"),
    ("дюйм", 1.0, "inch"),
    ("%", 1.0, "percent"),
)


_SCALE_SHORT = {
    "мм": (1.0, "length"),
    "см": (10.0, "length"),
    "м": (1000.0, "length"),
    "км": (1e6, "length"),
    "мг": (1e-6, "mass"),
    "г": (0.001, "mass"),
    "гр": (0.001, "mass"),
    "кг": (1.0, "mass"),
    "т": (1000.0, "mass"),
    "мл": (1.0, "volume"),
    "см3": (1.0, "volume"),
    "л": (1000.0, "volume"),
    "дм3": (1000.0, "volume"),
    "м3": (1e6, "volume"),
    "мб": (1 / 1024, "bytes"),
    "гб": (1.0, "bytes"),
    "тб": (1024.0, "bytes"),
    "шт": (1.0, "count"),
}


_COMPOUND = ("в ", "на ", "/")


def unit_of(text: str) -> tuple[float, str] | None:
    """(factor to the base unit, quantity) for the unit at the start of lowercase text:
    «м …» -> (1000, "length")."""
    text = text.lstrip(" (").replace("³", "3")
    found = next(((p, f, q) for p, f, q in _SCALE_PREFIXES if text.startswith(p)), None)
    if found is None:
        word = _WORD.match(text)
        if not word or word.group() not in _SCALE_SHORT:
            return None
        found = (word.group(), *_SCALE_SHORT[word.group()])
    rest = text[len(found[0]) :].lstrip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя").lstrip(" .")
    return None if rest.startswith(_COMPOUND) else found[1:]


def in_base(number: str, unit_text: str) -> tuple[float, str | None]:
    """«2», «м» -> (2000.0, "length"); rounded so that 0,1 кг and 100 г are equal floats.
    A number without a known unit keeps its value and has no quantity."""
    factor, quantity = unit_of(unit_text) or (1.0, None)
    return round(float(number.replace(",", ".")) * factor, 9), quantity


def same_quantity(a: str | None, b: str | None) -> bool:
    """A number without a unit is compared with anything; two units must measure one thing."""
    return a is None or b is None or a == b


def query_bounds(text: str) -> tuple[set, set, set]:
    """Lower and upper bounds and plain values with a unit in a row, as (value in base units,
    quantity): "не менее 3 м и менее 4000 мм, вес 100 г" ->
    {(3000, length)}, {(4000, length)}, {(0.1, mass)}."""
    text = (text or "").lower().replace("ё", "е")
    lower, upper, plain, bound_spans = set(), set(), set(), set()
    for pattern, found in ((_LOWER, lower), (_UPPER, upper)):
        for m in pattern.finditer(text):
            found.add(in_base(m.group(1), text[m.end() : m.end() + 40]))
            bound_spans.add(m.span(1))
    for m in _PLAIN.finditer(text):
        after = text[m.end() : m.end() + 40]
        if m.span(1) not in bound_spans and unit_of(after) is not None:
            plain.add(in_base(m.group(1), after))
    return lower, upper, plain


def unit_key(text: str) -> str | None:
    """«Литр», «л», «кубический дециметр» -> "l"; unknown units -> None.

    The EIS writes several names of one unit in a cell: «л; дм[3*]» - the first one counts."""
    text = (text or "").split(";")[0]
    text = " ".join(text.lower().replace("ё", "е").split()).strip(" .")
    if text in _UNIT_SHORT:
        return _UNIT_SHORT[text]
    return next((key for prefix, key in _UNIT_PREFIXES if text.startswith(prefix)), None)


def fixed_characteristics(characteristics: list) -> list[list]:
    """[[name, [values]]] of the characteristics the customer may not change."""
    fixed = []
    for characteristic in characteristics or []:
        if (characteristic.get("kind") or "").startswith("Неизменяемая"):
            values = [v.get("text") or "" for v in characteristic.get("values") or []]
            fixed.append([characteristic.get("name") or "", values[:MAX_FIXED_VALUES]])
    return fixed


def code_units(units: list | None) -> list[str]:
    """Unit keys of a code: ["Литр; кубический дециметр"] -> ["l"]."""
    keys = {unit_key(part) for unit in units or [] for part in str(unit).split(";")}
    return sorted(key for key in keys if key)


def numeric_bounds(characteristics: list) -> list[list]:
    """[[min, max, quantity, min is strict, max is strict], ...] of the numeric values in base
    units: «≥ 2 и < 3 Метр» -> [2000, 3000, "length", False, True]; an exact value «1 Штука» is
    [1, 1, "count", False, False]. None is an open side or a value without a known unit."""
    ranges = []
    for characteristic in characteristics or []:
        for value in characteristic.get("values") or []:
            low, high = value.get("min"), value.get("max")
            if not isinstance(low, int | float) and not isinstance(high, int | float):
                continue
            unit = str(value.get("unit") or "").lower()
            quantity = (unit_of(unit) or (1.0, None))[1]
            sides = [
                in_base(str(x), unit)[0] if isinstance(x, int | float) else None
                for x in (low, high)
            ]
            strict = [value.get("min_op") == ">", value.get("max_op") == "<"]
            ranges.append([*sides, quantity, *strict])
    return ranges


def inside(value: float, bounds: list) -> bool:
    """A plain value of the row inside [min, max, quantity, min strict, max strict] of a code."""
    low, high, _, low_strict, high_strict = bounds
    above = low is None or value > low or (value == low and not low_strict)
    below = high is None or value < high or (value == high and not high_strict)
    return above and below


def brief_chars(characteristics: list) -> tuple[list, list[str]]:
    """[[name, [values], unit]] for the UI and all value texts for word matching.

    One characteristic has one unit in the dump (2 exceptions of 330 743), so it is kept once."""
    brief, texts = [], []
    for characteristic in characteristics or []:
        raw = [v for v in characteristic.get("values") or [] if v.get("text")]
        values = [v["text"] for v in raw]
        texts.extend(values)
        if len(brief) < BRIEF_CHARS:
            unit = next((str(v["unit"]) for v in raw if v.get("unit")), "")
            brief.append(
                [characteristic.get("name") or "", [v[:80] for v in values[:BRIEF_VALUES]], unit]
            )
    return brief, texts


def with_unit(value: str, unit: str) -> str:
    """«≥ 10 и < 15» + «Грамм» -> «≥ 10 и < 15 Грамм»; text values have no unit."""
    return f"{value} {unit}" if unit else value
