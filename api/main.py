"""FastAPI app: PDF conversion API + built React frontend."""

from __future__ import annotations

import json
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import pymupdf
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from pdfconv.model import Block, Document, Page, Run, new_id
from pdfconv.ocr import TESSERACT
from pdfconv.ocr_engine import available_engines

from .jobs import EXPORTERS, JOB_ROOT, JobLimitError, JobManager

ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT / "fixtures"
WEB_DIST = ROOT / "web" / "dist"

MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "50"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
LANG_RE = re.compile(r"^[A-Za-z0-9_+]{1,64}$")

MEDIA_TYPES = {
    "docx": (
        "application/vnd.openxmlformats-officedocument"
        ".wordprocessingml.document"
    ),
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "json": "application/json",
    "pdf": "application/pdf",
    "pdf-inplace": "application/pdf",
}

MAX_BLOCKS = 500
MAX_ROWS = 100
MAX_COLS = 50
MAX_CELL = 10_000
MAX_TEXT = 200_000

manager = JobManager()


@asynccontextmanager
async def lifespan(_: FastAPI):
    global manager
    manager = JobManager()
    yield
    manager.shutdown()


app = FastAPI(title="pdfconv", version="0.1.0", lifespan=lifespan)

origins = [
    origin.strip()
    for origin in os.environ.get(
        "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if origin.strip()
]
if origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["*"],
    )


# -- helpers ---------------------------------------------------------------


def _fixtures() -> dict[str, Path]:
    if not FIXTURES_DIR.is_dir():
        return {}
    return {path.stem: path for path in sorted(FIXTURES_DIR.glob("*.pdf"))}


def _get_job(job_id: str):
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found or expired")
    return job


def _check_options(
    formats: str, dpi: int, lang: str, layout: str, engine: str
) -> list[str]:
    requested = [part.strip().lower() for part in formats.split(",") if part.strip()]
    if not requested:
        raise HTTPException(422, "choose at least one output format")
    unknown = sorted(set(requested) - set(EXPORTERS))
    if unknown:
        raise HTTPException(422, f"unsupported format(s): {', '.join(unknown)}")
    if not 72 <= dpi <= 600:
        raise HTTPException(422, "dpi must be between 72 and 600")
    if not LANG_RE.match(lang):
        raise HTTPException(422, "invalid language list")
    if layout not in ("auto", "flow", "facsimile"):
        raise HTTPException(422, "layout must be auto, flow or facsimile")
    if engine not in ("auto", "tesseract", "rapidocr"):
        raise HTTPException(422, "engine must be auto, tesseract or rapidocr")
    return list(dict.fromkeys(requested))


async def _save_upload(upload: UploadFile, dest: Path) -> None:
    size = 0
    with dest.open("wb") as fh:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                fh.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(
                    413, f"file is larger than the {MAX_UPLOAD_MB} MB limit"
                )
            fh.write(chunk)
    if size == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(422, "uploaded file is empty")
    with dest.open("rb") as fh:
        if fh.read(5) != b"%PDF-":
            dest.unlink(missing_ok=True)
            raise HTTPException(422, "that file is not a PDF")


# -- edit payloads ---------------------------------------------------------


class RunPayload(BaseModel):
    text: str = Field(default="", max_length=MAX_TEXT)
    bold: bool = False
    italic: bool = False
    font: str = Field(default="", max_length=120)
    size: float = Field(default=0, ge=0, le=200)
    color: str = Field(default="", max_length=9)

    @field_validator("color")
    @classmethod
    def _check_color(cls, value):
        return _validate_color(value)


