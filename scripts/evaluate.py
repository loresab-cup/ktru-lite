"""Accuracy of the running service on a labelled file (method: README, «Методика оценки»).

    python scripts/evaluate.py spec.xlsx reference.jsonl [--api http://127.0.0.1:8000] [--show]

reference.jsonl - one object per row, row_number is the row of the file:
{"row_number": 6, "ktru_code": "...", "name": "Мыло туалетное твердое", "unit": "кг",
 "characteristics": [{"name": "Вес", "value": "100 Грамм"}]}
Everything but row_number and ktru_code is optional. Prints the exact code, the code right by
meaning, top-5, attributes by meaning, time and the share of rows done by rules. Code data
comes from the service (GET /api/catalog/search?q=<code>); only stdlib and httpx are needed.
"""

import argparse
import json
import math
import os
import pathlib
import re
import time

import httpx

# Unit word -> (dimension, factor to the base unit of the dimension). Short forms must be
# followed by a non-letter («м» is not «мм», «г» is not «гб»), full names may be inflected.
UNITS = {
    "мм": ("length", 0.001),
    "миллиметр": ("length", 0.001),
    "см": ("length", 0.01),
    "сантиметр": ("length", 0.01),
    "дм": ("length", 0.1),
    "дециметр": ("length", 0.1),
    "м": ("length", 1),
    "метр": ("length", 1),
    "км": ("length", 1000),
    "километр": ("length", 1000),
    "дюйм": ("length", 0.0254),
    "мг": ("mass", 0.001),
    "миллиграмм": ("mass", 0.001),
    "г": ("mass", 1),
    "гр": ("mass", 1),
    "грамм": ("mass", 1),
    "кг": ("mass", 1000),
    "килограмм": ("mass", 1000),
    "т": ("mass", 1e6),
    "тонн": ("mass", 1e6),
    "мл": ("volume", 1),
    "миллилитр": ("volume", 1),
    "см3": ("volume", 1),
    "кубический сантиметр": ("volume", 1),
    "л": ("volume", 1000),
    "литр": ("volume", 1000),
    "дм3": ("volume", 1000),
    "кубический дециметр": ("volume", 1000),
    "м3": ("volume", 1e6),
    "кубический метр": ("volume", 1e6),
    "мм2": ("area", 1e-6),
    "см2": ("area", 1e-4),
    "м2": ("area", 1),
    "квадратный метр": ("area", 1),
    "г/м2": ("density", 1),
    "шт": ("count", 1),
    "штук": ("count", 1),
    "пара": ("pair", 1),
    "набор": ("set", 1),
    "комплект": ("set", 1),
    "упак": ("pack", 1),
    "лист": ("sheet", 1),
    "человек": ("person", 1),
    "условн": ("unit", 1),
    "%": ("percent", 1),
    "процент": ("percent", 1),
    "кб": ("bytes", 1),
    "килобайт": ("bytes", 1),
    "мб": ("bytes", 1024),
    "мегабайт": ("bytes", 1024),
    "гб": ("bytes", 1024**2),
    "гигабайт": ("bytes", 1024**2),
    "тб": ("bytes", 1024**3),
    "терабайт": ("bytes", 1024**3),
    "гц": ("frequency", 1),
    "герц": ("frequency", 1),
    "кгц": ("frequency", 1e3),
    "килогерц": ("frequency", 1e3),
    "мгц": ("frequency", 1e6),
    "мегагерц": ("frequency", 1e6),
    "ггц": ("frequency", 1e9),
    "гигагерц": ("frequency", 1e9),
    "вт": ("power", 1),
    "ватт": ("power", 1),
    "квт": ("power", 1000),
    "киловатт": ("power", 1000),
}
LONGEST_FIRST = sorted(UNITS, key=len, reverse=True)
# Unit keys of the service (search.unit_key) -> dimension
CODE_UNIT_DIMENSIONS = {"kg": "mass", "g": "mass", "t": "mass", "l": "volume", "ml": "volume"}
CODE_UNIT_DIMENSIONS |= {"m3": "volume", "m": "length", "m2": "area", "pcs": "count"}
SYNONYMS = {"вес": "масса"}  # «Вес» of a row is «Масса нетто» of a code
NUMBER = re.compile(r"\d+(?:\.\d+)?")
BOUND = re.compile(
    r"(?<![а-яa-z])(не менее|не более|не меньше|не больше|более|больше|свыше|менее|меньше"
    r"|от|до|≥|≤|>=|<=|>|<)\s*(\d+(?:\.\d+)?)"
)
LOWER_OPS = {"не менее", "не меньше", "более", "больше", "свыше", "от", "≥", ">=", ">"}
OPEN_OPS = {"более", "больше", "свыше", "менее", "меньше", ">", "<"}


