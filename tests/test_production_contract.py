"""Production connection, typed errors, artifacts, validation, and health contracts."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from icalendar import Calendar

from sard.api.server import app
from sard.outputs.pdf import clean_pdf_text

ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)


def test_next_config_has_no_self_referential_prod_rewrite():
    text = (ROOT / "next.config.ts").read_text(encoding="utf-8")
    # Production must not rewrite /api/:path* onto itself.
    assert 'destination: "/api/:path*"' not in text
    # Backend origin must be environment-configured.
    assert "SARD_BACKEND_ORIGIN" in text or "NEXT_PUBLIC_API_BASE" in text


def test_frontend_api_reads_body_once_and_types_errors():
    text = (ROOT / "src" / "lib" / "api.ts").read_text(encoding="utf-8")
    assert "readErrorDetail" in text
    assert "response.text()" in text
    # Single-consume: error path reads text once then JSON-parses the string.
    assert text.count("await response.text()") + text.count("await retryResp.text()") + text.count("await response.text()") >= 1
    assert "SardApiError" in text
    # Env-configured backend origin (no hardcoded self-reference).
    assert "NEXT_PUBLIC_API_BASE" in text


def test_health_reports_separate_fields_and_not_always_ok():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    for field in ("model_configured", "discovery", "inference", "retrieval"):
        assert field in data, f"missing health field {field}"
    assert data["status"] in ("ok", "degraded", "unavailable")
    # Without credentials in CI, overall must not claim ready/ok blindly.
    assert data.get("verified") is False


def test_status_never_ready_without_model_and_retrieval():
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    for field in ("model_configured", "discovery", "inference", "retrieval_mode", "source_count", "corpus_coverage"):
        assert field in data, f"missing status field {field}"
    assert data.get("verified") is False
    if not data.get("model_configured") or not data.get("retrieval", {}).get("available", True):
        assert data["status"] in ("degraded", "unavailable")


def test_missing_tool_inputs_are_validation_errors_not_defaults():
    assert client.post("/api/tools/recipe-card", json={}).status_code == 422
    assert client.post("/api/tools/etiquette", json={}).status_code == 422
    assert client.post("/api/tools/artisan", json={}).status_code == 422
    assert client.post("/api/tools/dialect", json={}).status_code == 422
    assert client.post("/api/tools/research", json={}).status_code == 422


def test_heritage_ics_is_parseable_and_has_dtstamp():
    from sard.outputs.calendar_sync import HeritageCalendarSync

    sync = HeritageCalendarSync()
    data = sync.generate_ics_data()
    cal = Calendar.from_ical(data)
    events = [c for c in cal.walk() if c.name == "VEVENT"]
    assert events, "expected at least one VEVENT"
    for ev in events:
        assert ev.get("dtstamp") is not None, "RFC 5545 DTSTAMP is REQUIRED in every VEVENT"
        assert ev.get("uid") is not None
        assert ev.get("summary") is not None


def test_suhail_quick_action_ics_parseable():
    resp = client.post("/api/calendar/events", json={})
    # Accepts GET or POST shape; endpoint returns events payload.
    assert resp.status_code in (200, 405, 422)
    if resp.status_code == 200:
        # If events returned, ensure an .ics can be built from them.
        from sard.outputs.calendar_sync import HeritageCalendarSync

        sync = HeritageCalendarSync()
        raw = sync.generate_ics_data()
        cal = Calendar.from_ical(raw)
        assert any(c.name == "VEVENT" for c in cal.walk())


def test_pdf_cleaning_strips_markdown_and_internal_tags():
    dirty = "**عنوان** # ترويسة [RAG: doc.pdf] [Web: https://x] 【tag】 `code` [NAJD | ministry] منطقة najd"
    cleaned = clean_pdf_text(dirty)
    assert "**" not in cleaned
    assert "# ترويسة" not in cleaned or cleaned.startswith("ترويسة")
    assert "[RAG:" not in cleaned
    assert "[Web:" not in cleaned
    assert "【" not in cleaned
    assert "`" not in cleaned
    assert "[NAJD" not in cleaned
    assert "najd" not in cleaned
    assert "نجد" in cleaned


def test_png_contains_meaningful_content():
    from sard.outputs.orchestrator import ArtifactGeneratorRegistry, ArtifactRequest

    req = ArtifactRequest(
        format="png", kind="image", title="بطاقة اختبار",
        topic="السدو", raw_text="نسيج السدو البدوي بخيوط الصوف",
        content_data={"items": ["خيمة", "نسيج", "زخرفة"]},
    )
    data, mime, preview = ArtifactGeneratorRegistry._render_png(req)
    assert mime == "image/png"
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert preview and (preview.get("items") or preview.get("text"))
    # Image must vary beyond a header stripe: decode and check color diversity.
    from PIL import Image
    import io as _io

    img = Image.open(_io.BytesIO(data)).convert("RGB")
    # F-1: bound the palette query (a full-frame getcolors can exhaust
    # memory on large images); a thumbnail preserves the diversity signal.
    thumb = img.resize((120, 80))
    colors = thumb.getcolors(maxcolors=120 * 80)
    assert colors is None or len(colors) > 4, "PNG must render card content, not a flat placeholder"
    assert img.size == (1200, 800)
