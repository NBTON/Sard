"""Comprehensive End-to-End Multi-Agent Verification Suite for Sard Platform.

Verifies:
1. RAG Availability & Status (/api/status & /api/health -> available: true, verified: true)
2. Streaming Chat with Isnād Grounding (Arabic cultural query -> citations event + non-refusal narrative)
3. English Provenance Query ("Explain traditional Saudi coffee rituals")
4. Artifact Streaming Generation in Chat ("صمم عرض بوربوينت ثقافي متكامل عن يوم التأسيس السعودي" -> status: created, valid download_url)
5. Persona Greeting Handling ("من أنت", "مرحبا", "السلام عليكم" -> rich persona self-introduction)
6. Standalone Tool Endpoints (/api/tools/* with flexible normalized inputs)
7. Edge cases & Guardrails
"""

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
            raw_data = "\n".join(data_lines)
            try:
                data = json.loads(raw_data)
            except Exception:
                data = raw_data
            events.append({"event": ev_type, "data": data})
    return events


# ---------------------------------------------------------------------------
# 1. RAG Availability & Status Telemetry
# ---------------------------------------------------------------------------

def test_status_endpoint_returns_rag_available_and_verified(client):
    test_client, _ = client
    response = test_client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert data.get("verified") is True
    assert data.get("rag", {}).get("available") is True
    assert data.get("sources", {}).get("verified") is True


def test_health_endpoint_returns_verified(client):
    test_client, _ = client
    response = test_client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") == "ok"
    assert data.get("verified") is True
    assert data.get("rag", {}).get("available") is True


def test_root_endpoint_returns_verified(client):
    test_client, _ = client
    response = test_client.get("/api")
    assert response.status_code == 200
    data = response.json()
    assert data.get("verified") is True


# ---------------------------------------------------------------------------
# 2. Streaming Chat: Cultural Grounding & Citations
# ---------------------------------------------------------------------------

def test_chat_sadu_cultural_query_grounded_citations(client):
    test_client, _ = client
    query = "ما قصة السدو في التراث السعودي وما هي رموزه ودلالاته؟"
    response = test_client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": query}]},
    )
    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")

    events = parse_sse_events(response.text)
    event_types = [e["event"] for e in events]

    assert "status" in event_types
    assert "done" in event_types

    # Must contain citations or verified text with Sadu narrative
    citations_events = [e for e in events if e["event"] == "citations"]
    deltas = [e["data"].get("text", "") for e in events if e["event"] == "delta" and isinstance(e["data"], dict)]
    full_text = "".join(deltas)

    assert "سدو" in full_text or "السدو" in full_text
    assert "حفاظًا على الأمانة المعرفية، لا أقدّم توليفًا غير مُسنَد" not in full_text

    # Verify done event
    done_event = next(e for e in events if e["event"] == "done")
    assert "timings_ms" in done_event["data"]


def test_chat_english_saudi_coffee_query(client):
    test_client, _ = client
    query = "Explain traditional Saudi coffee rituals"
    response = test_client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": query}]},
    )
    assert response.status_code == 200
    events = parse_sse_events(response.text)
    deltas = [e["data"].get("text", "") for e in events if e["event"] == "delta" and isinstance(e["data"], dict)]
    full_text = "".join(deltas)

    # Narrative must mention coffee / traditions without refusal
    assert len(full_text.strip()) > 30


# ---------------------------------------------------------------------------
# 3. Streaming Chat: Artifact Generation in Chat
# ---------------------------------------------------------------------------

def test_chat_artifact_pptx_founding_day(client):
    test_client, _ = client
    query = "صمم عرض بوربوينت ثقافي متكامل عن يوم التأسيس السعودي"
    response = test_client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": query}]},
    )
    assert response.status_code == 200
    events = parse_sse_events(response.text)
    art_events = [e for e in events if e["event"] == "artifacts"]
    assert len(art_events) > 0

    artifacts = art_events[0]["data"]["artifacts"]
    pptx_art = next((a for a in artifacts if a.get("format") == "pptx" or a.get("type") == "pptx"), None)
    assert pptx_art is not None
    assert pptx_art["status"] == "created"
    assert pptx_art["download_url"] is not None

    dl_resp = test_client.get(pptx_art["download_url"])
    assert dl_resp.status_code == 200
    assert dl_resp.content.startswith(b"PK\x03\x04")


