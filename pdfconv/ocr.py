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


def _osd_orientation(img: np.ndarray, dpi: int) -> int:
    """Return a rotation (0/90/180/270) to upright the page, or 0 if unsure."""
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        return 0
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
        fh.write(buf.tobytes())
        path = fh.name
    try:
        proc = subprocess.run(
            [TESSERACT, path, "stdout", "--psm", "0", "--dpi", str(dpi)],
            capture_output=True, text=True, timeout=60,
        )
    finally:
        os.unlink(path)
    rotation = 0
    confidence = 0.0
    for line in proc.stdout.splitlines():
        low = line.lower()
        if low.startswith("orientation in degrees"):
            try:
                rotation = int(line.split(":")[-1].strip())
            except ValueError:
                rotation = 0
        elif low.startswith("orientation confidence"):
            try:
                confidence = float(line.split(":")[-1].strip())
            except ValueError:
                confidence = 0.0
    if confidence < 5.0:
        return 0
    return rotation % 360


def _quality_metrics(img: np.ndarray) -> dict:
    h, w = img.shape
    contrast = float(img.std())
    # High-frequency energy as a noise proxy.
    lap = cv2.Laplacian(img, cv2.CV_64F)
    noise = float(np.abs(lap).mean())
    # Skew estimate via minAreaRect on ink.
    inv = 255 - img
    _, thr = cv2.threshold(inv, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    coords = cv2.findNonZero(thr)
    skew = 0.0
    if coords is not None:
        angle = cv2.minAreaRect(coords)[-1]
        if angle > 45:
            angle -= 90
        elif angle < -45:
            angle += 90
        skew = float(angle)
    return {"contrast": contrast, "noise": noise, "skew": skew}


def _deskew_projection(img: np.ndarray) -> tuple[np.ndarray, float]:
    """Fine deskew via horizontal projection profile search."""
    best_angle = 0.0
    best_score = -1.0
    h, w = img.shape

    def score_angle(angle):
        m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        rot = cv2.warpAffine(
            img, m, (w, h), flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_REPLICATE
        )
        proj = (rot < 128).sum(axis=1).astype(np.float64)
        return float(proj.var())

    # Coarse pass (1 degree).
    for deg in range(-15, 16):
        angle = float(deg)
        sc = score_angle(angle)
        if sc > best_score:
            best_score = sc
            best_angle = angle
    # Fine pass (0.1 degree) around best.
    for step in range(-10, 11):
        angle = best_angle + step / 10.0
        sc = score_angle(angle)
        if sc > best_score:
            best_score = sc
            best_angle = angle
    if abs(best_angle) < 0.25:
        return img, 0.0
    m = cv2.getRotationMatrix2D((w / 2, h / 2), best_angle, 1.0)
    out = cv2.warpAffine(
        img, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )
    return out, best_angle


def preprocess(img: np.ndarray, dpi: int) -> tuple[np.ndarray, float, dict]:
    """Return (enhanced image, deskew angle, metrics). Adaptive light/heavy."""
    metrics = _quality_metrics(img)
    heavy = metrics["contrast"] < 30 or metrics["noise"] > 12.0 or abs(metrics["skew"]) > 3.0

    # Orientation fix first.
    rotation = _osd_orientation(img, dpi)
    if rotation and rotation != 0:
        if rotation == 90:
            img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
        elif rotation == 180:
            img = cv2.rotate(img, cv2.ROTATE_180)
        elif rotation == 270:
            img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)

    # CLAHE (clip scaled to contrast).
    clip = 3.0 if heavy else 2.0
    clahe = cv2.createCLAHE(clipLimit=clip, tileGridSize=(8, 8))
    out = clahe.apply(img)

    if heavy:
        out = cv2.medianBlur(out, 3)
        # Mild sharpening.
        blur = cv2.GaussianBlur(out, (0, 0), 1.0)
        out = cv2.addWeighted(out, 1.4, blur, -0.4, 0)
    else:
        out = cv2.medianBlur(out, 3)

    # Fine deskew via minAreaRect on ink (guarded).
    out, angle = deskew(out)
    # Also apply same rotation to original? We work on enhanced only.
    metrics["heavy"] = heavy
    metrics["rotation"] = rotation
    metrics["angle"] = angle
    return out, angle, metrics


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


