"""Close table cells clipped by a browser's print page boundary."""

TOLERANCE = 3


def side_extent(edges, x, top, bottom):
    segments = sorted(
        (e["top"], e["bottom"])
        for e in edges
        if e["orientation"] == "v" and abs(e["x0"] - x) <= TOLERANCE
    )
    merged = []
    for start, end in segments:
        if merged and start <= merged[-1][1] + TOLERANCE:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    middle = (top + bottom) / 2
    return next((span for span in merged if span[0] <= middle <= span[1]), [top, bottom])


def closed_border(edges, left, right, y):
    covered = left
    spans = sorted(
        (e["x0"], e["x1"])
        for e in edges
        if e["orientation"] == "h" and abs(e["top"] - y) <= TOLERANCE
    )
    for start, end in spans:
        if end < covered:
            continue
        if start > covered + TOLERANCE:
            return False
        covered = max(covered, end)
    return covered >= right - TOLERANCE


def extract_tables(page):
    tables = page.find_tables()
    if not tables:
        return []
    edges = page.edges
    horizontal = next(e for e in edges if e["orientation"] == "h")
    additions, open_ends = [], []
    for table in tables:
        left, top, right, bottom = table.bbox
        a, b = side_extent(edges, left, top, bottom), side_extent(edges, right, top, bottom)
        start, end = max(a[0], b[0]), min(a[1], b[1])
        if not closed_border(edges, left, right, end):
            open_ends.append((left, right, end))
        for y in (start, end):
            if closed_border(edges, left, right, y):
                continue
            additions.append(
                horizontal
                | {
                    "x0": left,
                    "x1": right,
                    "width": right - left,
                    "top": y,
                    "bottom": y,
                    "height": 0,
                    "y0": page.height - y,
                    "y1": page.height - y,
                    "doctop": page.initial_doctop + y,
                }
            )
    if additions:
        tables = page.find_tables({"explicit_horizontal_lines": additions})
    return [
        (
            table.extract(),
            any(
                abs(table.bbox[0] - left) <= TOLERANCE
                and abs(table.bbox[2] - right) <= TOLERANCE
                and abs(table.bbox[3] - end) <= TOLERANCE
                for left, right, end in open_ends
            ),
        )
        for table in tables
    ]
