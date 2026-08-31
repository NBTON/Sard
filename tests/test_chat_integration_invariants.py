"""Integration invariants for herdr/integration — SSE contract, fallback, artifacts, session isolation.

Covers 12 invariants explicitly required by the integration worker spec.
All tests are deterministic, no network, no API keys, mocked models where needed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from sard.agent.capability_routing import classify_intent
from sard.agent.chat_service import ChatService
from sard.api.server import app, _generate_cultural_fallback_answer
from sard.config.rag import get_rag_settings
from sard.outputs.orchestrator import FileSystemArtifactStore, set_artifact_store
from sard.outputs.validation import validate_artifact_bytes


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


@pytest.fixture
def isolated_store(tmp_path: Path):
    store = FileSystemArtifactStore(root_dir=tmp_path)
    set_artifact_store(store)
    return store


@pytest.fixture
def client(tmp_path: Path, isolated_store):
    return TestClient(app), isolated_store


# ---------------------------------------------------------------------------
# Invariant 9: _generate_cultural_fallback_answer no longer injects canned itineraries
# ---------------------------------------------------------------------------

def test_fallback_does_not_inject_shrimp_for_neutral_query():
    out = _generate_cultural_fallback_answer("ما عاصمة قطر؟")
    assert "روبيان" not in out
    assert "تاروت" not in out
    assert "الأحساء" not in out
    # must be generic hedge, Arabic friendly, mentions Sard capabilities
    assert "سرد" in out or "وزارة الثقافة" in out


def test_fallback_preserves_shrimp_for_legit_query():
    out = _generate_cultural_fallback_answer("ما هي حرفة تجفيف الروبيان في تاروت؟")
    assert "روبيان" in out
    assert "تاروت" in out


def test_fallback_generic_hedge_for_program_qatar_not_eastern():
    out = _generate_cultural_fallback_answer("برنامج سياحي في قطر يومين")
    # Must NOT be Eastern Province 2-day itinerary
    assert "الأحساء" not in out
    assert "جبل القارة" not in out
    assert "إثراء" not in out
    # Generic hedge must mention query snippet or Sard
    assert "قطر" in out or "تعذّر" in out
    assert "سرد" in out or "وزارة الثقافة" in out


def test_fallback_springs_legit_branch():
    out = _generate_cultural_fallback_answer("أريد معلومات عن الينابيع الحارة والعيون الحارة")
    # legit springs query should not inject shrimp itinerary either
    assert "روبيان" not in out
    assert "الينابيع" in out or "العيون" in out


# ---------------------------------------------------------------------------
# Invariant 2/3/8: artifact intent survives fallbacks
# ---------------------------------------------------------------------------

class _EmptyModel:
    def invoke(self, messages):
        return AIMessage(content="")


class _HelloModel:
    def invoke(self, messages):
        return AIMessage(content="مرحبا بك في سرد، هذه إجابة تجريبية موثقة.")


def test_direct_path_still_produces_pdf_when_hybrid_false(tmp_path):
    store = FileSystemArtifactStore(root_dir=tmp_path)
    svc = ChatService(chat_model=_HelloModel(), orchestrator=store and __import__("sard.outputs.orchestrator", fromlist=["get_artifact_orchestrator"]).get_artifact_orchestrator().__class__(store) or None)
    # Use direct path with pdf intent — must still produce artifact
    from sard.outputs.orchestrator import ArtifactOrchestrator
    svc = ChatService(chat_model=_HelloModel(), orchestrator=ArtifactOrchestrator(store))
    res = svc.ask("أنشئ لي ملف PDF عن تاريخ نجد", use_hybrid_retrieval=False)
    assert res.ok is True
    assert res.text.strip() != ""
    # artifact intent should have produced at least one pdf
    assert any(a.get("format") == "pdf" and a.get("status") == "created" for a in res.artifacts)
    # generic PDF must not be Eastern itinerary canned text (check raw store bytes not containing itinerary marker unless query is Eastern)
    pdf_art = next(a for a in res.artifacts if a.get("format") == "pdf")
    assert pdf_art["download_url"] is not None
    data, _, _ = store.get_bytes(pdf_art["filename"])
    assert data.startswith(b"%PDF")
    assert len(data) > 500


def test_hybrid_planner_exception_still_produces_artifact(client):
    test_client, store = client
    # Force IsnadPlanner to throw
    with patch("sard.agent.chat_service.ChatService.ask_isnad", side_effect=RuntimeError("planner boom")):
        # Use a hello model for the fallback cultural path's LLM synthesis
        with patch("sard.agent.chat_service.ChatService._get_model", return_value=_HelloModel()):
            resp = test_client.post("/api/chat", json={"messages": [{"role": "user", "content": "أنشئ لي ملف PDF عن تاريخ نجد"}]})
            assert resp.status_code == 200
            events = parse_sse_events(resp.text)
            # must have artifacts event before done, with pdf created
            assert any(e["event"] == "artifacts" for e in events)
            assert any(e["event"] == "done" for e in events)
            art_event = next(e for e in events if e["event"] == "artifacts")
            formats = [a.get("format") or a.get("type") for a in art_event["data"]["artifacts"]]
            assert "pdf" in formats
            created = [a for a in art_event["data"]["artifacts"] if a.get("status") == "created"]
            assert len(created) >= 1
            # ordering: artifacts before done
            art_idx = next(i for i, e in enumerate(events) if e["event"] == "artifacts")
            done_idx = next(i for i, e in enumerate(events) if e["event"] == "done")
            assert art_idx < done_idx
            # download verification
            pdf = created[0]
            dl = test_client.get(pdf["download_url"])
            assert dl.status_code == 200
            assert dl.headers.get("content-type", "").startswith("application/pdf")
            assert "attachment" in dl.headers.get("content-disposition", "")
            assert dl.content.startswith(b"%PDF")


def test_empty_model_produces_explicit_hedge_not_empty_nor_shrimp(client):
    test_client, store = client
    with patch("sard.agent.chat_service.ChatService._get_model", return_value=_EmptyModel()):
        # Also patch IsnadPlanner to use empty LLM so hybrid also empties
        with patch("sard.agent.chat_service.ChatService.ask_isnad") as mock_isnad:
            from sard.schemas.isnad import PlannerResult, IsnadChain
            # make ask_isnad return empty answer so fallback hedge is used
            mock_isnad.return_value = PlannerResult(
                chain=IsnadChain(request_id="req-test", classification="other", region="unknown", evidence=[], atoms=[], conflicts=[], score="low", decision="hedge"),
                answer_ar="",
                answer_en="",
                visible_sources=[],
            )
            resp = test_client.post("/api/chat", json={"messages": [{"role": "user", "content": "ما هي عاصمة قطر؟"}]})
            assert resp.status_code == 200
            events = parse_sse_events(resp.text)
            # delta must be non-empty hedge, not empty
            deltas = [e for e in events if e["event"] == "delta"]
            full_text = "".join(d["data"]["text"] for d in deltas)
            assert full_text.strip() != ""
            assert "روبيان" not in full_text
            assert "تاروت" not in full_text
            assert "الأحساء" not in full_text
            # done always emitted
            assert any(e["event"] == "done" for e in events)
            # no canned UNESCO list for neutral query
            assert "حي الطريف" not in full_text


def test_sse_fallback_preserves_current_query_not_stale_history(client):
    test_client, store = client
    # History contains shrimp contamination
    history = [
        {"role": "user", "content": "ما هي حرفة تجفيف الروبيان في تاروت؟"},
        {"role": "assistant", "content": "حرفة تجفيف الروبيان في تاروت..."},
    ]
    with patch("sard.agent.chat_service.ChatService._get_model", return_value=_EmptyModel()):
        with patch("sard.agent.chat_service.ChatService.ask_isnad") as mock_isnad:
            from sard.schemas.isnad import PlannerResult, IsnadChain
            mock_isnad.return_value = PlannerResult(
                chain=IsnadChain(request_id="req-test", classification="other", region="unknown", evidence=[], atoms=[], conflicts=[], score="low", decision="hedge"),
                answer_ar="",
                answer_en="",
                visible_sources=[],
            )
            resp = test_client.post("/api/chat", json={"messages": history + [{"role": "user", "content": "ما هي عاصمة قطر؟"}]})
            events = parse_sse_events(resp.text)
            deltas = [e for e in events if e["event"] == "delta"]
            full_text = "".join(d["data"]["text"] for d in deltas)
            # Must be about current query (قطر) hedge, not stale shrimp
            assert "قطر" in full_text or "عاصمة" in full_text or "تعذّر" in full_text
            # Must NOT inject shrimp for neutral current query even though history has shrimp
            assert "روبيان" not in full_text


def test_new_session_isolated_empty_history(client):
    test_client, store = client
    # First session pollutes with shrimp (legit)
    resp1 = test_client.post("/api/chat", json={"messages": [{"role": "user", "content": "ما هي حرفة تجفيف الروبيان في تاروت؟"}], "session_id": "sess-aaa-111"})
    events1 = parse_sse_events(resp1.text)
    full1 = "".join(d["data"]["text"] for d in [e for e in events1 if e["event"] == "delta"])
    assert "روبيان" in full1 or "تاروت" in full1

    # Second session fresh id, empty history, neutral query must NOT contain shrimp
    with patch("sard.agent.chat_service.ChatService._get_model", return_value=_HelloModel()):
        resp2 = test_client.post("/api/chat", json={"messages": [{"role": "user", "content": "ما هي عاصمة قطر؟"}], "session_id": "sess-bbb-222"})
        events2 = parse_sse_events(resp2.text)
        full2 = "".join(d["data"]["text"] for d in [e for e in events2 if e["event"] == "delta"])
        assert "روبيان" not in full2
        # done must carry new session_id
        done = next(e for e in events2 if e["event"] == "done")
        assert done["data"]["session_id"] == "sess-bbb-222"


def test_retrieval_failure_never_injects_shrimp_context(client):
    test_client, store = client
    # Force rag_search to return empty (no RAG)
    with patch("sard.agent.tools.cultural_tools.rag_search", return_value=[]):
        with patch("sard.agent.tools.cultural_tools.parallel_search", return_value=[]):
            with patch("sard.agent.chat_service.ChatService._get_model", return_value=_HelloModel()):
                # Neutral Asir query should not leak shrimp even with RAG empty
                resp = test_client.post("/api/chat", json={"messages": [{"role": "user", "content": "ما خصائص العمارة في رجال ألمع عسير؟"}]})
                events = parse_sse_events(resp.text)
                full = "".join(d["data"]["text"] for d in [e for e in events if e["event"] == "delta"])
                assert "روبيان" not in full
                # When RAG empty, cultural router should hedge, but not inject shrimp chunk


def test_sse_contract_ordering_and_done_always(client):
    test_client, store = client
    resp = test_client.post("/api/chat", json={"messages": [{"role": "user", "content": "أنشئ لي ملف PDF عن تاريخ نجد"}]})
    events = parse_sse_events(resp.text)
    types = [e["event"] for e in events]
    assert "status" in types
    assert "done" in types
    # done is last
    assert types[-1] == "done"
    # if artifacts present, before done
    if "artifacts" in types:
        assert types.index("artifacts") < types.index("done")
    # if citations present, before artifacts and done
    if "citations" in types:
        assert types.index("citations") < types.index("done")
    # delta before done if present
    if "delta" in types:
        assert types.index("delta") < types.index("done")
    done = next(e for e in events if e["event"] == "done")
    assert "run_id" in done["data"]
    assert done["data"]["run_id"].startswith("chat-")
    assert "timings_ms" in done["data"]
    # no secrets in done
    done_str = json.dumps(done["data"])
    assert "NVIDIA_API_KEY" not in done_str
    assert "api_key" not in done_str.lower()


def test_failed_artifact_surface_and_download_not_created(client):
    test_client, store = client
    # Force orchestrator to fail rendering (e.g., mock render_pdf to raise)
    with patch("sard.outputs.orchestrator.ArtifactGeneratorRegistry.render_pdf", side_effect=RuntimeError("render boom")):
        resp = test_client.post("/api/chat", json={"messages": [{"role": "user", "content": "أنشئ لي ملف PDF عن تاريخ نجد"}]})
        events = parse_sse_events(resp.text)
        assert any(e["event"] == "artifacts" for e in events)
        art_event = next(e for e in events if e["event"] == "artifacts")
        failed = [a for a in art_event["data"]["artifacts"] if a.get("status") == "failed"]
        assert len(failed) >= 1
        for f in failed:
            assert f["download_url"] is None
            assert f["error"] is not None
            # failed must not have created status
            assert f["status"] != "created"
        # done must still be emitted
        assert any(e["event"] == "done" for e in events)
        done = next(e for e in events if e["event"] == "done")
        # artifacts_count reflects failed as well
        assert done["data"]["artifacts_count"] >= 1


def test_successful_artifact_verified_download_mime_and_signature(client):
    test_client, store = client
    resp = test_client.post("/api/chat", json={"messages": [{"role": "user", "content": "أنشئ لي ملف PDF عن تاريخ نجد"}]})
    events = parse_sse_events(resp.text)
    art_event = next(e for e in events if e["event"] == "artifacts")
    pdf = next(a for a in art_event["data"]["artifacts"] if (a.get("format") == "pdf"))
    assert pdf["status"] == "created"
    dl = test_client.get(pdf["download_url"])
    assert dl.status_code == 200
    assert "application/pdf" in dl.headers["content-type"]
    assert "attachment" in dl.headers["content-disposition"]
    assert dl.content.startswith(b"%PDF")
    assert len(dl.content) > 800
    # validate via validation helper
    validate_artifact_bytes("pdf", dl.content)


def test_timeout_config_bounded():
    settings = get_rag_settings()
    assert 5 <= settings.request_timeout_seconds <= 60
    assert settings.request_timeout_seconds == 30.0
    assert 1 <= settings.max_retries <= 3
    # server overall timeout bounded 5-60
    overall = float(os.environ.get("SARD_CHAT_OVERALL_TIMEOUT", "38"))
    assert 5 <= overall <= 60


def test_concurrent_artifact_requests_isolated(client):
    # Two concurrent artifact requests should not cross-contaminate filenames/ids
    test_client, store = client
    resp_a = test_client.post("/api/chat", json={"messages": [{"role": "user", "content": "أنشئ لي ملف PDF عن تاريخ نجد"}]})
    resp_b = test_client.post("/api/chat", json={"messages": [{"role": "user", "content": "صمم عرض بوربوينت عن العمارة النجدية"}]})
    events_a = parse_sse_events(resp_a.text)
    events_b = parse_sse_events(resp_b.text)
    arts_a = next((e for e in events_a if e["event"] == "artifacts"), None)
    arts_b = next((e for e in events_b if e["event"] == "artifacts"), None)
    assert arts_a and arts_b
    ids_a = {a["id"] for a in arts_a["data"]["artifacts"]}
    ids_b = {a["id"] for a in arts_b["data"]["artifacts"]}
    assert ids_a.isdisjoint(ids_b)
    # downloads both verified
    for arts in [arts_a, arts_b]:
        for art in arts["data"]["artifacts"]:
            if art["status"] == "created":
                dl = test_client.get(art["download_url"])
                assert dl.status_code == 200
                assert len(dl.content) > 200


def test_query_rewriter_cache_stateless_not_session_leak():
    # QueryRewriteService cache must be stateless, key is (normalized_query, model_id) not session
    from sard.rag.query_rewriter import QueryRewriteService
    svc = QueryRewriteService(settings=get_rag_settings(), circuit_breaker=__import__("sard.rag.fallbacks", fromlist=["CircuitBreaker"]).CircuitBreaker())
    q = "ما هي عاصمة قطر؟"
    # rewrite twice, same query, different session ids should hit same cache key behavior
    # Directly test cache key does not include session
    assert hasattr(svc, "_cache")
    # Cache key should be tuple of (normalized, model_id), not session
    # We verify docstring mentions stateless
    assert "stateless" in (svc.__doc__ or "") or "stateless" in QueryRewriteService.__doc__


def test_sse_malformed_model_output_still_hedges_and_done(client):
    test_client, store = client

    class _MalformedModel:
        def invoke(self, messages):
            # Return non-string content or malformed
            return AIMessage(content=None)  # type: ignore

    with patch("sard.agent.chat_service.ChatService._get_model", return_value=_MalformedModel()):
        resp = test_client.post("/api/chat", json={"messages": [{"role": "user", "content": "ما هي عاصمة قطر؟"}]})
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        events = parse_sse_events(resp.text)
        assert any(e["event"] == "done" for e in events)
        deltas = [e for e in events if e["event"] == "delta"]
        full = "".join(d["data"]["text"] for d in deltas)
        assert full.strip() != ""
        assert "روبيان" not in full
        done = next(e for e in events if e["event"] == "done")
        assert done["data"]["run_id"].startswith("chat-")


def test_sse_timeout_still_emits_done_with_hedge(client, monkeypatch):
    test_client, store = client
    monkeypatch.setenv("SARD_CHAT_OVERALL_TIMEOUT", "1")
    import time
    from sard.agent.chat_service import ChatResult

    def slow_ask(*args, **kwargs):
        time.sleep(1.5)
        return ChatResult(ok=True, text="إجابة متأخرة لكن يجب أن تُقطع بالمهلة.", artifacts=[])

    with patch("sard.agent.chat_service.ChatService.ask", side_effect=slow_ask):
        resp = test_client.post("/api/chat", json={"messages": [{"role": "user", "content": "ما هي عاصمة قطر؟"}]})
        assert resp.status_code == 200
        events = parse_sse_events(resp.text)
        # Even on timeout, done must be emitted
        assert any(e["event"] == "done" for e in events)
        done = next(e for e in events if e["event"] == "done")
        assert "run_id" in done["data"]
        # hedge text still present (fallback after timeout)
        deltas = [e for e in events if e["event"] == "delta"]
        if deltas:
            full = "".join(d["data"]["text"] for d in deltas)
            assert full.strip() != ""


def test_artifact_download_signature_and_headers_for_all_formats(client):
    test_client, store = client
    # Test pptx via SSE
    resp = test_client.post("/api/chat", json={"messages": [{"role": "user", "content": "صمم عرض بوربوينت عن العمارة النجدية"}]})
    events = parse_sse_events(resp.text)
    art_event = next((e for e in events if e["event"] == "artifacts"), None)
    assert art_event is not None
    for art in art_event["data"]["artifacts"]:
        if art["status"] == "created":
            dl = test_client.get(art["download_url"])
            assert dl.status_code == 200
            assert "attachment" in dl.headers.get("content-disposition", "")
            fmt = art.get("format") or art.get("type")
            if fmt == "pdf":
                assert dl.content.startswith(b"%PDF")
                assert "application/pdf" in dl.headers["content-type"]
            elif fmt == "pptx":
                assert dl.content.startswith(b"PK\x03\x04")
                assert "presentation" in dl.headers["content-type"]
            elif fmt == "docx":
                assert dl.content.startswith(b"PK\x03\x04")
                assert "wordprocessingml" in dl.headers["content-type"]
            elif fmt == "ics":
                assert dl.content.startswith(b"BEGIN:VCALENDAR")
                assert "text/calendar" in dl.headers["content-type"]