def _hough_grid(img: np.ndarray) -> tuple[list[float] | None, list[float] | None]:
    """Fallback grid detection via Hough lines for faint rules."""
    h, w = img.shape
    edges = cv2.Canny(img, 50, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=80, minLineLength=min(w, h) // 4, maxLineGap=10)
    if lines is None:
        return None, None
    horiz: list[float] = []
    vert: list[float] = []
    for x1, y1, x2, y2 in lines.reshape(-1, 4):
        if abs(y2 - y1) <= 2 and abs(x2 - x1) >= w // 6:
            horiz.append((y1 + y2) / 2.0)
        elif abs(x2 - x1) <= 2 and abs(y2 - y1) >= h // 6:
            vert.append((x1 + x2) / 2.0)
    if len(horiz) < 3 or len(vert) < 3:
        return None, None
    # Cluster nearby lines.
    def cluster(vals, tol):
        vals = sorted(vals)
        out = []
        for v in vals:
            if out and v - out[-1] < tol:
                out[-1] = (out[-1] + v) / 2
            else:
                out.append(v)
        return out
    rows = cluster(horiz, 6)
    cols = cluster(vert, 6)
    if len(rows) < 3 or len(cols) < 3:
        return None, None
    return rows, cols


def _cluster(vals, tol):
    vals = sorted(vals)
    out = []
    for v in vals:
        if out and v - out[-1] < tol:
            out[-1] = (out[-1] + v) / 2.0
        else:
            out.append(v)
    return out


def _region_table(img):
    """Region-first detection for faint ruled grids."""
    h, w = img.shape
    bin_inv = cv2.adaptiveThreshold(
        255 - img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, -5
    )
    _, otsu = cv2.threshold(255 - img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bin_inv = cv2.bitwise_or(bin_inv, otsu)

    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(w // 6, 180), 1))
    hor = cv2.morphologyEx(bin_inv, cv2.MORPH_OPEN, hk)
    hor = cv2.morphologyEx(hor, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (max(w // 5, 200), 3)))
    row_flags = (hor > 0).sum(axis=1) > w * 0.15

    rules = []
    run = []
    for i in range(h):
        if row_flags[i]:
            run.append(i)
        elif run:
            rules.append(sum(run) / len(run))
            run = []
    if run:
        rules.append(sum(run) / len(run))

    # Collect all candidate clusters, then prefer the one with a working vertical pass.
    candidates = []
    for start in range(len(rules)):
        cluster_rules = [rules[start]]
        for j in range(start + 1, len(rules)):
            if rules[j] - cluster_rules[-1] <= max(60, h * 0.12):
                cluster_rules.append(rules[j])
            else:
                break
        if len(cluster_rules) >= 3 and (cluster_rules[-1] - cluster_rules[0]) < h * 0.35:
            candidates.append(cluster_rules)
    if not candidates:
        return None

    # Try each candidate (most rules first); accept first with >=3 verticals.
    candidates.sort(key=len, reverse=True)
    for cand in candidates:
        y_top, y_bot = cand[0], cand[-1]
        band_h = y_bot - y_top
        if band_h < 20:
            continue
        band = img[int(y_top):int(y_bot) + 1, :]
        if band.size == 0:
            continue
        bin_band = cv2.adaptiveThreshold(
            255 - band, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, -5
        )
        _, otsu_band = cv2.threshold(255 - band, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        bin_band = cv2.bitwise_or(bin_band, otsu_band)
        vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(int(band_h) // 2, 20)))
        ver = cv2.morphologyEx(bin_band, cv2.MORPH_OPEN, vk)
        ver = cv2.morphologyEx(ver, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (3, max(int(band_h) // 2, 20))))
        col_flags = (ver > 0).sum(axis=0) > band_h * 0.5
        verts = [i for i, on in enumerate(col_flags) if on]
        cols = None
        if len(verts) >= 3:
            cols = _cluster(verts, 8)
        else:
            edges = cv2.Canny(band, 50, 150)
            hl = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=60,
                                minLineLength=int(band_h * 0.6), maxLineGap=8)
            if hl is not None:
                vs = []
                for seg in hl.reshape(-1, 4):
                    x1, y1, x2, y2 = [int(v) for v in seg]
                    if abs(x2 - x1) <= 2 and abs(y2 - y1) >= band_h * 0.6:
                        vs.append((x1 + x2) / 2.0)
                cols = _cluster(vs, 8)
        if cols is not None:
            cols = [c for c in cols if 50 <= c <= w - 50]
            if len(cols) >= 3 and (cols[-1] - cols[0]) >= w * 0.2:
                return cand, cols
    return None
def detect_grid(img):
    """Find ruled-table row/column line positions (pixels)."""
    h, w = img.shape
    rows = None
    cols = None

    region = _region_table(img)
    if region is not None:
        rows, cols = region

    if rows is None or cols is None:
        bin_inv = cv2.adaptiveThreshold(
            255 - img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, -5
        )
        _, otsu = cv2.threshold(255 - img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        bin_inv = cv2.bitwise_or(bin_inv, otsu)
        hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(40, w // 25), 1))
        vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(40, h // 40)))
        hor = cv2.morphologyEx(bin_inv, cv2.MORPH_OPEN, hk)
        ver = cv2.morphologyEx(bin_inv, cv2.MORPH_OPEN, vk)
        hor = cv2.morphologyEx(hor, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (max(40, w // 25), 3)))
        ver = cv2.morphologyEx(ver, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (3, max(40, h // 40))))
        r = _group_runs((hor > 0).sum(axis=1) > w * 0.15)
        c = _group_runs((ver > 0).sum(axis=0) > h * 0.05)
        if len(r) >= 3 and len(c) >= 3:
            rows, cols = r, c
        else:
            fb_r, fb_c = _hough_grid(img)
            if fb_r is not None:
                rows, cols = fb_r, fb_c

    if rows is None or cols is None:
        return None
    if not (3 <= len(rows) <= 30 and 3 <= len(cols) <= 20):
        return None
    if np.diff(rows).min() < 6 or np.diff(cols).min() < 6:
        return None
    if max(np.diff(rows)) > h * 0.4 or max(np.diff(cols)) > w * 0.7:
        return None
    box = (cols[0], rows[0], cols[-1], rows[-1])
    if (box[2] - box[0]) * (box[3] - box[1]) > 0.9 * w * h:
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
            [
                TESSERACT, path, "-", "-l", lang,
                "--oem", "1", "--psm", str(psm), "--dpi", str(dpi), "tsv",
                "-c", "preserve_interword_spaces=1",
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
    finally:
        os.unlink(path)
    if proc.returncode != 0:
        raise RuntimeError(f"tesseract failed: {proc.stderr.strip()[:400]}")

    words: list[dict] = []
    # Tesseract text can contain quotes; treat every field literally instead of
    # letting csv treat quote characters as quoting delimiters.
    reader = csv.DictReader(
        io.StringIO(proc.stdout), delimiter="\t", quoting=csv.QUOTE_NONE
    )
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

    # Tesseract's per-word boxes wobble by a pixel or two, which is enough to
    # make one line of a paragraph look like a larger heading. Estimate type
    # size once per Tesseract paragraph and reuse it for its lines.
    paragraphs: dict[tuple[int, int], float] = {}
    for (block, par, _line), ws in groups.items():
        heights = [d["h"] for d in ws]
        paragraphs[(block, par)] = float(np.median(heights))

    lines: list[Line] = []
    for key in sorted(groups):
        ws = sorted(groups[key], key=lambda d: d["x"])
        text = " ".join(d["text"] for d in ws)
        x0 = min(d["x"] for d in ws) + offset[0]
        y0 = min(d["y"] for d in ws) + offset[1]
        x1 = max(d["x"] + d["w"] for d in ws) + offset[0]
        y1 = max(d["y"] + d["h"] for d in ws) + offset[1]
        hpx = paragraphs[(key[0], key[1])]
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
            y0, y1 = int(rows[r]) + 5, int(rows[r + 1]) - 4
            x0, x1 = int(cols[c]) + 5, int(cols[c + 1]) - 4
            if y1 - y0 < 8 or x1 - x0 < 8:
                jobs.append(None)
                continue
            crop = img[y0:y1, x0:x1]
            if (crop < 128).mean() < 0.002:  # blank cell
                jobs.append(None)
                continue
            jobs.append(
                cv2.copyMakeBorder(crop, 18, 18, 18, 18, cv2.BORDER_CONSTANT, value=255)
            )

    def read_cell(job: np.ndarray | None) -> tuple[str, float]:
        if job is None:
            return "", 1.0
        best_text, best_conf = "", 0.0
        for psm in (6, 7, 13):
            ws = run_tesseract(job, psm=psm, lang=lang, dpi=dpi)
            if not ws:
                continue
            text = _clean_cell(" ".join(w["text"] for w in ws))
            conf = float(np.mean([w["conf"] for w in ws]))
            if conf > best_conf or (conf == best_conf and len(text) > len(best_text)):
                best_text, best_conf = text, conf
        if not best_text:
            return "", 0.0
        return best_text, best_conf

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
    widths = [round((cols[i + 1] - cols[i]) * scale, 2) for i in range(n_cols)]
    return Block(
        type="table",
        bbox=(box[0] * scale, box[1] * scale, box[2] * scale, box[3] * scale),
        rows=grid,
        conf=conf,
        col_widths=widths,
    )


def ocr_page(page, dpi: int = 300, lang: str = "ind+eng") -> list[Block]:
    img = render_gray(page, dpi)
    img, angle, _metrics = preprocess(img, dpi)
    scale = 72.0 / dpi

    table_block = None
    table_box = None
    grid = detect_grid(img)
    if grid is not None:
        rows, cols, box = grid
        candidate = _table_block(img, rows, cols, box, scale, lang, dpi)
        # Discard weak tables so the page falls back to plain text instead of
        # a grid of garbage cells.
        if candidate is not None and candidate.conf >= 0.7:
            nonempty = sum(1 for r in (candidate.rows or []) for c in r if c.strip())
            total = sum(len(r) for r in (candidate.rows or []))
            if total > 0 and nonempty / total >= 0.3:
                table_block = candidate
                table_box = box

    words = run_tesseract(img, psm=6, lang=lang, dpi=dpi)
    lines = _words_to_lines(words, scale)

    if table_box is not None:
        lines = [ln for ln in lines if not _center_inside(ln.bbox, table_box, scale)]

    groups = order_lines(lines, page.rect.width)
    ordered: list[Block] = []
    for column in groups:
        if not column:
            continue
        bounds = (min(ln.x0 for ln in column), max(ln.bbox[2] for ln in column))
        ordered.extend(
            merge_lines_to_blocks(
                column, page.rect.height, heading_ratio=1.12, column=bounds
            )
        )

    if table_block is not None:
        if len(groups) == 1:
            index = next(
                (
                    i
                    for i, block in enumerate(ordered)
                    if block.bbox[1] > table_block.bbox[3]
                ),
                len(ordered),
            )
            ordered.insert(index, table_block)
        else:
            ordered.append(table_block)
    return [b for b in ordered if b.text or b.rows]


def _center_inside(bbox, box_px, scale: float) -> bool:
    cx = (bbox[0] + bbox[2]) / 2 / scale
    cy = (bbox[1] + bbox[3]) / 2 / scale
    return box_px[0] <= cx <= box_px[2] and box_px[1] <= cy <= box_px[3]
