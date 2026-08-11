"""QueryRewriteService tests with a fake ChatNVIDIA-shaped model."""

from __future__ import annotations

from sard.config.rag import ModelRoute, RAGSettings
from sard.rag.query_rewriter import QueryRewriteService


def _settings(**overrides) -> RAGSettings:
    base = dict(
        nvidia_api_key="nvapi-test",
        chat_base_url=None,
        embedding_base_url=None,
        rerank_base_url=None,
        chat_route=ModelRoute("generation", "chat-primary", ()),
        query_route=ModelRoute("query_rewrite", "query-primary", ("query-fb1", "query-fb2")),
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


def test_rewrite_success_returns_structured_output():
    good_json = (
        '{"normalized_question": "الينابيع الحارة الاحساء", '
        '"search_variants": ["الينابيع الحارة الاحساء", "عيون الاحساء الحارة"], '
        '"entities": ["الأحساء"], "topic_filter": "springs", "exact_phrases": []}'
    )
    service = QueryRewriteService(
        settings=_settings(),
        chat_model_factory=lambda model_id, settings: _FakeChatModel(content=good_json),
    )
    result, events = service.rewrite("أين تقع الينابيع الحارة في الأحساء؟")
    assert result.rewrite_succeeded is True
    assert result.model_used == "query-primary"
    assert len(result.search_variants) >= 1
    assert events[-1].outcome == "success"


def test_rewrite_falls_back_deterministically_when_all_models_fail():
    service = QueryRewriteService(
        settings=_settings(),
        chat_model_factory=lambda model_id, settings: _FakeChatModel(raise_error=Exception("503 down")),
    )
    result, events = service.rewrite("ما تاريخ الينابيع الحارة؟")
    assert result.rewrite_succeeded is False
    assert result.model_used is None
    assert result.normalized_question  # deterministic normalization still ran
    assert result.original_question == "ما تاريخ الينابيع الحارة؟"


def test_rewrite_falls_back_on_malformed_json_and_tries_next_candidate():
    calls = []

    def factory(model_id, settings):
        calls.append(model_id)
        if model_id == "query-primary":
            return _FakeChatModel(content="ليس JSON على الإطلاق")
        return _FakeChatModel(
            content='{"normalized_question": "طبيعي", "search_variants": ["طبيعي"]}'
        )

    service = QueryRewriteService(settings=_settings(), chat_model_factory=factory)
    result, events = service.rewrite("سؤال تجريبي")
    assert calls[0] == "query-primary"
    assert "query-fb1" in calls
    assert result.rewrite_succeeded is True


def test_rewrite_always_includes_normalized_original_query():
    good_json = '{"normalized_question": "شيء آخر", "search_variants": ["شيء آخر"]}'
    service = QueryRewriteService(
        settings=_settings(),
        chat_model_factory=lambda model_id, settings: _FakeChatModel(content=good_json),
    )
    result, _ = service.rewrite("السؤال الأصلي")
    from sard.rag.normalize import normalize_arabic

    assert normalize_arabic("السؤال الأصلي") in result.search_variants


def test_rewrite_disabled_uses_deterministic_path_without_calling_model():
    called = []

    def factory(model_id, settings):
        called.append(model_id)
        return _FakeChatModel(content="{}")

    service = QueryRewriteService(settings=_settings(enable_query_rewrite=False), chat_model_factory=factory)
    result, events = service.rewrite("سؤال")
    assert not called
    assert result.rewrite_succeeded is False
    assert events == []


def test_successful_rewrite_is_cached_by_normalized_query_and_model():
    calls = []
    good_json = '{"normalized_question": "طبيعي", "search_variants": ["طبيعي"]}'

    def factory(model_id, settings):
        calls.append(model_id)
        return _FakeChatModel(content=good_json)

    service = QueryRewriteService(settings=_settings(), chat_model_factory=factory)
    service.rewrite("سؤال يتكرر")
    service.rewrite("سؤال يتكرر")
    # The second call should be served entirely from the rewrite cache,
    # so the chat model factory (and therefore `.invoke`) is never called
    # a second time for the same normalized query + model.
    assert calls == ["query-primary"]
