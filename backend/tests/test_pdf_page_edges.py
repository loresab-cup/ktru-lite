"""Browser print can leave table cells open at a page boundary."""

from pathlib import Path

import pytest

import parser


@pytest.mark.parametrize("repeat_header", [False, True])
def test_open_page_cells_do_not_lose_products_or_continuations(repeat_header):
    path = Path(__file__).parent / "data" / f"browser-split-{str(repeat_header).lower()}.pdf"
    rows = parser.parse_file(path)
    assert len(rows) == 3
    assert [r.text for r in rows] == [
        "Mouse. Wireless",
        "Keyboard. Connection: USB wired",
        "Display. 24 inch",
    ]
