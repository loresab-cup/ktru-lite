"""Specification file -> list of positions (one Item per product row).

Formats: xlsx (every sheet), csv (utf-8 / cp1251, ; , or tab), docx (tables, then paragraphs),
pdf (text of up to 200 pages), txt (one position per line).
"""

import csv
import io
import logging
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_ITEMS = 1000
MAX_CELLS = 300_000
MAX_PDF_PAGES = 200
MAX_UNZIPPED_BYTES = 200 * 1024 * 1024  # zip bomb guard for xlsx and docx
MAX_CELL_CHARS = 2000

# Extension -> first bytes of a valid file (None = plain text, no signature)
SIGNATURES = {".xlsx": b"PK", ".docx": b"PK", ".pdf": b"%PDF", ".csv": None, ".txt": None}

HEADER_WORDS = ("наименование", "товар", "характеристик", "кол-во", "количество")
NAME_WORDS = ("наименование", "товар", "название", "предмет", "позиция")
NOT_NAME_PREFIXES = ("код", "№", "номер", "артикул", "окпд", "ктру", "товарный знак")
# Columns whose values describe the product and go into the position text with the name
DETAIL_WORDS = ("характерист", "бренд", "марка", "модель", "производител", "описание")
SKIP_PREFIXES = ("итого", "всего", "итог")
MAX_HEADER_CELL = 40
NUMBERING = re.compile(r"^\s*\d{1,4}\s*[.)]\s+")

logging.getLogger("pypdf").setLevel(logging.ERROR)  # broken fonts spam warnings


class ParseError(ValueError):
    """A file the user must fix; the message is shown as is (Russian)."""


@dataclass
class Item:
    row_number: int
    text: str
    columns: dict[str, str] = field(default_factory=dict)


def check_signature(ext: str, head: bytes) -> None:
    if ext not in SIGNATURES:
        raise ParseError("Формат не поддерживается: загрузите xlsx, csv, docx, pdf или txt")
    signature = SIGNATURES[ext]
    if signature is not None and not head.startswith(signature):
        raise ParseError(f"Файл не похож на {ext}: содержимое не совпадает с расширением")


def parse_file(path: Path) -> list[Item]:
    ext = path.suffix.lower()
    data = path.read_bytes()
    if len(data) > MAX_FILE_BYTES:
        raise ParseError("Файл больше 10 МБ")
    check_signature(ext, data[:8])
    readers = {".xlsx": read_xlsx, ".csv": read_csv, ".docx": read_docx, ".pdf": read_pdf}
    items = readers[ext](path) if ext in readers else parse_lines(decode_text(data).splitlines())
    if not items:
        raise ParseError("Не найдено ни одной позиции: проверьте колонку с наименованием")
    if len(items) > MAX_ITEMS:
        raise ParseError(f"В файле {len(items)} позиций, допустимо не больше {MAX_ITEMS}")
    return items


# --- readers -------------------------------------------------------------------------------


