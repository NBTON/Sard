"""Workstream D matrix: Parallel -> Tavily -> Exa failover (mocked httpx)."""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from sard.rag import search_providers as sp
from sard.rag.search_providers import (
    ExaProvider,
    ParallelProvider,
    TavilyProvider,
    canonicalize_url,
    detect_language,
    dict_to_search_result,
    fanout_search,
    make_search_result,
    resolve_tavily_key,
    to_legacy_dict,
)


# ---------------------------------------------------------------------------
# Fake httpx plumbing
# --------------------------------------------------------------------------

class FakeResp:
    def __init__(self, status_code=200, payload=None, json_error=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error
        self.text = text or ("" if payload is None else json.dumps(payload)[:500])

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            req = httpx.Request("POST", "http://test.invalid")
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=req,
                response=httpx.Response(self.status_code, request=req),
            )


class FakeClient:
    """Monkeypatched httpx.Client. Routes on URL substring via handler."""

    calls: list = []
    handler = None

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, headers=None, json=None):
        FakeClient.calls.append({"url": url, "headers": dict(headers or {}), "json": json})
        assert FakeClient.handler is not None
        outcome = FakeClient.handler(url, headers or {}, json)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.fixture
def fake_http(monkeypatch):
    FakeClient.calls = []
    FakeClient.handler = None
    monkeypatch.setattr(httpx, "Client", FakeClient)
    return FakeClient


