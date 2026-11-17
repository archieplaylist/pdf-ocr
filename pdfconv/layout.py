"""Shared layout helpers: column splitting, line->paragraph merging."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .model import Block


@dataclass
class Line:
    bbox: tuple[float, float, float, float]
    text: str
    size: float = 0.0
    bold: bool = False
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
        return [sorted(lines, key=lambda l: (round(l.y0, 1), l.x0))]

    lo = int(min(ln.x0 for ln in lines))
    hi = int(max(ln.bbox[2] for ln in lines))
    if hi - lo < 40:
        return [sorted(lines, key=lambda l: (round(l.y0, 1), l.x0))]

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
        return [sorted(lines, key=lambda l: (round(l.y0, 1), l.x0))]

    cut_lo, cut_hi = lo + g0, lo + g1
    left = [ln for ln in lines if ln.bbox[2] <= cut_lo + 2]
    right = [ln for ln in lines if ln.x0 >= cut_hi - 2]
    taken = {id(ln) for ln in left} | {id(ln) for ln in right}
    spanning = [ln for ln in lines if id(ln) not in taken]

    # A gutter only counts as a column separator if both sides are substantial.
    need = max(2, int(0.2 * len(lines)))
    if len(left) < need or len(right) < need:
        return [sorted(lines, key=lambda l: (round(l.y0, 1), l.x0))]

    groups: list[list[Line]] = []
    for group in (spanning, left, right):
        if group:
            groups.append(sorted(group, key=lambda l: (round(l.y0, 1), l.x0)))
    return groups


def merge_lines_to_blocks(
    lines: list[Line],
    page_height: float,
    heading_ratio: float = 1.12,
    title_ratio: float = 1.5,
) -> list[Block]:
    """Greedy line->paragraph merge, then classify by size."""
    lines = [ln for ln in lines if ln.text.strip()]
    if not lines:
        return []
    lines.sort(key=lambda l: (round(l.y0, 1), l.x0))

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
        size = max((l.size for l in group), default=base)
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
        blocks.append(Block(type=kind, bbox=bbox, text=text, size=size, conf=conf))
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
