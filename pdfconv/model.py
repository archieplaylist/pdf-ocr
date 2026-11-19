"""Intermediate document model. Every exporter reads only from here.

The model is also the editable source of truth for the web app: it can be
round-tripped through JSON (`to_json` / `from_json`) and blocks carry stable
ids so a client can address them while content is added, removed or reordered.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path


def new_id() -> str:
    """Short id for a block. Only needs to be unique inside one document."""
    return uuid.uuid4().hex[:12]


@dataclass
class Run:
    """A styled span inside a block. ``None`` runs on a block means the whole
    block uses the block-level style hints."""

    text: str = ""
    bold: bool = False
    italic: bool = False
    font: str = ""
    size: float = 0.0
    color: str = ""
    align: str = ""  # only meaningful for the first run; kept for round-trips


@dataclass
class Block:
    type: str  # title | heading | para | table | image
    bbox: tuple[float, float, float, float]
    text: str = ""
    size: float = 0.0
    conf: float = 1.0
    rows: list[list[str]] | None = None
    # Presentation hints. All optional so older model JSON still loads.
    align: str = "left"  # left | center | right
    bold: bool = False
    italic: bool = False
    font: str = ""
    color: str = ""  # hex like #rrggbb
    indent: float = 0.0
    space_before: float = 0.0
    tab_stops: list[float] | None = None
    col_widths: list[float] | None = None
    # Inline styled spans. ``None`` means "use block-level styles".
    runs: list[Run] | None = None
    # Stable identity for the editor. Generated on first save/load.
    id: str = ""
    # True once the user changed this block in the web editor.
    edited: bool = False
    # True for blocks created in the editor (not present in the source PDF).
    added: bool = False
    # Image payload (not serialized to JSON).
    image_data: bytes | None = field(default=None, repr=False)
    image_ext: str = "png"

    @property
    def plain_text(self) -> str:
        if self.runs:
            return "".join(run.text for run in self.runs)
        return self.text

    def as_dict(self) -> dict:
        d = asdict(self)
        d["bbox"] = [round(v, 2) for v in self.bbox]
        d["conf"] = round(self.conf, 3)
        if self.col_widths:
            d["col_widths"] = [round(v, 2) for v in self.col_widths]
        # Never serialize raw image bytes.
        d.pop("image_data", None)
        return d


@dataclass
class Page:
    number: int
    width: float
    height: float
    source: str  # "native" | "ocr"
    blocks: list[Block] = field(default_factory=list)
    # A page that is one big image with an OCR text layer, e.g. a scanned
    # letter. Auto layout keeps these pages as images.
    scan: bool = False
    # Whether a scanned page is reliable enough to be rebuilt as editable text
    # in `auto` layout instead of kept as a page image.
    ocr_editable: bool = False
    # Optimistic-concurrency counter, bumped by every edit.
    rev: int = 0
    # Bounding boxes removed by the editor. The in-place PDF exporter whites
    # these out because the blocks themselves are gone.
    removed: list[list[float]] = field(default_factory=list)

    @property
    def edited(self) -> bool:
        return bool(self.removed) or any(b.edited for b in self.blocks)

    def as_dict(self) -> dict:
        return {
            "number": self.number,
            "size": [round(self.width, 1), round(self.height, 1)],
            "source": self.source,
            "scan": self.scan,
            "ocr_editable": self.ocr_editable,
            "rev": self.rev,
            "edited": self.edited,
            "removed": [[round(v, 2) for v in box] for box in self.removed],
            "blocks": [b.as_dict() for b in self.blocks],
        }


@dataclass
class Document:
    path: str
    pages: list[Page] = field(default_factory=list)

    def to_json(self, indent: int = 1) -> str:
        return json.dumps(
            {"path": self.path, "pages": [p.as_dict() for p in self.pages]},
            ensure_ascii=False,
            indent=indent,
        )

    @classmethod
    def from_json(cls, raw: str) -> "Document":
        return cls.from_dict(json.loads(raw))

    @classmethod
    def from_dict(cls, data: dict) -> "Document":
        """Load a model, tolerating JSON written by older versions."""
        pages: list[Page] = []
        for raw_page in data.get("pages", []):
            size = raw_page.get("size") or [595.0, 842.0]
            page = Page(
                number=int(raw_page.get("number", len(pages) + 1)),
                width=float(size[0]),
                height=float(size[1]),
                source=raw_page.get("source", "native"),
                scan=bool(raw_page.get("scan")),
                ocr_editable=bool(raw_page.get("ocr_editable")),
                rev=int(raw_page.get("rev", 0)),
                removed=[_bbox(box) for box in raw_page.get("removed", [])],
            )
            page.blocks = [_load_block(raw) for raw in raw_page.get("blocks", [])]
            _ensure_ids(page.blocks)
            pages.append(page)
        return cls(path=data.get("path", ""), pages=pages)

    def text(self) -> str:
        out: list[str] = []
        for page in self.pages:
            for b in page.blocks:
                if b.type == "table" and b.rows:
                    out.extend(" | ".join(r) for r in b.rows)
                elif b.plain_text:
                    out.append(b.plain_text)
        return "\n".join(out)

    @property
    def mean_conf(self) -> float:
        vals = [b.conf for p in self.pages for b in p.blocks if b.plain_text]
        return sum(vals) / len(vals) if vals else 1.0

    @property
    def edited(self) -> bool:
        return any(page.edited for page in self.pages)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{uuid.uuid4().hex[:6]}.tmp")
        tmp.write_text(self.to_json(), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: str | Path) -> "Document":
        return cls.from_json(Path(path).read_text(encoding="utf-8"))


def _load_block(raw: dict) -> Block:
    bbox = raw.get("bbox") or [0.0, 0.0, 0.0, 0.0]
    return Block(
        type=raw.get("type", "para"),
        bbox=_bbox(bbox),
        text=raw.get("text", "") or "",
        size=float(raw.get("size", 0.0) or 0.0),
        conf=float(raw.get("conf") if raw.get("conf") is not None else 1.0),
        rows=[[str(cell) for cell in row] for row in raw.get("rows") or []]
        or None,
        align=raw.get("align", "left") or "left",
        bold=bool(raw.get("bold", False)),
        italic=bool(raw.get("italic", False)),
        font=raw.get("font", "") or "",
        color=raw.get("color", "") or "",
        indent=float(raw.get("indent", 0.0) or 0.0),
        space_before=float(raw.get("space_before", 0.0) or 0.0),
        tab_stops=[float(v) for v in raw.get("tab_stops") or []] or None,
        col_widths=[float(v) for v in raw.get("col_widths") or []] or None,
        id=raw.get("id", "") or "",
        edited=bool(raw.get("edited", False)),
        added=bool(raw.get("added", False)),
        runs=_load_runs(raw.get("runs")),
    )


def _load_runs(raw) -> list[Run] | None:
    if not raw:
        return None
    out: list[Run] = []
    for item in raw:
        out.append(
            Run(
                text=item.get("text", "") or "",
                bold=bool(item.get("bold", False)),
                italic=bool(item.get("italic", False)),
                font=item.get("font", "") or "",
                size=float(item.get("size", 0.0) or 0.0),
                color=item.get("color", "") or "",
            )
        )
    return out or None
    values = list(values or [])[:4]
    values += [0.0] * (4 - len(values))
    return (float(values[0]), float(values[1]), float(values[2]), float(values[3]))


def _bbox(values) -> tuple[float, float, float, float]:
    values = list(values or [])[:4]
    values += [0.0] * (4 - len(values))
    return (float(values[0]), float(values[1]), float(values[2]), float(values[3]))


def _ensure_ids(blocks: list[Block]) -> None:
    seen = {block.id for block in blocks if block.id}
    for block in blocks:
        if block.id:
            continue
        candidate = new_id()
        while candidate in seen:
            candidate = new_id()
        block.id = candidate
        seen.add(candidate)


def ensure_ids(doc: Document) -> None:
    """Assign ids to blocks from older models before they are first edited."""
    for page in doc.pages:
        _ensure_ids(page.blocks)