@pytest.fixture
def clean_keys(monkeypatch):
    for k in ("PARALLEL_API_KEY", "TAVILY_API_KEY", "TIVALY_API_KEY", "EXA_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    yield monkeypatch


def _parallel_payload(url, title="T", excerpt="body text here", date="2024-01-01"):
    return {"results": [{"url": url, "title": title, "excerpts": [excerpt], "publish_date": date}]}


def _tavily_payload(url, content="tavily body", date="2024-02-02"):
    return {"results": [{"url": url, "title": "TT", "content": content,
                         "score": 0.9, "published_date": date, "author": "ed"}]}


def _exa_payload(url, text="exa body", date="2024-03-03"):
    return {"results": [{"url": url, "title": "ET", "text": text,
                         "score": 0.8, "publishedDate": date, "author": "ax"}]}


# ---------------------------------------------------------------------------
# Shape / normalization
# --------------------------------------------------------------------------

def test_search_result_shape_and_ids():
    r = make_search_result(
        provider="tavily", query="q", url="https://Example.com/Path/?utm_source=x#frag",
        title="t", snippet="s" * 500, content="hello world",
        published_at="2024-01-01", author="a",
    )
    assert r.canonical_url == "https://example.com/path"
    assert len(r.snippet) <= 400
    assert r.content_hash == sp.content_hash_of("t", "hello world")
    assert len(r.content_hash) == 40
    assert r.id == hashlib.sha1(f"{r.canonical_url}|{r.content_hash}".encode()).hexdigest()
    assert r.domain == "example.com"
    assert r.language in ("ar", "en", "other")
    assert r.retrieved_at.endswith("Z")
    assert isinstance(r.metadata, dict)


def test_language_bias_helper():
    assert detect_language("السلام عليكم ورحمة الله") == "ar"
    assert detect_language("hello world heritage") == "en"
    assert detect_language("12345 !!!") == "other"
    assert canonicalize_url("https://A.com/X/?utm_medium=e&k=1") == "https://a.com/x?k=1"


def test_legacy_adapter_carries_both_aliases():
    # Planner-shaped mismatch input: parallel returns excerpts/publish_date only.
    raw = {"url": "https://example.com/a", "title": "T",
           "excerpts": ["first excerpt body"], "publish_date": "2024-05-01"}
    res = dict_to_search_result(raw, provider="parallel", query="q")
    legacy = to_legacy_dict(res)
    assert legacy["content"] and "first excerpt" in legacy["content"]
    assert legacy["published_date"] == "2024-05-01"
    assert legacy["publish_date"] == "2024-05-01"
    assert legacy["excerpts"] and legacy["excerpts"][0]
    # Router-side normalizer fills the other direction too.
    from sard.agent.cultural_router import _normalize_router_web_hit
    only_content = _normalize_router_web_hit(
        {"url": "https://example.com/b", "title": "T2", "content": "c body",
         "published_date": "2024-06-01"})
    assert only_content["excerpts"] == ["c body"]
    assert only_content["publish_date"] == "2024-06-01"


# ---------------------------------------------------------------------------
# Missing keys -> [] + warning, no HTTP
# --------------------------------------------------------------------------

def test_missing_keys_fail_closed_no_http(clean_keys, fake_http, recwarn):
    def _boom(url, headers, payload):
        raise AssertionError("no HTTP expected without keys")

    fake_http.handler = _boom
    results, telemetry, flags = fanout_search("objective", ["q1"], depth="normal")
    assert results == []
    assert flags["provider_unavailable"] is True
    assert flags["web_unavailable_warning"] is True
    assert all(t["status"] == "provider_unavailable" for t in telemetry)
    assert fake_http.calls == []


# ---------------------------------------------------------------------------
# Timeout / 429 / 401 / malformed -> continue chain
# --------------------------------------------------------------------------

def test_parallel_timeout_falls_through_to_tavily(clean_keys, fake_http, monkeypatch):
    monkeypatch.setenv("PARALLEL_API_KEY", "pk")
    monkeypatch.setenv("TAVILY_API_KEY", "tk")

    def handler(url, headers, payload):
        if "parallel" in url:
            return httpx.ConnectTimeout("slow")
        if "tavily" in url:
            return FakeResp(200, _tavily_payload("https://example.com/t1"))
        return FakeResp(404, {}, text="nope")

    fake_http.handler = handler
    results, telemetry, _flags = fanout_search("obj", ["q"], run_extract=False)
    assert [r.provider for r in results] == ["tavily"]
    by = {t["provider"]: t for t in telemetry}
    assert by["parallel"]["status"] == "timeout" and by["parallel"]["ok"] is False
    assert by["tavily"]["ok"] is True


def test_parallel_429_falls_through(clean_keys, fake_http, monkeypatch):
    monkeypatch.setenv("PARALLEL_API_KEY", "pk")
    monkeypatch.setenv("TAVILY_API_KEY", "tk")

    def handler(url, headers, payload):
        if "parallel" in url:
            return FakeResp(429, {}, text="rate limited")
        return FakeResp(200, _tavily_payload("https://example.com/t2"))

    fake_http.handler = handler
    results, telemetry, _flags = fanout_search("obj", ["q"], run_extract=False)
    assert results and results[0].provider == "tavily"
    assert {t["provider"]: t for t in telemetry}["parallel"]["status"] == "http_429"


def test_401_single_attempt_no_retry_storm(clean_keys, fake_http, monkeypatch):
    monkeypatch.setenv("PARALLEL_API_KEY", "pk")
    monkeypatch.setenv("TAVILY_API_KEY", "tk")

    def handler(url, headers, payload):
        if "parallel" in url:
            return FakeResp(401, {}, text="unauthorized")
        return FakeResp(200, _tavily_payload("https://example.com/t3"))

    fake_http.handler = handler
    results, telemetry, _flags = fanout_search("obj", ["q"], run_extract=False)
    parallel_calls = [c for c in fake_http.calls if "parallel" in c["url"]]
    assert len(parallel_calls) == 1  # no retry storm
    assert {t["provider"]: t for t in telemetry}["parallel"]["status"] == "http_401"
    assert results and results[0].provider == "tavily"


def test_malformed_json_continues(clean_keys, fake_http, monkeypatch):
    monkeypatch.setenv("PARALLEL_API_KEY", "pk")
    monkeypatch.setenv("TAVILY_API_KEY", "tk")

    def handler(url, headers, payload):
        if "parallel" in url:
            return FakeResp(200, None, json_error=ValueError("bad json"), text="{oops")
        return FakeResp(200, _tavily_payload("https://example.com/t4"))

    fake_http.handler = handler
    results, _telemetry, _flags = fanout_search("obj", ["q"], run_extract=False)
    assert results and results[0].provider == "tavily"


def test_all_down_empty_never_fabricate(clean_keys, fake_http, monkeypatch):
    monkeypatch.setenv("PARALLEL_API_KEY", "pk")
    monkeypatch.setenv("TAVILY_API_KEY", "tk")
    monkeypatch.setenv("EXA_API_KEY", "ek")

    def handler(url, headers, payload):
        if "parallel" in url:
            return httpx.ConnectTimeout("down")
        if "tavily" in url:
            return FakeResp(500, {}, text="err")
        return FakeResp(200, None, json_error=ValueError("bad"), text="{")

    fake_http.handler = handler
    results, telemetry, flags = fanout_search("obj", ["q"], run_extract=False)
    assert results == []  # RAG proceeds alone; nothing fabricated
    assert flags["web_unavailable_warning"] is True
    assert all(t["ok"] is False for t in telemetry)


# ---------------------------------------------------------------------------
# Dedup
# --------------------------------------------------------------------------

def test_dup_url_single_entry(clean_keys, fake_http, monkeypatch):
    monkeypatch.setenv("PARALLEL_API_KEY", "pk")
    monkeypatch.setenv("TAVILY_API_KEY", "tk")

    def handler(url, headers, payload):
        if "parallel" in url:
            return FakeResp(200, _parallel_payload(
                "https://example.com/Dup/?utm_source=x", excerpt="same body text here"))
        return FakeResp(200, _tavily_payload("https://example.com/dup/", content="same body text here"))

    fake_http.handler = handler
    results, _t, _f = fanout_search("obj", ["q"], run_extract=False)
    assert len(results) == 1
    assert results[0].canonical_url == "https://example.com/dup"


def test_near_dup_keep_one(clean_keys, fake_http, monkeypatch):
    monkeypatch.setenv("PARALLEL_API_KEY", "pk")
    monkeypatch.setenv("TAVILY_API_KEY", "tk")
    body = "heritage festival dates venues official program " * 6

    def handler(url, headers, payload):
        if "parallel" in url:
            return FakeResp(200, _parallel_payload("https://a.example.com/x", excerpt=body))
        return FakeResp(200, _tavily_payload("https://b.example.com/y", content=body + " "))

    fake_http.handler = handler
    results, _t, _f = fanout_search("obj", ["q"], run_extract=False)
    assert len(results) == 1


def test_institutional_rerank_first(clean_keys, fake_http, monkeypatch):
    monkeypatch.setenv("PARALLEL_API_KEY", "pk")

    def handler(url, headers, payload):
        return FakeResp(200, {"results": [
            {"url": "https://blog.example.com/post", "title": "B",
             "excerpts": ["general blog heritage notes"], "publish_date": None},
            {"url": "https://moc.gov.sa/heritage", "title": "M",
             "excerpts": ["ministry heritage documentation"], "publish_date": None},
        ]})

    fake_http.handler = handler
    results, _t, _f = fanout_search("obj", ["q"], run_extract=False)
    assert results and "moc.gov.sa" in results[0].url


# ---------------------------------------------------------------------------
# Keys: precedence, alias, hygiene
# --------------------------------------------------------------------------

def test_arg_vs_env_precedence(clean_keys, fake_http, monkeypatch):
    monkeypatch.setenv("PARALLEL_API_KEY", "ENV-PAR")
    monkeypatch.setenv("TAVILY_API_KEY", "ENV-TAV")

    seen = {}

    def handler(url, headers, payload):
        if "parallel" in url:
            seen["parallel"] = headers.get("x-api-key")
            return FakeResp(200, _parallel_payload("https://example.com/p"))
        seen["tavily"] = (payload or {}).get("api_key")
        return FakeResp(200, _tavily_payload("https://example.com/t"))

    fake_http.handler = handler
    p = ParallelProvider(api_key="ARG-PAR")
    t = TavilyProvider(api_key="ARG-TAV")
    assert p.has_key() and t.has_key()
    p.search("o", ["q"])
    t.search("o", ["q"])
    assert seen["parallel"] == "ARG-PAR"
    assert seen["tavily"] == "ARG-TAV"


def test_tivaly_alias_warns_and_fallback(clean_keys, monkeypatch):
    monkeypatch.setenv("TIVALY_API_KEY", "alias-key")
    with pytest.warns(DeprecationWarning):
        assert resolve_tavily_key() == "alias-key"


def test_canonical_tavily_wins_over_alias(clean_keys, monkeypatch, recwarn):
    monkeypatch.setenv("TAVILY_API_KEY", "canon-key")
    monkeypatch.setenv("TIVALY_API_KEY", "alias-key")
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert resolve_tavily_key() == "canon-key"


def test_telemetry_shape_no_secrets(clean_keys, fake_http, monkeypatch):
    monkeypatch.setenv("PARALLEL_API_KEY", "SECRET-PK")
    monkeypatch.setenv("TAVILY_API_KEY", "SECRET-TK")

    def handler(url, headers, payload):
        return FakeResp(200, _parallel_payload("https://example.com/s")) \
            if "parallel" in url else FakeResp(200, _tavily_payload("https://example.com/u"))

    fake_http.handler = handler
    results, telemetry, _flags = fanout_search("obj", ["q"], run_extract=False)
    assert results
    for entry in telemetry:
        assert set(entry) == {"provider", "ok", "latency_ms", "raw_n",
                              "kept_n", "status", "error_class"}
    blob = str(telemetry) + str([r.metadata for r in results])
    assert "SECRET-PK" not in blob and "SECRET-TK" not in blob


# ---------------------------------------------------------------------------
# Depths
# --------------------------------------------------------------------------

class _StubProvider:
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


def _stub_hits(n, provider="stub"):
    return [make_search_result(provider=provider, query="q", url=f"https://example.com/{provider}-{i}",
                               title=f"T{i}", content=f"distinct content item number {i} alpha beta gamma delta {i*7}")
            for i in range(n)]


def test_depth_budgets_queries_and_extract():
    rec = {}
    stubs = [_StubProvider(_stub_hits(25), rec)]
    res, _t, flags = fanout_search("o", ["q1", "q2", "q3", "q4", "q5", "q6"], depth="simple",
                                   providers=stubs, run_extract=True)
    assert rec["queries_n"] == 1 and flags["depth"] == "simple"
    assert len(res) <= 5 and stubs[0].extract_calls == 0

    rec2 = {}
    stubs2 = [_StubProvider(_stub_hits(25), rec2)]
    res2, _t2, _f2 = fanout_search("o", ["q1", "q2", "q3", "q4", "q5", "q6"], depth="deep",
                                   providers=stubs2, run_extract=True)
    assert rec2["queries_n"] == 6  # capped at 8, got all 6
    assert len(res2) <= 12


def test_normal_depth_default_single_extract_slot():
    rec = {}
    stubs = [_StubProvider(_stub_hits(8), rec)]
    res, _t, _f = fanout_search("o", ["q1", "q2"], depth="normal",
                                providers=stubs, run_extract=True)
    assert len(res) <= 10


def test_config_additive_search_keys(clean_keys, monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k1")
    monkeypatch.setenv("EXA_API_KEY", "k2")
    monkeypatch.setenv("SEARCH_TIMEOUT_S_PARALLEL", "15")
    monkeypatch.setenv("SEARCH_TIMEOUT_S_TAVILY", "11")
    monkeypatch.setenv("SEARCH_TIMEOUT_S_EXA", "9")
    from sard.config.rag import get_rag_settings

    s = get_rag_settings()
    assert s.tavily_api_key == "k1" and s.exa_api_key == "k2"
    assert (s.search_timeout_s_parallel, s.search_timeout_s_tavily,
            s.search_timeout_s_exa) == (15.0, 11.0, 9.0)


def test_exa_provider_parses_contents_aliases(clean_keys, fake_http, monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "ek")

    def handler(url, headers, payload):
        assert headers.get("x-api-key") == "ek"
        return FakeResp(200, _exa_payload("https://example.com/e1", text="exa text body"))

    fake_http.handler = handler
    prov = ExaProvider()
    hits = prov.search("o", ["q"])
    assert hits and hits[0].content == "exa text body"
    assert hits[0].published_at == "2024-03-03"
