import os
from sard.agent.events import sanitize_text

def test_sanitize_redacts_api_key():
    t = "OPENROUTER_API_KEY=sk-or-v1-1234567890abcdef1234567890"
    out = sanitize_text(t)
    assert "sk-or" not in out
    assert "[REDACTED]" in out

def test_env_example_has_placeholder():
    path = os.path.join(os.path.dirname(__file__), "..", ".env.example")
    text = open(path, encoding="utf-8").read()
    assert "OPENROUTER_API_KEY=" in text
    # placeholder must be empty, not real key
    for line in text.splitlines():
        if line.startswith("OPENROUTER_API_KEY="):
            assert line.strip() == "OPENROUTER_API_KEY="
            break
    else:
        assert False, "OPENROUTER_API_KEY not found"

def test_no_secret_in_logs_simulation(monkeypatch):
    # ensure sanitize works for bearer
    out = sanitize_text("Authorization: Bearer nvapi-secret12345678901234567890")
    assert "secret" not in out.lower() or "[REDACTED]" in out

def test_parallel_key_is_empty_and_placeholder_empty():
    path = os.path.join(os.path.dirname(__file__), "..", ".env.example")
    text = open(path, encoding="utf-8").read()
    assert "PARALLEL_API_KEY=" in text
    for line in text.splitlines():
        if line.startswith("PARALLEL_API_KEY="):
            assert line.strip() == "PARALLEL_API_KEY="
            break
    else:
        assert False, "PARALLEL_API_KEY placeholder not found"

def test_sanitize_redacts_generic_tokens():
    out = sanitize_text("PARALLEL_API_KEY=sampletoken12345678901234567890")
    assert "sampletoken" not in out
    assert "[REDACTED]" in out

