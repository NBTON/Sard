"""Focused fault tests for the release-model-retrieval-fix remediation lane.

Offline, deterministic, no secrets, no network. Covers the audited release
blockers owned by this lane:

- failure classification: 429 / timeout / distinct 5xx / auth /
  context-window+413 / dimension mismatch / invalid JSON
- nonretryable categories advance immediately (no same-candidate retry):
  auth, invalid request, context length, malformed output, dimension
- transient-only retry: 429/timeout/5xx retry once then advance; 404-style
  unknown-model never retries
- CircuitBreaker: thread-safe, coherently namespaced, atomic half-open
  single-flight probe (exactly one concurrent winner; no stampede)
- Reserve protection: per-attempt/per-provider budgets derive from
  reserve_remaining(), never remaining(); timing tests prove the terminal
  reserve is never consumed
- AgentModelService.invoke/invoke_json: typed DeadlineCancelledError
  propagates (never converted to TIMEOUT responses); graph guard converts
  it to a non-retryable cancelled node failure distinguishable from timeout
- Invalid JSON advances IMMEDIATELY in ModelRouter.invoke_json and
  AgentModelService.invoke_json: exact call order/counts asserted
- fanout_search: deadline/cancel/reserve params; per-provider
  remaining-budget allocation; cancel halts the chain; reserve exhaustion
  halts with timeout telemetry
- planner.retrieve: web path re-raises typed cancellation (never swallowed)
- planner depth/trigger: unified web trigger (fresh/empty/low-confidence),
  depth-aware rag_k/web_max requested, canonical-URL cross-dedup keeps
  distinct content and drops near-identical duplicates
"""

from __future__ import annotations

import threading
import time

import pytest

from sard.agent.models import AgentModelService
from sard.config.model_router import ModelRouter
from sard.config.routing_table import RouteCandidate, TaskClass
from sard.memory.l0_evidence import L0EvidenceStore
from sard.planner.retrieve import (
    GroundedRetriever,
    infer_search_depth,
    should_trigger_web_search,
)
from sard.rag.fallbacks import (
    AllCandidatesFailedError,
    CircuitBreaker,
    FailureCategory,
    FallbackClassifiedError,
    ModelCandidate,
    classify_exception,
    is_transient_failure,
    run_with_fallback,
)
from sard.rag.search_providers import (
    canonicalize_url,
    fanout_search,
    is_duplicate_of_seen,
    make_search_result,
)


def _cands(*ids: str) -> list[ModelCandidate]:
    return [
        ModelCandidate(model_id=mid, endpoint_type="hosted", label=("primary" if i == 0 else f"fallback_{i}"))
        for i, mid in enumerate(ids)
    ]


# --- classification ----------------------------------------------------------


def test_classify_429_timeout_5xx_auth_context_dimension_json():
    assert classify_exception(Exception("429 Too Many Requests")) is FailureCategory.RATE_LIMIT
    assert classify_exception(TimeoutError("read timed out")) is FailureCategory.TIMEOUT
    assert classify_exception(Exception("503 Service Unavailable")) is FailureCategory.MODEL_UNAVAILABLE
    assert classify_exception(Exception("401 invalid api key")) is FailureCategory.AUTHENTICATION
    assert classify_exception(Exception("input too long for context")) is FailureCategory.CONTEXT_LENGTH
    assert classify_exception(Exception("413 Payload Too Large")) is FailureCategory.CONTEXT_LENGTH
    assert classify_exception(Exception("embedding dimension mismatch 1024 vs 768")) is (
        FailureCategory.EMBEDDING_DIMENSION_MISMATCH
    )
    assert classify_exception(ValueError("malformed structured output")) is FailureCategory.MALFORMED_OUTPUT
    try:
        import json as _json

        _json.loads("{oops")
    except Exception as exc:
        assert classify_exception(exc) is FailureCategory.MALFORMED_OUTPUT


def test_nonretryable_set_covers_audited_categories():
    from sard.rag.fallbacks import NON_RETRYABLE_CATEGORIES

    for category in (
        FailureCategory.AUTHENTICATION,
        FailureCategory.INVALID_REQUEST,
        FailureCategory.CONTEXT_LENGTH,
        FailureCategory.MALFORMED_OUTPUT,
        FailureCategory.EMBEDDING_DIMENSION_MISMATCH,
        FailureCategory.ZVEC_SCHEMA_MISMATCH,
    ):
        assert category in NON_RETRYABLE_CATEGORIES


@pytest.mark.parametrize(
    "message",
    [
        "401 unauthorized",
        "400 bad request",
        "request too large for context window",
        "embedding dimension mismatch",
        "invalid json: expecting value",
    ],
)
def test_nonretryable_advances_immediately(message):
    calls: list[str] = []
    cands = _cands("m1", "m2")

    def _call(candidate):
        calls.append(candidate.model_id)
        raise Exception(message)

    with pytest.raises(AllCandidatesFailedError):
        run_with_fallback("nonretry", cands, _call, max_retries_per_candidate=3, sleep_fn=lambda s: None,
                           circuit_breaker=CircuitBreaker())
    assert calls == ["m1", "m2"]


def test_404_unknown_model_never_retries():
    calls: list[str] = []

    def _call(candidate):
        calls.append(candidate.model_id)
        raise Exception("404 model not found")

    with pytest.raises(AllCandidatesFailedError):
        run_with_fallback("notfound", _cands("m1"), _call, max_retries_per_candidate=3,
                           sleep_fn=lambda s: None, circuit_breaker=CircuitBreaker())
    assert calls == ["m1"]


def test_transient_429_retries_once_then_advances():
    calls: list[str] = []

    def _call(candidate):
        calls.append(candidate.model_id)
        if candidate.model_id == "m1":
            raise Exception("429 Too Many Requests")
        return "ok"

    result, _events = run_with_fallback(
        "transient", _cands("m1", "m2"), _call, max_retries_per_candidate=2,
        sleep_fn=lambda s: None, circuit_breaker=CircuitBreaker(),
    )
    assert result == "ok"
    assert calls == ["m1", "m1", "m2"]


def test_distinct_5xx_transient_but_plain_500_message_shape():
    assert is_transient_failure(FailureCategory.MODEL_UNAVAILABLE, Exception("500 internal server error")) is True
    assert is_transient_failure(FailureCategory.MODEL_UNAVAILABLE, Exception("model not found 404")) is False
    assert is_transient_failure(FailureCategory.AUTHENTICATION, Exception("401 nope")) is False
    assert is_transient_failure(FailureCategory.CONTEXT_LENGTH, Exception("input too long")) is False
    assert is_transient_failure(FailureCategory.MALFORMED_OUTPUT, Exception("bad json")) is False


# --- CircuitBreaker ----------------------------------------------------------


def test_breaker_thread_safe_and_coherently_namespaced():
    breaker = CircuitBreaker(failure_threshold=10, cooldown_seconds=60.0)

    def _hammer():
        for _ in range(200):
            breaker.record_failure("uc", "m", "HOSTED")
            breaker.is_open("uc", "m", "hosted")
            breaker.record_success("uc", "m", "hosted")

    threads = [threading.Thread(target=_hammer) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert breaker.is_open("uc", "m", "hosted") is False
    normalized_variant = CircuitBreaker._key("uc", "m", "hosted")
    assert CircuitBreaker._key("uc", "  m  ", "Hosted") == normalized_variant
    opener = CircuitBreaker(failure_threshold=2, cooldown_seconds=60.0)
    opener.record_failure("  uc ", "m", "HOSTED")
    opener.record_failure("uc", "m", "hosted")
    assert opener.is_open("uc", "m", "hosted") is True


def test_breaker_half_open_single_flight_probe():
    """Atomic half-open gate: exactly one concurrent caller holds the probe.

    After cooldown the circuit still reports open; the first claimant wins
    try_acquire_probe exactly once, every other concurrent caller loses and
    must skip. Settling (success) closes the circuit and releases the gate.
    """
    from sard.rag.fallbacks import breaker_allows_call

    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.05)
    breaker.record_failure("uc", "m", "hosted")
    assert breaker.is_open("uc", "m", "hosted") is True
    time.sleep(0.06)
    # Post-cooldown the circuit still reports open until a probe settles it.
    assert breaker.is_open("uc", "m", "hosted") is True

    winners: list[int] = []
    losers: list[int] = []
    barrier = threading.Barrier(8)

    def _contend(slot: int):
        barrier.wait(timeout=5.0)
        allowed, is_probe = breaker_allows_call(breaker, "uc", "m", "hosted")
        if allowed and is_probe:
            winners.append(slot)
        else:
            losers.append(slot)

    threads = [threading.Thread(target=_contend, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10.0)
    assert len(winners) == 1, f"exactly one probe holder, got {winners}"
    assert len(losers) == 7

    # Probe settles successfully -> circuit closed, gate released.
    breaker.record_success("uc", "m", "hosted")
    assert breaker.is_open("uc", "m", "hosted") is False
    allowed, is_probe = breaker_allows_call(breaker, "uc", "m", "hosted")
    assert (allowed, is_probe) == (True, False)

    # Closed circuit never mints a probe.
    assert breaker.try_acquire_probe("uc", "m", "hosted") is False


def test_breaker_probe_failure_reopens_with_fresh_cooldown():
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.05)
    breaker.record_failure("uc", "m", "hosted")
    time.sleep(0.06)
    assert breaker.try_acquire_probe("uc", "m", "hosted") is True
    breaker.record_failure("uc", "m", "hosted")  # probe failed
    assert breaker.is_open("uc", "m", "hosted") is True
    assert breaker.try_acquire_probe("uc", "m", "hosted") is False  # cooling again


# --- AgentModelService deadline/cancel ---------------------------------------


