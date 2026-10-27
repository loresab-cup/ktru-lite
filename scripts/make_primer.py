"""The example file of the site (frontend/public/primer.xlsx) and its reference.

    python scripts/make_primer.py

Rows of the organizers' files and the real food specification, written the way a customer
fills the template: name, characteristics in one cell, unit, quantity. Most go to auto; the
kefir shows a row the system sends to review (two codes of one name that the row cannot tell
apart), the security service shows that services are coded too. The reference goes to
dataset/primer/primer_reference.jsonl for scripts/evaluate.py.
"""

import json
import pathlib

import openpyxl
from openpyxl.styles import Alignment, Font

ROOT = pathlib.Path(__file__).resolve().parent.parent
HEADER = ["№", "Наименование товара", "Характеристики", "Ед. изм.", "Кол-во"]
# name, characteristics, unit, quantity, KTRU code of the reference
ROWS = [
    (
        "Ноутбук",
        (
            "Диагональ экрана: не менее 15.6 дюйма; оперативная память 16 ГБ; тип накопителя SSD; "
            "разрешение экрана Full HD; количество ядер процессора 4"
        ),
        "шт",
        2,
        "26.20.11.110-00000165",
    ),
    (
        "Мышь компьютерная",
        (
            "Тип подключения: беспроводной; интерфейс USB; питание от аккумулятора; "
            "мультифункциональные клавиши: да"
        ),
        "шт",
        10,
        "26.20.16.170-00000003",
    ),
    (
        "Клавиатура",
        "Тип подключения: проводная; полноразмерная; интерфейс USB; длина кабеля 1,6 м",
        "шт",
        10,
        "26.20.16.110-00000002",
    ),
    (
        "Тетрадь ученическая школьная",
        "Линовка: линейка; 12 листов",
        "шт",
        500,
        "17.23.13.194-00000001",
    ),
    (
        "Папка картонная",
        "Формат А3; папка-обложка без скоросшивателя",
        "шт",
        100,
        "17.23.13.193-00000002",
    ),
    (
        "Полотенце бумажное",
        "Форма выпуска: лист; 50 листов в упаковке",
        "упак",
        40,
        "17.22.11.130-00000005",
    ),
    (
        "Лампа светодиодная",
        "Двухцокольная; мощность 10 Вт; цоколь G23; цветовая температура 2700 К",
        "шт",
        50,
        "27.40.15.150-00000001",
    ),
    (
        "Телевизор",
        "Тип экрана QLED; разрешение 8K UHD; диагональ 40 дюймов; Smart TV: да; 3D-экран: да",
        "шт",
        1,
        "26.40.20.122-00000006",
    ),
    (
        "Масло сливочное",
        "Сладко-сливочное, Крестьянское, высший сорт, несоленое",
        "кг",
        15,
        "10.51.30.110-00000004",
    ),
    (
        "Кефир",
        "Тип молочного сырья: нормализованное молоко; 2,5 процента",
        "л",
        30,
        "10.51.52.140-00000005",
    ),
    (
        "Услуги частной охраны (выставление поста охраны)",
        (
            "Охрана имущества и обеспечение пропускного режима на объектах с требованиями "
            "к антитеррористической защищенности"
        ),
        "усл. ед",
        1,
        "80.10.12.000-00000002",
    ),
]


def main() -> None:
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Спецификация"
    sheet.append(HEADER)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for number, (name, chars, unit, quantity, _) in enumerate(ROWS, start=1):
        sheet.append([number, name, chars, unit, quantity])
    for column, width in zip("ABCDE", (5, 42, 70, 10, 8), strict=True):
        sheet.column_dimensions[column].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    book.save(ROOT / "frontend/public/primer.xlsx")
    folder = ROOT / "dataset/primer"
    folder.mkdir(exist_ok=True)
    lines = [
        json.dumps({"row_number": number, "ktru_code": code, "name": name}, ensure_ascii=False)
        for number, (name, _, _, _, code) in enumerate(ROWS, start=2)
    ]
    (folder / "primer_reference.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(ROWS)} rows")


if __name__ == "__main__":
    main()
