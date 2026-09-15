"""J4: search provider failure matrix (boundary D, mocked, deterministic).

Spec chain: primary -> Tavily -> Exa -> transparent degraded.
Production today wires Parallel Search only
(sard/agent/tools/cultural_tools.py:parallel_search,
 sard/agent/cultural_router.py:route_and_retrieve); Tavily/Exa are the
spec-ordered web fallbacks. The contract harness below proves the ORDER
offline; the router integration tests prove the honest-degraded TAIL
(no fabricated citations) against the real CulturalRouter.
No live network, no secrets.
"""
from __future__ import annotations

import httpx

from sard.rag.search_providers import fanout_search, make_search_result

from .conftest import (
    SearchFallbackChain,
    ProviderOutcome,
    assert_no_fabricated_citations,
    make_offline_router,
    web_result,
)


def _raise_timeout():
    raise TimeoutError("parallel search timed out after 15s")


def _raise_429():
    raise Exception("429 Too Many Requests: parallel quota exhausted")


def test_04a_primary_timeout_falls_to_tavily():
    chain = SearchFallbackChain(
        {
            "primary": _raise_timeout,
            "tavily": lambda: ProviderOutcome("tavily", True, [web_result("https://example.test/tavily-1")]),
            "exa": lambda: ProviderOutcome("exa", True, [web_result("https://example.test/exa-1")]),
        }
    )
    results, used, attempted = chain.search()
    assert used == "tavily"
    assert attempted == ["primary", "tavily"]
    assert results[0]["url"] == "https://example.test/tavily-1"


def test_04b_parallel_429_falls_to_tavily():
    chain = SearchFallbackChain(
        {
            "primary": _raise_429,
            "tavily": lambda: ProviderOutcome("tavily", True, [web_result("https://example.test/tavily-2")]),
            "exa": lambda: ProviderOutcome("exa", True, [web_result("https://example.test/exa-2")]),
        }
    )
    results, used, attempted = chain.search()
    assert used == "tavily"
    assert attempted == ["primary", "tavily"]


def test_04c_invalid_response_falls_to_tavily():
    chain = SearchFallbackChain(
        {
            "primary": lambda: ProviderOutcome("primary", True, [{"title": "missing url"}]),
            "tavily": lambda: ProviderOutcome("tavily", True, [web_result("https://example.test/tavily-3")]),
            "exa": lambda: ProviderOutcome("exa", True, [web_result("https://example.test/exa-3")]),
        }
    )
    results, used, _ = chain.search()
    assert used == "tavily"
    assert results[0]["url"].endswith("tavily-3")


def test_04d_parallel_and_tavily_fail_falls_to_exa():
    chain = SearchFallbackChain(
        {
            "primary": _raise_429,
            "tavily": lambda: ProviderOutcome("tavily", False, [], error="boom", error_kind="unavailable"),
            "exa": lambda: ProviderOutcome("exa", True, [web_result("https://example.test/exa-4")]),
        }
    )
    results, used, attempted = chain.search()
    assert used == "exa"
    assert attempted == ["primary", "tavily", "exa"]


def test_04e_all_fail_transparent_degraded_contract():
    chain = SearchFallbackChain(
        {
            "primary": _raise_timeout,
            "tavily": _raise_429,
            "exa": lambda: ProviderOutcome("exa", True, [{"no": "url"}]),
        }
    )
    results, used, attempted = chain.search()
    assert results == []
    assert used == "degraded"
    assert attempted == ["primary", "tavily", "exa"]


# --- Real-router honest-degraded tail (no fabricated citations) --------------


def _assert_honest_degraded(result):
    assert result.decision.web_search_count == 0
    assert result.web_sources == []
    web_cits = [c for c in result.citations if str(c.get("channel") or c.get("type")) == "web"]
    assert web_cits == [], f"fabricated web citations: {web_cits}"
    assert_no_fabricated_citations(result.citations)
    # Honest abstention, never an invented itinerary/article.
    assert "لم تتوفر مصادر موثقة" in result.answer_text or "Insufficient verified sources" in result.answer_text
    assert "روبيان" not in result.answer_text
    assert "الأحساء" not in result.answer_text


def test_04f_router_primary_timeout_degrades_transparently():
    def _boom(**kwargs):
        raise TimeoutError("parallel search timed out")

    router = make_offline_router(rag_docs=[], web_side_effect=_boom)
    result = router.answer_query("ما هي العمارة النجدية؟")
    assert result.decision.web_search_triggered is True
    assert result.decision.web_unavailable_warning is True
    _assert_honest_degraded(result)


def test_04g_router_parallel_429_degrades_transparently():
    def _boom(**kwargs):
        raise Exception("429 Too Many Requests")

    router = make_offline_router(rag_docs=[], web_side_effect=_boom)
    result = router.answer_query("حدثني عن تاريخ الدرعية")
    assert result.decision.web_unavailable_warning is True
    _assert_honest_degraded(result)


def test_04h_router_invalid_response_is_filtered_not_cited():
    def _invalid(**kwargs):
        return [{"title": "no url here"}, {"url": "", "title": "empty", "excerpts": ["x"]}]

    router = make_offline_router(rag_docs=[], web_side_effect=_invalid)
    result = router.answer_query("ما هي العمارة النجدية؟")
    _assert_honest_degraded(result)


