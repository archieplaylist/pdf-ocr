"""Intermediate document model. Every exporter reads only from here."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


@dataclass
class Block:
    type: str  # title | heading | para | table
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
    col_widths: list[float] | None = None

    def as_dict(self) -> dict:
        d = asdict(self)
        d["bbox"] = [round(v, 2) for v in self.bbox]
        d["conf"] = round(self.conf, 3)
        if self.col_widths:
            d["col_widths"] = [round(v, 2) for v in self.col_widths]
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


@dataclass
class Document:
    path: str
    pages: list[Page] = field(default_factory=list)

    def to_json(self, indent: int = 1) -> str:
        return json.dumps(
            {
                "path": self.path,
                "pages": [
                    {
                        "number": p.number,
                        "size": [round(p.width, 1), round(p.height, 1)],
                        "source": p.source,
                        "scan": p.scan,
                        "blocks": [b.as_dict() for b in p.blocks],
                    }
                    for p in self.pages
                ],
            },
            ensure_ascii=False,
            indent=indent,
        )

    def text(self) -> str:
        out: list[str] = []
        for page in self.pages:
            for b in page.blocks:
                if b.type == "table" and b.rows:
                    out.extend(" | ".join(r) for r in b.rows)
                elif b.text:
                    out.append(b.text)
        return "\n".join(out)

    @property
    def mean_conf(self) -> float:
        vals = [b.conf for p in self.pages for b in p.blocks if b.text]
        return sum(vals) / len(vals) if vals else 1.0
