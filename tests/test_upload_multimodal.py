"""Tests for Multimodal File Uploads and Extraction."""

import io
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from sard.api.server import app
from sard.agent.tools.multimodal_tools import extract_multimodal_context


@pytest.fixture
def client():
    return TestClient(app)


def test_upload_valid_pdf(client):
    file_bytes = b"%PDF-1.4 sample pdf content for testing"
    response = client.post(
        "/api/upload",
        files={"file": ("historical_document.pdf", io.BytesIO(file_bytes), "application/pdf")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["attachment_id"].startswith("att_")
    assert data["filename"] == "historical_document.pdf"
    assert data["size_bytes"] == len(file_bytes)
    assert data["url"] is not None

    # Test downloading the uploaded attachment
    dl_resp = client.get(data["url"])
    assert dl_resp.status_code == 200
    assert dl_resp.content == file_bytes


def test_upload_valid_image(client):
    # Minimal 1x1 GIF or PNG
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
    response = client.post(
        "/api/upload",
        files={"file": ("alula_monument.png", io.BytesIO(png_bytes), "image/png")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["attachment_id"].startswith("att_")


def test_upload_unsupported_extension_rejected(client):
    response = client.post(
        "/api/upload",
        files={"file": ("malicious_script.exe", io.BytesIO(b"MZ..."), "application/octet-stream")},
    )
    assert response.status_code == 400
    assert "غير مدعومة" in response.json()["detail"]


def test_upload_empty_file_rejected(client):
    response = client.post(
        "/api/upload",
        files={"file": ("empty.txt", io.BytesIO(b""), "text/plain")},
    )
    assert response.status_code == 400


def test_upload_filename_traversal_sanitized(client):
    response = client.post(
        "/api/upload",
        files={"file": ("../../escape.txt", io.BytesIO(b"hello"), "text/plain")},
    )
    assert response.status_code == 200
    data = response.json()
    assert ".." not in data["filename"]
    assert "escape.txt" in data["filename"]


def test_multimodal_extractor_with_mock_attachment(tmp_path: Path):
    sample_img = tmp_path / "artifact.jpg"
    sample_img.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 100)

    items = extract_multimodal_context(
        f"صف هذه القطعة @{sample_img.name}",
        base_dir=tmp_path,
    )
    assert len(items) == 1
    assert items[0].file_type == "image"
    assert items[0].filename == sample_img.name