def norm(text) -> str:
    text = str(text or "").lower().replace("ё", "е").replace("³", "3").replace("²", "2")
    return " ".join(text.split())


def words(text) -> set[str]:
    return set(re.findall(r"[a-zа-я0-9]+", norm(text)))


def stems(text) -> set[str]:
    return {SYNONYMS.get(w, w)[:5] for w in words(text) if len(w) >= 3}


def unit_of(text: str) -> tuple[str, float] | None:
    """The unit at the start of the text: «Грамм», «кг)», «дм3» -> (dimension, factor)."""
    text = norm(text).lstrip(" (").replace(".", "")
    for key in LONGEST_FIRST:
        if text.startswith(key):
            rest = text[len(key) :]
            if len(key) > 3 or not rest[:1].isalpha():
                return UNITS[key]
    return None


def interval(text, unit: str = "") -> tuple | None:
    """«≥ 10 и < 15 Грамм» -> ("mass", 10, False, 15, True): dimension, low, low is open,
    high, high is open; a plain «2 м» is [2, 2]; None when there is no number."""
    low = norm(text).replace(",", ".")
    bounds = list(BOUND.finditer(low))
    plain = NUMBER.search(low)
    if not plain:
        return None
    last = bounds[-1] if bounds else plain
    dimension, factor = unit_of(low[last.end() :]) or unit_of(unit) or (None, 1.0)
    if not bounds:
        value = float(plain.group()) * factor
        return dimension, value, False, value, False
    lo, lo_open, hi, hi_open = -math.inf, False, math.inf, False
    for match in bounds:
        op, value = match.group(1), float(match.group(2)) * factor
        if op in LOWER_OPS:
            lo, lo_open = value, op in OPEN_OPS
        else:
            hi, hi_open = value, op in OPEN_OPS
    return dimension, lo, lo_open, hi, hi_open


def comparable(a: tuple, b: tuple) -> bool:
    return a[0] is None or b[0] is None or a[0] == b[0]


def overlap(a: tuple, b: tuple) -> bool:
    """Some value satisfies both: «≥ 16» and «≥ 8 и < 32», «100 г» and «≥ 90 и < 110 г»."""
    lo, lo_open = max((a[1], a[2]), (b[1], b[2]))  # of equal bounds the open one is tighter
    hi, hi_closed = min((a[3], not a[4]), (b[3], not b[4]))
    if math.isclose(lo, hi):
        return not lo_open and hi_closed
    return lo < hi


def same_interval(a: tuple, b: tuple) -> bool:
    ends = zip((a[1], a[3]), (b[1], b[3]), strict=True)
    return comparable(a, b) and all(x == y or math.isclose(x, y) for x, y in ends)


def same_text(a, b) -> bool:
    """Case, ё/е and word order do not matter; one may contain the other."""
    wa, wb = words(a), words(b)
    return bool(wa and wb) and (wa <= wb or wb <= wa)


def same_name(a, b) -> bool:
    return same_text(a, b)


def same_char_name(a, b) -> bool:
    """«Вес» = «Масса нетто», «Наличие ароматизатора» = «Наличие ароматической отдушки»."""
    sa, sb = stems(a), stems(b)
    return bool(sa and sb) and (sa <= sb or sb <= sa)


def same_value(a: str, b: str) -> bool:
    """«2000 мм» = «2 м», «0,1 кг» = «100 г», «не менее 5» = «≥ 5», «Да» = «да»."""
    ia, ib = interval(a), interval(b)
    if ia and ib:
        return same_interval(ia, ib)
    return not ia and not ib and same_text(a, b)


