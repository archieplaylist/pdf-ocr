"""OCR path: render -> deskew -> engine -> blocks, plus ruled-table extraction."""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np
import pymupdf

from .layout import Line, merge_lines_to_blocks, order_lines
from .model import Block
from .ocr_engine import (
    OcrError,
    TesseractEngine,
    _ensure_tessdata,
    _tesseract_binary,
    get_engine,
)

# Kept for the health endpoint: the system binary path ("" when absent).
TESSERACT = _tesseract_binary()
_ensure_tessdata(TESSERACT)


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


def _osd_orientation(img: np.ndarray, dpi: int, engine=None) -> int:
    """Return a rotation (0/90/180/270) to upright the page, or 0 if unsure."""
    engine = engine or get_engine()
    try:
        return engine.orientation(img, dpi)
    except (OcrError, OSError):
        return 0


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


def preprocess(img: np.ndarray, dpi: int, engine=None) -> tuple[np.ndarray, float, dict]:
    """Return (enhanced image, deskew angle, metrics). Adaptive light/heavy."""
    metrics = _quality_metrics(img)
    heavy = metrics["contrast"] < 30 or metrics["noise"] > 12.0 or abs(metrics["skew"]) > 3.0

    # Orientation fix first.
    rotation = _osd_orientation(img, dpi, engine)
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
    """Back-compat helper: always the Tesseract engine."""
    return TesseractEngine().words(img, psm=psm, lang=lang, dpi=dpi)


def run_engine(
    img: np.ndarray, psm: int = 6, lang: str = "ind+eng", dpi: int = 300, engine=None
) -> list[dict]:
    return (engine or get_engine()).words(img, psm=psm, lang=lang, dpi=dpi)


_BODY_PT = 10.5
# Deliberately coarse: OCR sizes are inferred, not measured. Snapping to a few
# levels keeps equal-looking lines exactly equal (so they merge into one
# paragraph) and keeps headings clearly apart from body text.
_SIZE_LEVELS = (0.7, 0.8, 0.9, 1.0, 1.15, 1.3, 1.5, 1.7, 2.0, 2.3)


def _weighted_median(pairs: list[tuple[float, float]]) -> float:
    pairs = sorted(pairs)
    total = sum(weight for _, weight in pairs)
    acc = 0.0
    for value, weight in pairs:
        acc += weight
        if acc >= total / 2:
            return value
    return pairs[-1][0]


def _normalise_sizes(lines: list[Line]) -> None:
    """Assign consistent, page-relative sizes to OCR lines.

    OCR engines report glyph boxes, not type sizes: a line with descenders is
    taller than one without, so box height can swing by +-30% between lines set
    in the same font. Raw heights therefore produce random headings and stop
    paragraph merging. Use the page body size as reference and only promote or
    demote a line when both its height *and* its average character advance
    agree; otherwise treat it as body text.
    """
    stats = []
    for ln in lines:
        chars = len(ln.text.replace(" ", ""))
        width = ln.bbox[2] - ln.bbox[0]
        stats.append((ln, chars, ln.height, width / max(chars, 1)))
    if not stats:
        return
    sample = [s for s in stats if s[1] >= 10] or stats
    body_h = _weighted_median([(s[2], s[1]) for s in sample])
    body_w = _weighted_median([(s[3], s[1]) for s in sample])
    if body_h <= 0 or body_w <= 0:
        return
    for ln, _chars, height, char_w in stats:
        h_rel, w_rel = height / body_h, char_w / body_w
        if (h_rel >= 1.10 and w_rel >= 1.10) or (h_rel <= 0.85 and w_rel <= 0.85):
            rel = math.sqrt(max(h_rel, 0.01) * max(w_rel, 0.01))
        else:
            rel = 1.0
        rel = min(max(rel, 0.65), 2.5)
        level = min(_SIZE_LEVELS, key=lambda value: abs(math.log(rel / value)))
        ln.size = round(_BODY_PT * level, 1)


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
        group = ws[0].get("group", key[0] * 100 + key[1])
        lines.append(
            Line(
                bbox=(x0 * scale, y0 * scale, x1 * scale, y1 * scale),
                text=text,
                size=round(hpx * scale * 1.15, 1),
                conf=conf,
                group=group,
            )
        )
    _normalise_sizes(lines)
    return lines


_BORDER_JUNK = "|[]{}~^_`\\/ "


