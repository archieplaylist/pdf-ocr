"""Per-page native/scan detection."""

from __future__ import annotations

MIN_CHARS = 20


def page_source(page) -> str:
    """Return 'native' when the page has a usable embedded text layer."""
    text = page.get_text("text").strip()
    return "native" if len(text) >= MIN_CHARS else "ocr"
