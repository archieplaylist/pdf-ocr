"""Native text extraction: embedded text layer -> blocks."""

from __future__ import annotations

from .layout import Line, merge_lines_to_blocks, order_lines
from .model import Block


def extract_native(page) -> list[Block]:
    data = page.get_text("dict")
    lines: list[Line] = []
    for raw in data.get("blocks", []):
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
            lines.append(Line(bbox=tuple(ln["bbox"]), text=text, size=size, bold=bold))

    table_blocks = native_tables(page)
    if table_blocks:
        lines = [ln for ln in lines if not _inside_any(ln.bbox, [t.bbox for t in table_blocks])]

    blocks: list[Block] = []
    groups = order_lines(lines, page.rect.width)
    for column in groups:
        blocks.extend(merge_lines_to_blocks(column, page.rect.height))

    if table_blocks:
        if len(groups) <= 1:
            blocks.extend(table_blocks)
            blocks.sort(key=lambda b: b.bbox[1])
        else:
            blocks.extend(table_blocks)
    return blocks


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
        out.append(Block(type="table", bbox=tuple(tab.bbox), text="", rows=rows))
    return out


def _inside_any(bbox, boxes) -> bool:
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    return any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in boxes)
