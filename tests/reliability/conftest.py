"""Shared fixtures for workstream J (reliability / fault-injection).

Offline, deterministic, no secrets, no live network. Every boundary
(model-router B, search D, storage F, deadline E, verification G) is
mocked with in-process fakes. Nothing here edits production code.
"""
from __future__ import annotations

import io
import json
import logging
import sys
import urllib.request as urllib_request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence
from urllib.error import HTTPError

import pytest

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ---------------------------------------------------------------------------
# Environment hygiene: never leak real credentials into reliability tests.
# ---------------------------------------------------------------------------

_SENSITIVE_ENV = (
    "NVIDIA_API_KEY",
    "PARALLEL_API_KEY",
    "BLOB_READ_WRITE_TOKEN",
    "SARD_BLOB_TOKEN",
    "SARD_BLOB_ENDPOINT",
    "SARD_BLOB_PUBLIC_BASE_URL",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENROUTER_API_KEY",
    "TAVILY_API_KEY",
    "EXA_API_KEY",
)


@pytest.fixture(autouse=True)
def _sanitize_env(monkeypatch):
    for key in _SENSITIVE_ENV:
        monkeypatch.delenv(key, raising=False)
    # Keep overall SSE deadline parsing deterministic.
    monkeypatch.setenv("SARD_CHAT_OVERALL_TIMEOUT", "35")
    yield


# ---------------------------------------------------------------------------
# Model-router fakes (boundary B)
# ---------------------------------------------------------------------------

