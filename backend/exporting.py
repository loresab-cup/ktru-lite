"""Spreadsheet encoding and formula escaping; independent of HTTP and database state."""

import csv
import io

from parser import CONTROL


def cell(value):
    """Formula injection guard: a text starting with = + - @ gets a leading apostrophe.
    Control characters go: openpyxl refuses them and the whole export would fail."""
    if isinstance(value, str):
        value = CONTROL.sub("", value)
    # Leading spaces or a line break do not hide a formula from a spreadsheet
    if isinstance(value, str) and (
        value[:1] in ("\t", "\r", "\n") or value.lstrip()[:1] in ("=", "+", "-", "@")
    ):
        return "'" + value
    return value


def to_csv(rows: list[list]) -> bytes:
    buffer = io.StringIO()
    csv.writer(buffer, delimiter=";").writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")


def to_xlsx(rows: list[list]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "КТРУ"
    for row in rows:
        sheet.append(row)
    for header_cell in sheet[1]:
        header_cell.font = Font(bold=True)
    sheet.freeze_panes = "B2"
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
