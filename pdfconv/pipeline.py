"""Pipeline: PDF -> intermediate document model."""

from __future__ import annotations

from pathlib import Path

import pymupdf

from .detect import page_source
from .extract import extract_native
from .model import Document, Page
from .ocr import ocr_page


def convert(path: str | Path, dpi: int = 300, lang: str = "ind+eng") -> Document:
    path = Path(path)
    pdf = pymupdf.open(str(path))
    if pdf.needs_pass:
        pdf.close()
        raise ValueError(f"{path.name}: password-protected PDF is not supported")

    pages: list[Page] = []
    for number, page in enumerate(pdf, start=1):
        source = page_source(page)
        blocks = extract_native(page) if source == "native" else ocr_page(page, dpi, lang)
        pages.append(
            Page(
                number=number,
                width=page.rect.width,
                height=page.rect.height,
                source=source,
                blocks=blocks,
            )
        )
    pdf.close()
    return Document(path=str(path), pages=pages)