def make_test_settings(primary: str = "test-primary", fallbacks: Sequence[str] = ("test-fallback",)):
    """Minimal RAGSettings for AgentModelService without network or keys."""
    from sard.config.rag import ModelRoute, RAGSettings

    return RAGSettings(
        nvidia_api_key="test-dummy",
        chat_base_url=None,
        embedding_base_url=None,
        rerank_base_url=None,
        chat_route=ModelRoute("generation", primary, tuple(fallbacks)),
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


class FakeModelResponse:
    def __init__(self, content: Any):
        self.content = content


class ScriptedChatModel:
    """Fake chat model driven by a scripted behaviour string or callable."""

    def __init__(self, behaviour: Any):
        self.behaviour = behaviour
        self.calls: list[list[Any]] = []

    def invoke(self, messages: list[Any], **kwargs: Any) -> FakeModelResponse:
        self.calls.append(list(messages))
        b = self.behaviour
        if callable(b):
            return b(messages)
        if b == "raise-429":
            raise Exception("429 Too Many Requests: rate limit exceeded for primary")
        if b == "raise-timeout":
            raise TimeoutError("timed out after 30s: deadline exceeded")
        if b == "raise-500":
            raise Exception("503 Service Unavailable: model overloaded")
        if b == "raise-auth":
            raise Exception("401 Unauthorized: invalid api key")
        if b == "empty":
            return FakeModelResponse("")
        if isinstance(b, str) and b.startswith("ok:"):
            return FakeModelResponse(b[len("ok:"):])
        raise AssertionError(f"unknown scripted behaviour: {b!r}")


def make_factory(plan: Dict[str, Any], call_log: Optional[list] = None):
    """Build a chat_model_factory fake from {model_id: behaviour}."""

    def factory(model_id: str, settings: Any):
        if call_log is not None:
            call_log.append(model_id)
        return ScriptedChatModel(plan.get(model_id, "raise-500"))

    return factory


@pytest.fixture
def fresh_breaker():
    from sard.rag.fallbacks import CircuitBreaker

    return CircuitBreaker()


@pytest.fixture
def noop_sleep():
    calls: list[float] = []

    def _sleep(seconds: float) -> None:
        calls.append(seconds)

    _sleep.calls = calls  # type: ignore[attr-defined]
    return _sleep


# ---------------------------------------------------------------------------
# Search-chain contract harness (boundary D)
# ---------------------------------------------------------------------------
# Production today wires Parallel Search only
# (sard/agent/tools/cultural_tools.py:parallel_search,
#  sard/agent/cultural_router.py:route_and_retrieve). Tavily/Exa are the
# spec-ordered web fallbacks. This harness models the SPEC chain
# deterministically so the matrix is runnable offline; integration tests
# below assert the router's honest-degraded tail (no fabricated citations)
# against the real CulturalRouter.

@dataclass
class ProviderOutcome:
    provider: str
    ok: bool
    results: list[dict] = field(default_factory=list)
    error: str = ""
    error_kind: str = ""  # timeout | rate_limit | invalid_response | unavailable


class SearchFallbackChain:
    """Spec chain: primary -> Tavily -> Exa -> transparent degraded.

    Each provider is an injected callable returning ProviderOutcome.
    No network is performed here; tests script timeouts/429/invalid.
    """

    ORDER = ("primary", "tavily", "exa")

    def __init__(self, providers: Dict[str, Callable[[], ProviderOutcome]]):
        self.providers = providers
        self.attempted: list[str] = []

    def search(self) -> tuple[list[dict], str, list[str]]:
        """Return (results, used_provider, attempted). degraded => ([], 'degraded', ...)."""
        errors: list[str] = []
        for name in self.ORDER:
            fn = self.providers.get(name)
            if fn is None:
                errors.append(f"{name}:unavailable")
                continue
            self.attempted.append(name)
            try:
                outcome = fn()
            except TimeoutError as exc:
                errors.append(f"{name}:timeout:{exc}")
                continue
            except Exception as exc:
                text = f"{type(exc).__name__} {exc}".lower()
                kind = "rate_limit" if ("429" in text or "rate limit" in text) else "unavailable"
                errors.append(f"{name}:{kind}:{exc}")
                continue
            if not outcome.ok:
                errors.append(f"{name}:{outcome.error_kind or 'unavailable'}:{outcome.error}")
                continue
            if not self._valid_response(outcome.results):
                errors.append(f"{name}:invalid_response")
                continue
            return outcome.results, name, list(self.attempted)
        return [], "degraded", list(self.attempted)

    @staticmethod
    def _valid_response(results: Any) -> bool:
        if not isinstance(results, list) or not results:
            return False
        for item in results:
            if not isinstance(item, dict):
                return False
            if not str(item.get("url") or "").strip():
                return False
            if "excerpts" not in item and "text" not in item and "snippet" not in item:
                return False
        return True


def web_result(url: str, title: str = "Verified cultural source", excerpt: str = "موثق من هيئة التراث.") -> dict:
    return {"url": url, "title": title, "excerpts": [excerpt], "publish_date": None}


def assert_no_fabricated_citations(citations: Sequence[dict], allowed_channels: Sequence[str] = ("rag", "media")) -> None:
    """Degraded answers must not invent Web citations out of thin air."""
    for cit in citations:
        channel = str((cit or {}).get("channel") or (cit or {}).get("type") or "")
        if channel.startswith("Web:") or channel == "web":
            raise AssertionError(f"fabricated web citation in degraded path: {cit!r}")
    # also reject legacy string citations that look like web evidence
    for cit in citations:
        text = json.dumps(cit, ensure_ascii=False, default=str)
        assert "[Web:" not in text or "web" in set(allowed_channels), f"raw web marker leaked: {text[:200]}"


def make_offline_router(rag_docs: Optional[list] = None, web_side_effect: Any = None, web_results: Optional[list] = None):
    """Build a CulturalRouter with all network boundaries faked."""
    from sard.agent.cultural_router import CulturalRouter

    docs = list(rag_docs or [])

    def _rag_search(query: str, k: int):
        return list(docs)[:k]

    if web_side_effect is not None:
        def _parallel_search(**kwargs):
            return web_side_effect(**kwargs)
    else:
        _results = list(web_results or [])

        def _parallel_search(**kwargs):
            return list(_results)

    def _parallel_extract(**kwargs):
        return []

    def _multimodal(query: str, mock_files: Any = None, uploaded_files: Any = None):
        return []

    return CulturalRouter(
        rag_search_fn=_rag_search,
        parallel_search_fn=_parallel_search,
        parallel_extract_fn=_parallel_extract,
        multimodal_extract_fn=_multimodal,
    )


# ---------------------------------------------------------------------------
# Blob fakes (boundary F) — in-memory HTTP blob contract, no filesystem share
# ---------------------------------------------------------------------------

class FakeBlobBackend:
    """Minimal in-memory blob REST double: PUT data + PUT index, GET both.

    Keys are URL paths (unquoted). Enforces If-None-Match:* idempotency
    with HTTP 412 semantics for the index document.
    """

    def __init__(self):
        self.blobs: Dict[str, bytes] = {}
        self.indexes: Dict[str, bytes] = {}
        self.puts: list[str] = []
        self.gets: list[str] = []

    # -- helpers to inspect urllib Request objects -------------------------
    @staticmethod
    def _header(request: Any, name: str) -> str:
        wanted = name.lower()
        try:
            for key, value in request.header_items():
                if str(key).lower() == wanted:
                    return str(value)
        except Exception as exc:
            logger.debug("header probe via header_items skipped (%s)", type(exc).__name__)
        try:
            for key, value in dict(getattr(request, "headers", {}) or {}).items():
                if str(key).lower() == wanted:
                    return str(value)
        except Exception as exc:
            logger.debug("header probe via headers skipped (%s)", type(exc).__name__)
        try:
            for key, value in dict(getattr(request, "unredirected_hdrs", {}) or {}).items():
                if str(key).lower() == wanted:
                    return str(value)
        except Exception as exc:
            logger.debug("header probe via unredirected_hdrs skipped (%s)", type(exc).__name__)
        try:
            value = request.get_header(name)
            if value is not None:
                return str(value)
        except Exception as exc:
            logger.debug("header probe via get_header skipped (%s)", type(exc).__name__)
        return ""


class FakeBlobResponse:
    def __init__(self, payload: bytes = b"", status: int = 200):
        self._payload = bytes(payload)
        self.status = status

    def read(self, *args: Any, **kwargs: Any) -> bytes:
        return self._payload

    def __enter__(self) -> "FakeBlobResponse":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


def install_fake_blob(monkeypatch: pytest.MonkeyPatch, backend: FakeBlobBackend, endpoint: str = "https://blob.test"):
    """Patch urllib.request.urlopen so ConfigurableBlobArtifactStore stays offline."""
    import urllib.parse

    base = endpoint.rstrip("/")

    def _fake_urlopen(request: Any, timeout: Any = None):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        assert url.startswith(base), f"unexpected blob host: {url}"
        key = urllib.parse.unquote(url[len(base) + 1:])
        method = ""
        try:
            method = str(request.get_method()).upper()
        except Exception:
            method = "GET"
        data = getattr(request, "data", None)
        if method == "PUT":
            backend.puts.append(key)
            if_none_match = FakeBlobBackend._header(request, "If-None-Match")
            if key.endswith(".json"):
                if if_none_match.strip() == "*" and key in backend.indexes:
                    raise HTTPError(url, 412, "Precondition Failed", {}, io.BytesIO(b"exists"))
                backend.indexes[key] = bytes(data or b"")
            else:
                # data blobs are content-addressed by key; duplicate key with
                # If-None-Match:* must not silently overwrite.
                if if_none_match.strip() == "*" and key in backend.blobs:
                    raise HTTPError(url, 412, "Precondition Failed", {}, io.BytesIO(b"exists"))
                backend.blobs[key] = bytes(data or b"")
            ctype = FakeBlobBackend._header(request, "Content-Type")
            payload = json.dumps({"url": url, "contentType": ctype}).encode()
            return FakeBlobResponse(payload)
        backend.gets.append(key)
        if key.endswith(".json"):
            if key not in backend.indexes:
                raise HTTPError(url, 404, "Not Found", {}, io.BytesIO(b"missing"))
            return FakeBlobResponse(backend.indexes[key])
        if key not in backend.blobs:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b"missing"))
        return FakeBlobResponse(backend.blobs[key])

    monkeypatch.setattr(urllib_request, "urlopen", _fake_urlopen)
    return _fake_urlopen


