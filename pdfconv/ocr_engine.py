"""Pluggable OCR backends.

Two local engines are supported:

* ``tesseract`` — the system binary, word boxes + confidence, tiny install.
* ``rapidocr``  — PP-OCRv6 ONNX models via onnxruntime, line boxes (plus word
  boxes when asked) and a much stronger detector on phone photos and skewed
  scans.  Models ship inside the wheel, so it runs fully offline.

The rest of the OCR pipeline only talks to the small interface below, so the
layout merge, table grid detection and exporters are engine-independent.

Every ``words`` record has the Tesseract TSV shape used by ``ocr.py``:
``block``, ``par``, ``line``, ``x``, ``y``, ``w``, ``h``, ``conf``, ``text``
(conf in 0..1, pixels).
"""

from __future__ import annotations

import csv
import glob
import io
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

import cv2
import numpy as np


class OcrError(RuntimeError):
    pass


def _tesseract_binary() -> str:
    found = shutil.which("tesseract")
    if found:
        return found
    for cand in sorted(glob.glob("/nix/store/*-tesseract-*/bin/tesseract")):
        if os.access(cand, os.X_OK):
            return cand
    return ""


def _ensure_tessdata(binary: str) -> None:
    if not binary or os.environ.get("TESSDATA_PREFIX"):
        return
    guess = Path(binary).resolve().parent.parent / "share" / "tessdata"
    if (guess / "eng.traineddata").exists():
        os.environ["TESSDATA_PREFIX"] = str(guess)


class TesseractEngine:
    name = "tesseract"

    def __init__(self) -> None:
        self.binary = _tesseract_binary()
        if not self.binary:
            raise OcrError(
                "tesseract not found; install it (nix-shell -p tesseract) or use "
                "the rapidocr engine"
            )
        _ensure_tessdata(self.binary)

    @property
    def available(self) -> bool:
        return bool(self.binary)

    def orientation(self, img: np.ndarray, dpi: int) -> int:
        """Page rotation (0/90/180/270) from Tesseract OSD, 0 when unsure."""
        ok, buf = cv2.imencode(".png", img)
        if not ok:
            return 0
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
            fh.write(buf.tobytes())
            path = fh.name
        try:
            proc = subprocess.run(
                [self.binary, path, "stdout", "--psm", "0", "--dpi", str(dpi)],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired):
            return 0
        finally:
            os.unlink(path)
        rotation, confidence = 0, 0.0
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
        return rotation % 360 if confidence >= 5.0 else 0

    def words(
        self, img: np.ndarray, psm: int = 6, lang: str = "ind+eng", dpi: int = 300
    ) -> list[dict]:
        ok, buf = cv2.imencode(".png", img)
        if not ok:
            return []
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
            fh.write(buf.tobytes())
            path = fh.name
        try:
            proc = subprocess.run(
                [
                    self.binary, path, "-", "-l", lang,
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
            raise OcrError(f"tesseract failed: {proc.stderr.strip()[:400]}")

        words: list[dict] = []
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


class RapidEngine:
    """PP-OCRv6 through RapidOCR/onnxruntime (offline, CPU)."""

    name = "rapidocr"

    def __init__(self, params: dict | None = None) -> None:
        try:
            from rapidocr import RapidOCR
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise OcrError(
                "rapidocr is not installed; pip install rapidocr onnxruntime"
            ) from exc
        # Small PP-OCRv6 models ship in the wheel.  The detector runs on a
        # downscaled copy of the page (recogniser crops are mapped back to the
        # full-resolution image), so the default 2000 px keeps 300 DPI pages
        # inside the 5 s/page budget.  Raise PDFCONV_OCR_MAX_SIDE to 4096 for
        # slightly better small-text detection at ~1.7 s/page more.
        side = int(os.environ.get("PDFCONV_OCR_MAX_SIDE", "2000"))
        # The per-line orientation classifier misfires on long document lines
        # (it flipped clean lines by 180 degrees), so keep it off. Page-level
        # orientation is handled once in preprocess() via OSD.
        config = {
            "Global.max_side_len": side,
            "Global.use_cls": False,
            "Global.log_level": "warning",
        }
        config |= params or {}
        self._engine = RapidOCR(params=config)
        # RapidOCR mutates its own config on every call, so keep concurrent
        # jobs (the API runs two workers) from stepping on each other.
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return True

    def orientation(self, img: np.ndarray, dpi: int) -> int:
        # RapidOCR's classifier only handles 180-degree flips; page-level 90
        # degree detection stays with Tesseract when it is installed.
        try:
            return get_engine("tesseract").orientation(img, dpi)
        except OcrError:
            return 0

    def _run(self, img: np.ndarray) -> list[dict]:
        with self._lock:
            out = self._engine(img)
        if out is None or out.boxes is None or not out.txts:
            return []
        lines: list[dict] = []
        for box, text, score in zip(out.boxes, out.txts, out.scores):
            xs, ys = box[:, 0], box[:, 1]
            lines.append(
                {
                    "x": float(xs.min()),
                    "y": float(ys.min()),
                    "w": float(xs.max() - xs.min()),
                    "h": float(ys.max() - ys.min()),
                    "text": (text or "").strip(),
                    "conf": float(score),
                }
            )
        return [ln for ln in lines if ln["text"]]

    def words(
        self, img: np.ndarray, psm: int = 6, lang: str = "ind+eng", dpi: int = 300
    ) -> list[dict]:
        """Return one record per detected line.

        RapidOCR has no PSM equivalent: detection always finds its own lines,
        which is what we want for both whole pages and table crops. Keeping
        the recognised line text intact matters — the per-word boxes the model
        reports can split a word into syllables.
        """
        return [
            {
                "block": 0,
                "par": index,
                "line": 0,
                "x": line["x"],
                "y": line["y"],
                "w": line["w"],
                "h": line["h"],
                "conf": line["conf"],
                "text": line["text"],
                "group": None,
            }
            for index, line in enumerate(self._run(img))
        ]

    def lines(self, img: np.ndarray) -> list[dict]:
        """Natural text lines; used for table regions and low-conf retries."""
        return self._run(img)


_ENGINES = {"tesseract": TesseractEngine, "rapidocr": RapidEngine}
_CACHE: dict[str, object] = {}


def available_engines() -> dict[str, bool]:
    status = {}
    for name in _ENGINES:
        try:
            get_engine(name)
            status[name] = True
        except Exception:  # noqa: BLE001 - availability probe only
            status[name] = False
    return status


def default_engine_name() -> str:
    return os.environ.get("PDFCONV_OCR_ENGINE", "auto").strip().lower()


def get_engine(name: str | None = None):
    """Return a cached engine instance.

    ``auto`` picks RapidOCR when installed, else Tesseract.
    """
    wanted = (name or default_engine_name()).strip().lower()
    if wanted in ("", "auto"):
        status = available_engines()
        wanted = "rapidocr" if status.get("rapidocr") else "tesseract"
    if wanted not in _ENGINES:
        raise OcrError(
            f"unknown OCR engine {wanted!r}; choose from {', '.join(_ENGINES)}"
        )
    if wanted not in _CACHE:
        _CACHE[wanted] = _ENGINES[wanted]()
    return _CACHE[wanted]
