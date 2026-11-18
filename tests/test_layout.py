"""Layout and detection unit tests (no OCR process needed)."""

from __future__ import annotations

import pymupdf
import pytest

from pdfconv.detect import page_is_scan
from pdfconv.layout import Line, _align, merge_lines_to_blocks, order_lines


def line(x0: float, y0: float, x1: float, y1: float, text: str) -> Line:
    return Line(bbox=(x0, y0, x1, y1), text=text, size=y1 - y0)


def test_rows_order_labels_before_values():
    # Same visual row, but y differs by fractions of a point. A plain y-sort
    # puts the right-hand date before the label on the left.
    lines = [
        line(406.1, 97.9, 520.0, 109.0, "Surabaya, 31 Oktober 2026"),
        line(154.1, 98.0, 400.0, 109.0, ": 045/SIK/PT.PERTAMA/X/26"),
        line(80.9, 98.3, 120.0, 109.0, "Nomor"),
    ]
    ordered = [ln.text for column in order_lines(lines, 595) for ln in column]
    assert ordered == [
        "Nomor",
        ": 045/SIK/PT.PERTAMA/X/26",
        "Surabaya, 31 Oktober 2026",
    ]


def test_alignment_checks_margins_before_centre():
    column = (80.0, 520.0)
    # Starts on the left margin even though it is wide enough to look centred.
    assert _align((80.0, 200.0, 410.0, 210.0), column, 10.0) == "left"
    assert _align((430.0, 200.0, 520.0, 210.0), column, 10.0) == "right"
    assert _align((240.0, 200.0, 360.0, 210.0), column, 10.0) == "center"


def test_merged_block_keeps_alignment_and_style():
    lines = [
        line(80.0, 100.0, 380.0, 112.0, "Ringkasan"),
        line(80.0, 115.0, 380.0, 127.0, "isi paragraf"),
    ]
    blocks = merge_lines_to_blocks(lines, 842, column=(80.0, 520.0))
    assert len(blocks) == 1
    assert blocks[0].align == "left"
    assert blocks[0].bbox[0] == pytest.approx(80.0)


def test_scan_with_text_layer_is_detected(tmp_path):
    png = tmp_path / "page.png"
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 595, 842), 0)
    pix.clear_with(200)
    pix.save(png)

    path = tmp_path / "scan.pdf"
    pdf = pymupdf.open()
    page = pdf.new_page(width=595, height=842)
    page.insert_image(page.rect, filename=str(png))
    page.insert_text((72, 100), "embedded ocr layer " * 10)
    pdf.save(path)
    pdf.close()

    with pymupdf.open(path) as pdf:
        assert page_is_scan(pdf[0]) is True
        # A regular digital page is not a scan.
        digital = pdf.new_page(width=595, height=842)
        digital.insert_text((72, 100), "real text " * 20)
        assert page_is_scan(digital) is False