@pytest.fixture
def blob_endpoint() -> str:
    return "https://blob.test"


@pytest.fixture
def fake_blob_backend() -> FakeBlobBackend:
    return FakeBlobBackend()


# ---------------------------------------------------------------------------
# Verification fakes (boundary G)
# ---------------------------------------------------------------------------

def make_claim_records(n_supported: int, n_unsupported: int, unsupported_index: Optional[int] = None):
    """Build ClaimRecord list: supported claims carry CIT ids, unsupported none/weak."""
    from sard.agent.state import ClaimRecord, ClaimStatus

    claims = []
    for i in range(n_supported + n_unsupported):
        is_unsupported = (
            (unsupported_index is not None and i == unsupported_index)
            or (unsupported_index is None and i >= n_supported)
        )
        cid = f"CIT-{100 + i:03d}"
        claims.append(
            ClaimRecord(
                claim_id=f"claim-{i:02d}",
                text=f"حقيقة موثقة رقم {i} عن التراث السعودي" if not is_unsupported else "ادعاء غير مدعوم عن تاريخ غير موثق",
                citation_ids=() if is_unsupported else (cid,),
                supporting_chunk_ids=(f"chunk-{i}",) if not is_unsupported else (),
                status=ClaimStatus.UNSUPPORTED if is_unsupported else ClaimStatus.SUPPORTED,
                explanation="" if not is_unsupported else "no covering evidence",
            )
        )
    return claims


def make_citation_sources(count: int):
    from sard.outputs.schemas import CitationSource

    return tuple(
        CitationSource(citation_id=f"CIT-{100 + i:03d}", title=f"مصدر موثق {i}", url=f"https://example.test/src-{i}")
        for i in range(count)
    )


# ---------------------------------------------------------------------------
# Artifact helpers
# ---------------------------------------------------------------------------

AR_TOPIC = "العمارة النجدية"
AR_TEXT = "تتميز العمارة النجدية باستخدام الطين واللبن والزخارف الجصية المثلثية.\n\nتضم قصور المصمك وطريف شواهد معمارية موثقة."


def artifact_request(fmt: str, **overrides: Any):
    from sard.outputs.orchestrator import ArtifactRequest

    params = dict(format=fmt, kind="document", title=f"تقرير: {AR_TOPIC}", topic=AR_TOPIC, raw_text=AR_TEXT)
    params.update(overrides)
    return ArtifactRequest(**params)


class CountingStore:
    """Wrap an ArtifactStore and count store_bytes calls (cancellation asserts)."""

    def __init__(self, inner: Any):
        self.inner = inner
        self.store_calls: list[dict] = []

    def store_bytes(self, artifact_id: str, filename: str, data: bytes, mime_type: str, metadata: Any = None):
        self.store_calls.append({"artifact_id": artifact_id, "filename": filename, "bytes": len(data or b"")})
        return self.inner.store_bytes(artifact_id, filename, data, mime_type, metadata)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)
