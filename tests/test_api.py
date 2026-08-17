"""Tests for the Sard FastAPI backend server endpoints."""

import pytest
from fastapi.testclient import TestClient

from sard.api.server import app


@pytest.fixture
def client():
    return TestClient(app)


def test_health_endpoint(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "sard-agent"
    assert "rag" in data


def test_status_endpoint(client):
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert "status_label" in data
    assert "model" in data
    assert "rag" in data
    assert "moc_branding" in data


def test_corpus_endpoint(client):
    response = client.get("/api/corpus")
    assert response.status_code == 200
    data = response.json()
    assert "topics" in data
    assert isinstance(data["topics"], list)


def test_chat_endpoint_empty_query_fails(client):
    response = client.post("/api/chat", json={"messages": [], "query": ""})
    assert response.status_code == 400


def test_chat_endpoint_streaming_events(client):
    response = client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "ما هي حرفة تجفيف الروبيان في تاروت؟"}]},
    )
    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")
    content = response.text
    assert "event: status" in content
    assert "event: delta" in content
    assert "event: done" in content


def test_artifact_download_not_found(client):
    response = client.get("/api/artifacts/non_existent_file_123.pdf")
    assert response.status_code == 404
