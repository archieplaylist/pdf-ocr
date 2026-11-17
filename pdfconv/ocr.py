"""OCR path: render -> deskew -> tesseract -> blocks, plus ruled-table extraction."""

from __future__ import annotations

import csv
import glob
import io
import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import pymupdf

from .layout import Line, merge_lines_to_blocks, order_lines
from .model import Block


def _find_tesseract() -> str:
    found = shutil.which("tesseract")
    if found:
        return found
    for cand in sorted(glob.glob("/nix/store/*-tesseract-*/bin/tesseract")):
        if os.access(cand, os.X_OK):
            return cand
    raise RuntimeError(
        "tesseract not found; run this script inside a shell that provides it "
        "(nix-shell -p tesseract)"
    )


TESSERACT = _find_tesseract()


def _ensure_tessdata() -> None:
    if os.environ.get("TESSDATA_PREFIX"):
        return
    guess = Path(TESSERACT).resolve().parent.parent / "share" / "tessdata"
    if (guess / "eng.traineddata").exists():
        os.environ["TESSDATA_PREFIX"] = str(guess)


_ensure_tessdata()


def render_gray(page, dpi: int) -> np.ndarray:
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
    buf = np.frombuffer(pix.samples, dtype=np.uint8)
    return buf.reshape(pix.height, pix.stride)[:, : pix.width].copy()


