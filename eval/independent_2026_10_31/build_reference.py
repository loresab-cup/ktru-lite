"""Builds the independent reference from saved EIS print forms. Never calls the classifier.

Selection rules, fixed before any site run:
- only positions of type "Товар" whose print form gives a full KTRU code;
- document order, at most 30 rows per purchase;
- full duplicates by normalized name + characteristics + unit are dropped;
- rows equal to the old labelled sets are dropped and logged;
- a code cancelled in the catalog snapshot goes to the reserve; a code missing from it stays
  in the set and is reported (a coverage limit, not a reason to hide the row).
The catalog is read only by exact code, to fetch a card.

python build_reference.py <eis_sources dir> <dataset dir> <catalog.jsonl.gz> <out dir>
"""

import csv
import gzip
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

import openpyxl
from lxml import html

SRC, DATASET, CATALOG, OUT = map(Path, sys.argv[1:5])
CODE = re.compile(r"^\d{2}\.\d{2}\.\d{2}\.\d{3}-\d{5,8}$")
MAX_PER_PURCHASE = 30
OLD_SETS = ["organizers", "modified", "human_2026_10_28", "real_produkty", "eis"]
HEAD = ["№", "Наименование", "Характеристики", "Ед. изм.", "Количество"]


def text(node):
    return " ".join(node.text_content().split())


def tokens(value):
    return tuple(sorted(re.findall(r"[0-9a-zа-я]+", value.lower().replace("ё", "е"))))


def characteristics(table):
    """One table of a position; a rowspan name owns the value rows below it."""
    found, name, unit, left = [], "", "", 0
    rows = table.xpath("./tr")
    columns = [text(c) for c in rows[1].xpath("./th")]
    unit_at = next((i for i, c in enumerate(columns) if c.startswith("Единица")), None)
    for row in rows[2:]:
        cells = row.xpath("./td")
        if left > 0 and len(cells) == 1:
            found[-1][1].append(text(cells[0]))
            left -= 1
            continue
        if len(cells) < 2:
            continue
        name = text(cells[0])
        unit = text(cells[unit_at]) if unit_at is not None and unit_at < len(cells) else ""
        left = int(cells[0].get("rowspan") or 1) - 1
        found.append((name, [text(cells[1])], unit))
    return "; ".join(
        f"{n}: {', '.join(v for v in values if v)}{' ' + u if u else ''}" for n, values, u in found
    )


def positions(path):
    """The rows of «Объект закупки» with the characteristics table that follows each of them."""
    tree = html.fromstring(path.read_bytes().decode("utf-8", "ignore"))
    current = []
    for table in tree.xpath("//table"):
        rows = table.xpath("./tr")
        if not rows:
            continue
        head = [text(c) for c in rows[0].xpath("./th")]
        if head[:2] == ["Наименование товара, работы, услуги", "Код позиции"]:
            for row in rows[1:]:
                cells = row.xpath("./td")
                if len(cells) < 6:
                    continue
                name = re.sub(r"\s*Идентификатор:\s*\d+\s*$", "", text(cells[0]))
                current.append({
                    "name": name, "code": text(cells[1]), "type": text(cells[2]),
                    "unit": text(cells[3]), "qty": text(cells[5]), "chars": "",
                })
        elif head and head[0].startswith("Характеристики товара") and current:
            current[-1]["chars"] = characteristics(table)
    return current


def quantity(value):
    try:
        number = float(value.replace(" ", "").replace(",", "."))
    except ValueError:
        return value
    return int(number) if number == int(number) else number


def old_rows():
    known = set()
    for folder in OLD_SETS:
        for file in (DATASET / folder).glob("*.xlsx"):
            sheet = openpyxl.load_workbook(file, read_only=True, data_only=True).active
            for row in sheet.iter_rows(values_only=True):
                joined = " ".join(str(c) for c in row[:2] if c is not None)
                if joined:
                    known.add(tokens(joined))
    return known