def value_fits(value: str, allowed: list[str]) -> bool | None:
    """True if the row value fits one allowed value of a code, None if nothing is comparable."""
    row = interval(value)
    verdict = None
    for option in allowed:
        code = interval(option)
        if row and code and comparable(row, code):
            if overlap(row, code):
                return True
            verdict = False
        elif not row and not code:
            if same_text(value, option):
                return True
            verdict = False
    return verdict


def chars_fit(characteristics: list[dict], code: dict) -> bool:
    """No characteristic of the row contradicts the allowed values of the code.

    A characteristic the code does not have is skipped: in EIS it is an extra one."""
    for char in characteristics:
        for name, allowed in code.get("characteristics") or []:
            if same_char_name(char["name"], name):
                if value_fits(char["value"], allowed) is False:
                    return False
                break
    return True


def dimensions(units) -> set[str]:
    return {CODE_UNIT_DIMENSIONS.get(u, u) for u in units or []}


def right_by_meaning(chosen: dict | None, right: dict | None, expected: dict) -> bool:
    """Name by meaning, the same unit after conversion, values of the row allowed."""
    if not chosen:
        return False
    if right and chosen["code"] == right["code"]:
        return True
    names = [right["name"] if right else "", expected.get("name", "")]
    if not any(same_name(chosen["name"], name) for name in names if name):
        return False
    want = dimensions(right.get("units")) if right else set()
    have = dimensions(chosen.get("units"))
    if want and have and not want & have:
        return False
    return chars_fit(expected.get("characteristics") or [], chosen)


def attribute_hits(attributes: dict, expected: dict) -> dict[str, list[int]]:
    """[checked, right] per attribute: name, unit and every characteristic of the row."""
    hits = {"name": [0, 0], "unit": [0, 0], "characteristics": [0, 0]}
    if expected.get("name"):
        hits["name"] = [1, int(same_name(attributes.get("name"), expected["name"]))]
    want = unit_of(expected.get("unit") or "")
    if want:  # units missing from UNITS are not checked
        have = unit_of(attributes.get("measure") or "")
        hits["unit"] = [1, int(bool(have and have[0] == want[0]))]
    extracted = attributes.get("characteristics") or []
    for char in expected.get("characteristics") or []:
        hits["characteristics"][0] += 1
        hits["characteristics"][1] += any(
            same_char_name(char["name"], got.get("name"))
            and same_value(char["value"], f"{got.get('value', '')} {got.get('unit', '')}")
            for got in extracted
        )
    return hits


SURE = "seed_reference"  # label_status of a row whose code is known (human_1000)


def log_in(client: httpx.Client) -> None:
    """The site has accounts: EVAL_LOGIN and EVAL_PASSWORD (a user of backend/users.py).

    Without them nothing is sent: a server with AUTH_REQUIRED=0 needs no login."""
    login, password = os.getenv("EVAL_LOGIN", ""), os.getenv("EVAL_PASSWORD", "")
    if login and password:
        client.post(
            "/api/auth/login", json={"login": login, "password": password}
        ).raise_for_status()


def upload(client: httpx.Client, path: pathlib.Path) -> str:
    with path.open("rb") as f:
        return client.post("/api/jobs", files={"file": (path.name, f)}).json()["job_id"]


def run(client: httpx.Client, job_id: str) -> tuple[dict, list[dict]]:
    """Waits for the job and returns it with all its items."""
    while True:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed", "cancelled"):
            break
        time.sleep(1)
    items, offset = [], 0
    while offset < job["total"]:
        page = client.get(f"/api/jobs/{job_id}/items", params={"limit": 200, "offset": offset})
        items += page.json()["items"]
        offset += 200
    return job, items


def code_data(client: httpx.Client, code: str | None, cache: dict) -> dict | None:
    if code and code not in cache:
        found = client.get("/api/catalog/search", params={"q": code}).json()
        cache[code] = next((c for c in found if c["code"] == code), None)
    return cache.get(code) if code else None


def percent(right: int, total: int) -> str:
    return f"{right}/{total} = {right / total:.1%}" if total else "-"


