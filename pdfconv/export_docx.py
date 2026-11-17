"""DOCX exporter: semantic flow layout (headings, paragraphs, real tables)."""

from __future__ import annotations

from pathlib import Path

from docx import Document as DocxDocument
from docx.enum.text import WD_COLOR_INDEX

from .model import Document

LOW_CONF = 0.70


def write_docx(doc: Document, out: Path) -> None:
    dx = DocxDocument()
    title_used = False

    for page in doc.pages:
        if page.number > 1:
            dx.add_page_break()
        for block in page.blocks:
            if block.type == "table" and block.rows:
                _add_table(dx, block.rows)
            elif block.text:
                _add_text(dx, block, allow_title=not title_used)
                if block.type == "title":
                    title_used = True

    out.parent.mkdir(parents=True, exist_ok=True)
    dx.save(str(out))


def _add_text(dx, block, allow_title: bool) -> None:
    if block.type == "title" and allow_title:
        para = dx.add_heading(block.text, level=0)
    elif block.type == "heading":
        para = dx.add_heading(block.text, level=1)
    elif block.type == "title":
        para = dx.add_paragraph()
        run = para.add_run(block.text)
        run.bold = True
    else:
        para = dx.add_paragraph(block.text)
        if block.conf < LOW_CONF:
            for run in para.runs:
                run.font.highlight_color = WD_COLOR_INDEX.YELLOW
        return
    if block.conf < LOW_CONF:
        for run in para.runs:
            run.font.highlight_color = WD_COLOR_INDEX.YELLOW


def _add_table(dx, rows: list[list[str]]) -> None:
    n_cols = max(len(r) for r in rows)
    table = dx.add_table(rows=len(rows), cols=n_cols)
    table.style = "Table Grid"
    for r, row in enumerate(rows):
        for c in range(n_cols):
            value = row[c] if c < len(row) else ""
            cell = table.cell(r, c)
            cell.text = value
            if r == 0:
                for run in cell.paragraphs[0].runs:
                    run.bold = True
