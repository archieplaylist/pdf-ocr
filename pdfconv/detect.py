"""Per-page native/scan detection."""

from __future__ import annotations

MIN_CHARS = 20


def page_source(page) -> str:
    """Return 'native' when the page has a usable embedded text layer."""
    text = page.get_text("text").strip()
    return "native" if len(text) >= MIN_CHARS else "ocr"


def page_is_scan(page) -> bool:
    """True for a scanned page that also carries an OCR text layer.

    Such a page is visually one large image; the text layer is only a search
    aid and is often low quality. ``auto`` layout keeps these pages as images.
    """
    if len(page.get_text("text").strip()) < MIN_CHARS:
        return False
    area = page.rect.width * page.rect.height
    if area <= 0:
        return False
    try:
        blocks = page.get_text("dict").get("blocks", [])
    except Exception:  # noqa: BLE001 - malformed PDF, treat as regular page
        return False
    for block in blocks:
        if block.get("type") != 1:
            continue
        bbox = block.get("bbox")
        if not bbox:
            continue
        width = max(0.0, bbox[2] - bbox[0])
        height = max(0.0, bbox[3] - bbox[1])
        if width * height >= 0.9 * area:
            return True
    return False
