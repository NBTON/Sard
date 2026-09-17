"""DG-5: standalone tool download URLs must round-trip through the artifact store.

Every tool endpoint that returns a ``download_url`` must serve bytes with
the correct MIME on GET. Unknown inputs keep their honest failure shapes
(no URL) instead of fake success.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from sard.api.server import app
from sard.outputs.orchestrator import FileSystemArtifactStore, set_artifact_store


JAREESH = "\u0627\u0644\u062c\u0631\u064a\u0634"
CURATED_QUERY = "\u0627\u0644\u0645\u0631\u0628\u0639\u0627\u0646\u064a\u0629"


def _client(tmp_path: Path) -> TestClient:
    set_artifact_store(FileSystemArtifactStore(tmp_path))
    return TestClient(app)


def _roundtrip(client: TestClient, url: str, signature: bytes, mime: str) -> int:
    assert url is not None and url.startswith("/api/artifacts/")
    resp = client.get(url)
    assert resp.status_code == 200, url
    assert resp.content.startswith(signature), url
    assert mime in resp.headers.get("content-type", ""), url
    assert "attachment" in resp.headers.get("content-disposition", "")
    return len(resp.content)


def test_recipe_card_download_roundtrip(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/api/tools/recipe-card", json={"item_name": JAREESH})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    size = _roundtrip(client, body["download_url"], b"%PDF", "application/pdf")
    assert size > 500


def test_presentation_download_roundtrip(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/api/tools/presentation", json={
        "topic": "Diriyah heritage",
        "overview_text": "Overview of Diriyah heritage with enough substance.",
        "key_takeaways": ["Takeaway one", "Takeaway two"],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    size = _roundtrip(client, body["download_url"], b"PK",
                      "application/vnd.openxmlformats")
    assert size > 500


def test_greeting_card_download_roundtrip(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/api/tools/greeting-card", json={
        "occasion": "foundation_day", "recipient_name": "Sara",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    size = _roundtrip(client, body["download_url"], b"%PDF", "application/pdf")
    assert size > 500


def test_memoir_download_roundtrip(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/api/tools/memoir", json={
        "family_name": "test family",
        "raw_notes": [{"topic": "childhood", "content": "old town memories",
                       "era": "past"}],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    size = _roundtrip(client, body["download_url"], b"%PDF", "application/pdf")
    assert size > 500


def test_curated_calendar_tool_download_roundtrip(tmp_path):
    from sard.agent.tools.cultural_agentic_tools import tool_sync_heritage_calendar

    client = _client(tmp_path)
    res = tool_sync_heritage_calendar(query=CURATED_QUERY)
    assert res["success"] is True
    assert res["filename"] is not None and res["filename"].endswith(".ics")
    size = _roundtrip(client, res["download_url"], b"BEGIN:VCALENDAR",
                      "text/calendar")
    assert size > 100


def test_unknown_recipe_stays_honest_without_url(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/api/tools/recipe-card", json={"item_name": "test dish"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body.get("download_url") is None
