"""In-memory job registry and background conversion workers.

Jobs live for ``JOB_TTL_MIN`` minutes and are deleted from disk afterwards.
State is per-process, so run uvicorn with a single worker.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from pdfconv.export_docx import write_docx
from pdfconv.export_pdf import write_pdf_inplace, write_pdf_reflow
from pdfconv.export_xlsx import write_xlsx
from pdfconv.model import Document
from pdfconv.pipeline import convert

EXPORTERS = {"docx": write_docx, "xlsx": write_xlsx, "pdf": write_pdf_reflow}
ARTIFACT_FORMATS = ("docx", "xlsx", "pdf", "pdf-inplace")

JOB_ROOT = Path(
    os.environ.get("PDFCONV_JOB_DIR", Path(tempfile.gettempdir()) / "pdfconv-jobs")
)
TTL_SECONDS = int(os.environ.get("JOB_TTL_MIN", "60")) * 60
MAX_ACTIVE = int(os.environ.get("MAX_ACTIVE_JOBS", "4"))
MAX_PAGES = int(os.environ.get("MAX_PAGES", "100"))
WORKERS = int(os.environ.get("MAX_WORKERS", "2"))


class JobLimitError(RuntimeError):
    """Too many conversions are queued or running."""


class JobCancelled(Exception):
    """Raised inside a worker when its job was discarded by the client."""


@dataclass
class Job:
    id: str
    filename: str
    stem: str
    source: str  # "upload" | "fixture"
    formats: list[str]
    dpi: int
    lang: str
    created: float
    updated: float
    directory: Path
    layout: str = "auto"
    engine: str = "auto"
    status: str = "queued"  # queued | running | done | error | cancelled
    cancelled: bool = False
    pages_total: int = 0
    pages_done: int = 0
    started_at: float = 0.0
    pages: list[dict] = field(default_factory=list)
    elapsed: float = 0.0
    mean_conf: float = 1.0
    error: str | None = None
    edited: bool = False
    # Serialises edits and artifact regeneration for this job.
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def pdf_path(self) -> Path:
        return self.directory / "input.pdf"

    @property
    def model_path(self) -> Path:
        return self.directory / "model.json"

    def output_path(self, fmt: str) -> Path:
        if fmt == "json":
            return self.model_path
        if fmt == "pdf-inplace":
            return self.directory / f"{self.stem}-original.pdf"
        return self.directory / f"{self.stem}.{fmt}"

    def load_document(self) -> Document:
        return Document.load(self.model_path)

    def render(self, fmt: str, target: Path | None = None) -> Path:
        """Regenerate one artifact from the current (possibly edited) model."""
        target = target or self.output_path(fmt)
        doc = self.load_document()
        if fmt == "docx":
            write_docx(doc, target, layout=self.layout)
        elif fmt == "xlsx":
            write_xlsx(doc, target)
        elif fmt == "pdf":
            write_pdf_reflow(doc, target, layout=self.layout)
        elif fmt == "pdf-inplace":
            write_pdf_inplace(doc, target)
        else:
            raise ValueError(f"unknown format: {fmt}")
        return target

    def invalidate_artifacts(self) -> None:
        for fmt in ARTIFACT_FORMATS:
            self.output_path(fmt).unlink(missing_ok=True)

    def snapshot(self) -> dict:
        elapsed = self.elapsed
        if self.status == "running" and self.started_at:
            elapsed = time.time() - self.started_at
        return {
            "id": self.id,
            "status": self.status,
            "filename": self.filename,
            "source": self.source,
            "formats": self.formats,
            "dpi": self.dpi,
            "lang": self.lang,
            "layout": self.layout,
            "engine": self.engine,
            "pages_total": self.pages_total,
            "pages_done": self.pages_done,
            "pages": list(self.pages),
            "elapsed": round(elapsed, 2),
            "sec_per_page": round(elapsed / max(self.pages_done, 1), 2),
            "mean_conf": round(self.mean_conf, 4),
            "error": self.error,
            "edited": self.edited,
            "created": self.created,
            "updated": self.updated,
            "downloads": {
                fmt: f"/api/jobs/{self.id}/download/{fmt}" for fmt in self.formats
            }
            | {
                "json": f"/api/jobs/{self.id}/download/json",
                "pdf": f"/api/jobs/{self.id}/download/pdf",
                "pdf-inplace": f"/api/jobs/{self.id}/download/pdf-inplace",
            },
        }


def _safe_stem(name: str) -> str:
    stem = Path(name).stem
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return stem[:60] or "document"


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=WORKERS, thread_name_prefix="pdfconv"
        )
        self._stop = threading.Event()
        self._sweeper = threading.Thread(
            target=self._sweep_loop, name="pdfconv-sweeper", daemon=True
        )
        JOB_ROOT.mkdir(parents=True, exist_ok=True)
        (JOB_ROOT / "_incoming").mkdir(exist_ok=True)
        self._sweeper.start()

    # -- lifecycle ---------------------------------------------------------

    def submit(
        self,
        pdf_path: Path,
        filename: str,
        source: str,
        formats: list[str],
        dpi: int,
        lang: str,
        layout: str = "auto",
        engine: str = "auto",
    ) -> Job:
        pdf_path = Path(pdf_path)
        with self._lock:
            active = sum(
                1 for job in self._jobs.values() if job.status in ("queued", "running")
            )
            if active >= MAX_ACTIVE:
                raise JobLimitError(
                    "too many conversions in progress; wait for one to finish"
                )

        total = self._validate(pdf_path)

        now = time.time()
        job = Job(
            id=uuid.uuid4().hex[:12],
            filename=filename,
            stem=_safe_stem(filename),
            source=source,
            formats=list(formats),
            dpi=dpi,
            lang=lang,
            layout=layout,
            engine=engine,
            created=now,
            updated=now,
            directory=JOB_ROOT / uuid.uuid4().hex[:12],
        )
        job.directory.mkdir(parents=True, exist_ok=True)
        if source == "fixture":
            # Fixtures are part of the repo; never move them out of place.
            shutil.copyfile(pdf_path, job.pdf_path)
        else:
            try:
                shutil.move(str(pdf_path), job.pdf_path)
            except OSError:
                shutil.copyfile(pdf_path, job.pdf_path)
                pdf_path.unlink(missing_ok=True)
        job.pages_total = total

        with self._lock:
            self._jobs[job.id] = job
        self._executor.submit(self._run, job)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def delete(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            if job.status in ("queued", "running"):
                # The worker notices at the next page boundary and cleans up.
                job.cancelled = True
                return True
            self._jobs.pop(job_id, None)
        shutil.rmtree(job.directory, ignore_errors=True)
        return True

    def _forget(self, job: Job) -> None:
        with self._lock:
            self._jobs.pop(job.id, None)
        shutil.rmtree(job.directory, ignore_errors=True)

    def shutdown(self) -> None:
        self._stop.set()
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._sweeper.join(timeout=1)

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _validate(pdf_path: Path) -> int:
        try:
            pdf = pymupdf.open(str(pdf_path))
        except Exception as exc:  # noqa: BLE001 - any open failure means bad input
            raise ValueError(f"not a readable PDF: {exc}") from exc
        try:
            if pdf.needs_pass:
                raise ValueError("password-protected PDF is not supported")
            total = pdf.page_count
        finally:
            pdf.close()
        if total == 0:
            raise ValueError("PDF has no pages")
        if total > MAX_PAGES:
            raise ValueError(f"PDF has {total} pages; the limit is {MAX_PAGES}")
        return total

    def _run(self, job: Job) -> None:
        started = time.perf_counter()
        with self._lock:
            if job.cancelled:
                job.status = "cancelled"
                job.error = "Conversion cancelled."
                job.updated = time.time()
                should_stop = True
            else:
                job.status = "running"
                job.started_at = time.time()
                job.updated = time.time()
                should_stop = False
        if should_stop:
            self._forget(job)
            return

        def on_page(page, seconds: float) -> None:
            tables = sum(1 for block in page.blocks if block.type == "table")
            with self._lock:
                if job.cancelled:
                    raise JobCancelled
                job.pages.append(
                    {
                        "number": page.number,
                        "source": page.source,
                        "scan": page.scan,
                        "blocks": len(page.blocks),
                        "tables": tables,
                        "seconds": round(seconds, 2),
                    }
                )
                job.pages_done += 1
                job.updated = time.time()

        try:
            doc: Document = convert(
                job.pdf_path,
                dpi=job.dpi,
                lang=job.lang,
                on_page=on_page,
                engine=job.engine,
            )
            for fmt in job.formats:
                if fmt == "docx":
                    write_docx(doc, job.output_path(fmt), layout=job.layout)
                elif fmt == "pdf":
                    write_pdf_reflow(doc, job.output_path(fmt), layout=job.layout)
                else:
                    EXPORTERS[fmt](doc, job.output_path(fmt))
            job.model_path.write_text(doc.to_json(), encoding="utf-8")
            job.edited = doc.edited
        except JobCancelled:
            with self._lock:
                job.status = "cancelled"
                job.error = "Conversion cancelled."
                job.elapsed = time.perf_counter() - started
                job.updated = time.time()
            self._forget(job)
            return
        except Exception as exc:  # noqa: BLE001 - surfaced to the client
            traceback.print_exc()
            with self._lock:
                job.status = "error"
                job.error = str(exc) or exc.__class__.__name__
                job.elapsed = time.perf_counter() - started
                job.updated = time.time()
            return

        with self._lock:
            job.status = "done"
            job.mean_conf = doc.mean_conf
            job.elapsed = time.perf_counter() - started
            job.updated = time.time()

    def _sweep_loop(self) -> None:
        while not self._stop.wait(30):
            cutoff = time.time() - TTL_SECONDS
            with self._lock:
                expired = [
                    job
                    for job in self._jobs.values()
                    if job.status not in ("queued", "running") and job.updated < cutoff
                ]
                for job in expired:
                    self._jobs.pop(job.id, None)
                active_dirs = {job.directory.name for job in self._jobs.values()}
            for job in expired:
                shutil.rmtree(job.directory, ignore_errors=True)
            self._clean_orphans(cutoff, active_dirs)
            self._clean_incoming(cutoff)

    @staticmethod
    def _clean_orphans(cutoff: float, active_dirs: set[str]) -> None:
        """Remove job directories left behind by a previous process."""
        for path in JOB_ROOT.iterdir():
            if not path.is_dir() or path.name == "_incoming" or path.name in active_dirs:
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:
                pass

    @staticmethod
    def _clean_incoming(cutoff: float) -> None:
        incoming = JOB_ROOT / "_incoming"
        for path in incoming.glob("*"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass
