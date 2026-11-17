"""XLSX exporter: one sheet per detected table, text fallback otherwise."""

from __future__ import annotations

import re
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

from .model import Document

_NUM = re.compile(r"^-?\d+(\.\d+)?$")


def _cell_value(raw: str):
    """Keep numeric cells numeric, without mangling IDs or leading zeros."""
    text = raw.strip()
    if _NUM.match(text):
        as_int = str(int(text)) if "." not in text else str(float(text))
        if as_int == text:
            return int(text)
        return float(text)
    return text


def write_xlsx(doc: Document, out: Path) -> None:
    wb = Workbook()
    wb.remove(wb.active)
    tables = 0

    for page in doc.pages:
        for block in page.blocks:
            if block.type != "table" or not block.rows:
                continue
            tables += 1
            ws = wb.create_sheet(f"P{page.number}_T{tables}")
            for r, row in enumerate(block.rows, start=1):
                for c, value in enumerate(row, start=1):
                    cell = ws.cell(row=r, column=c, value=_cell_value(value))
                    if r == 1 and cell.value:
                        cell.font = Font(bold=True)
            for c in range(1, len(block.rows[0]) + 1):
                ws.column_dimensions[ws.cell(row=1, column=c).column_letter].width = 22

    if tables == 0:
        ws = wb.create_sheet("Text")
        r = 1
        for page in doc.pages:
            ws.cell(row=r, column=1, value=f"--- halaman {page.number} ---").font = Font(bold=True)
            r += 1
            for block in page.blocks:
                if block.text:
                    ws.cell(row=r, column=1, value=block.text)
                    r += 1

    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(out))