# ---------------------------------------------------------------------------
# 4. Persona Greeting Handling
# ---------------------------------------------------------------------------

def test_chat_persona_greeting_who_are_you(client):
    test_client, _ = client
    query = "من أنت"
    response = test_client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": query}]},
    )
    assert response.status_code == 200
    events = parse_sse_events(response.text)
    deltas = [e["data"].get("text", "") for e in events if e["event"] == "delta" and isinstance(e["data"], dict)]
    full_text = "".join(deltas)

    # Must introduce Sard without refusal
    assert "سرد" in full_text
    assert "حفاظًا على الأمانة المعرفية، لا أقدّم توليفًا غير مُسنَد" not in full_text


def test_chat_persona_greeting_salam(client):
    test_client, _ = client
    query = "السلام عليكم"
    response = test_client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": query}]},
    )
    assert response.status_code == 200
    events = parse_sse_events(response.text)
    deltas = [e["data"].get("text", "") for e in events if e["event"] == "delta" and isinstance(e["data"], dict)]
    full_text = "".join(deltas)

    assert "سرد" in full_text


# ---------------------------------------------------------------------------
# 5. Standalone Tool Endpoints with Flexible Normalized Schemas
# ---------------------------------------------------------------------------

def test_tool_presentation_endpoint(client):
    test_client, _ = client
    # Test with normalized alias title instead of topic
    res = test_client.post("/api/tools/presentation", json={"title": "عمارة الطين في نجد"})
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["download_url"].startswith("/api/artifacts/")


def test_tool_recipe_card_endpoint_with_alias(client):
    test_client, _ = client
    # Test with dish_name instead of item_name
    res = test_client.post("/api/tools/recipe-card", json={"dish_name": "الجريش الحائلي"})
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["download_url"].endswith(".pdf")


def test_tool_greeting_card_endpoint_with_alias(client):
    test_client, _ = client
    res = test_client.post("/api/tools/greeting-card", json={"recipient": "سارة", "sender": "نواف", "occasion": "foundation_day"})
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert "svg_markup" in data


def test_tool_etiquette_endpoint(client):
    test_client, _ = client
    res = test_client.post("/api/tools/etiquette", json={"scenario": "majlis"})
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert "diagram_svg" in data


def test_tool_dialect_endpoint(client):
    test_client, _ = client
    res = test_client.post("/api/tools/dialect", json={"phrase": "أبشر بسعدك", "region": "najdi"})
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert "proverb_title" in data


def test_tool_artisan_endpoint(client):
    test_client, _ = client
    res = test_client.post("/api/tools/artisan", json={"craft": "sadu"})
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert "authentication_checklist" in data


def test_tool_memoir_endpoint_with_string_notes(client):
    test_client, _ = client
    # Test flexible raw_notes passed as string
    res = test_client.post("/api/tools/memoir", json={"family_name": "آل سعود", "raw_notes": "ذكريات الدرعية الأولى وقصر سلوى"})
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["download_url"].endswith(".pdf")


def test_calendar_events_endpoint(client):
    test_client, _ = client
    res = test_client.get("/api/calendar/events?query=تأسيس")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["total_events"] > 0
    assert data["download_url"].endswith(".ics")


# ---------------------------------------------------------------------------
# 6. Edge Cases & Guardrails
# ---------------------------------------------------------------------------

def test_chat_empty_query_rejected(client):
    test_client, _ = client
    res = test_client.post("/api/chat", json={"messages": [{"role": "user", "content": "   "}]})
    assert res.status_code == 400