def decode_text(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1251", errors="replace")


def check_zip(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            total = sum(info.file_size for info in archive.infolist())
    except zipfile.BadZipFile as exc:
        raise ParseError("Файл повреждён: откройте и сохраните его заново") from exc
    if total > MAX_UNZIPPED_BYTES:
        raise ParseError("Файл слишком большой после распаковки")


def read_xlsx(path: Path) -> list[Item]:
    from openpyxl import load_workbook

    check_zip(path)
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # broken zip or xml inside
        raise ParseError("Файл xlsx повреждён: откройте и сохраните его заново") from exc
    items: list[Item] = []
    cells = 0
    try:
        for sheet in workbook.worksheets:
            rows = []
            for row in sheet.iter_rows(values_only=True):
                cells += len(row)
                if cells > MAX_CELLS:
                    raise ParseError("В файле слишком много ячеек")
                rows.append(row)
            items.extend(parse_rows(rows))
    finally:
        workbook.close()
    return items


def read_csv(path: Path) -> list[Item]:
    text = decode_text(path.read_bytes())
    try:
        delimiter = csv.Sniffer().sniff(text[:4096], delimiters=";,\t").delimiter
    except csv.Error:
        delimiter = ";"
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    if sum(len(row) for row in rows) > MAX_CELLS:
        raise ParseError("В файле слишком много ячеек")
    return parse_rows(rows)


def read_docx(path: Path) -> list[Item]:
    from docx import Document

    check_zip(path)
    try:
        document = Document(str(path))
    except Exception as exc:
        raise ParseError("Файл docx повреждён: откройте и сохраните его заново") from exc
    items: list[Item] = []
    for table in document.tables:
        rows = [[cell.text for cell in row.cells] for row in table.rows]
        items.extend(parse_rows(rows))
    if items:
        return items
    return parse_lines([paragraph.text for paragraph in document.paragraphs])


def read_pdf(path: Path) -> list[Item]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(path))
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ParseError(f"В PDF больше {MAX_PDF_PAGES} страниц")
        lines = [line for page in reader.pages for line in (page.extract_text() or "").split("\n")]
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError("PDF повреждён или защищён паролем") from exc
    return parse_lines(lines)


# --- rows and lines -> items ---------------------------------------------------------------


def clean(value) -> str:
    return " ".join(("" if value is None else str(value)).split())[:MAX_CELL_CHARS]


def is_skipped(text: str) -> bool:
    lowered = text.lower()
    return not text or lowered.startswith(SKIP_PREFIXES) or text.replace(" ", "").isdigit()


def is_header_cell(cell: str) -> bool:
    lowered = cell.lower()
    return 0 < len(lowered) <= MAX_HEADER_CELL and any(w in lowered for w in HEADER_WORDS)


def is_name_cell(cell: str) -> bool:
    lowered = cell.lower()
    return (
        0 < len(lowered) <= MAX_HEADER_CELL
        and not lowered.startswith(NOT_NAME_PREFIXES)
        and any(word in lowered for word in NAME_WORDS)
    )


def find_header(rows: list[list[str]]) -> int | None:
    for index, row in enumerate(rows[:30]):
        if sum(1 for cell in row if cell) >= 2 and any(is_header_cell(c) for c in row):
            return index
    return None


def name_column(header: list[str], body: list[list[str]]) -> int:
    for index, cell in enumerate(header):
        if is_name_cell(cell):
            return index
    # No name header: the column with the most text
    width = max((len(row) for row in body), default=1)
    totals = [sum(len(row[i]) for row in body if i < len(row)) for i in range(width)]
    return totals.index(max(totals))


def parse_rows(raw_rows) -> list[Item]:
    """Table rows -> items; the header row is found by words like «наименование»."""
    rows = [[clean(value) for value in row] for row in raw_rows]
    header_index = find_header(rows)
    if header_index is None:
        # No header: a table of one text column is the same as lines of text
        # Cells are joined with '. ': the name stays apart from the characteristics
        return parse_lines([". ".join(cell for cell in row if cell) for row in rows])
    header = rows[header_index]
    body = rows[header_index + 1 :]
    name_index = name_column(header, body)
    details = [
        i
        for i, cell in enumerate(header)
        if i != name_index and any(w in cell.lower() for w in DETAIL_WORDS)
    ]
    header_key = [cell.lower() for cell in header if cell]
    items = []
    for offset, row in enumerate(body):
        name = row[name_index] if name_index < len(row) else ""
        if is_skipped(name) or [cell.lower() for cell in row if cell] == header_key:
            continue
        parts = [name, *(row[i] for i in details if i < len(row) and row[i])]
        columns = {
            (title or f"Колонка {i + 1}"): (row[i] if i < len(row) else "")
            for i, title in enumerate(header)
        }
        items.append(Item(header_index + offset + 2, ". ".join(parts), columns))
    return items


def parse_lines(lines: list[str]) -> list[Item]:
    """Plain text: every non-empty line is a position; numbering «1.» is cut off."""
    items = []
    for number, line in enumerate(lines, start=1):
        text = clean(NUMBERING.sub("", line))
        lowered = text.lower()
        header_like = (
            len(text) <= MAX_HEADER_CELL * 2 and sum(word in lowered for word in HEADER_WORDS) >= 2
        )
        if is_skipped(text) or header_like or len(text) < 3:
            continue
        items.append(Item(number, text, {"Текст": text}))
    return items