def deskew(img: np.ndarray) -> tuple[np.ndarray, float]:
    inv = 255 - img
    _, thr = cv2.threshold(inv, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    coords = cv2.findNonZero(thr)
    if coords is None:
        return img, 0.0
    angle = cv2.minAreaRect(coords)[-1]
    if angle > 45:
        angle -= 90
    elif angle < -45:
        angle += 90
    if abs(angle) < 0.25 or abs(angle) > 15:
        return img, 0.0
    h, w = img.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    out = cv2.warpAffine(
        img, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )
    return out, angle


def _group_runs(flags: np.ndarray) -> list[float]:
    out: list[float] = []
    run: list[int] = []
    for i, on in enumerate(flags):
        if on:
            run.append(i)
        elif run:
            out.append(sum(run) / len(run))
            run = []
    if run:
        out.append(sum(run) / len(run))
    return out


def detect_grid(img: np.ndarray) -> tuple[list[float], list[float], tuple] | None:
    """Find ruled-table row/column line positions (pixels)."""
    _, bin_inv = cv2.threshold(255 - img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    h, w = img.shape
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(40, w // 25), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(40, h // 40)))
    hor = cv2.morphologyEx(bin_inv, cv2.MORPH_OPEN, hk)
    ver = cv2.morphologyEx(bin_inv, cv2.MORPH_OPEN, vk)

    rows = _group_runs((hor > 0).sum(axis=1) > w * 0.25)
    cols = _group_runs((ver > 0).sum(axis=0) > h * 0.08)

    if not (3 <= len(rows) <= 20 and 3 <= len(cols) <= 14):
        return None
    if np.diff(rows).min() < 8 or np.diff(cols).min() < 8:
        return None
    if max(np.diff(rows)) > h * 0.3 or max(np.diff(cols)) > w * 0.5:
        return None
    box = (cols[0], rows[0], cols[-1], rows[-1])
    if (box[2] - box[0]) * (box[3] - box[1]) > 0.85 * w * h:
        return None
    return rows, cols, box


def run_tesseract(img: np.ndarray, psm: int, lang: str, dpi: int) -> list[dict]:
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        return []
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
        fh.write(buf.tobytes())
        path = fh.name
    try:
        proc = subprocess.run(
            [TESSERACT, path, "-", "-l", lang, "--psm", str(psm), "--dpi", str(dpi), "tsv"],
            capture_output=True,
            text=True,
            timeout=180,
        )
    finally:
        os.unlink(path)
    if proc.returncode != 0:
        raise RuntimeError(f"tesseract failed: {proc.stderr.strip()[:400]}")

    words: list[dict] = []
    reader = csv.DictReader(io.StringIO(proc.stdout), delimiter="\t")
    for row in reader:
        if row.get("level") != "5":
            continue
        text = (row.get("text") or "").strip()
        if not text:
            continue
        words.append(
            {
                "block": int(row["block_num"]),
                "par": int(row["par_num"]),
                "line": int(row["line_num"]),
                "x": float(row["left"]),
                "y": float(row["top"]),
                "w": float(row["width"]),
                "h": float(row["height"]),
                "conf": max(float(row["conf"]), 0.0) / 100.0,
                "text": text,
            }
        )
    return words


def _words_to_lines(words: list[dict], scale: float, offset=(0.0, 0.0)) -> list[Line]:
    groups: dict[tuple[int, int, int], list[dict]] = {}
    for w in words:
        groups.setdefault((w["block"], w["par"], w["line"]), []).append(w)
    lines: list[Line] = []
    for key in sorted(groups):
        ws = sorted(groups[key], key=lambda d: d["x"])
        text = " ".join(d["text"] for d in ws)
        x0 = min(d["x"] for d in ws) + offset[0]
        y0 = min(d["y"] for d in ws) + offset[1]
        x1 = max(d["x"] + d["w"] for d in ws) + offset[0]
        y1 = max(d["y"] + d["h"] for d in ws) + offset[1]
        hpx = max(d["h"] for d in ws)
        conf = float(np.mean([d["conf"] for d in ws]))
        lines.append(
            Line(
                bbox=(x0 * scale, y0 * scale, x1 * scale, y1 * scale),
                text=text,
                size=round(hpx * scale * 1.15, 1),
                conf=conf,
                group=key[0] * 100 + key[1],
            )
        )
    return lines


_BORDER_JUNK = "|[]{}~^_`\\/ "


def _clean_cell(text: str) -> str:
    """Drop artifacts from ruling lines that survive the cell inset."""
    text = " ".join(text.split())
    return text.strip(_BORDER_JUNK)


def _table_block(
    img: np.ndarray, rows: list[float], cols: list[float], box, scale: float, lang: str, dpi: int
) -> Block | None:
    n_rows, n_cols = len(rows) - 1, len(cols) - 1

    jobs: list[np.ndarray | None] = []
    for r in range(n_rows):
        for c in range(n_cols):
            y0, y1 = int(rows[r]) + 6, int(rows[r + 1]) - 4
            x0, x1 = int(cols[c]) + 6, int(cols[c + 1]) - 4
            if y1 - y0 < 6 or x1 - x0 < 6:
                jobs.append(None)
                continue
            crop = img[y0:y1, x0:x1]
            if (crop < 128).mean() < 0.004:  # blank cell
                jobs.append(None)
                continue
            jobs.append(
                cv2.copyMakeBorder(crop, 14, 14, 14, 14, cv2.BORDER_CONSTANT, value=255)
            )

    def read_cell(job: np.ndarray | None) -> tuple[str, float]:
        if job is None:
            return "", 1.0
        ws = run_tesseract(job, psm=7, lang=lang, dpi=dpi)
        if not ws:
            return "", 0.0
        text = _clean_cell(" ".join(w["text"] for w in ws))
        return text, float(np.mean([w["conf"] for w in ws]))

    # One tesseract process per cell is the accurate path (a whole-region pass
    # gets confused by the ruling lines), so pay for it in parallel.
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(read_cell, jobs))

    grid: list[list[str]] = []
    confs: list[float] = []
    for r in range(n_rows):
        line: list[str] = []
        for c in range(n_cols):
            text, conf = results[r * n_cols + c]
            line.append(text)
            confs.append(conf)
        grid.append(line)

    grid = [r for r in grid if any(c for c in r)]
    if len(grid) < 2 or max((len(r) for r in grid), default=0) < 2:
        return None
    if not any(any(c for c in r) for r in grid):
        return None
    conf = float(np.mean(confs)) if confs else 0.5
    return Block(
        type="table",
        bbox=(box[0] * scale, box[1] * scale, box[2] * scale, box[3] * scale),
        rows=grid,
        conf=conf,
    )


def ocr_page(page, dpi: int = 300, lang: str = "ind+eng") -> list[Block]:
    img = render_gray(page, dpi)
    img, angle = deskew(img)
    scale = 72.0 / dpi

    table_block = None
    table_box = None
    grid = detect_grid(img)
    if grid is not None:
        rows, cols, box = grid
        table_block = _table_block(img, rows, cols, box, scale, lang, dpi)
        if table_block is not None:
            table_box = box

    words = run_tesseract(img, psm=6, lang=lang, dpi=dpi)
    lines = _words_to_lines(words, scale)

    if table_box is not None:
        lines = [ln for ln in lines if not _center_inside(ln.bbox, table_box, scale)]

    groups = order_lines(lines, page.rect.width)
    ordered: list[Block] = []
    for column in groups:
        ordered.extend(merge_lines_to_blocks(column, page.rect.height, heading_ratio=1.06))

    if table_block is not None:
        if len(groups) == 1:
            ordered.append(table_block)
            ordered.sort(key=lambda b: b.bbox[1])
        else:
            ordered.append(table_block)
    return [b for b in ordered if b.text or b.rows]


def _center_inside(bbox, box_px, scale: float) -> bool:
    cx = (bbox[0] + bbox[2]) / 2 / scale
    cy = (bbox[1] + bbox[3]) / 2 / scale
    return box_px[0] <= cx <= box_px[2] and box_px[1] <= cy <= box_px[3]