def evaluate(client: httpx.Client, items: list[dict], reference: dict, show: bool) -> dict:
    counts = dict.fromkeys(("rows", "code", "meaning", "top5", "auto", "auto_right"), 0)
    counts |= {"name_exact": [0, 0], "zones": {zone: [0, 0] for zone in ("high", "medium", "low")}}
    attributes = {"name": [0, 0], "unit": [0, 0], "characteristics": [0, 0]}
    cache: dict = {}
    for item in items:
        expected = reference.get(item["row_number"])
        if not expected:
            continue
        right = item["ktru_code"] == expected["ktru_code"]
        chosen = code_data(client, item["ktru_code"], cache)
        meaning = right or right_by_meaning(
            chosen, code_data(client, expected["ktru_code"], cache), expected
        )
        counts["rows"] += 1
        counts["code"] += right
        counts["meaning"] += meaning
        counts["top5"] += any(c["code"] == expected["ktru_code"] for c in item["candidates"])
        counts["auto"] += item["status"] == "auto"
        counts["auto_right"] += item["status"] == "auto" and right
        zone = counts["zones"].setdefault(item["zone"], [0, 0])
        zone[0] += 1
        zone[1] += right
        if expected.get("name"):
            counts["name_exact"][0] += 1
            counts["name_exact"][1] += norm(item["attributes"].get("name")) == norm(
                expected["name"]
            )
        for key, (checked, hit) in attribute_hits(item["attributes"], expected).items():
            attributes[key][0] += checked
            attributes[key][1] += hit
        if show and not right:
            mark = "по смыслу" if meaning else "НЕВЕРНО"
            print(
                f"  row {item['row_number']}: {item['ktru_code']} vs {expected['ktru_code']} {mark}"
            )
    return counts | {"attributes": attributes}


def main() -> None:
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("file", type=pathlib.Path)
    args.add_argument("reference", type=pathlib.Path, nargs="?")
    args.add_argument("--api", default="http://127.0.0.1:8000")
    args.add_argument("--show", action="store_true", help="print the rows with another code")
    args.add_argument("--job", help="evaluate a finished job of this file instead of a new run")
    options = args.parse_args()
    reference = {}
    if options.reference:
        lines = options.reference.read_text(encoding="utf-8").splitlines()
        reference = {int(row["row_number"]): row for row in map(json.loads, filter(None, lines))}
    # human_1000: incomplete and service rows have no reliable code; only their auto share counts
    unsure = {
        n: row for n, row in reference.items() if row.get("label_status", "") not in ("", SURE)
    }
    reference = {n: row for n, row in reference.items() if n not in unsure}

    started = time.monotonic()
    with httpx.Client(base_url=options.api, timeout=120) as client:
        log_in(client)
        job, items = run(client, options.job or upload(client, options.file))
        seconds = time.monotonic() - started
        result = evaluate(client, items, reference, options.show)
    if options.job:
        seconds = (job.get("stage_seconds") or {}).get("total", 0)
    rules = job.get("fallback") or 0
    print(f"{options.file.name}: {job['status']}, {len(items)} rows in {seconds:.0f} s")
    print(f"rows by rules (no neural network): {percent(rules, len(items))}")
    failed = sum(item["status"] == "failed" for item in items)
    print(f"failed rows: {failed}")
    for status in sorted({row["label_status"] for row in unsure.values()}):
        rows = [
            item
            for item in items
            if unsure.get(item["row_number"], {}).get("label_status") == status
        ]
        auto = sum(item["status"] == "auto" for item in rows)
        print(f"{status} rows (not in the accuracy): {len(rows)}, in auto {auto}")
    n = result["rows"]
    if not n:
        return
    confidence = sum(item["confidence"] or 0 for item in items) / len(items)
    print(f"mean confidence:     {confidence:.1f} % (calibrated if close to the exact code)")
    print(f"exact code:          {percent(result['code'], n)}")
    print(f"code by meaning:     {percent(result['meaning'], n)}")
    print(f"right code in top-5: {percent(result['top5'], n)}")
    print(f"auto: {result['auto']} rows, right {result['auto_right']}")
    for zone, (rows, right) in result["zones"].items():
        print(f"zone {zone + ':':8}{percent(right, rows)} right")
    wrong = n - result["code"]
    caught = wrong - (result["auto"] - result["auto_right"])
    print(f"wrong codes sent to review: {percent(caught, wrong)}")
    checked, hit = result["name_exact"]
    print(f"name exact:          {percent(hit, checked)}")
    for key, (checked, hit) in result["attributes"].items():
        print(f"{key + ' by meaning:':21}{percent(hit, checked)}")


if __name__ == "__main__":
    main()
