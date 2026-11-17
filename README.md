# PDF Converter — POC

CLI that converts a native or scanned PDF into DOCX and/or XLSX. All
extractors write to a single intermediate document model (`pdfconv/model.py`);
exporters only read from that model.

## Quick start (NixOS)

```bash
nix-shell -p python312 python312Packages.pip
python3 -m venv .venv
./py -c "import pymupdf, docx, openpyxl, cv2; print('ok')"
./py -m pdfconv.cli input.pdf --out out --json out/model.json
```

`./py` is a tiny wrapper that puts the NixOS `libstdc++` and `zlib` on
`LD_LIBRARY_PATH` so the wheel-based dependencies load.

## Dependencies

```
pymupdf python-docx openpyxl opencv-python-headless reportlab
```

OCR uses the system `tesseract` binary (language data for `ind` and `eng`
are required). On NixOS, `nix-shell -p tesseract` provides both.

## Usage

```
pdfconv in.pdf --to docx,xlsx --out out/ [--dpi 300] [--lang ind+eng] [--json model.json]
```

- Per-page type detection: a page with an embedded text layer is extracted
  directly (fast, exact); otherwise it is rendered at 300 DPI, deskewed,
  binarised and OCR'd with Tesseract.
- Ruled tables are detected via OpenCV line morphology and OCR'd cell by
  cell (parallel, per-cell PSM 7).
- DOCX is semantic "flow" layout (headings, paragraphs, real tables), with
  low-confidence OCR runs highlighted in yellow.
- XLSX gets one sheet per detected table; if no table is found, text is
  dumped to a single sheet.

## Quality (5 synthetic fixtures, `./py eval.py`)

| Document               | CER    | WER    | s/page | Table cells |
|------------------------|-------:|-------:|-------:|------------:|
| doc1 native 2-col      | 0.00%  | 0.00%  | 0.44   | n/a         |
| doc2 native invoice    | 0.00%  | 0.00%  | 0.21   | 25/25       |
| doc3 clean scan (OCR)  | 0.00%  | 0.00%  | 1.04   | n/a         |
| doc4 phone photo (OCR) | 0.00%  | 0.00%  | 2.38   | 25/25       |
| doc5 mixed PDF         | 0.00%  | 0.00%  | 0.44   | n/a         |

## Known limits

- Only flow-mode DOCX, no text-box/exact-layout mode.
- Tables must be ruled (visible grid lines) on scanned pages; borderless
  tables fall back to text.
- Merged cells are not modelled.
- PPTX is out of scope for this POC.
- Only single-line cell text is recognised (PSM 7).
- Handwriting and complex nested layouts are not handled.
- Docling (IBM) was evaluated as a drop-in alternative (Step 0 gate):
  Docling CER ≈ 26% on doc3/doc4 vs 0% for this pipeline, and ~3.5× slower
  on doc3. The custom pipeline is retained.
