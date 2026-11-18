"""DOCX exporter: editable flow layout, or page images for scanned pages."""

from __future__ import annotations

import re
from collections import Counter
from io import BytesIO
from pathlib import Path
from statistics import median

import pymupdf
from docx import Document as DocxDocument
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_COLOR_INDEX
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from .model import Block, Document

LOW_CONF = 0.70
LAYOUTS = ("auto", "flow", "facsimile")

_ALIGNMENTS = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
}

_NUMBER_RE = re.compile(r"^[\s\d.,%:/+-]+$")


def write_docx(doc: Document, out: Path, layout: str = "auto") -> None:
    """Write a DOCX.

    ``layout`` is ``auto`` (scanned pages stay images, native pages are
    rebuilt), ``flow`` (always rebuild text) or ``facsimile`` (every page is
    an image).
    """
    if layout not in LAYOUTS:
        raise ValueError(f"unknown layout: {layout}")

    dx = DocxDocument()
    _configure_styles(dx)
    body = _body_size(doc, layout)
    pdf = None
    if any(_is_image_page(p, layout) for p in doc.pages):
        pdf = pymupdf.open(doc.path)

    try:
        for page in doc.pages:
            section = (
                dx.sections[0]
                if page.number == 1
                else dx.add_section(WD_SECTION.NEW_PAGE)
            )
            if _is_image_page(page, layout):
                _set_page_geometry(section, page, margins=0)
                _add_image_page(dx, pdf, page)
            else:
                _set_page_geometry(section, page)
                for block in page.blocks:
                    if block.type == "table" and block.rows:
                        _add_table(dx, block, body)
                    elif block.text:
                        _add_text(dx, block, body)
    finally:
        if pdf is not None:
            pdf.close()

    out.parent.mkdir(parents=True, exist_ok=True)
    dx.save(str(out))


def _configure_styles(dx) -> None:
    normal = dx.styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = RGBColor(0, 0, 0)
    normal.paragraph_format.space_after = Pt(4)
    normal.paragraph_format.line_spacing = 1.15


def _is_image_page(page, layout: str) -> bool:
    """True when this page should be written as a page-sized image."""
    if layout == "facsimile":
        return True
    return layout == "auto" and (page.scan or page.source == "ocr")


def _body_size(doc: Document, layout: str) -> float:
    weights: Counter[float] = Counter()
    for page in doc.pages:
        if _is_image_page(page, layout):
            continue
        for block in page.blocks:
            if block.text and block.size > 0:
                weights[round(block.size, 1)] += max(len(block.text), 1)
    return weights.most_common(1)[0][0] if weights else 10.5


def _set_page_geometry(section, page, margins: float | None = None) -> None:
    section.page_width = Pt(page.width)
    section.page_height = Pt(page.height)
    if margins is not None:
        section.left_margin = Pt(margins)
        section.right_margin = Pt(margins)
        section.top_margin = Pt(margins)
        section.bottom_margin = Pt(margins)
        section.header_distance = Pt(0)
        section.footer_distance = Pt(0)
        return

    left, right = _margins(page)
    section.left_margin = Pt(left)
    section.right_margin = Pt(right)
    section.top_margin = Pt(56.7)
    section.bottom_margin = Pt(56.7)
    section.header_distance = Pt(18)
    section.footer_distance = Pt(18)


def _margins(page) -> tuple[float, float]:
    lefts = [
        block.bbox[0]
        for block in page.blocks
        if block.text and block.bbox[0] > 20
    ]
    rights = [
        page.width - block.bbox[2]
        for block in page.blocks
        if block.text and block.bbox[2] < page.width - 20
    ]
    left = median(lefts) if lefts else 56.7
    right = median(rights) if rights else left
    return (_clamp_margin(left), _clamp_margin(right))


def _clamp_margin(value: float) -> float:
    return min(max(value, 36.0), 90.0)


