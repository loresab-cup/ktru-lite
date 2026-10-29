"""A real text PDF longer than 50 pages is accepted asynchronously."""

import io
import time
from pathlib import Path

from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

import job_queue
import parser


def pdf_bytes(pages=51):
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    for _ in range(pages):
        page = writer.add_blank_page(width=595, height=842)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 40 780 Td (Notebook 16 GB) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_pdf_51_pages_is_parsed_and_upload_is_nonblocking(api_database, monkeypatch, tmp_path):
    data = pdf_bytes()
    path = tmp_path / "large.pdf"
    path.write_bytes(data)
    assert len(parser.parse_file(path)) == 51
    published = []
    monkeypatch.setattr(job_queue, "publish", lambda job_id: published.append(job_id))
    with TestClient(api_database.app) as client:
        started = time.monotonic()
        response = client.post("/api/jobs", files={"file": ("large.pdf", data, "application/pdf")})
        elapsed = time.monotonic() - started
        assert response.status_code == 202 and elapsed < 5
        assert len(published) == 1
        assert client.get("/api/jobs/" + response.json()["job_id"]).json()["status"] == "queued"


DATA = Path(__file__).parent / "data"


def test_a_word_table_pdf_gives_one_position_per_row():
    """14 rows of the 292-row organizers' file, saved by Word as PDF: long rows break over five
    page ends. Line by line it was one position per line of a cell; now every table row is one
    position, its rest from the next page included."""
    items = parser.parse_file(DATA / "table_spec.pdf")
    assert len(items) == 14
    assert items[0].text.startswith("Ноутбук. Диагональ экрана")
    assert all("Единица измерения товара" in item.text for item in items)  # no lost tails


def test_rows_split_by_pages_and_repeated_headers():
    rows = [["№", "Наименование"], ["1", "Ноутбук"]]
    assert parser.continues(rows, ["", "16 ГБ"])
    assert not parser.continues(rows, ["2", "Мышь"])
    assert not parser.continues([], ["", "Мышь"])
    table = [["№", "Наименование"], ["1", "Ноутбук"], ["№", "Наименование"], ["2", "Мышь"]]
    assert parser.without_repeated_headers(table) == [table[0], table[1], table[3]]
