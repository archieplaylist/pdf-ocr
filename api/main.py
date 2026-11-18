"""FastAPI app: PDF conversion API + built React frontend."""

from __future__ import annotations

import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

import pymupdf
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from pdfconv.ocr import TESSERACT

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
}

manager = JobManager()


@asynccontextmanager
async def lifespan(_: FastAPI):
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
        allow_methods=["GET", "POST", "DELETE"],
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


def _check_options(formats: str, dpi: int, lang: str, layout: str) -> list[str]:
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


# -- API -------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "tesseract": bool(TESSERACT),
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
) -> dict:
    requested = _check_options(formats, dpi, lang, layout)

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
    if fmt not in EXPORTERS and fmt != "json":
        raise HTTPException(404, "unknown format")
    if fmt != "json" and fmt not in job.formats:
        raise HTTPException(404, "format was not requested")
    path = job.output_path(fmt)
    if not path.is_file():
        raise HTTPException(410, "artifact is no longer available")
    return FileResponse(
        path,
        media_type=MEDIA_TYPES[fmt],
        filename=f"{job.stem}.{fmt}",
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
    model = json.loads(job.model_path.read_text(encoding="utf-8"))
    for page in model["pages"]:
        if page["number"] == number:
            return {
                "number": page["number"],
                "width": page["size"][0],
                "height": page["size"][1],
                "source": page["source"],
                "scan": bool(page.get("scan")),
                "blocks": page["blocks"],
            }
    raise HTTPException(404, "no such page")


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
