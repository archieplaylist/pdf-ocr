"""PDF exporters.

Two modes share the same editable model:

* ``write_pdf_reflow`` rebuilds every text page with reportlab (same
  ``auto`` / ``flow`` / ``facsimile`` semantics as the DOCX exporter).
* ``write_pdf_inplace`` keeps the original PDF and rewrites only the blocks
  the user edited, preserving the source layout everywhere else.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

import pymupdf
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import Image, Paragraph, Table, TableStyle

from .export_docx import LOW_CONF, _body_size, _is_image_page, _margins
from .model import Block, Document, Page

_ALIGN = {"left": TA_LEFT, "center": TA_CENTER, "right": TA_RIGHT}
_MARGIN = 56.7
_MIN_SIZE = 6.0
_MAX_SIZE = 72.0

_FONT_FAMILIES = {
    "Times New Roman": ("Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic"),
    "Courier New": ("Courier", "Courier-Bold", "Courier-Oblique", "Courier-BoldOblique"),
    "Helvetica": ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique"),
}

_PYMUPDF_FONTS = {
    "Times New Roman": ("tiro", "tibo", "tiit", "tibi"),
    "Courier New": ("cour", "cobo", "coit", "cobi"),
    "Helvetica": ("helv", "hebo", "heit", "hebi"),
}


def write_pdf_reflow(doc: Document, out: Path, layout: str = "auto") -> None:
    """Write a fresh, text-based PDF from the document model."""
    if layout not in ("auto", "flow", "facsimile"):
        raise ValueError(f"unknown layout: {layout}")

    body = _body_size(doc, layout)
    pdf = None
    if any(_is_image_page(page, layout) for page in doc.pages) or any(
        block.type == "image" and not block.image_data
        for page in doc.pages
        for block in page.blocks
    ):
        pdf = pymupdf.open(doc.path)

    out.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(out))
    try:
        for page in doc.pages:
            if _is_image_page(page, layout):
                _draw_page_image(c, pdf, page)
                c.showPage()
                continue
            _draw_text_page(c, page, body, pdf)
    finally:
        c.save()
        if pdf is not None:
            pdf.close()


def write_pdf_inplace(doc: Document, out: Path) -> None:
    """Keep the original PDF and rewrite only edited blocks.

    Text and table edits are redacted and redrawn at the original position.
    Reordering has no visual meaning here; use the reflow export for that.
    """
    src = pymupdf.open(doc.path)
    try:
        for page_model in doc.pages:
            changed = [block for block in page_model.blocks if block.edited]
            if not changed and not page_model.removed:
                continue
            page = src[page_model.number - 1]
            for block in changed:
                _add_redaction(page, block.bbox)
            for box in page_model.removed:
                _add_redaction(page, box)
            page.apply_redactions(
                images=pymupdf.PDF_REDACT_IMAGE_NONE,
                graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                text=pymupdf.PDF_REDACT_TEXT_REMOVE,
            )
            for block in changed:
                _draw_block(page, block)
        out.parent.mkdir(parents=True, exist_ok=True)
        src.save(str(out), garbage=3, deflate=True)
    finally:
        src.close()


# -- reflow helpers --------------------------------------------------------


def _draw_text_page(c, page: Page, body: float, pdf) -> None:
    c.setPageSize((page.width, page.height))
    left, right = _margins(page)
    top = bottom = _MARGIN
    available = max(page.width - left - right, 60.0)
    y = page.height - top

    for block in page.blocks:
        flowable = _flowable(block, body, available, pdf, page)
        if flowable is None:
            continue
        before = flowable.getSpaceBefore()
        after = flowable.getSpaceAfter()
        width, height = flowable.wrapOn(c, available, max(y - bottom, 1.0))
        if height + before > (y - bottom) and y < page.height - top - 1:
            c.showPage()
            c.setPageSize((page.width, page.height))
            y = page.height - top
            width, height = flowable.wrapOn(c, available, max(y - bottom, 1.0))
        y -= before
        flowable.drawOn(c, left, y - height)
        y -= height + after

    c.showPage()


def _flowable(block: Block, body: float, available: float, pdf, page: Page):
    if block.type == "table" and block.rows:
        return _table(block, body, available)
    if block.type == "image":
        return _image(block, available, pdf, page)
    if not block.plain_text.strip():
        return None
    return _paragraph(block, body)


def _paragraph(block: Block, body: float) -> Paragraph:
    size = _font_size(block, body)
    family = _family(block.font)
    name = _reportlab_font(family, block.bold, block.italic)
    style = ParagraphStyle(
        name=f"block-{block.id or 'x'}",
        fontName=name,
        fontSize=size,
        leading=size * 1.22,
        textColor=_color(block.color),
        alignment=_ALIGN.get(block.align, TA_LEFT),
        spaceBefore=max(block.space_before or 0, 0),
        spaceAfter=_space_after(block),
        leftIndent=max(block.indent or 0, 0),
        backColor=(
            colors.Color(1.0, 0.94, 0.45) if block.conf < LOW_CONF else None
        ),
    )
    return Paragraph(_markup_for(block), style)


def _markup_for(block: Block) -> str:
    if block.runs:
        pieces = []
        for run in block.runs:
            text = _markup(run.text)
            tags = []
            if run.bold or block.bold:
                tags.append("b")
            if run.italic or block.italic:
                tags.append("i")
            font = run.font or block.font
            rsize = run.size or 0
            color = run.color or ""
            if font or rsize or color:
                attrs = []
                if font:
                    attrs.append(f'name="{_reportlab_font(_family(font), False, False)}"')
                if rsize:
                    attrs.append(f'size="{round(min(max(rsize, 6.0), 72.0), 1)}"')
                if color:
                    hexcolor = color if color.startswith("#") else f"#{color}"
                    attrs.append(f'color="{hexcolor}"')
                if attrs:
                    text = f'<font {" ".join(attrs)}>{text}</font>'
            for tag in reversed(tags):
                text = f"<{tag}>{text}</{tag}>"
            pieces.append(text)
        return "".join(pieces) if pieces else " "
    return _markup(block.plain_text)


def _markup(text: str) -> str:
    # reportlab paragraphs do not understand tab stops; collapse them to a
    # readable gap. Newlines become line breaks.
    text = text.replace("\t", "    ")
    parts = [escape(part) for part in text.split("\n")]
    return "<br/>".join(parts)


def _table(block: Block, body: float, available: float) -> Table | None:
    rows = [list(row) for row in (block.rows or [])]
    if not rows:
        return None
    n_cols = max(len(row) for row in rows)
    for row in rows:
        row.extend([""] * (n_cols - len(row)))

    size = max(min(block.size or body, 14.0), 7.0)
    widths = _col_widths(block, n_cols, available)
    cell_style = ParagraphStyle(
        "cell",
        fontName="Helvetica",
        fontSize=size,
        leading=size * 1.15,
        textColor=_color(block.color),
    )
    header_style = ParagraphStyle("cell-header", parent=cell_style, fontName="Helvetica-Bold")

    data = []
    for r, row in enumerate(rows):
        cells = []
        for value in row:
            style = header_style if r == 0 else cell_style
            cells.append(Paragraph(_markup(value) if value else " ", style))
        data.append(cells)

    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#8a8577")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#ededed")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    return table


def _col_widths(block: Block, n_cols: int, available: float) -> list[float]:
    if block.col_widths and len(block.col_widths) >= n_cols:
        widths = [max(width, 12.0) for width in block.col_widths[:n_cols]]
        total = sum(widths)
        if total > available:
            widths = [width * available / total for width in widths]
        return widths
    return [available / n_cols] * n_cols


def _image(block: Block, available: float, pdf, page: Page):
    data = block.image_data or _crop_image(pdf, page, block)
    if not data:
        return None
    x0, y0, x1, y1 = block.bbox
    source_width = max(x1 - x0, 1.0)
    source_height = max(y1 - y0, 1.0)
    width = min(source_width, available)
    height = width * source_height / source_width
    try:
        image = Image(ImageReader(BytesIO(data)), width=width, height=height)
    except Exception:
        return None
    image.spaceBefore = max(block.space_before or 0, 0)
    image.spaceAfter = 4
    return image


def _crop_image(pdf, page: Page, block: Block) -> bytes | None:
    if pdf is None:
        return None
    try:
        rect = pymupdf.Rect(block.bbox)
        if rect.is_empty:
            return None
        pix = pdf[page.number - 1].get_pixmap(clip=rect, dpi=200, alpha=False)
        return pix.tobytes("png")
    except Exception:
        return None


def _draw_page_image(c, pdf, page: Page) -> None:
    c.setPageSize((page.width, page.height))
    if pdf is None:
        return
    try:
        pix = pdf[page.number - 1].get_pixmap(dpi=200, alpha=False)
        image = ImageReader(BytesIO(pix.tobytes("png")))
        c.drawImage(image, 0, 0, width=page.width, height=page.height)
    except Exception:
        pass


# -- in-place helpers ------------------------------------------------------


def _add_redaction(page, bbox) -> None:
    rect = pymupdf.Rect(bbox)
    if rect.is_empty or rect.width <= 0 or rect.height <= 0:
        return
    page.add_redact_annot(rect, fill=(1, 1, 1))


def _draw_block(page, block: Block) -> None:
    if block.type == "image" or block.type == "table" and not block.rows:
        return
    rect = pymupdf.Rect(block.bbox)
    if rect.is_empty:
        return
    if block.type == "table" and block.rows:
        _draw_table(page, block, rect)
        return
    text = block.plain_text.strip()
    if not text:
        return

    size = _font_size(block, 10.5)
    font = _pymupdf_font(_family(block.font), block.bold, block.italic)
    color = _rgb(block.color)
    align = {"left": 0, "center": 1, "right": 2}.get(block.align, 0)
    while size >= _MIN_SIZE:
        remaining = page.insert_textbox(
            rect,
            text,
            fontsize=size,
            fontname=font,
            color=color,
            align=align,
        )
        if remaining >= 0:
            return
        size -= 0.5


def _draw_table(page, block: Block, rect: pymupdf.Rect) -> None:
    rows = [list(row) for row in (block.rows or [])]
    if not rows:
        return
    n_cols = max(len(row) for row in rows)
    for row in rows:
        row.extend([""] * (n_cols - len(row)))

    widths = _col_widths(block, n_cols, rect.width)
    scale = rect.width / max(sum(widths), 1.0)
    widths = [width * scale for width in widths]
    row_height = rect.height / len(rows)

    page.draw_rect(rect, color=_rgb(""), fill=None, width=0.6)
    x = rect.x0
    for width in widths[:-1]:
        x += width
        page.draw_line((x, rect.y0), (x, rect.y1), color=_rgb(""), width=0.5)
    y = rect.y0
    for _ in range(len(rows) - 1):
        y += row_height
        page.draw_line((rect.x0, y), (rect.x1, y), color=_rgb(""), width=0.5)

    size = max(min(block.size or 9.0, 14.0), 6.0)
    font = _pymupdf_font("Helvetica", block.bold or False, block.italic or False)
    y = rect.y0
    for row in rows:
        x = rect.x0
        for index, value in enumerate(row):
            cell = pymupdf.Rect(x + 2, y + 1, x + widths[index] - 2, y + row_height - 1)
            if value and cell.width > 2 and cell.height > 2:
                page.insert_textbox(
                    cell,
                    value,
                    fontsize=size,
                    fontname=font,
                    color=_rgb(block.color),
                )
            x += widths[index]
        y += row_height


# -- shared style helpers --------------------------------------------------


def _family(font: str) -> str:
    name = (font or "").lower()
    if "times" in name or ("serif" in name and "sans" not in name):
        return "Times New Roman"
    if "courier" in name or "mono" in name:
        return "Courier New"
    return "Helvetica"


def _reportlab_font(family: str, bold: bool, italic: bool) -> str:
    regular, bold_name, italic_name, both = _FONT_FAMILIES[family]
    if bold and italic:
        return both
    if bold:
        return bold_name
    if italic:
        return italic_name
    return regular


def _pymupdf_font(family: str, bold: bool, italic: bool) -> str:
    regular, bold_name, italic_name, both = _PYMUPDF_FONTS[family]
    if bold and italic:
        return both
    if bold:
        return bold_name
    if italic:
        return italic_name
    return regular


def _font_size(block: Block, body: float) -> float:
    size = block.size or body
    return round(min(max(size, _MIN_SIZE), _MAX_SIZE), 1)


def _space_after(block: Block) -> float:
    if block.type == "title":
        return 10
    if block.type == "heading":
        return 6
    return 4


def _color(value: str):
    if not value:
        return colors.black
    try:
        return colors.HexColor(value if value.startswith("#") else f"#{value}")
    except Exception:
        return colors.black


def _rgb(value: str) -> tuple[float, float, float]:
    color = _color(value)
    return (color.red, color.green, color.blue)
