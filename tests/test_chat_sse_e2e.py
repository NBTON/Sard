"""End-to-End SSE Chat Tests for Sard."""

import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from sard.api.server import app
from sard.outputs.orchestrator import FileSystemArtifactStore, set_artifact_store


@pytest.fixture
def client(tmp_path: Path):
    store = FileSystemArtifactStore(root_dir=tmp_path)
    set_artifact_store(store)
    return TestClient(app), store


def parse_sse_events(sse_text: str):
    events = []
    normalized = sse_text.replace("\r\n", "\n")
    blocks = normalized.strip().split("\n\n")
    for block in blocks:
        if not block.strip():
            continue
        ev_type = "message"
        data_lines = []
        for line in block.split("\n"):
            line = line.strip()
            if line.startswith("event:"):
                ev_type = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if data_lines:
            try:
                data = json.loads("\n".join(data_lines))
            except Exception:
                data = "\n".join(data_lines)
            events.append({"event": ev_type, "data": data})
    return events


def test_chat_sse_explicit_pdf_request(client):
    test_client, store = client
    response = test_client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "أنشئ لي ملف PDF عن تاريخ نجد"}]},
    )
    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")

    events = parse_sse_events(response.text)
    event_types = [e["event"] for e in events]

    assert "status" in event_types
    assert "artifacts" in event_types
    assert "done" in event_types

    # Verify artifacts payload
    art_event = next(e for e in events if e["event"] == "artifacts")
    artifacts = art_event["data"]["artifacts"]
    assert len(artifacts) >= 1

    pdf_art = next(a for a in artifacts if (a.get("format") == "pdf" or a.get("type") == "pdf"))
    assert pdf_art["status"] == "created"
    assert pdf_art["download_url"] is not None

    # Verify download of advertised URL
    dl_resp = test_client.get(pdf_art["download_url"])
    assert dl_resp.status_code == 200
    assert dl_resp.content.startswith(b"%PDF")
    assert len(dl_resp.content) > 500

    # Verify done event metadata
    done_event = next(e for e in events if e["event"] == "done")
    assert done_event["data"]["artifacts_count"] >= 1


def test_chat_sse_pptx_request(client):
    test_client, store = client
    response = test_client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "صمم عرض بوربوينت عن العمارة النجدية"}]},
    )
    assert response.status_code == 200

    events = parse_sse_events(response.text)
    art_events = [e for e in events if e["event"] == "artifacts"]
    assert len(art_events) > 0

    artifacts = art_events[0]["data"]["artifacts"]
    pptx_art = next(a for a in artifacts if (a.get("format") == "pptx" or a.get("type") == "pptx"))
    assert pptx_art["status"] == "created"

    dl_resp = test_client.get(pptx_art["download_url"])
    assert dl_resp.status_code == 200
    assert dl_resp.content.startswith(b"PK\x03\x04")


def test_chat_sse_itinerary_pdf_and_ics(client):
    test_client, store = client
    response = test_client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "برنامج رحلة ثلاثة أيام في الرياض بصيغة PDF وICS"}]},
    )
    assert response.status_code == 200

    events = parse_sse_events(response.text)
    art_events = [e for e in events if e["event"] == "artifacts"]
    assert len(art_events) > 0

    artifacts = art_events[0]["data"]["artifacts"]
    formats = [a.get("format") or a.get("type") for a in artifacts]
    assert "pdf" in formats
    assert "ics" in formats


def test_chat_sse_empty_text_with_attachments_works(client):
    test_client, store = client
    response = test_client.post(
        "/api/chat",
        json={
            "messages": [],
            "query": "",
            "attachments": [
                {
                    "attachment_id": "att_sample_1",
                    "filename": "heritage_guide.pdf",
                    "mime_type": "application/pdf",
                    "size_bytes": 1024,
                }
            ],
        },
    )
    assert response.status_code == 200
    events = parse_sse_events(response.text)
    event_types = [e["event"] for e in events]
    assert "delta" in event_types or "status" in event_types
    assert "done" in event_types
