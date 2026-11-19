"""Pipeline: PDF -> intermediate document model."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

import pymupdf

from .detect import page_is_scan, page_source
from .extract import extract_native
from .model import Document, Page
from .ocr import ocr_page
from .ocr_engine import get_engine

PageCallback = Callable[[Page, float], None]


def _scan_is_editable(page: Page) -> bool:
    if not page.blocks:
        return False
    relevant = [b for b in page.blocks if (b.text or b.rows)]
    if not relevant:
        return False
    confs = [b.conf for b in relevant]
    if (sum(confs) / len(confs)) < 0.80:
        return False
    if any(b.conf < 0.60 for b in relevant):
        return False
    for b in relevant:
        if b.type == "table" and b.rows:
            if b.conf < 0.75:
                return False
            nonempty = sum(1 for r in b.rows for c in r if c.strip())
            total = sum(len(r) for r in b.rows)
            if total and nonempty / total < 0.3:
                return False
    return True


def convert(
    path: str | Path,
    dpi: int = 300,
    lang: str = "ind+eng",
    on_page: PageCallback | None = None,
    engine: str | None = None,
) -> Document:
    """Convert *path* into the intermediate model.

    ``on_page`` is called with each finished Page and its processing time in
    seconds. It is purely observational: exceptions from it are not swallowed,
    so callers that don't want to abort conversion should catch their own.
    """
    path = Path(path)
    pdf = pymupdf.open(str(path))
    if pdf.needs_pass:
        pdf.close()
        raise ValueError(f"{path.name}: password-protected PDF is not supported")

    pages: list[Page] = []
    try:
        for number, page in enumerate(pdf, start=1):
            started = time.perf_counter()
            scan = page_is_scan(page)
            # A scanned page's text layer was produced by some earlier OCR run
            # and is often worse than ours. Re-OCR the image instead of trusting
            # it, so flow layout still gets readable text.
            source = "ocr" if scan else page_source(page)
            blocks = (
                extract_native(page)
                if source == "native"
                else ocr_page(page, dpi, lang, engine=get_engine(engine))
            )
            result = Page(
                number=number,
                width=page.rect.width,
                height=page.rect.height,
                source=source,
                blocks=blocks,
                scan=scan,
            )
            # In auto layout, a scanned page may still be rebuilt as text when
            # OCR is reliable enough. Native pages are always text.
            if source == "ocr":
                result.ocr_editable = _scan_is_editable(result)
            pages.append(result)
            if on_page is not None:
                on_page(result, time.perf_counter() - started)
    finally:
        pdf.close()
    return Document(path=str(path), pages=pages)
