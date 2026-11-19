"""Edit API + PDF export tests."""

from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path

import pymupdf
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


def create(client, **data):
    payload = {"formats": "docx,xlsx", "dpi": "300", "lang": "ind+eng"} | data
    response = client.post("/api/jobs", data=payload)
    assert response.status_code == 202, response.text
    return response.json()


def test_patch_text_reflects_everywhere(client):
    created = create(client, fixture="doc2_native_invoice")
    job = wait_for(client, created["id"])
    assert job["status"] == "done"
    page = client.get(f"/api/jobs/{job['id']}/pages/1/blocks").json()
    rev = page["rev"]
    block = next(b for b in page["blocks"] if b["text"])
    edited_text = "EDITED BLOCK TEXT 12345"
    response = client.patch(
        f"/api/jobs/{job['id']}/pages/1/blocks/{block['id']}",
        json={"rev": rev, "text": edited_text},
    )
    assert response.status_code == 200, response.text
    assert response.json()["rev"] == rev + 1

    model = client.get(f"/api/jobs/{job['id']}/download/json").json()
    assert any(b.get("text") == edited_text for p in model["pages"] for b in p["blocks"])

    docx = client.get(f"/api/jobs/{job['id']}/download/docx").content
    with zipfile.ZipFile(io.BytesIO(docx)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
    assert "EDITED BLOCK TEXT 12345" in xml


def test_patch_table_cell(client):
    created = create(client, fixture="doc2_native_invoice")
    job = wait_for(client, created["id"])
    page = client.get(f"/api/jobs/{job['id']}/pages/1/blocks").json()
    table = next(b for b in page["blocks"] if b["type"] == "table")
    new_rows = [row[:] for row in table["rows"]]
    new_rows[0][0] = "HEADER-EDITED"
    response = client.patch(
        f"/api/jobs/{job['id']}/pages/1/blocks/{table['id']}",
        json={"rev": page["rev"], "rows": new_rows},
    )
    assert response.status_code == 200, response.text
    model = client.get(f"/api/jobs/{job['id']}/download/json").json()
    table_model = next(
        b for p in model["pages"] for b in p["blocks"] if b["type"] == "table"
    )
    assert table_model["rows"][0][0] == "HEADER-EDITED"


def test_structure_add_and_delete(client):
    created = create(client, fixture="doc2_native_invoice")
    job = wait_for(client, created["id"])
    page = client.get(f"/api/jobs/{job['id']}/pages/1/blocks").json()
    rev = page["rev"]
    new_block = {
        "type": "para",
        "text": "ADDED PARAGRAPH XYZ",
        "bbox": [72, 700, 500, 720],
    }
    response = client.put(
        f"/api/jobs/{job['id']}/pages/1/blocks",
        json={"rev": rev, "blocks": page["blocks"] + [new_block]},
    )
    assert response.status_code == 200, response.text
    assert any(b.get("text") == "ADDED PARAGRAPH XYZ" for b in response.json()["blocks"])

    # delete the added block
    page2 = response.json()
    target_id = next(b["id"] for b in page2["blocks"] if b.get("text") == "ADDED PARAGRAPH XYZ")
    remaining = [b for b in page2["blocks"] if b["id"] != target_id]
    response = client.put(
        f"/api/jobs/{job['id']}/pages/1/blocks",
        json={"rev": page2["rev"], "blocks": remaining},
    )
    assert response.status_code == 200, response.text
    assert not any(b.get("text") == "ADDED PARAGRAPH XYZ" for b in response.json()["blocks"])


def test_stale_rev_conflict(client):
    created = create(client, fixture="doc2_native_invoice")
    job = wait_for(client, created["id"])
    page = client.get(f"/api/jobs/{job['id']}/pages/1/blocks").json()
    rev = page["rev"]
    # first edit succeeds
    client.patch(
        f"/api/jobs/{job['id']}/pages/1/blocks/{page['blocks'][0]['id']}",
        json={"rev": rev, "text": "first"},
    )
    # stale edit fails
    response = client.patch(
        f"/api/jobs/{job['id']}/pages/1/blocks/{page['blocks'][0]['id']}",
        json={"rev": rev, "text": "second"},
    )
    assert response.status_code == 409


def test_edit_rejected_before_done(client):
    pytest.skip("conversion finishes too quickly to test the not-done state reliably")


def test_pdf_downloads(client):
    created = create(client, fixture="doc2_native_invoice")
    job = wait_for(client, created["id"])
    assert job["status"] == "done"
    for fmt in ("pdf", "pdf-inplace"):
        response = client.get(f"/api/jobs/{job['id']}/download/{fmt}")
        assert response.status_code == 200, response.text
        assert response.content[:4] == b"%PDF"
        # edited text path: reflow pdf should contain the original text
        doc = pymupdf.open(stream=response.content, filetype="pdf")
        text = "".join(page.get_text() for page in doc)
        doc.close()
        assert len(text.strip()) > 0


def test_pdf_reflects_edits(client):
    created = create(client, fixture="doc2_native_invoice")
    job = wait_for(client, created["id"])
    page = client.get(f"/api/jobs/{job['id']}/pages/1/blocks").json()
    block = next(b for b in page["blocks"] if b["text"])
    marker = "PDF-EDIT-MARKER-98765"
    client.patch(
        f"/api/jobs/{job['id']}/pages/1/blocks/{block['id']}",
        json={"rev": page["rev"], "text": marker},
    )
    response = client.get(f"/api/jobs/{job['id']}/download/pdf")
    assert response.status_code == 200
    doc = pymupdf.open(stream=response.content, filetype="pdf")
    text = "".join(p.get_text() for p in doc)
    doc.close()
    assert marker in text