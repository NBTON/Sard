"""AnswerService tests: citation repair + generation fallback (no network)."""

from __future__ import annotations

from sard.config.rag import ModelRoute, RAGSettings
from sard.rag.answer import AnswerService, pack_context
from sard.rag.schemas import RetrievedCandidate


def _settings(**overrides) -> RAGSettings:
    base = dict(
        nvidia_api_key="nvapi-test",
        chat_base_url=None,
        embedding_base_url=None,
        rerank_base_url=None,
        chat_route=ModelRoute("generation", "chat-primary", ("chat-fb1", "chat-fb2")),
        query_route=ModelRoute("query_rewrite", "query-primary", ()),
        embedding_route=ModelRoute("embedding", "embed-primary", ()),
        embedding_fallback_model="nv-embed-v1",
        rerank_route=ModelRoute("rerank", "rerank-primary", ()),
        vision_route=ModelRoute("vision", "vision-primary", ()),
        translation_route=ModelRoute("translation", "translate-primary", ()),
        safety_route=ModelRoute("safety", "safety-primary", ()),
        request_timeout_seconds=5.0,
        max_retries=1,
        zvec_collection_path="data/zvec/test",
        dense_candidates=10,
        fts_candidates=10,
        fused_candidates=10,
        final_top_k=5,
        enable_query_rewrite=True,
        enable_fts=True,
        enable_rerank=True,
    )
    base.update(overrides)
    return RAGSettings(**base)


def _candidate(citation_id, content="محتوى تجريبي") -> RetrievedCandidate:
    return RetrievedCandidate(
        chunk_id=f"chunk-{citation_id}",
        document_id="DOC-1",
        citation_id=citation_id,
        content=content,
        title="عنوان المصدر",
        source_name="مصدر تجريبي",
        source_url="https://example.com",
        topic="springs",
        language="ar",
        publication_date="2020-01-01",
        page_number=None,
    )


class _FakeResponse:
    def __init__(self, content: str):
        self.content = content


class _FakeChatModel:
    def __init__(self, content=None, raise_error=None):
        self.content = content
        self.raise_error = raise_error

    def invoke(self, messages):
        if self.raise_error:
            raise self.raise_error
        return _FakeResponse(self.content)


def test_pack_context_includes_citation_ids_and_titles():
    candidates = [_candidate("CIT-AAAAAAAAAAAA", "نص عن الينابيع")]
    context = pack_context(candidates)
    assert "CIT-AAAAAAAAAAAA" in context
    assert "نص عن الينابيع" in context


def test_generate_success_keeps_valid_citations():
    candidates = [_candidate("CIT-AAAAAAAAAAAA")]
    answer_text = "الينابيع الحارة معروفة في الأحساء [CIT-AAAAAAAAAAAA]."
    service = AnswerService(
        settings=_settings(),
        chat_model_factory=lambda model_id, settings: _FakeChatModel(content=answer_text),
    )
    result, events = service.generate("سؤال", candidates)
    assert result.generation_mode == "generative"
    assert "[CIT-AAAAAAAAAAAA]" in result.answer_text
    assert len(result.citations) == 1
    assert result.citations[0].citation_id == "CIT-AAAAAAAAAAAA"


def test_generate_repairs_fabricated_citation_ids():
    candidates = [_candidate("CIT-AAAAAAAAAAAA")]
    answer_text = "حقيقة مسندة [CIT-AAAAAAAAAAAA] وحقيقة مختلقة [CIT-FFFFFFFFFFFF]."
    service = AnswerService(
        settings=_settings(),
        chat_model_factory=lambda model_id, settings: _FakeChatModel(content=answer_text),
    )
    result, _ = service.generate("سؤال", candidates)
    assert "CIT-FFFFFFFFFFFF" not in result.answer_text
    assert "CIT-AAAAAAAAAAAA" in result.answer_text
    assert all(c.citation_id != "CIT-FFFFFFFFFFFF" for c in result.citations)
    assert result.warnings  # repair was logged as a warning


def test_generate_falls_back_to_extractive_summary_when_all_models_fail():
    candidates = [_candidate("CIT-AAAAAAAAAAAA", "نص أدلة مباشر")]
    service = AnswerService(
        settings=_settings(),
        chat_model_factory=lambda model_id, settings: _FakeChatModel(raise_error=Exception("503")),
    )
    result, events = service.generate("سؤال", candidates)
    assert result.generation_mode == "extractive_fallback"
    assert "نص أدلة مباشر" in result.answer_text
    assert "CIT-AAAAAAAAAAAA" in result.answer_text
    assert result.model_used is None


def test_generate_with_no_candidates_states_insufficient_evidence():
    service = AnswerService(settings=_settings())
    result, events = service.generate("سؤال بلا أدلة", [])
    assert result.generation_mode == "extractive_fallback"
    assert result.citations == []
    assert events == []


def test_generate_tries_fallback_model_after_primary_failure():
    calls = []

    def factory(model_id, settings):
        calls.append(model_id)
        if model_id == "chat-primary":
            return _FakeChatModel(raise_error=Exception("500"))
        return _FakeChatModel(content="إجابة من نموذج احتياطي [CIT-AAAAAAAAAAAA]")

    candidates = [_candidate("CIT-AAAAAAAAAAAA")]
    service = AnswerService(settings=_settings(), chat_model_factory=factory)
    result, events = service.generate("سؤال", candidates)
    assert calls[0] == "chat-primary"
    assert result.model_used == "chat-fb1"
    assert result.generation_mode == "generative"


def test_generate_rejects_empty_model_output_and_falls_back():
    candidates = [_candidate("CIT-AAAAAAAAAAAA", "دليل احتياطي")]
    service = AnswerService(
        settings=_settings(),
        chat_model_factory=lambda model_id, settings: _FakeChatModel(content="   "),
    )
    result, events = service.generate("سؤال", candidates)
    assert result.generation_mode == "extractive_fallback"