def cards(codes):
    found = {}
    with gzip.open(CATALOG, "rt", encoding="utf-8") as lines:
        for line in lines:
            row = json.loads(line)
            if row.get("code") in codes:
                found[row["code"]] = row
    return found


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    manifest = list(csv.DictReader((SRC / "manifest.csv").open(encoding="utf-8-sig"), delimiter=";"))
    meta = {r["номер_закупки"]: r for r in manifest}
    known = old_rows()
    raw = []
    for number in sorted(meta):
        for index, item in enumerate(positions(SRC / number / "printform.html"), 1):
            raw.append(item | {"purchase": number, "place": f"printform.html, объект закупки, позиция {index}"})
    catalog = cards({i["code"] for i in raw})

    log, chosen, seen, per_purchase = [], [], set(), Counter()
    for item in raw:
        key = (tokens(item["name"] + " " + item["chars"]), item["unit"].lower())
        card = catalog.get(item["code"])
        status = (card or {}).get("status", "") or (card or {}).get("state", "")
        reason = ""
        if item["type"] != "Товар":
            reason = "не товар"
        elif not CODE.match(item["code"]):
            reason = "нет полного кода КТРУ"
        elif card and "ACTIVE" not in str(status).upper():
            reason = f"код не действует в каталоге: {status}"
        elif key in seen:
            reason = "дубль"
        elif key[0] in known or tokens(item["name"] + " " + item["chars"].replace(";", " ")) in known:
            reason = "совпадает со старым набором"
        elif per_purchase[item["purchase"]] >= MAX_PER_PURCHASE:
            reason = f"больше {MAX_PER_PURCHASE} строк на закупку"
        log.append(item | {"decision": "исключена" if reason else "включена", "reason": reason})
        if not reason:
            seen.add(key)
            per_purchase[item["purchase"]] += 1
            chosen.append(item)

    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "candidates.csv").open("w", encoding="utf-8-sig", newline="") as out:
        writer = csv.DictWriter(out, ["purchase", "place", "name", "code", "type", "unit", "qty",
                                      "chars", "decision", "reason"], delimiter=";")
        writer.writeheader()
        writer.writerows(log)

    source = openpyxl.Workbook()
    a = source.active
    a.title = "Позиции"
    a.append(HEAD)
    ref = openpyxl.Workbook()
    b = ref.active
    b.title = "Эталон"
    b.append(HEAD + ["КТРУ", "Наименование КТРУ", "Категория", "Номер закупки", "URL источника",
                     "Документ", "Место в документе", "Локальный исходник", "SHA-256 исходника",
                     "Дата проверки", "Статус кода", "Есть в каталоге проекта",
                     "Название строки = название карточки", "Автор разметки", "Проверил",
                     "Проверка: ок / ошибка", "Комментарий"])
    lines = []
    for n, item in enumerate(chosen, 1):
        row = [n, item["name"], item["chars"], item["unit"], quantity(item["qty"])]
        a.append(row)
        for cell in a[a.max_row][1:4]:
            cell.number_format = "@"
        card = catalog.get(item["code"]) or {}
        m = meta[item["purchase"]]
        doc = SRC / item["purchase"] / "printform.html"
        url = f"https://zakupki.gov.ru/epz/order/notice/printForm/view.html?regNumber={item['purchase']}"
        b.append(row + [item["code"], card.get("name", ""), m["категория"], item["purchase"], url,
                        "Печатная форма извещения", item["place"],
                        f"{item['purchase']}/printform.html", sha(doc), "",
                        str(card.get("status", card.get("state", ""))), "да" if card else "нет",
                        "да" if tokens(item["name"]) == tokens(card.get("name", "")) else "нет",
                        "код указан заказчиком", "", "", ""])
        lines.append({"row_number": n + 1, "item_id": n, "ktru_code": item["code"],
                      "name": item["name"], "unit": item["unit"],
                      "validation_status": "customer_code_not_human_verified", "source_url": url})
    source.save(OUT / "input_without_ktru.xlsx")
    ref.save(OUT / "reference_with_ktru.xlsx")
    (OUT / "reference.jsonl").write_text(
        "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8")

    missing = sorted({i["code"] for i in chosen if i["code"] not in catalog})
    same = sum(tokens(i["name"]) == tokens((catalog.get(i["code"]) or {}).get("name", "")) for i in chosen)
    summary = {
        "positions_in_print_forms": len(raw),
        "included": len(chosen),
        "purchases": len(per_purchase),
        "categories": dict(Counter(meta[i["purchase"]]["категория"] for i in chosen).most_common()),
        "excluded": dict(Counter(x["reason"].split(":")[0] for x in log if x["reason"]).most_common()),
        "codes_missing_from_catalog": missing,
        "rows_with_missing_code": sum(i["code"] in missing for i in chosen),
        "rows_name_equals_catalog_card_name": same,
        "rows_without_characteristics": sum(not i["chars"] for i in chosen),
        "catalog_card_fields": sorted(next(iter(catalog.values()), {}).keys()),
    }
    (OUT / "validation.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    for item in chosen[:4]:
        print(item["name"], "|", item["chars"][:150], "|", item["unit"], "|", item["qty"])


main()
