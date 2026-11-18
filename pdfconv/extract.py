"""Native text extraction: embedded text layer -> blocks."""

from __future__ import annotations

from .layout import Line, merge_lines_to_blocks, order_lines
from .model import Block


def extract_native(page) -> list[Block]:
    data = page.get_text("dict")
    lines: list[Line] = []
    for block_index, raw in enumerate(data.get("blocks", [])):
        if raw.get("type") != 0:
            continue
        for ln in raw.get("lines", []):
            spans = [s for s in ln.get("spans", []) if s.get("text", "").strip()]
            if not spans:
                continue
            text = "".join(s["text"] for s in spans).strip()
            if not text:
                continue
            size = max(s["size"] for s in spans)
            bold = any("Bold" in s["font"] or s["flags"] & 16 for s in spans)
            italic = any("Italic" in s["font"] or s["flags"] & 2 for s in spans)
            lines.append(
                Line(
                    bbox=tuple(ln["bbox"]),
                    text=text,
                    size=size,
                    bold=bold,
                    italic=italic,
                    font=spans[0].get("font", ""),
                    # PyMuPDF already groups lines into paragraphs. Keeping its
                    # boundary stops the merge heuristic from gluing separate
                    # lines (e.g. an invoice's address block) together.
                    group=block_index,
                )
            )

    table_blocks = native_tables(page)
    if table_blocks:
        lines = [ln for ln in lines if not _inside_any(ln.bbox, [t.bbox for t in table_blocks])]

    blocks: list[Block] = []
    groups = order_lines(lines, page.rect.width)
    for column in groups:
        bounds = _bounds(column)
        blocks.extend(merge_lines_to_blocks(column, page.rect.height, column=bounds))

    if table_blocks:
        if len(groups) <= 1:
            # Insert in vertical order without re-sorting the text blocks: a
            # plain y-sort interleaves labels and values that share a baseline.
            for table in table_blocks:
                index = next(
                    (
                        i
                        for i, block in enumerate(blocks)
                        if block.bbox[1] > table.bbox[3]
                    ),
                    len(blocks),
                )
                blocks.insert(index, table)
        else:
            blocks.extend(table_blocks)
    return blocks


def _bounds(lines: list[Line]) -> tuple[float, float] | None:
    if not lines:
        return None
    return (min(ln.x0 for ln in lines), max(ln.bbox[2] for ln in lines))


def native_tables(page) -> list[Block]:
    out: list[Block] = []
    try:
        found = page.find_tables()
    except Exception:
        return out
    for tab in getattr(found, "tables", []):
        rows = [
            [(cell or "").strip().replace("\n", " ") for cell in row]
            for row in tab.extract()
        ]
        rows = [r for r in rows if any(c for c in r)]
        if len(rows) < 2 or max(len(r) for r in rows) < 2:
            continue
        widths: list[float] = []
        try:
            cells = tab.rows[0].cells
            widths = [
                round(cell[2] - cell[0], 2) if cell else 0.0 for cell in cells
            ]
        except Exception:
            widths = []
        out.append(
            Block(
                type="table",
                bbox=tuple(tab.bbox),
                text="",
                rows=rows,
                col_widths=widths or None,
            )
        )
    return out


def _inside_any(bbox, boxes) -> bool:
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    return any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in boxes)
