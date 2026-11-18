"""pdfconv CLI: convert native or scanned PDFs to DOCX / XLSX."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .export_docx import write_docx
from .export_xlsx import write_xlsx
from .pipeline import convert

EXPORTERS = {"docx": write_docx, "xlsx": write_xlsx}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pdfconv", description=__doc__)
    ap.add_argument("input", type=Path, help="input PDF")
    ap.add_argument("--to", default="docx,xlsx", help="comma-separated: docx,xlsx")
    ap.add_argument("--out", type=Path, default=Path("out"), help="output directory")
    ap.add_argument("--dpi", type=int, default=300, help="OCR render DPI (default 300)")
    ap.add_argument("--lang", default="ind+eng", help="tesseract languages")
    ap.add_argument(
        "--layout",
        default="auto",
        choices=("auto", "flow", "facsimile"),
        help="auto keeps scanned pages as images; flow rebuilds text; "
        "facsimile uses page images everywhere",
    )
    ap.add_argument("--json", type=Path, help="also dump the document model")
    args = ap.parse_args(argv)

    formats = [f.strip().lower() for f in args.to.split(",") if f.strip()]
    unknown = [f for f in formats if f not in EXPORTERS]
    if unknown:
        ap.error(f"unsupported output format(s): {', '.join(unknown)}")

    if not args.input.exists():
        ap.error(f"no such file: {args.input}")

    started = time.perf_counter()
    try:
        doc = convert(args.input, dpi=args.dpi, lang=args.lang)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    elapsed = time.perf_counter() - started

    for page in doc.pages:
        tables = sum(1 for b in page.blocks if b.type == "table")
        print(
            f"page {page.number}: {page.source:6s} "
            f"{len(page.blocks):2d} blocks, {tables} table(s)"
        )
    print(
        f"{len(doc.pages)} page(s) in {elapsed:.2f}s "
        f"({elapsed / max(len(doc.pages), 1):.2f}s/page), "
        f"mean conf {doc.mean_conf:.2f}"
    )

    stem = args.input.stem
    args.out.mkdir(parents=True, exist_ok=True)
    for fmt in formats:
        target = args.out / f"{stem}.{fmt}"
        if fmt == "docx":
            write_docx(doc, target, layout=args.layout)
        else:
            EXPORTERS[fmt](doc, target)
        print(f"wrote {target}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(doc.to_json(), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
