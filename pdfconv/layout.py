"""Shared layout helpers: column splitting, line->paragraph merging."""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass

from .model import Block


@dataclass
class Line:
    bbox: tuple[float, float, float, float]
    text: str
    size: float = 0.0
    bold: bool = False
    italic: bool = False
    font: str = ""
    conf: float = 1.0
    group: int | None = None

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def y1(self) -> float:
        return self.bbox[3]

    @property
    def height(self) -> float:
        return self.bbox[3] - self.bbox[1]


def body_size(lines: list[Line]) -> float:
    """Most common font size, weighted by character count."""
    weights: Counter[float] = Counter()
    for ln in lines:
        weights[round(ln.size, 1)] += max(len(ln.text), 1)
    return weights.most_common(1)[0][0] if weights else 10.0


def order_lines(lines: list[Line], page_width: float) -> list[list[Line]]:
    """Split lines into columns using the widest vertical whitespace gutter.

    Returns groups already in reading order: spanning blocks first, then the
    left column, then the right column. Single-column pages return one group.
    """
    lines = [ln for ln in lines if ln.text.strip()]
    if not lines:
        return []
    if len(lines) < 6:
        return [_order_rows(lines)]

    lo = int(min(ln.x0 for ln in lines))
    hi = int(max(ln.bbox[2] for ln in lines))
    if hi - lo < 40:
        return [_order_rows(lines)]

    coverage = bytearray(hi - lo + 1)
    for ln in lines:
        a = max(0, int(ln.x0) - lo)
        b = min(len(coverage) - 1, int(ln.bbox[2]) - lo)
        for x in range(a, b + 1):
            coverage[x] = 1

    best = (0, 0, 0)
    run_start: int | None = None
    for i, filled in enumerate(coverage):
        if not filled and run_start is None:
            run_start = i
        elif filled and run_start is not None:
            if i - run_start > best[0]:
                best = (i - run_start, run_start, i)
            run_start = None
    if run_start is not None and len(coverage) - run_start > best[0]:
        best = (len(coverage) - run_start, run_start, len(coverage))

    gutter_width, g0, g1 = best
    min_gutter = max(12.0, 0.03 * page_width)
    if gutter_width < min_gutter:
        return [_order_rows(lines)]

    cut_lo, cut_hi = lo + g0, lo + g1
    left = [ln for ln in lines if ln.bbox[2] <= cut_lo + 2]
    right = [ln for ln in lines if ln.x0 >= cut_hi - 2]
    taken = {id(ln) for ln in left} | {id(ln) for ln in right}
    spanning = [ln for ln in lines if id(ln) not in taken]

    # A gutter only counts as a column separator if both sides are substantial.
    need = max(2, int(0.2 * len(lines)))
    if len(left) < need or len(right) < need:
        return [_order_rows(lines)]

    groups: list[list[Line]] = []
    for group in (spanning, left, right):
        if group:
            groups.append(_order_rows(group))
    return groups


def _order_rows(lines: list[Line]) -> list[Line]:
    """Sort top-to-bottom, then left-to-right *within* a visual row.

    PDFs often place labels and values on the same baseline with a fraction of
    a point difference. Sorting by exact ``y`` interleaves them (and can put a
    right-hand date before the label on its left). Cluster near-equal baselines
    first, then order each row by x.
    """
    if not lines:
        return []
    heights = sorted(ln.height for ln in lines if ln.height > 0)
    tol = max(2.0, min(8.0, 0.5 * heights[len(heights) // 2])) if heights else 3.0
    ordered = sorted(lines, key=lambda ln: (ln.y0, ln.x0))
    rows: list[list[Line]] = []
    for ln in ordered:
        if rows and abs(ln.y0 - rows[-1][0].y0) <= tol:
            rows[-1].append(ln)
        else:
            rows.append([ln])
    for row in rows:
        row.sort(key=lambda ln: ln.x0)
    return [ln for row in rows for ln in row]


def _align(bbox, column: tuple[float, float] | None, line_h: float) -> str:
    if column is None:
        return "left"
    x0, x1 = column
    width = x1 - x0
    block_width = bbox[2] - bbox[0]
    if width <= 0 or block_width >= 0.85 * width:
        return "left"
    slack = max(12.0, 3.0 * line_h, 0.05 * width)
    center = (bbox[0] + bbox[2]) / 2
    # Check the page edges before the centre: a line starting at the left
    # margin is left-aligned even when its width makes it look centred.
    if bbox[0] - x0 <= slack:
        return "left"
    if x1 - bbox[2] <= slack:
        return "right"
    if abs(center - (x0 + x1) / 2) <= slack:
        return "center"
    return "left"


def merge_lines_to_blocks(
    lines: list[Line],
    page_height: float,
    heading_ratio: float = 1.12,
    title_ratio: float = 1.5,
    column: tuple[float, float] | None = None,
) -> list[Block]:
    """Greedy line->paragraph merge, then classify by size."""
    lines = [ln for ln in lines if ln.text.strip()]
    if not lines:
        return []
    lines = _order_rows(lines)

    base = body_size(lines)
    paras: list[tuple[list[Line], bool]] = []
    cur: list[Line] = [lines[0]]
    for prev, ln in zip(lines, lines[1:]):
        dy = ln.y0 - prev.y0
        line_h = max(prev.height, ln.height, 1.0)
        same_style = round(ln.size, 1) == round(prev.size, 1) and ln.bold == prev.bold
        # Baseline distance is far more stable than glyph-box gaps: line boxes
        # grow and shrink with ascenders/descenders.
        breaking = (
            dy > 1.45 * line_h
            or dy < 0.2 * line_h
            or not same_style
            or (ln.group is not None and prev.group is not None and ln.group != prev.group)
            or abs(ln.x0 - prev.x0) > 2.5 * line_h
        )
        if breaking:
            paras.append((cur, _standalone(cur, base, heading_ratio)))
            cur = [ln]
        else:
            cur.append(ln)
    paras.append((cur, _standalone(cur, base, heading_ratio)))

    blocks: list[Block] = []
    for group, standalone in paras:
        text = _join_lines(group)
        if not text:
            continue
        sizes = [l.size for l in group if l.size > 0]
        size = float(statistics.median(sizes)) if sizes else base
        bbox = (
            min(l.x0 for l in group),
            min(l.y0 for l in group),
            max(l.bbox[2] for l in group),
            max(l.y1 for l in group),
        )
        if size >= base * title_ratio:
            kind = "title"
        elif size >= base * heading_ratio or standalone:
            kind = "heading"
        else:
            kind = "para"
        conf = sum(l.conf for l in group) / len(group)
        bold = sum(1 for l in group if l.bold) * 2 >= len(group)
        italic = sum(1 for l in group if l.italic) * 2 >= len(group)
        fonts = Counter(l.font for l in group if l.font)
        font = fonts.most_common(1)[0][0] if fonts else ""
        blocks.append(
            Block(
                type=kind,
                bbox=bbox,
                text=text,
                size=size,
                conf=conf,
                align=_align(bbox, column, max(l.height for l in group)),
                bold=bold,
                italic=italic,
                font=font,
            )
        )
    return blocks


def _standalone(group: list[Line], base: float, heading_ratio: float) -> bool:
    return len(group) == 1 and (group[0].bold or group[0].size > base * heading_ratio)


def _join_lines(group: list[Line]) -> str:
    parts: list[str] = []
    for ln in group:
        t = ln.text.strip()
        if not t:
            continue
        if parts and parts[-1].endswith("-"):
            parts[-1] = parts[-1][:-1] + t
        else:
            parts.append(t)
    return " ".join(parts)
