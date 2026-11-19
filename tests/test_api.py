"""API tests. They run the real pipeline on the bundled fixtures."""

from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import app

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def wait_for(client: TestClient, job_id: str, timeout: float = 120.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] in ("done", "error", "cancelled"):
            return body
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


def create(client: TestClient, **data) -> dict:
    payload = {"formats": "docx,xlsx", "dpi": "300", "lang": "ind+eng"} | data
    response = client.post("/api/jobs", data=payload)
    assert response.status_code == 202, response.text
    return response.json()


def test_health(client: TestClient):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["tesseract"] is True
    assert body["engines"]["rapidocr"] is True
    assert body["max_upload_mb"] > 0


def test_fixtures_list(client: TestClient):
    fixtures = client.get("/api/fixtures").json()["fixtures"]
    assert len(fixtures) == 5
    assert all(item["pages"] >= 1 for item in fixtures)


def test_native_fixture_end_to_end(client: TestClient):
    created = create(client, fixture="doc2_native_invoice")
    job = wait_for(client, created["id"])

    assert job["status"] == "done", job["error"]
    assert job["pages_total"] == 1
    assert job["pages_done"] == 1
    assert job["pages"][0]["source"] == "native"
    assert job["pages"][0]["tables"] >= 1
    assert job["mean_conf"] == pytest.approx(1.0)
    assert job["elapsed"] > 0

    docx = client.get(f"/api/jobs/{job['id']}/download/docx")
    assert docx.status_code == 200
    assert docx.content[:2] == b"PK"
    assert "wordprocessingml" in docx.headers["content-type"]

    xlsx = client.get(f"/api/jobs/{job['id']}/download/xlsx")
    assert xlsx.status_code == 200
    assert xlsx.content[:2] == b"PK"

    model = client.get(f"/api/jobs/{job['id']}/download/json")
    assert model.status_code == 200
    assert model.json()["pages"][0]["number"] == 1

    preview = client.get(f"/api/jobs/{job['id']}/pages/1/preview.png")
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/png"
    assert preview.content.startswith(b"\x89PNG")

    blocks = client.get(f"/api/jobs/{job['id']}/pages/1/blocks").json()
    assert blocks["width"] > 0 and blocks["height"] > 0
    assert blocks["blocks"]
    block = blocks["blocks"][0]
    assert set(["type", "bbox", "text", "conf"]).issubset(block)


def test_scan_fixture_uses_ocr(client: TestClient):
    created = create(client, fixture="doc3_scan_clean", formats="docx")
    job = wait_for(client, created["id"])
    assert job["status"] == "done", job["error"]
    assert job["pages"][0]["source"] == "ocr"
    assert job["mean_conf"] > 0.5
    # xlsx was not requested, so the endpoint must refuse it
    assert client.get(f"/api/jobs/{job['id']}/download/xlsx").status_code == 404
    assert client.get(f"/api/jobs/{job['id']}/download/json").status_code == 200


def test_tesseract_engine_is_selectable(client: TestClient):
    created = create(
        client, fixture="doc3_scan_clean", formats="docx", engine="tesseract"
    )
    job = wait_for(client, created["id"])
    assert job["status"] == "done", job["error"]
    assert job["engine"] == "tesseract"
    assert job["pages"][0]["source"] == "ocr"


def test_facsimile_layout_embeds_page_image(client: TestClient):
    created = create(
        client, fixture="doc3_scan_clean", formats="docx", layout="facsimile"
    )
    job = wait_for(client, created["id"])
    assert job["status"] == "done", job["error"]
    assert job["layout"] == "facsimile"

    docx = client.get(f"/api/jobs/{job['id']}/download/docx")
    assert docx.status_code == 200
    # A facsimile page is a picture, not rebuilt text.
    with zipfile.ZipFile(io.BytesIO(docx.content)) as archive:
        media = [
            name for name in archive.namelist() if name.startswith("word/media/")
        ]
        assert media


def test_layout_is_reported_and_defaults_to_auto(client: TestClient):
    created = create(client, fixture="doc1_native_2col", formats="docx")
    job = wait_for(client, created["id"])
    assert job["layout"] == "auto"
    assert job["pages"][0]["scan"] is False