class BlockPatch(BaseModel):
    """Partial update for one block. ``rev`` guards against stale writes."""

    rev: int = Field(ge=0)
    text: str | None = Field(default=None, max_length=MAX_TEXT)
    rows: list[list[str]] | None = None
    type: Literal["title", "heading", "para", "table"] | None = None
    align: Literal["left", "center", "right"] | None = None
    bold: bool | None = None
    italic: bool | None = None
    size: float | None = Field(default=None, ge=1, le=200)
    font: str | None = Field(default=None, max_length=120)
    color: str | None = Field(default=None, max_length=9)
    indent: float | None = Field(default=None, ge=0, le=1000)
    space_before: float | None = Field(default=None, ge=0, le=1000)
    runs: list[RunPayload] | None = None

    @field_validator("rows")
    @classmethod
    def _check_rows(cls, value):
        return _validate_rows(value)

    @field_validator("color")
    @classmethod
    def _check_color(cls, value):
        return _validate_color(value)


class BlockPayload(BaseModel):
    id: str = Field(default="", max_length=64)
    type: Literal["title", "heading", "para", "table", "image"] = "para"
    bbox: list[float] | None = None
    text: str = Field(default="", max_length=MAX_TEXT)
    size: float | None = Field(default=None, ge=0, le=200)
    conf: float | None = Field(default=None, ge=0, le=1)
    rows: list[list[str]] | None = None
    align: Literal["left", "center", "right"] = "left"
    bold: bool = False
    italic: bool = False
    font: str = Field(default="", max_length=120)
    color: str = Field(default="", max_length=9)
    indent: float = Field(default=0, ge=0, le=1000)
    space_before: float = Field(default=0, ge=0, le=1000)
    tab_stops: list[float] | None = None
    col_widths: list[float] | None = None
    runs: list[RunPayload] | None = None

    @field_validator("rows")
    @classmethod
    def _check_rows(cls, value):
        return _validate_rows(value)

    @field_validator("color")
    @classmethod
    def _check_color(cls, value):
        return _validate_color(value)

    @field_validator("bbox")
    @classmethod
    def _check_bbox(cls, value):
        if value is None:
            return None
        if len(value) != 4:
            raise ValueError("bbox must have four numbers")
        return [float(item) for item in value]


class PageBlocksUpdate(BaseModel):
    rev: int = Field(ge=0)
    blocks: list[BlockPayload] = Field(max_length=MAX_BLOCKS)


def _validate_rows(value):
    if value is None:
        return None
    if len(value) > MAX_ROWS:
        raise ValueError(f"at most {MAX_ROWS} rows are allowed")
    cleaned: list[list[str]] = []
    for row in value:
        if len(row) > MAX_COLS:
            raise ValueError(f"at most {MAX_COLS} columns are allowed")
        cleaned.append([str(cell)[:MAX_CELL] for cell in row])
    return cleaned


def _validate_color(value):
    if value is None or value == "":
        return value
    if not re.fullmatch(r"#?[0-9a-fA-F]{6}", value):
        raise ValueError("color must be a hex value like #rrggbb")
    return value if value.startswith("#") else f"#{value}"


# -- edit helpers ----------------------------------------------------------


def _editable_job(job_id: str):
    job = _get_job(job_id)
    if job.status != "done":
        raise HTTPException(409, "conversion is not finished")
    if not job.model_path.is_file():
        raise HTTPException(404, "document model is missing")
    return job


def _find_page(doc: Document, number: int) -> Page:
    for page in doc.pages:
        if page.number == number:
            return page
    raise HTTPException(404, "no such page")


def _page_snapshot(page: Page) -> dict:
    return {
        "number": page.number,
        "width": page.width,
        "height": page.height,
        "source": page.source,
        "scan": page.scan,
        "ocr_editable": page.ocr_editable,
        "rev": page.rev,
        "edited": page.edited,
        "removed": page.removed,
        "blocks": [block.as_dict() for block in page.blocks],
    }


def _mark_edited(job) -> None:
    job.edited = True
    job.updated = time.time()
    job.invalidate_artifacts()


