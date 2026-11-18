"""Native text extraction: embedded text layer -> blocks."""

from __future__ import annotations

import pymupdf

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
            colors = [s.get("color", 0) for s in spans]
            dom_color = max(set(colors), key=colors.count) if colors else 0
            color = "#{:06x}".format(dom_color) if dom_color else ""
            lines.append(
                Line(
                    bbox=tuple(ln["bbox"]),
                    text=text,
                    size=size,
                    bold=bold,
                    italic=italic,
                    font=spans[0].get("font", ""),
                    color=color,
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

    # Interleave embedded images/logos by vertical position.
    image_blocks = native_images(page)
    if image_blocks:
        merged: list[Block] = []
        i = j = 0
        while i < len(blocks) and j < len(image_blocks):
            if blocks[i].bbox[1] <= image_blocks[j].bbox[1]:
                merged.append(blocks[i]); i += 1
            else:
                merged.append(image_blocks[j]); j += 1
        merged.extend(blocks[i:])
        merged.extend(image_blocks[j:])
        blocks = merged

    return blocks


def _bounds(lines: list[Line]) -> tuple[float, float] | None:
    if not lines:
        return None
    return (min(ln.x0 for ln in lines), max(ln.bbox[2] for ln in lines))


def native_images(page) -> list[Block]:
    out: list[Block] = []
    try:
        raw_blocks = page.get_text("dict").get("blocks", [])
    except Exception:
        raw_blocks = []
    for raw in raw_blocks:
        if raw.get("type") != 1:
            continue
        bbox = raw.get("bbox")
        if not bbox:
            continue
        # Try to extract the image bytes.
        image_bytes = None
        ext = "png"
        try:
            xref = raw.get("xref") or 0
            if xref:
                info = page.parent.extract_image(xref)
                image_bytes = info.get("image")
                ext = info.get("ext", "png")
        except Exception:
            image_bytes = None
        if image_bytes is None:
            # Fall back to rendering the bbox region.
            try:
                pix = page.get_pixmap(clip=pymupdf.Rect(bbox), dpi=150)
                image_bytes = pix.tobytes("png")
                ext = "png"
            except Exception:
                continue
        if not image_bytes:
            continue
        out.append(
            Block(
                type="image",
                bbox=tuple(bbox),
                text="",
                conf=1.0,
                image_data=image_bytes,
                image_ext=ext,
            )
        )
    # De-duplicate overlapping image blocks.
    deduped: list[Block] = []
    for b in out:
        if any(
            abs(b.bbox[0] - o.bbox[0]) < 2 and abs(b.bbox[1] - o.bbox[1]) < 2
            for o in deduped
        ):
            continue
        deduped.append(b)
    return deduped


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