def test_04i_router_error_dict_items_never_become_citations():
    def _error_items(**kwargs):
        return [{"error": "provider down", "url": "https://example.test/should-be-skipped"}]

    router = make_offline_router(rag_docs=[], web_side_effect=_error_items)
    result = router.answer_query("ما هي العمارة النجدية؟")
    _assert_honest_degraded(result)


# --- Coordination with workstream D: real fanout_search, stub providers ------
# Stubs never touch httpx/network; they script timeout / 429 / malformed /
# missing-key per provider to prove the Parallel -> Tavily -> Exa order and
# the never-fabricate tail of sard/rag/search_providers.py.


class StubProvider:
    """Minimal SearchProvider double for fanout_search (no httpx, no keys)."""

    def __init__(self, name, behaviour, keyed=True):
        self.name = name
        self.behaviour = behaviour
        self._keyed = keyed
        self.search_calls: list[dict] = []

    def has_key(self):
        return self._keyed

    def search(self, objective, queries, max_results=8, timeout_s=10.0):
        self.search_calls.append({"objective": objective, "queries": list(queries)})
        b = self.behaviour
        if b == "timeout":
            raise httpx.ConnectTimeout("connect timed out", request=httpx.Request("POST", "https://test.invalid"))
        if b == "http-429":
            req = httpx.Request("POST", "https://test.invalid")
            raise httpx.HTTPStatusError("429", request=req, response=httpx.Response(429, request=req))
        if b == "malformed":
            raise ValueError("malformed JSON: unexpected token")
        query = (list(queries) or [objective])[0]
        if isinstance(b, list):
            return [make_search_result(provider=self.name, query=query, url=u) for u in b]
        raise AssertionError(f"unknown stub behaviour {b!r}")

    def extract(self, urls, objective="", timeout_s=15.0):
        return []


def _telemetry_by_provider(telemetry):
    return {t["provider"]: t for t in telemetry}


def test_04j_fanout_primary_timeout_tavily_fills_in():
    providers = [
        StubProvider("parallel", "timeout"),
        StubProvider("tavily", ["https://example.test/tavily-j"]),
        StubProvider("exa", ["https://example.test/exa-j"]),
    ]
    ranked, telemetry, flags = fanout_search("obj", ["q"], providers=providers, run_extract=False)
    by = _telemetry_by_provider(telemetry)
    assert by["parallel"]["ok"] is False and by["parallel"]["status"] == "timeout"
    assert by["tavily"]["ok"] is True
    urls = {r.url for r in ranked}
    assert "https://example.test/tavily-j" in urls
    assert flags["web_unavailable_warning"] is False


def test_04k_fanout_parallel_429_tavily_fills_in():
    providers = [
        StubProvider("parallel", "http-429"),
        StubProvider("tavily", ["https://example.test/tavily-k"]),
        StubProvider("exa", []),
    ]
    ranked, telemetry, flags = fanout_search("obj", ["q"], providers=providers, run_extract=False)
    by = _telemetry_by_provider(telemetry)
    assert by["parallel"]["status"] == "http_429"
    assert any(r.provider == "tavily" for r in ranked)
    assert flags["web_unavailable_warning"] is False


def test_04l_fanout_malformed_primary_tavily_fills_in():
    providers = [
        StubProvider("parallel", "malformed"),
        StubProvider("tavily", ["https://example.test/tavily-l"]),
        StubProvider("exa", []),
    ]
    ranked, telemetry, _ = fanout_search("obj", ["q"], providers=providers, run_extract=False)
    by = _telemetry_by_provider(telemetry)
    assert by["parallel"]["ok"] is False and by["parallel"]["status"] == "malformed"
    assert [r.url for r in ranked] == ["https://example.test/tavily-l"]


def test_04m_fanout_parallel_and_tavily_down_exa_serves():
    providers = [
        StubProvider("parallel", "http-429"),
        StubProvider("tavily", "timeout"),
        StubProvider("exa", ["https://example.test/exa-m"]),
    ]
    ranked, telemetry, flags = fanout_search("obj", ["q"], providers=providers, run_extract=False)
    assert [r.url for r in ranked] == ["https://example.test/exa-m"]
    assert ranked[0].provider == "exa"
    assert flags["web_unavailable_warning"] is False


def test_04n_fanout_all_down_returns_empty_never_fabricates():
    providers = [
        StubProvider("parallel", "timeout"),
        StubProvider("tavily", "http-429"),
        StubProvider("exa", "malformed"),
    ]
    ranked, telemetry, flags = fanout_search("obj", ["q"], providers=providers, run_extract=False)
    assert ranked == []
    assert all(t["ok"] is False for t in telemetry)
    assert flags["web_unavailable_warning"] is True
    # Telemetry carries counts only: no keys, no URLs, no payloads.
    for entry in telemetry:
        blob = str(entry)
        assert "test-key" not in blob and "example.test" not in blob


def test_04o_fanout_missing_key_fails_closed_chain_continues():
    providers = [
        StubProvider("parallel", ["https://example.test/should-not-run"], keyed=False),
        StubProvider("tavily", ["https://example.test/tavily-o"]),
        StubProvider("exa", [], keyed=False),
    ]
    ranked, telemetry, _ = fanout_search("obj", ["q"], providers=providers, run_extract=False)
    by = _telemetry_by_provider(telemetry)
    assert by["parallel"]["status"] == "provider_unavailable"
    assert providers[0].search_calls == []
    assert [r.url for r in ranked] == ["https://example.test/tavily-o"]
