"""Tests for Generators, ArtifactStore, and Retrieval Endpoints."""

import hashlib
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from sard.api.server import app
from sard.outputs.orchestrator import (
    ArtifactOrchestrator,
    ArtifactRequest,
    FileSystemArtifactStore,
    set_artifact_store,
)


@pytest.fixture
def test_client(tmp_path: Path):
    store = FileSystemArtifactStore(root_dir=tmp_path)
    set_artifact_store(store)
    return TestClient(app), store, tmp_path


def test_pdf_report_generation_and_storage(test_client):
    client, store, root = test_client
    orchestrator = ArtifactOrchestrator(store)

    req = ArtifactRequest(
        format="pdf",
        kind="document",
        title="تاريخ العمارة النجدية",
        topic="العمارة النجدية",
        raw_text="تتميز العمارة النجدية باستخدام الطين واللبن والزخارف الجصية المثلثية.",
    )

    res = orchestrator.generate_artifact(req)
    assert res.status == "created"
    assert res.download_url is not None
    assert res.size_bytes > 0
    assert res.mime_type == "application/pdf"

    # Verify physical file
    target = store.get_file_path(res.filename)
    assert target is not None
    assert target.exists()

    data = target.read_bytes()
    assert data.startswith(b"%PDF")
    assert hashlib.sha256(data).hexdigest() == res.checksum

    # Verify HTTP download
    resp = client.get(res.download_url)
    assert resp.status_code == 200
    assert "application/pdf" in resp.headers.get("content-type", "")
    assert res.filename in resp.headers.get("content-disposition", "")


def test_docx_report_generation_and_storage(test_client):
    client, store, root = test_client
    orchestrator = ArtifactOrchestrator(store)

    req = ArtifactRequest(
        format="docx",
        kind="document",
        title="تقرير العمارة العسيرية",
        topic="العمارة العسيرية",
        raw_text="يشتهر فن القط العسيري في عسير بتزيين جدران المنازل التراثية.",
    )

    res = orchestrator.generate_artifact(req)
    assert res.status == "created"
    assert res.download_url is not None
    assert res.size_bytes > 0
    assert "wordprocessingml" in res.mime_type

    target = store.get_file_path(res.filename)
    assert target is not None
    assert target.exists()

    data = target.read_bytes()
    assert data.startswith(b"PK\x03\x04")  # Valid ZIP / OOXML archive header

    resp = client.get(res.download_url)
    assert resp.status_code == 200
    assert "wordprocessingml" in resp.headers.get("content-type", "")


def test_pptx_presentation_generation_and_storage(test_client):
    client, store, root = test_client
    orchestrator = ArtifactOrchestrator(store)

    req = ArtifactRequest(
        format="pptx",
        kind="presentation",
        title="عرض يوم التأسيس",
        topic="يوم التأسيس السعودي",
        raw_text="إيجاز ثقافي عن تأسيس الدولة السعودية الأولى عام 1727م.",
    )

    res = orchestrator.generate_artifact(req)
    assert res.status == "created"
    assert res.download_url is not None
    assert res.size_bytes > 0

    target = store.get_file_path(res.filename)
    assert target is not None
    assert target.exists()

    data = target.read_bytes()
    assert data.startswith(b"PK\x03\x04")

    resp = client.get(res.download_url)
    assert resp.status_code == 200
    assert "presentation" in resp.headers.get("content-type", "")


def test_ics_calendar_generation_and_storage(test_client):
    client, store, root = test_client
    orchestrator = ArtifactOrchestrator(store)

    req = ArtifactRequest(
        format="ics",
        kind="calendar",
        title="مواسم التقويم الثقافي",
        topic="سهيل",
    )

    res = orchestrator.generate_artifact(req)
    assert res.status == "created"
    assert res.download_url is not None
    assert res.size_bytes > 0

    target = store.get_file_path(res.filename)
    assert target is not None
    assert target.exists()

    data = target.read_bytes()
    assert data.startswith(b"BEGIN:VCALENDAR")
    assert b"SUMMARY:" in data
    assert b"END:VCALENDAR" in data

    resp = client.get(res.download_url)
    assert resp.status_code == 200
    assert "text/calendar" in resp.headers.get("content-type", "")


def test_path_traversal_protection(test_client):
    client, store, root = test_client
    with pytest.raises(ValueError):
        store.store_bytes("bad", "../../escaped.txt", b"malicious", "text/plain")

    # Client GET traversal check
    resp = client.get("/api/artifacts/../../etc/passwd")
    assert resp.status_code in (400, 404)