def _merge_row_runs(lines: list[Line]) -> list[Line]:
    """Join detection runs that sit on one visual row.

    Detection-based engines return one box per text run, so a label, its value
    and a right-aligned date on the same visual row arrive as separate boxes.
    Left alone they become separate paragraphs and each gets its own alignment.
    Join every run on the row, using a tab for field-sized gaps so the value
    keeps its original x position.
    """
    out: list[Line] = []
    for ln in lines:
        prev = out[-1] if out else None
        if prev is not None and _same_row(prev, ln):
            gap = ln.x0 - prev.bbox[2]
            separator = "\t" if gap > 0.8 * max(prev.height, ln.height) else " "
            prev.text = f"{prev.text.rstrip()}{separator}{ln.text.lstrip()}"
            if separator == "\t":
                prev.tab_stops.append(ln.x0)
            prev.bbox = (
                min(prev.bbox[0], ln.bbox[0]),
                min(prev.bbox[1], ln.bbox[1]),
                max(prev.bbox[2], ln.bbox[2]),
                max(prev.bbox[3], ln.bbox[3]),
            )
            prev.conf = min(prev.conf, ln.conf)
        else:
            out.append(ln)
    return out


def _same_row(a: Line, b: Line) -> bool:
    center_a = (a.bbox[1] + a.bbox[3]) / 2
    center_b = (b.bbox[1] + b.bbox[3]) / 2
    return abs(center_a - center_b) <= 0.35 * min(a.height, b.height)


def _clean_cell(text: str) -> str:
    """Drop artifacts from ruling lines that survive the cell inset."""
    text = " ".join(text.split())
    return text.strip(_BORDER_JUNK)


def _table_block(
    img: np.ndarray,
    rows: list[float],
    cols: list[float],
    box,
    scale: float,
    lang: str,
    dpi: int,
    engine=None,
) -> Block | None:
    engine = engine or get_engine()
    n_rows, n_cols = len(rows) - 1, len(cols) - 1

    if hasattr(engine, "lines"):
        # Detection-based engines read the whole table region in one pass and
        # the line boxes are assigned to cells afterwards. This keeps wrapped
        # cell text together and costs one model run instead of one per cell.
        results = _read_table_region(img, rows, cols, box, engine)
    else:
        results = _read_table_cells(img, rows, cols, n_rows, n_cols, lang, dpi)

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


def _read_table_cells(
    img: np.ndarray,
    rows: list[float],
    cols: list[float],
    n_rows: int,
    n_cols: int,
    lang: str,
    dpi: int,
) -> list[tuple[str, float]]:
    """Per-cell Tesseract pass with several page-segmentation modes."""
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
        return list(pool.map(read_cell, jobs))


def _read_table_region(
    img: np.ndarray, rows: list[float], cols: list[float], box, engine
) -> list[tuple[str, float]]:
    """One detection pass over the table, then line boxes -> cells."""
    n_rows, n_cols = len(rows) - 1, len(cols) - 1
    pad = 18
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    x0, y0 = max(0, x0), max(0, y0)
    x1 = min(img.shape[1] - 1, x1)
    y1 = min(img.shape[0] - 1, y1)
    crop = img[y0 : y1 + 1, x0 : x1 + 1]
    if crop.size == 0:
        return [("", 0.0)] * (n_rows * n_cols)
    crop = cv2.copyMakeBorder(crop, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=255)

    buckets: list[list[tuple[float, str, float]]] = [[] for _ in range(n_rows * n_cols)]
    for ln in engine.lines(crop):
        cx = ln["x"] + ln["w"] / 2.0 + x0 - pad
        cy = ln["y"] + ln["h"] / 2.0 + y0 - pad
        row = next((r for r in range(n_rows) if rows[r] <= cy <= rows[r + 1]), None)
        if row is None:
            continue
        col = next((c for c in range(n_cols) if cols[c] <= cx <= cols[c + 1]), None)
        if col is None:
            continue
        buckets[row * n_cols + col].append((cy, ln["text"], ln["conf"]))

    results: list[tuple[str, float]] = []
    for index, bucket in enumerate(buckets):
        if not bucket:
            # Detection can miss a lone digit next to a ruling line. Retry
            # just that cell with the detector zoomed in on it.
            row, col = divmod(index, n_cols)
            cy0, cy1 = int(rows[row]) + 5, int(rows[row + 1]) - 4
            cx0, cx1 = int(cols[col]) + 5, int(cols[col + 1]) - 4
            cell = img[max(0, cy0) : max(0, cy1), max(0, cx0) : max(0, cx1)]
            if cell.size and (cell < 128).mean() >= 0.002:
                padded = cv2.copyMakeBorder(
                    cell, 18, 18, 18, 18, cv2.BORDER_CONSTANT, value=255
                )
                found = engine.lines(padded)
                if found:
                    results.append(
                        (
                            _clean_cell(_join_wrapped(ln["text"] for ln in found)),
                            float(np.mean([ln["conf"] for ln in found])),
                        )
                    )
                    continue
            results.append(("", 1.0))
            continue
        parts = [text for _, text, _ in sorted(bucket)]
        conf = float(np.mean([c for _, _, c in bucket]))
        results.append((_clean_cell(_join_wrapped(parts)), conf))
    return results


def _join_wrapped(parts) -> str:
    """Join wrapped cell lines.

    A trailing hyphen is kept: in tables it is usually part of a code
    (``SP-59607-T20-`` + ``8-0``), not a typographic hyphenation.
    """
    out = ""
    for part in parts:
        part = " ".join(part.split())
        if not part:
            continue
        if out and out.endswith("-"):
            out = out + part
        else:
            out = f"{out} {part}".strip()
    return out


