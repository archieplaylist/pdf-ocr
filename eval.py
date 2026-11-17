"""Measure conversion quality against the fixture ground truth.

Reports CER/WER per document, table cell accuracy and time per page, and
applies the pass thresholds from the plan.

Usage: ./py eval.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from docx import Document as DocxDocument
from openpyxl import load_workbook

from pdfconv.export_docx import write_docx
from pdfconv.export_xlsx import write_xlsx
from pdfconv.pipeline import convert

ROOT = Path(__file__).resolve().parent
FIX = ROOT / "fixtures"
GT = FIX / "gt"
OUT = ROOT / "out" / "eval"

# name -> (expected source per page, max CER, mean page seconds)
CASES = {
    "doc1_native_2col": ("native", 0.005, 5.0),
    "doc2_native_invoice": ("native", 0.005, 5.0),
    "doc3_scan_clean": ("ocr", 0.03, 5.0),
    "doc4_photo_invoice": ("ocr", 0.08, 5.0),
    "doc5_mixed": ("mixed", 0.03, 5.0),
}
CELL_CASES = {"doc2_native_invoice", "doc4_photo_invoice"}


def gt_key(name: str) -> str:
    """doc1_native_2col -> doc1 (ground-truth file naming)."""
    return name.split("_", 1)[0]


def levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def normalize(text: str) -> str:
    return " ".join(text.split())


def cer(ref: str, hyp: str) -> float:
    ref, hyp = normalize(ref), normalize(hyp)
    return levenshtein(ref, hyp) / max(len(ref), 1)


def wer(ref: str, hyp: str) -> float:
    r, h = normalize(ref).split(), normalize(hyp).split()
    return levenshtein(r, h) / max(len(r), 1)


def docx_text(path: Path) -> str:
    """Extract text in true document order (paragraphs and tables interleaved)."""
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    d = DocxDocument(str(path))
    parts: list[str] = []
    for child in d.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            text = Paragraph(child, d).text.strip()
            if text:
                parts.append(text)
        elif tag == "tbl":
            for row in Table(child, d).rows:
                parts.append(" | ".join(c.text.strip() for c in row.cells))
    return "\n".join(parts)


def xlsx_tables(path: Path) -> list[list[list[str]]]:
    wb = load_workbook(str(path), data_only=True)
    out = []
    for ws in wb.worksheets:
        if ws.title == "Text":
            continue
        rows = []
        for row in ws.iter_rows(values_only=True):
            if row is None or all(v is None for v in row):
                continue
            rows.append(["" if v is None else str(v).strip() for v in row])
        if rows:
            out.append(rows)
    return out


def _flat_cells(rows: list[list[str]]) -> list[str]:
    return [c for row in rows for c in row if c.strip()]


def compare_cells(expected: list[list[str]], actual: list[list[str]]) -> tuple[int, int, list[str]]:
    exp = _flat_cells(expected)
    act = _flat_cells(actual)
    if len(exp) != len(act):
        return 0, len(exp), [f"cell count {len(act)} != {len(exp)}"]
    bad = []
    for i, (e, a) in enumerate(zip(exp, act)):
        if normalize(e) != normalize(a):
            bad.append(f"#{i}: {a!r} != {e!r}")
    return len(exp) - len(bad), len(exp), bad


def main() -> int:
    if not FIX.exists():
        print("fixtures missing; run ./py tools/make_fixtures.py", file=sys.stderr)
        return 2
    OUT.mkdir(parents=True, exist_ok=True)

    print(f"{'document':26s} {'source':12s} {'CER':>7s} {'WER':>7s} "
          f"{'s/page':>7s} {'table':>12s} result")
    print("-" * 82)
    failures = 0

    for name, (expected_src, max_cer, max_secs) in CASES.items():
        pdf = FIX / f"{name}.pdf"
        t0 = time.perf_counter()
        doc = convert(pdf)
        elapsed = time.perf_counter() - t0
        per_page = elapsed / max(len(doc.pages), 1)

        docx_path = OUT / f"{name}.docx"
        xlsx_path = OUT / f"{name}.xlsx"
        write_docx(doc, docx_path)
        write_xlsx(doc, xlsx_path)

        got = docx_text(docx_path)
        ref = (GT / f"{gt_key(name)}.txt").read_text(encoding="utf-8")
        c, w = cer(ref, got), wer(ref, got)

        sources = [p.source for p in doc.pages]
        src_ok = (
            (expected_src == "mixed" and "native" in sources and "ocr" in sources)
            or (expected_src != "mixed" and all(s == expected_src for s in sources))
        )

        table_report = "n/a"
        table_ok = True
        cells_file = GT / f"{gt_key(name)}_cells.json"
        if name in CELL_CASES and cells_file.exists():
            expected_cells = json.loads(cells_file.read_text(encoding="utf-8"))
            tables = xlsx_tables(xlsx_path)
            if not tables:
                table_ok = False
                table_report = "no table"
            else:
                good, total, bad = compare_cells(expected_cells, tables[0])
                table_report = f"{good}/{total}"
                table_ok = good == total
                for line in bad[:5]:
                    print(f"    cell mismatch: {line}")

        ok = c <= max_cer and src_ok and per_page <= max_secs and table_ok
        failures += 0 if ok else 1
        print(
            f"{name:26s} {'+'.join(sources):12s} {c:7.2%} {w:7.2%} "
            f"{per_page:7.2f} {table_report:>12s} {'PASS' if ok else 'FAIL'}"
        )
        if not src_ok:
            print(f"    routing expected {expected_src}, got {sources}")

    print("-" * 82)
    print(f"{failures} failing case(s)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