def _add_image_page(dx, pdf, page) -> None:
    pix = pdf[page.number - 1].get_pixmap(dpi=200, alpha=False)
    stream = BytesIO(pix.tobytes("png"))
    para = dx.add_paragraph()
    fmt = para.paragraph_format
    fmt.space_before = Pt(0)
    fmt.space_after = Pt(0)
    fmt.line_spacing = 1
    # One point less than the page keeps the section-break paragraph from
    # pushing a full-page image onto an extra blank page.
    run = para.add_run()
    run.add_picture(
        stream,
        width=Pt(page.width),
        height=Pt(page.height - 1),
    )


def _add_text(dx, block: Block, body: float) -> None:
    text = block.text.strip()
    if not text:
        return
    para = dx.add_paragraph()
    fmt = para.paragraph_format
    fmt.space_before = Pt(0)
    fmt.space_after = Pt(_space_after(block))
    fmt.line_spacing = 1.15
    if block.type in ("title", "heading"):
        fmt.keep_with_next = True
    para.alignment = _ALIGNMENTS.get(block.align, WD_ALIGN_PARAGRAPH.LEFT)

    run = para.add_run(text)
    _style_run(run, block, body)
    if block.type in ("title", "heading"):
        run.bold = True
    if block.conf < LOW_CONF:
        run.font.highlight_color = WD_COLOR_INDEX.YELLOW


def _style_run(run, block: Block, body: float) -> None:
    run.font.name = _font_name(block.font)
    run.font.size = Pt(_font_size(block, body))
    run.bold = block.bold or block.type in ("title", "heading")
    run.italic = block.italic


def _font_size(block: Block, body: float) -> float:
    size = block.size or body
    return round(min(max(size, 6.0), 72.0), 1)


def _font_name(font: str) -> str:
    name = (font or "").lower()
    if "times" in name or ("serif" in name and "sans" not in name):
        return "Times New Roman"
    if "courier" in name or "mono" in name:
        return "Courier New"
    if not font or "helvetica" in name or "arial" in name:
        return "Arial"
    return font.split("+")[-1] or "Arial"


def _space_after(block: Block) -> float:
    if block.type == "title":
        return 10
    if block.type == "heading":
        return 6
    return 4


def _add_table(dx, block: Block, body: float) -> None:
    rows = block.rows or []
    if not rows:
        return
    n_cols = max(len(row) for row in rows)
    table = dx.add_table(rows=len(rows), cols=n_cols)
    table.style = "Table Grid"
    table.autofit = False
    table.allow_autofit = False

    widths = _column_widths(block, n_cols)
    for index, width in enumerate(widths):
        table.columns[index].width = Pt(width)
        for cell in table.columns[index].cells:
            cell.width = Pt(width)

    for r, row in enumerate(rows):
        tr_pr = table.rows[r]._tr.get_or_add_trPr()
        tr_pr.append(OxmlElement("w:cantSplit"))
        if r == 0:
            header = OxmlElement("w:tblHeader")
            header.set(qn("w:val"), "true")
            tr_pr.append(header)

        for c in range(n_cols):
            value = row[c] if c < len(row) else ""
            cell = table.cell(r, c)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            para = cell.paragraphs[0]
            para.paragraph_format.space_before = Pt(0)
            para.paragraph_format.space_after = Pt(0)
            para.paragraph_format.line_spacing = 1.0
            if r == 0:
                _shade_cell(cell, "EDEDED")
            if _is_number(value) and r > 0:
                para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            run = para.add_run(value)
            run.font.name = "Arial"
            run.font.size = Pt(max(min(block.size or body, 14), 7))
            run.bold = r == 0


def _column_widths(block: Block, n_cols: int) -> list[float]:
    if block.col_widths and len(block.col_widths) >= n_cols:
        widths = [max(w, 12.0) for w in block.col_widths[:n_cols]]
        total = sum(widths)
        # Keep tables inside the printable width of a typical A4 page.
        limit = 470.0
        if total > limit:
            widths = [w * limit / total for w in widths]
        return widths
    return [470.0 / n_cols] * n_cols


def _is_number(value: str) -> bool:
    value = value.strip()
    return bool(value) and bool(_NUMBER_RE.match(value)) and any(
        ch.isdigit() for ch in value
    )


def _shade_cell(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), fill)
    tc_pr.append(shading)