def _apply_payload(block: Block, payload: BlockPayload) -> None:
    if block.type == "image":
        # Pictures are not editable, but they can be reordered.
        return
    block.type = payload.type
    block.text = payload.text
    block.rows = payload.rows
    block.align = payload.align
    block.bold = payload.bold
    block.italic = payload.italic
    block.font = payload.font
    block.color = payload.color or ""
    block.indent = payload.indent
    block.space_before = payload.space_before
    block.tab_stops = payload.tab_stops
    block.col_widths = payload.col_widths
    if payload.size is not None:
        block.size = payload.size
    if payload.conf is not None:
        block.conf = payload.conf
    if payload.runs is not None:
        block.runs = [
            Run(
                text=item.text,
                bold=item.bold,
                italic=item.italic,
                font=item.font,
                size=item.size or 0.0,
                color=item.color or "",
            )
            for item in payload.runs
        ]
        block.text = "".join(run.text for run in block.runs)


def _new_block(payload: BlockPayload, page: Page, index: int) -> Block:
    bbox = tuple(payload.bbox) if payload.bbox else _default_bbox(page, index)
    return Block(
        type=payload.type,
        bbox=bbox,
        text=payload.text,
        size=payload.size or 0.0,
        conf=1.0,
        rows=payload.rows,
        align=payload.align,
        bold=payload.bold,
        italic=payload.italic,
        font=payload.font,
        color=payload.color or "",
        indent=payload.indent,
        space_before=payload.space_before,
        tab_stops=payload.tab_stops,
        col_widths=payload.col_widths,
        id=new_id(),
        edited=True,
        added=True,
        runs=(
            [
                Run(
                    text=item.text,
                    bold=item.bold,
                    italic=item.italic,
                    font=item.font,
                    size=item.size or 0.0,
                    color=item.color or "",
                )
                for item in payload.runs
            ]
            if payload.runs is not None
            else None
        ),
    )


def _default_bbox(page: Page, index: int) -> tuple[float, float, float, float]:
    left = 72.0
    width = max(page.width - 2 * left, 80.0)
    if page.blocks:
        y = max(block.bbox[3] for block in page.blocks) + 8
    else:
        y = left
    y = min(y, max(page.height - 24, left))
    return (left, y, left + width, y + 18)


# -- API -------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "tesseract": bool(TESSERACT),
        "engines": available_engines(),
        "max_upload_mb": MAX_UPLOAD_MB,
    }


@app.get("/api/fixtures")
def list_fixtures() -> dict:
    fixtures = []
    for stem, path in _fixtures().items():
        fixtures.append(
            {
                "id": stem,
                "name": path.name,
                "size_kb": round(path.stat().st_size / 1024),
                "pages": _page_count(path),
            }
        )
    return {"fixtures": fixtures}


def _page_count(path: Path) -> int:
    try:
        pdf = pymupdf.open(str(path))
    except Exception:  # noqa: BLE001 - optional cosmetic metadata
        return 0
    try:
        return pdf.page_count
    finally:
        pdf.close()