def _refine_low_conf(
    img: np.ndarray, blocks: list[Block], scale: float, lang: str, dpi: int,
    engine=None,
) -> list[Block]:
    engine = engine or get_engine()
    out: list[Block] = []
    for block in blocks:
        if (
            block.type == "table"
            or not block.text
            or block.conf >= 0.85
            or "\t" in block.text
        ):
            out.append(block)
            continue
        x0, y0, x1, y1 = block.bbox
        px = dpi / 72.0
        pad = 12
        ix0 = max(0, int(x0 * px) - pad)
        iy0 = max(0, int(y0 * px) - pad)
        ix1 = min(img.shape[1], int(x1 * px) + pad)
        iy1 = min(img.shape[0], int(y1 * px) + pad)
        crop = img[iy0:iy1, ix0:ix1]
        if crop.size == 0:
            out.append(block)
            continue
        ci = (crop < 128).sum(axis=0)
        ri = (crop < 128).sum(axis=1)
        xs = [i for i, v in enumerate(ci) if v > 3]
        ys = [i for i, v in enumerate(ri) if v > 3]
        if not xs or not ys:
            out.append(block)
            continue
        crop = crop[min(ys) : max(ys) + 1, min(xs) : max(xs) + 1]
        crop = cv2.copyMakeBorder(
            crop, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255
        )

        best_text, best_conf = block.text, block.conf
        candidates: list[tuple[str, float]] = []
        if hasattr(engine, "lines"):
            lines = engine.lines(crop)
            if lines:
                text = " ".join(ln["text"] for ln in lines)
                candidates.append(
                    (" ".join(text.split()), float(np.mean([ln["conf"] for ln in lines])))
                )
        else:
            for psm in (4, 6, 7, 8, 11, 12, 13):
                ws = run_engine(crop, psm=psm, lang=lang, dpi=dpi, engine=engine)
                if not ws:
                    continue
                text = " ".join(w["text"] for w in ws)
                candidates.append(
                    (" ".join(text.split()), float(np.mean([w["conf"] for w in ws])))
                )
        for text, conf in candidates:
            if not text:
                continue
            if conf > best_conf + 0.05 and len(text) >= max(3, len(block.text) * 0.5):
                best_text, best_conf = text, conf
        if best_text != block.text:
            block.text = best_text
            block.conf = best_conf
        # Tidy residual junk before a known label.
        for label in ("Website", "Email", "Nomor", "Lampiran", "Perihal"):
            idx = block.text.find(label)
            if 0 < idx < 40:
                block.text = block.text[idx:]
                break
        out.append(block)
    return out


def ocr_page(
    page, dpi: int = 300, lang: str = "ind+eng", engine=None
) -> list[Block]:
    engine = engine or get_engine()
    img = render_gray(page, dpi)
    img, angle, _metrics = preprocess(img, dpi, engine)
    scale = 72.0 / dpi

    table_block = None
    table_box = None
    grid = detect_grid(img)
    if grid is not None:
        rows, cols, box = grid
        candidate = _table_block(img, rows, cols, box, scale, lang, dpi, engine)
        # Discard weak tables so the page falls back to plain text instead of
        # a grid of garbage cells.
        if candidate is not None and candidate.conf >= 0.7:
            nonempty = sum(1 for r in (candidate.rows or []) for c in r if c.strip())
            total = sum(len(r) for r in (candidate.rows or []))
            if total > 0 and nonempty / total >= 0.3:
                table_block = candidate
                table_box = box

    words = run_engine(img, psm=6, lang=lang, dpi=dpi, engine=engine)
    lines = _words_to_lines(words, scale)

    if table_box is not None:
        lines = [ln for ln in lines if not _center_inside(ln.bbox, table_box, scale)]

    groups = order_lines(lines, page.rect.width)
    ordered: list[Block] = []
    for column in groups:
        if not column:
            continue
        column = _merge_row_runs(column)
        bounds = (min(ln.x0 for ln in column), max(ln.bbox[2] for ln in column))
        ordered.extend(
            merge_lines_to_blocks(
                column,
                page.rect.height,
                heading_ratio=1.12,
                column=bounds,
                break_on_short=True,
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
    result = [b for b in ordered if b.text or b.rows]
    # Second pass: re-OCR low-confidence text blocks on a trimmed crop. This
    # removes leading junk and recovers words that the full-page pass garbled.
    result = _refine_low_conf(img, result, scale, lang, dpi, engine)
    return result


def _center_inside(bbox, box_px, scale: float) -> bool:
    cx = (bbox[0] + bbox[2]) / 2 / scale
    cy = (bbox[1] + bbox[3]) / 2 / scale
    return box_px[0] <= cx <= box_px[2] and box_px[1] <= cy <= box_px[3]