def test_uploaded_pdf(client: TestClient):
    pdf = (FIXTURES / "doc1_native_2col.pdf").read_bytes()
    response = client.post(
        "/api/jobs",
        data={"formats": "docx", "dpi": "200", "lang": "eng"},
        files={"file": ("my report.pdf", pdf, "application/pdf")},
    )
    assert response.status_code == 202, response.text
    job = wait_for(client, response.json()["id"])
    assert job["status"] == "done", job["error"]
    assert job["filename"] == "my report.pdf"
    # the download filename is derived from a sanitized stem, not the raw name
    assert "my_report.docx" in client.get(
        f"/api/jobs/{job['id']}/download/docx"
    ).headers["content-disposition"]


@pytest.mark.parametrize(
    "payload",
    [
        {"fixture": "nope"},
        {"formats": "pdf"},
        {"formats": "docx", "dpi": "10"},
        {"formats": "docx", "lang": "bad lang!"},
        {"formats": "docx", "layout": "boxes"},
        {"formats": "docx", "engine": "magic"},
        {},
    ],
)
def test_rejects_bad_requests(client: TestClient, payload: dict):
    response = client.post("/api/jobs", data=payload)
    assert response.status_code in (404, 422), response.text


def test_rejects_non_pdf_upload(client: TestClient):
    response = client.post(
        "/api/jobs",
        data={"formats": "docx"},
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 422


def test_rejects_oversized_upload(client: TestClient, monkeypatch):
    import api.main as main

    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 2048)
    pdf = (FIXTURES / "doc1_native_2col.pdf").read_bytes()
    response = client.post(
        "/api/jobs",
        data={"formats": "docx"},
        files={"file": ("big.pdf", pdf, "application/pdf")},
    )
    assert response.status_code == 413


def test_traversal_filename_is_sanitized(client: TestClient):
    pdf = (FIXTURES / "doc1_native_2col.pdf").read_bytes()
    response = client.post(
        "/api/jobs",
        data={"formats": "docx"},
        files={"file": ("../../evil report.pdf", pdf, "application/pdf")},
    )
    assert response.status_code == 202, response.text
    job = wait_for(client, response.json()["id"])
    assert job["status"] == "done", job["error"]
    disposition = client.get(
        f"/api/jobs/{job['id']}/download/docx"
    ).headers["content-disposition"]
    assert "evil_report.docx" in disposition
    assert ".." not in disposition


def test_cancel_job(client: TestClient):
    created = create(client, fixture="doc4_photo_invoice", formats="docx")
    assert client.delete(f"/api/jobs/{created['id']}").status_code == 204
    for _ in range(100):
        if client.get(f"/api/jobs/{created['id']}").status_code == 404:
            break
        time.sleep(0.1)
    assert client.get(f"/api/jobs/{created['id']}").status_code == 404
    assert (
        client.get(f"/api/jobs/{created['id']}/download/docx").status_code == 404
    )


def test_unknown_job_and_pages(client: TestClient):
    assert client.get("/api/jobs/doesnotexist").status_code == 404
    assert client.delete("/api/jobs/doesnotexist").status_code == 404

    created = create(client, fixture="doc1_native_2col", formats="docx")
    job = wait_for(client, created["id"])
    assert client.get(f"/api/jobs/{job['id']}/pages/0/preview.png").status_code == 404
    assert (
        client.get(f"/api/jobs/{job['id']}/pages/99/preview.png").status_code == 404
    )
    assert (
        client.get(f"/api/jobs/{job['id']}/pages/99/blocks").status_code == 404
    )


def test_delete_removes_job(client: TestClient):
    created = create(client, fixture="doc1_native_2col", formats="docx")
    job = wait_for(client, created["id"])
    assert client.delete(f"/api/jobs/{job['id']}").status_code == 204
    assert client.get(f"/api/jobs/{job['id']}").status_code == 404
    assert client.get(f"/api/jobs/{job['id']}/download/docx").status_code == 404
