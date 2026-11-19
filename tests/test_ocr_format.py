"""OCR text sizing and same-row assembly (no OCR process needed)."""

from __future__ import annotations

from pdfconv.layout import Line, merge_lines_to_blocks
from pdfconv.ocr import _merge_row_runs, _normalise_sizes


def line(x0: float, y0: float, x1: float, y1: float, text: str) -> Line:
    return Line(bbox=(x0, y0, x1, y1), text=text)


def test_ocr_sizes_are_consistent_and_page_relative():
    # Descenders make a body line's box taller than one without; both must
    # still come out at the same size, or paragraphs stop merging.
    body_tall = line(80, 100, 400, 114, "Setiap pengiriman wajib diperiksa")
    body_short = line(80, 116, 150, 126, "Nomor")
    heading = line(180, 40, 420, 60, "SURAT IZIN KERJA")
    _normalise_sizes([heading, body_tall, body_short])

    assert body_tall.size == body_short.size == 10.5
    assert heading.size > body_tall.size


def test_same_row_runs_merge_and_keep_their_x_positions():
    merged = _merge_row_runs(
        [
            line(80, 100, 140, 113, "Nomor"),
            line(250, 100, 330, 113, ": 045"),
            line(460, 99, 540, 112, "Surabaya, 31 Oktober 2026"),
        ]
    )

    assert len(merged) == 1
    assert merged[0].text == "Nomor\t: 045\tSurabaya, 31 Oktober 2026"
    assert merged[0].tab_stops == [250, 460]


def test_same_row_runs_on_different_baselines_stay_separate():
    merged = _merge_row_runs(
        [
            line(80, 100, 200, 113, "Pertama"),
            line(80, 120, 300, 133, "Telp. : 0811"),
        ]
    )

    assert [ln.text for ln in merged] == ["Pertama", "Telp. : 0811"]


def test_tabbed_rows_do_not_join_into_one_paragraph():
    first = line(80, 100, 330, 113, "Nomor\t: 045")
    first.tab_stops = [250.0]
    second = line(80, 115, 330, 128, "Lampiran\t: 1 berkas")
    second.tab_stops = [250.0]

    blocks = merge_lines_to_blocks([first, second], 842, column=(80.0, 540.0))

    assert len(blocks) == 2


def test_short_ocr_line_starts_a_new_paragraph():
    short = line(80, 100, 300, 113, "Kepada Yth :")
    following = line(80, 115, 400, 128, "Sr. Augusta")

    split = merge_lines_to_blocks(
        [short, following], 842, column=(80.0, 540.0), break_on_short=True
    )
    # Native pages keep the old greedy behaviour.
    joined = merge_lines_to_blocks([short, following], 842, column=(80.0, 540.0))

    assert len(split) == 2
    assert len(joined) == 1