@app.post("/api/jobs", status_code=202)
async def create_job(
    file: UploadFile | None = File(None),
    fixture: str | None = Form(None),
    formats: str = Form("docx,xlsx"),
    dpi: int = Form(300),
    lang: str = Form("ind+eng"),
    layout: str = Form("auto"),
    engine: str = Form("auto"),
) -> dict:
    requested = _check_options(formats, dpi, lang, layout, engine)

    if file is not None and file.filename:
        destination = _incoming_path()
        try:
            await _save_upload(file, destination)
            job = manager.submit(
                destination,
                filename=Path(file.filename).name,
                source="upload",
                formats=requested,
                dpi=dpi,
                lang=lang,
                layout=layout,
                engine=engine,
            )
        except ValueError as exc:
            destination.unlink(missing_ok=True)
            raise HTTPException(422, str(exc)) from exc
        except JobLimitError as exc:
            destination.unlink(missing_ok=True)
            raise HTTPException(429, str(exc)) from exc
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
    elif fixture:
        path = _fixtures().get(fixture)
        if path is None or not path.is_file():
            raise HTTPException(404, "unknown fixture")
        try:
            job = manager.submit(
                path,
                filename=path.name,
                source="fixture",
                formats=requested,
                dpi=dpi,
                lang=lang,
                layout=layout,
                engine=engine,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except JobLimitError as exc:
            raise HTTPException(429, str(exc)) from exc
    else:
        raise HTTPException(422, "no file or fixture supplied")

    return {"id": job.id, "status": job.status}


def _incoming_path() -> Path:
    incoming = JOB_ROOT / "_incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    return incoming / f"{os.urandom(8).hex()}.pdf"


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    return _get_job(job_id).snapshot()


@app.delete("/api/jobs/{job_id}", status_code=204)
def delete_job(job_id: str) -> Response:
    if not manager.delete(job_id):
        raise HTTPException(404, "job not found or expired")
    return Response(status_code=204)


@app.get("/api/jobs/{job_id}/download/{fmt}")
def download(job_id: str, fmt: str):
    job = _get_job(job_id)
    if job.status != "done":
        raise HTTPException(409, "conversion is not finished")
    if fmt not in EXPORTERS and fmt not in ("json", "pdf-inplace"):
        raise HTTPException(404, "unknown format")
    if fmt in ("docx", "xlsx") and fmt not in job.formats:
        raise HTTPException(404, "format was not requested")
    if not job.model_path.is_file():
        raise HTTPException(410, "document model is no longer available")

    if fmt == "json":
        path = job.model_path
    else:
        # Regenerate from the current model so edits are always reflected.
        with job.lock:
            target = job.output_path(fmt)
            tmp = target.with_name(f".{target.name}.{os.urandom(4).hex()}.tmp")
            try:
                job.render(fmt, tmp)
                os.replace(tmp, target)
            except Exception as exc:  # noqa: BLE001 - surfaced to the client
                tmp.unlink(missing_ok=True)
                raise HTTPException(
                    500, f"could not build {fmt}: {exc}"
                ) from exc
        path = target

    if not path.is_file():
        raise HTTPException(410, "artifact is no longer available")
    filename = (
        f"{job.stem}-original.pdf"
        if fmt == "pdf-inplace"
        else f"{job.stem}.{fmt}"
    )
    return FileResponse(
        path,
        media_type=MEDIA_TYPES[fmt],
        filename=filename,
    )


@app.get("/api/jobs/{job_id}/pages/{number}/preview.png")
def page_preview(
    job_id: str,
    number: int,
    dpi: int = Query(110, ge=40, le=200),
):
    job = _get_job(job_id)
    if number < 1 or number > job.pages_total:
        raise HTTPException(404, "no such page")

    cache_dir = job.directory / "previews"
    cache = cache_dir / f"{number}-{dpi}.png"
    if not cache.is_file():
        try:
            pdf = pymupdf.open(str(job.pdf_path))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"cannot open source PDF: {exc}") from exc
        try:
            page = pdf[number - 1]
            pix = page.get_pixmap(dpi=dpi, alpha=False)
            png = pix.tobytes("png")
        finally:
            pdf.close()
        cache_dir.mkdir(parents=True, exist_ok=True)
        # Concurrent requests can render the same page; publish atomically.
        tmp = cache.with_suffix(f".{os.urandom(4).hex()}.tmp")
        tmp.write_bytes(png)
        os.replace(tmp, cache)

    # Read the bytes instead of using FileResponse: a concurrent DELETE may
    # remove the job directory between returning and stat-ing the file.
    try:
        content = cache.read_bytes()
    except FileNotFoundError as exc:
        raise HTTPException(404, "job was removed") from exc
    return Response(
        content=content,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.get("/api/jobs/{job_id}/pages/{number}/blocks")
def page_blocks(job_id: str, number: int) -> dict:
    job = _get_job(job_id)
    if job.status != "done":
        raise HTTPException(409, "conversion is not finished")
    if not job.model_path.is_file():
        raise HTTPException(404, "document model is missing")
    with job.lock:
        doc = job.load_document()
        page = _find_page(doc, number)
        return _page_snapshot(page)


@app.patch("/api/jobs/{job_id}/pages/{number}/blocks/{block_id}")
def patch_block(
    job_id: str, number: int, block_id: str, patch: BlockPatch
) -> dict:
    job = _editable_job(job_id)
    with job.lock:
        doc = job.load_document()
        page = _find_page(doc, number)
        if patch.rev != page.rev:
            raise HTTPException(
                409,
                f"page changed since it was loaded (current rev {page.rev})",
            )
        block = next((item for item in page.blocks if item.id == block_id), None)
        if block is None:
            raise HTTPException(404, "no such block")
        if block.type == "image":
            raise HTTPException(422, "image blocks are read-only")

        changes = patch.model_dump(exclude_unset=True)
        changes.pop("rev", None)
        if "runs" in changes and changes["runs"] is not None:
            runs = [
                Run(
                    text=item["text"],
                    bold=item.get("bold", False),
                    italic=item.get("italic", False),
                    font=item.get("font", ""),
                    size=item.get("size", 0.0) or 0.0,
                    color=item.get("color", "") or "",
                )
                for item in changes["runs"]
            ]
            block.runs = runs
            block.text = "".join(run.text for run in runs)
            changes.pop("runs", None)
        for key, value in changes.items():
            if key == "type" and value == "image":
                raise HTTPException(422, "cannot turn a block into an image")
            if key == "rows" and not value:
                value = None
            setattr(block, key, value)
        block.edited = True
        page.rev += 1
        doc.save(job.model_path)
        _mark_edited(job)
        return _page_snapshot(page)


@app.put("/api/jobs/{job_id}/pages/{number}/blocks")
def replace_page_blocks(job_id: str, number: int, update: PageBlocksUpdate) -> dict:
    """Replace a page's ordered block list (add, delete, reorder)."""
    job = _editable_job(job_id)
    with job.lock:
        doc = job.load_document()
        page = _find_page(doc, number)
        if update.rev != page.rev:
            raise HTTPException(
                409,
                f"page changed since it was loaded (current rev {page.rev})",
            )

        existing = {block.id: block for block in page.blocks if block.id}
        blocks: list[Block] = []
        used_ids: set[str] = set()

        for index, payload in enumerate(update.blocks):
            current = existing.pop(payload.id, None) if payload.id else None
            if current is not None:
                if current.type == "image" and payload.type != "image":
                    raise HTTPException(422, "image blocks are read-only")
                _apply_payload(current, payload)
                current.edited = True
                blocks.append(current)
                used_ids.add(current.id)
                continue

            if payload.type == "image":
                raise HTTPException(422, "cannot add an image block")
            block = _new_block(payload, page, index)
            while block.id in used_ids:
                block.id = new_id()
            used_ids.add(block.id)
            blocks.append(block)

        removed = list(page.removed)
        for block in existing.values():
            # Only real source content needs to be erased from the original
            # PDF; blocks the user added never existed there.
            if not block.added:
                removed.append([round(value, 2) for value in block.bbox])

        page.blocks = blocks
        page.removed = removed
        page.rev += 1
        doc.save(job.model_path)
        _mark_edited(job)
        return _page_snapshot(page)


# -- built frontend --------------------------------------------------------

if WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="spa")
else:

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def dev_hint() -> str:
        return (
            "<h1>pdfconv api</h1>"
            "<p>The API is running. Build the frontend with "
            "<code>cd web &amp;&amp; npm install &amp;&amp; npm run build</code>, "
            "or use the Vite dev server at http://localhost:5173.</p>"
            "<p><a href='/docs'>API docs</a></p>"
        )
