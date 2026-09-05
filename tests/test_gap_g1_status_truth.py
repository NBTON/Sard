"""G1: /api/status truthful model_configured (no settings.api_key AttributeError)."""
from fastapi.testclient import TestClient
from sard.api.server import app, _is_model_configured

def test_gap_g1_status_model_configured_truthful(monkeypatch):
    # Provider nvidia with key -> configured True
    monkeypatch.setenv("MODEL_PROVIDER", "nvidia")
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key-123")
    assert _is_model_configured("nvidia") is True
    # No keys, explicit provider -> False
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_CHAT_BASE_URL", raising=False)
    assert _is_model_configured("nvidia") is False
    # Auto with no keys -> False
    for k in ["GEMINI_API_KEY","GOOGLE_API_KEY","OPENAI_API_KEY","ANTHROPIC_API_KEY","OPENROUTER_API_KEY","NVIDIA_API_KEY","NVIDIA_CHAT_BASE_URL"]:
        monkeypatch.delenv(k, raising=False)
    assert _is_model_configured("") is False
    assert _is_model_configured("auto") is False

def test_gap_g1_status_endpoint_has_storage_and_model_fields():
    c = TestClient(app)
    r = c.get("/api/status")
    assert r.status_code == 200
    j = r.json()
    assert "model_configured" in j
    assert "storage" in j
    assert j["storage"].get("mode") in ("durable_blob","local_filesystem","ephemeral_unconfigured","unknown")
    assert j["status"] in ("ready","degraded","unavailable")