def _model_settings():
    from sard.config.rag import ModelRoute, RAGSettings

    return RAGSettings(
        nvidia_api_key="test-dummy",
        chat_base_url=None,
        embedding_base_url=None,
        rerank_base_url=None,
        chat_route=ModelRoute("generation", "primary", ("fallback",)),
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


class _Reply:
    def __init__(self, content):
        self.content = content


class _ScriptedModel:
    def __init__(self, script):
        self._script = script

    def invoke(self, messages, **kwargs):
        outcome = self._script.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return _Reply(outcome)


def _service_factory(plan, calls):
    def _factory(model_id, settings):
        calls.append(model_id)
        return _ScriptedModel(list(plan.get(model_id, ["boom"])))

    return _factory


def test_invoke_propagates_deadline_and_cancel_to_every_call():
    from sard.agent.deadline import deadline_from_timeout

    calls: list[str] = []
    plan = {"primary": ["ok-primary"], "fallback": ["unused"]}
    svc = AgentModelService(
        settings=_model_settings(),
        chat_model_factory=_service_factory(plan, calls),
        circuit_breaker=CircuitBreaker(),
        sleep_fn=lambda s: None,
    )
    dl = deadline_from_timeout(5.0, label="t")
    resp = svc.invoke("factual", "sys", "hello", deadline=dl)
    assert resp.success is True and resp.model_used == "primary"

    calls2: list[str] = []
    svc2 = AgentModelService(
        settings=_model_settings(),
        chat_model_factory=_service_factory(plan, calls2),
        circuit_breaker=CircuitBreaker(),
        sleep_fn=lambda s: None,
    )
    ev = threading.Event()
    ev.set()
    # Typed cancellation propagates (never a TIMEOUT response): graph/server
    # keep cancel distinguishable from timeout.
    from sard.agent.deadline import DeadlineCancelledError

    with pytest.raises(DeadlineCancelledError):
        svc2.invoke("factual", "sys", "hello", deadline=deadline_from_timeout(5.0), cancel_event=ev)
    assert calls2 == []


def test_invoke_exhausted_budget_returns_timeout_without_factory_call():
    from sard.agent.deadline import deadline_from_timeout

    calls: list[str] = []
    svc = AgentModelService(
        settings=_model_settings(),
        chat_model_factory=_service_factory({"primary": ["ok"]}, calls),
        circuit_breaker=CircuitBreaker(),
        sleep_fn=lambda s: None,
    )
    dl = deadline_from_timeout(5.0)
    dl.monotonic_end = time.monotonic() - 1.0
    resp = svc.invoke("factual", "sys", "hello", deadline=dl)
    assert resp.success is False
    assert resp.failure_category is FailureCategory.TIMEOUT
    assert calls == []


def test_invoke_json_cancel_raises_typed_before_factory():
    from sard.agent.deadline import DeadlineCancelledError, deadline_from_timeout

    calls: list[str] = []
    svc = AgentModelService(
        settings=_model_settings(),
        chat_model_factory=_service_factory({"primary": ['{"a": 1}']}, calls),
        circuit_breaker=CircuitBreaker(),
        sleep_fn=lambda s: None,
    )
    ev = threading.Event()
    ev.set()
    # Cancellation propagates typed (never absorbed into a failure tuple):
    # the graph guard converts it to a non-retryable cancelled node failure.
    with pytest.raises(DeadlineCancelledError):
        svc.invoke_json("structured", "sys", "hi", allowed_keys=("a",),
                        deadline=deadline_from_timeout(5.0), cancel_event=ev)
    assert calls == []


def test_invoke_json_exhausted_deadline_stops_new_attempts():
    from sard.agent.deadline import deadline_from_timeout

    calls: list[str] = []
    svc = AgentModelService(
        settings=_model_settings(),
        chat_model_factory=_service_factory({"primary": ['{"a": 1}']}, calls),
        circuit_breaker=CircuitBreaker(),
        sleep_fn=lambda s: None,
    )
    dl = deadline_from_timeout(5.0)
    dl.monotonic_end = time.monotonic() - 1.0
    parsed, resp = svc.invoke_json("structured", "sys", "hi", allowed_keys=("a",), deadline=dl)
    assert parsed is None and resp.success is False
    assert calls == []


def test_services_do_not_share_default_breaker():
    svc_a = AgentModelService(settings=_model_settings())
    svc_b = AgentModelService(settings=_model_settings())
    assert svc_a._breaker is not svc_b._breaker


# --- ModelRouter deadline/cancel ---------------------------------------------


class _RouterScriptedModel:
    def __init__(self, script, calls):
        self._script = list(script)
        self._calls = calls

    def invoke(self, messages):
        self._calls.append(messages)
        if not self._script:
            return _Reply("default-ok")
        action, payload = self._script.pop(0)
        if action == "ok":
            return _Reply(payload)
        raise payload


class _RouterScriptedProvider:
    def __init__(self, name, scripts):
        self.name = name
        self._scripts = scripts
        self.builds: list = []
        self.calls: list = []

    def build_chat(self, model_id, timeout_s):
        self.builds.append((model_id, float(timeout_s)))
        return _RouterScriptedModel(self._scripts.get(model_id, []), self.calls)

    def build_embeddings(self, model_id, timeout_s):
        raise AssertionError("not used here")

    def supports(self, task_class):
        return True


def _router(scripts_a, scripts_b=None):
    prov_a = _RouterScriptedProvider("openrouter", scripts_a)
    prov_b = _RouterScriptedProvider("nvidia", scripts_b or {})
    return ModelRouter(providers={"openrouter": prov_a, "nvidia": prov_b}, sleep_fn=lambda s: None), prov_a, prov_b


def _legs(*mids, provider="openrouter", deadline=5.0):
    return [
        RouteCandidate(provider=provider, model_id=mid, deadline_s=deadline,
                       label="primary" if i == 0 else f"fallback_{i}", degraded=i > 0)
        for i, mid in enumerate(mids)
    ]


def test_router_invalid_request_and_context_advance_immediately():
    router, prov_a, _ = _router({"m-a": [("raise", Exception("400 bad request"))]}, {"m-b": [("ok", "hi")]})
    result = router.invoke_chat(TaskClass.COMPOSE_SHORT, ["hi"], candidates=_legs("m-a") + _legs("m-b", provider="nvidia"))
    assert result.success and result.model_used == "m-b"
    assert len([r for r in result.receipts if r.model == "m-a"]) == 1

    router2, _, _ = _router({"m-a": [("raise", Exception("input too long")), ("ok", "late")]}, {"m-b": [("ok", "hi")]})
    result2 = router2.invoke_chat(TaskClass.COMPOSE_SHORT, ["hi"], candidates=_legs("m-a") + _legs("m-b", provider="nvidia"))
    assert result2.success and result2.model_used == "m-b"
    assert len([r for r in result2.receipts if r.model == "m-a"]) == 1


def test_router_5xx_retries_once_while_404_advances():
    router, _, _ = _router({"m-a": [("raise", Exception("503 Service Unavailable"))]}, {"m-b": [("ok", "hi")]})
    result = router.invoke_chat(TaskClass.COMPOSE_SHORT, ["hi"], candidates=_legs("m-a") + _legs("m-b", provider="nvidia"))
    assert result.success
    assert len([r for r in result.receipts if r.model == "m-a"]) == 2

    router2, _, _ = _router({"m-a": [("raise", Exception("404 model not found"))]}, {"m-b": [("ok", "hi")]})
    result2 = router2.invoke_chat(TaskClass.COMPOSE_SHORT, ["hi"], candidates=_legs("m-a") + _legs("m-b", provider="nvidia"))
    assert result2.success
    assert len([r for r in result2.receipts if r.model == "m-a"]) == 1


def test_router_exhausted_deadline_stops_chat_legs():
    from sard.agent.deadline import deadline_from_timeout

    router, prov_a, _ = _router({"m-a": [("ok", "hi")]})
    dl = deadline_from_timeout(5.0)
    dl.monotonic_end = time.monotonic() - 1.0
    result = router.invoke_chat(TaskClass.COMPOSE_SHORT, ["hi"], candidates=_legs("m-a"), deadline=dl)
    assert result.success is False
    assert prov_a.builds == []


def test_router_cancel_raises_typed_and_skips_provider():
    import threading as _threading

    from sard.agent.deadline import DeadlineCancelledError

    router, prov_a, _ = _router({"m-a": [("ok", "hi")]})
    ev = _threading.Event()
    ev.set()
    with pytest.raises(DeadlineCancelledError):
        router.invoke_chat(TaskClass.COMPOSE_SHORT, ["hi"], candidates=_legs("m-a"), cancel_event=ev)
    assert prov_a.builds == []


def test_router_embedding_cancel_and_exhausted_deadline():
    import threading as _threading

    from sard.agent.deadline import DeadlineCancelledError, deadline_from_timeout

    class _EmbProvider(_RouterScriptedProvider):
        def build_embeddings(self, model_id, timeout_s):
            raise AssertionError("must not build after cancel")

    prov = _EmbProvider("nvidia", {})
    router = ModelRouter(providers={"openrouter": _RouterScriptedProvider("openrouter", {}), "nvidia": prov},
                         sleep_fn=lambda s: None)
    legs = [RouteCandidate(provider="nvidia", model_id="emb", deadline_s=5.0, label="primary",
                           runtime_route="embedding")]
    ev = _threading.Event()
    ev.set()
    with pytest.raises(DeadlineCancelledError):
        router.invoke_embeddings(TaskClass.EMBED_TEXT, ["نص"], candidates=legs, cancel_event=ev)
    dl = deadline_from_timeout(5.0)
    dl.monotonic_end = time.monotonic() - 1.0
    failed = router.invoke_embeddings(TaskClass.EMBED_TEXT, ["نص"], candidates=legs, deadline=dl)
    assert failed.success is False
    assert failed.failure_category is FailureCategory.TIMEOUT


# --- embedding cancellation checks -------------------------------------------


def test_embedding_query_cancel_event_aborts_between_attempts():
    import threading as _threading

    from sard.agent.deadline import DeadlineCancelledError
    from sard.rag.embeddings import EmbeddingService

    class _FakeModel:
        def embed_query(self, text):
            return [0.1, 0.2]

    svc = EmbeddingService(settings=_model_settings(), circuit_breaker=CircuitBreaker(),
                           model_factory=lambda model_id, settings: _FakeModel())
    ev = _threading.Event()
    real_run = run_with_fallback

    def _spying(use_case, candidates, call, **kwargs):
        ev.set()
        kwargs["cancel_event"] = ev
        return real_run(use_case, candidates, call, **kwargs)

    import sard.rag.embeddings as _emb_mod

    monkey = pytest.MonkeyPatch()
    monkey.setattr(_emb_mod, "run_with_fallback", _spying)
    try:
        with pytest.raises(DeadlineCancelledError):
            svc.embed_query("embed-primary", "نص تجريبي")
    finally:
        monkey.undo()


# --- search depth budgets ----------------------------------------------------


class _DepthStub:
    name = "stub"

    def __init__(self, hits, record):
        self._hits = hits
        self.record = record
        self.extract_calls = 0

    def has_key(self):
        return True

    def search(self, objective, queries, max_results=8, timeout_s=10.0):
        self.record["queries_n"] = len(queries)
        self.record["max_results"] = max_results
        return self._hits[:max_results]

    def extract(self, urls, objective="", timeout_s=15.0):
        self.extract_calls += 1
        return []


def _depth_hits(n):
    return [make_search_result(provider="stub", query="q", url=f"https://example.com/s-{i}",
                               title=f"T{i}", content=f"distinct depth content item {i} alpha beta {i * 3}")
            for i in range(n)]


def test_depth_budgets_reach_providers_without_fetch_then_truncate():
    rec: dict = {}
    stubs = [_DepthStub(_depth_hits(25), rec)]
    res, _t, flags = fanout_search("o", ["q1", "q2"], depth="simple", providers=stubs, run_extract=True)
    assert rec["queries_n"] == 1 and rec["max_results"] == 5
    assert len(res) <= 5 and stubs[0].extract_calls == 0 and flags["depth"] == "simple"

    rec2: dict = {}
    stubs2 = [_DepthStub(_depth_hits(30), rec2)]
    res2, _t2, flags2 = fanout_search("o", ["q1", "q2", "q3"], depth="deep", providers=stubs2, run_extract=True)
    assert rec2["queries_n"] == 3 and rec2["max_results"] == 20
    assert len(res2) <= 12 and flags2["depth"] == "deep"
    assert stubs2[0].extract_calls == 1

    rec3: dict = {}
    stubs3 = [_DepthStub(_depth_hits(30), rec3)]
    res3, _t3, _f3 = fanout_search("o", ["q1"], depth="deep", providers=stubs3, max_results=3, run_extract=False)
    assert rec3["max_results"] == 3
    assert len(res3) <= 3


# --- planner depth/trigger/cross-dedup ---------------------------------------


def test_unified_web_trigger_semantics():
    trigger, _reason = should_trigger_web_search("ما مواعيد مهرجان 2026؟", [{"score": 0.9}])
    assert trigger is True
    trigger_empty, _ = should_trigger_web_search("سؤال عادي عن التراث", [])
    assert trigger_empty is True
    trigger_low, _ = should_trigger_web_search("سؤال عادي عن التراث", [{"score": 0.2}])
    assert trigger_low is True
    trigger_strong, _ = should_trigger_web_search("سؤال عادي عن التراث", [{"score": 0.9}])
    assert trigger_strong is False


def test_infer_search_depth_request_aware():
    assert infer_search_depth("س؟", None) == "simple"
    assert infer_search_depth("ما مواعيد مهرجان 2026؟", None) == "deep"
    assert infer_search_depth("x" * 300, None) == "deep"
    assert infer_search_depth("سؤال متوسط الطول عن التراث السعودي والعادات", "simple") == "simple"
    assert infer_search_depth("anything", "bogus") in ("simple", "normal", "deep")


def test_canonical_cross_dedup_keeps_distinct_content():
    canon = canonicalize_url("https://Example.com/Page/?utm_source=x#frag")
    assert canon == "https://example.com/page"
    seen = {canon: ["نص محلي عن العمارة النجدية والطين"]}
    assert is_duplicate_of_seen(canon, "نص محلي عن العمارة النجدية والطين", "", seen) is True
    assert is_duplicate_of_seen(canon, "تقرير مختلف تماما عن المهرجانات والمواعيد والفعاليات الجديدة", "", seen) is False


def test_planner_depth_requests_and_dedups_local_web():
    store = L0EvidenceStore()
    rag_calls: dict = {}
    web_calls: dict = {}

    def _rag(query, k):
        rag_calls["k"] = k
        return [{
            "chunk": "نص محلي عن العمارة النجدية والطين",
            "title": "وثيقة نجد",
            "source_name": "دارة الملك عبدالعزيز",
            "doc_id": "https://example.com/page",
            "score": 0.2,
            "metadata": {"source_url": "https://example.com/page?utm_source=x", "region": "najd"},
        }]

    def _web(**kwargs):
        web_calls["max_results"] = kwargs.get("max_results")
        return [
            {"title": "نفس الصفحة", "content": "نص محلي عن العمارة النجدية والطين",
             "url": "https://example.com/page/", "published_date": None},
            {"title": "صفحة مميزة", "content": "تقرير مختلف تماما عن المهرجانات والمواعيد والفعاليات الجديدة",
             "url": "https://example.com/page/", "published_date": None},
            {"title": "صفحة أخرى", "content": "محتوى ويب مستقل عن الحرف اليدوية",
             "url": "https://example.com/other", "published_date": None},
        ]

    retriever = GroundedRetriever(
        l0_store=store,
        rag_search_fn=_rag,
        parallel_search_fn=_web,
        multimodal_extract_fn=lambda q, **kw: [],
    )
    evidence, logs = retriever.retrieve("س؟", allow_web_search=True)
    assert rag_calls["k"] == 3
    assert web_calls["max_results"] == 3
    assert any("تم تجاهل مصدر ويب مكرر" in line for line in logs)
    assert any("تم استرجاع مصدر ويب" in line for line in logs)
    assert len(evidence) >= 3


def test_planner_no_web_when_strong_rag_and_not_fresh():
    store = L0EvidenceStore()
    web_calls: list = []

    def _rag(query, k):
        return [{
            "chunk": "نص موثق قوي عن التراث",
            "title": "وثيقة",
            "source_name": "هيئة التراث",
            "doc_id": "doc-1",
            "score": 0.9,
            "metadata": {"region": "najd"},
        }]

    def _web(**kwargs):
        web_calls.append(kwargs)
        return []

    retriever = GroundedRetriever(
        l0_store=store,
        rag_search_fn=_rag,
        parallel_search_fn=_web,
        multimodal_extract_fn=lambda q, **kw: [],
    )
    evidence, _logs = retriever.retrieve(
        "سؤال اعتيادي مطول عن التراث السعودي والعادات والتقاليد", allow_web_search=True)
    assert web_calls == []
    assert len(evidence) == 1


def test_classified_error_passthrough_for_dimension():
    err = FallbackClassifiedError(FailureCategory.EMBEDDING_DIMENSION_MISMATCH, "dims differ")
    assert classify_exception(err) is FailureCategory.EMBEDDING_DIMENSION_MISMATCH


# --- Fix 1: reserve-protected attempt budgets --------------------------------


def test_attempt_budget_excludes_terminal_reserve():
    """Per-attempt budgets come from reserve_remaining(), never remaining().

    10s total with 8s reserve -> only ~2s is offerable; with 4 attempts left
    each attempt gets ~0.5s (not 10/4 = 2.5s). The terminal reserve is never
    offered to provider attempts.
    """
    from sard.agent.deadline import Deadline
    from sard.rag.fallbacks import reserve_aware_attempt_budget

    dl = Deadline(monotonic_end=time.monotonic() + 10.0, reserve_s=8.0, label="t")
    budget = reserve_aware_attempt_budget(dl, 4, 30.0)
    unreserved = dl.reserve_remaining()
    assert 0 < budget <= unreserved / 4 + 0.6  # polling slop, not reserve
    assert budget < 2.5 - 1.0  # strictly less than remaining()/attempts_left
    gone = Deadline(monotonic_end=time.monotonic() + 10.0, reserve_s=10.0, label="t")
    assert reserve_aware_attempt_budget(gone, 4, 30.0) == 0.0


def test_run_with_fallback_attempts_capped_by_unreserved_budget():
    """A hung chain cannot burn the reserve: attempts stop, reserve intact."""
    from sard.agent.deadline import deadline_from_timeout

    calls: list[str] = []

    def _hang(candidate):
        calls.append(candidate.model_id)
        time.sleep(30.0)
        return "never"

    dl = deadline_from_timeout(1.0, reserve_s=0.7, label="t")
    t0 = time.monotonic()
    with pytest.raises(AllCandidatesFailedError):
        run_with_fallback("reserve_cap", _cands("m1", "m2"), _hang,
                           max_retries_per_candidate=2, deadline=dl,
                           sleep_fn=lambda s: None, circuit_breaker=CircuitBreaker())
    elapsed = time.monotonic() - t0
    # Unreserved budget ~0.3s: attempts abandoned fast; reserve preserved.
    assert elapsed < 0.3 + 0.7 + 1.5, f"attempts consumed reserve ({elapsed:.2f}s)"
    assert len(calls) <= 2


def test_router_attempt_timeout_excludes_reserve():
    """ModelRouter._attempt_timeout shrinks by reserve_remaining/legs_left."""
    from sard.agent.deadline import Deadline

    router, _, _ = _router({})
    dl = Deadline(monotonic_end=time.monotonic() + 10.0, reserve_s=8.0, label="t")
    leg = RouteCandidate(provider="openrouter", model_id="m", deadline_s=20.0, label="primary")
    timeout = router._attempt_timeout(leg, dl, 4)
    assert timeout <= dl.reserve_remaining() / 4 + 0.6
    assert timeout < 5.0  # would be ~2.5+ without reserve; must be ~0.5
    gone = Deadline(monotonic_end=time.monotonic() + 10.0, reserve_s=10.0, label="t")
    assert router._attempt_timeout(leg, gone, 4) == pytest.approx(0.05)


# --- Fix 2: typed cancellation end-to-end ------------------------------------


def test_graph_cancel_event_yields_cancelled_not_timeout_failure():
    """run_pipeline with a set cancel flag: cancelled node failures.

    Each failure is distinguishable from timeout: kind timeout (fixed
    taxonomy) + retryable False + '(cancelled)' marker in the message.
    No node degrades/continues as an ordinary failure.
    """
    import threading as _threading

    from sard.agent.graph import GraphDependencies, run_pipeline

    ev = _threading.Event()
    ev.set()
    deps = GraphDependencies(model_service=None, rag_service=None)
    result = run_pipeline("خطة رحلة إلى الرياض", dependencies=deps, cancel_event=ev)
    assert result["node_failures"], "cancel must record node failures, not silent degrade"
    for error in result["errors"]:
        assert error.retryable is False
        assert "(cancelled)" in error.message


def test_understand_node_raises_typed_cancel():
    """Node-level: set cancel raises instead of degraded-continue."""
    import threading as _threading

    from sard.agent.deadline import DeadlineCancelledError
    from sard.agent.graph import GraphDependencies
    from sard.agent.nodes.understand import understand

    ev = _threading.Event()
    ev.set()
    deps = GraphDependencies(model_service=None, cancel_event=ev)
    with pytest.raises(DeadlineCancelledError):
        understand({"run_id": "t", "original_request": "رحلة إلى الرياض"}, deps)


def test_invoke_midflight_cancel_raises_typed():
    """Cancel arriving during a hung call propagates typed, not TIMEOUT."""
    import threading as _threading

    from sard.agent.deadline import DeadlineCancelledError

    ev = _threading.Event()

    def _factory(model_id, settings):
        class _Hang:
            def invoke(self, messages, **kwargs):
                time.sleep(30.0)
                return _Reply("late")

        return _Hang()

    svc = AgentModelService(settings=_model_settings(), chat_model_factory=_factory,
                            circuit_breaker=CircuitBreaker(), sleep_fn=lambda s: None)
    timer = _threading.Timer(0.2, ev.set)
    timer.start()
    try:
        with pytest.raises(DeadlineCancelledError):
            svc.invoke("factual", "sys", "hello", cancel_event=ev)
    finally:
        timer.cancel()


# --- Fix 3: invalid JSON immediate advance -----------------------------------


def test_service_invalid_json_exact_call_order():
    """Exact order/counts: [primary, fallback], one transport call each."""
    calls: list[str] = []
    plan = {"primary": ["not json {{{", "SHOULD-NEVER-BE-CONSUMED"], "fallback": ['{"city": "الرياض"}']}
    svc = AgentModelService(
        settings=_model_settings(),
        chat_model_factory=_service_factory(plan, calls),
        circuit_breaker=CircuitBreaker(),
        sleep_fn=lambda s: None,
        max_retries_per_candidate=2,
        max_structured_attempts=3,
    )
    parsed, resp = svc.invoke_json("structured", "sys", "hi", allowed_keys=("city",))
    assert parsed == {"city": "الرياض"}
    assert resp.success is True and resp.model_used == "fallback"
    assert calls == ["primary", "fallback"]


def test_service_all_malformed_single_call_per_candidate():
    """All-invalid: each candidate asked exactly once, explicit failure."""
    calls: list[str] = []
    plan = {"primary": ["nope {{"], "fallback": ["also nope }}"]}
    svc = AgentModelService(
        settings=_model_settings(),
        chat_model_factory=_service_factory(plan, calls),
        circuit_breaker=CircuitBreaker(),
        sleep_fn=lambda s: None,
        max_structured_attempts=5,
    )
    parsed, resp = svc.invoke_json("structured", "sys", "hi", allowed_keys=("a",))
    assert parsed is None and resp.success is False
    assert resp.failure_category is FailureCategory.MALFORMED_OUTPUT
    assert calls == ["primary", "fallback"]


# --- Fix 4: fanout deadline/cancel + retrieve re-raise ------------------------


class _BudgetStub:
    """Stub provider honoring timeout_s (like real httpx timeouts)."""

    name = "stub"

    def __init__(self, delay, hits, record, keyed=True, on_call=None):
        self.delay = delay
        self._hits = hits
        self.record = record
        self._keyed = keyed
        self.on_call = on_call

    def has_key(self):
        return self._keyed

    def search(self, objective, queries, max_results=8, timeout_s=10.0):
        self.record.setdefault("timeouts", []).append(timeout_s)
        self.record["calls"] = self.record.get("calls", 0) + 1
        if self.on_call is not None:
            self.on_call()
        time.sleep(min(self.delay, timeout_s))
        return self._hits[:max_results]

    def extract(self, urls, objective="", timeout_s=15.0):
        return []


def test_fanout_provider_timeouts_exclude_reserve():
    """Sequential provider timeouts cannot consume the terminal reserve.

    2.0s total / 1.5s reserve -> ~0.5s unreserved across 3 providers; each
    provider's timeout_s <= reserve_remaining/providers_left at launch.
    """
    from sard.agent.deadline import deadline_from_timeout

    rec: dict = {}
    hits = [make_search_result(provider="stub", query="q", url="https://example.com/b-0",
                               title="T", content="budget body alpha beta gamma")]
    stubs = [_BudgetStub(5.0, hits, rec) for _ in range(3)]
    dl = deadline_from_timeout(2.0, reserve_s=1.5, label="t")
    t0 = time.monotonic()
    ranked, telemetry, _flags = fanout_search("o", ["q"], depth="normal", providers=stubs,
                                              run_extract=False, deadline=dl)
    elapsed = time.monotonic() - t0
    assert rec["calls"] == 3
    for timeout in rec["timeouts"]:
        assert timeout <= 0.5 + 0.35, f"provider budget ate reserve: {timeout}"
    # All three bounded timeouts (~0.17s each) fit inside unreserved ~0.5s;
    # the 1.5s reserve is never consumed.
    assert elapsed < 1.5, f"chain consumed reserve ({elapsed:.2f}s)"
    assert ranked and all(t["ok"] for t in telemetry)


def test_fanout_cancel_halts_chain_no_further_providers():
    """Cancel mid-chain: typed raise, later providers never launched."""
    import threading as _threading

    from sard.agent.deadline import DeadlineCancelledError

    ev = _threading.Event()
    launched: list[str] = []
    hits = [make_search_result(provider="s", query="q", url="https://example.com/c-0",
                               title="T", content="cancel body alpha beta gamma")]

    class _Named(_BudgetStub):
        def __init__(self, pname, **kwargs):
            super().__init__(0.0, hits, {"timeouts": [], "calls": 0}, **kwargs)
            self.name = pname

    first = _Named("first")
    orig_search = first.search

    def _first_search(*args, **kwargs):
        launched.append("first")
        out = orig_search(*args, **kwargs)
        ev.set()  # client disconnects after the first provider
        return out

    first.search = _first_search
    second = _Named("second")

    def _second_search(*args, **kwargs):
        launched.append("second")
        return orig_search(*args, **kwargs)

    second.search = _second_search
    with pytest.raises(DeadlineCancelledError):
        fanout_search("o", ["q"], providers=[first, second], run_extract=False, cancel_event=ev)
    assert launched == ["first"], f"chain continued after cancel: {launched}"


def test_fanout_entry_cancel_raises_before_any_provider():
    from sard.agent.deadline import DeadlineCancelledError

    ev = threading.Event()
    ev.set()
    rec: dict = {}
    stubs = [_BudgetStub(0.0, [], rec)]
    with pytest.raises(DeadlineCancelledError):
        fanout_search("o", ["q"], providers=stubs, cancel_event=ev)
    assert rec.get("calls", 0) == 0


def test_fanout_reserve_gone_halts_with_timeout_telemetry():
    """Reserve-exhausted entry: no provider launched, timeout telemetry."""
    from sard.agent.deadline import deadline_from_timeout

    rec: dict = {}
    stubs = [_BudgetStub(0.0, [], rec)]
    dl = deadline_from_timeout(5.0, reserve_s=0.0, label="t")
    dl.monotonic_end = time.monotonic() - 1.0
    ranked, telemetry, flags = fanout_search("o", ["q"], providers=stubs, deadline=dl, run_extract=False)
    assert ranked == []
    assert rec.get("calls", 0) == 0
    assert telemetry and telemetry[0]["status"] == "timeout"
    assert flags["web_unavailable_warning"] is True


def test_retriever_web_cancel_reraises_typed():
    """GroundedRetriever web path: cancel is re-raised, never swallowed."""
    from sard.agent.deadline import DeadlineCancelledError

    store = L0EvidenceStore()

    def _rag(query, k):
        return []

    def _web(**kwargs):
        raise DeadlineCancelledError("cancelled during web", stage="retrieve:web")

    retriever = GroundedRetriever(
        l0_store=store,
        rag_search_fn=_rag,
        parallel_search_fn=_web,
        multimodal_extract_fn=lambda q, **kw: [],
    )
    with pytest.raises(DeadlineCancelledError):
        retriever.retrieve("س؟", allow_web_search=True)


def test_retriever_late_cancel_flag_converts_to_typed():
    """Provider raising while cancel lands: typed raise, not a warning."""
    import threading as _threading

    from sard.agent.deadline import DeadlineCancelledError

    store = L0EvidenceStore()
    ev = _threading.Event()

    def _rag(query, k):
        return []

    def _web(**kwargs):
        ev.set()  # cancel lands exactly as the provider fails
        raise TimeoutError("web provider timed out")

    retriever = GroundedRetriever(
        l0_store=store,
        rag_search_fn=_rag,
        parallel_search_fn=_web,
        multimodal_extract_fn=lambda q, **kw: [],
    )
    with pytest.raises(DeadlineCancelledError):
        retriever.retrieve("س؟", allow_web_search=True, cancel_event=ev)
