"""Failover web-search provider chain: Parallel -> Tavily -> Exa.

Every provider returns normalized :class:`SearchResult` records. Missing API
keys fail closed per-provider (``provider_unavailable`` telemetry) without
aborting the chain. No keys are ever logged or stored in results/telemetry.
When every provider is down the chain returns ``[]`` so RAG proceeds alone --
results are never fabricated.

Ownership (workstream D): this module + additive keys in
``sard/config/rag.py`` + ``.env.example`` SEARCH docs + adapter shims in
``cultural_tools.py`` / ``cultural_router.py``. Do NOT touch retrieval
fusion, verification, outputs, server, or frontend from here.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
import warnings
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Optional, Protocol, Sequence
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

logger = logging.getLogger("sard.rag.search_providers")

PARALLEL_SEARCH_URL = "https://api.parallel.ai/v1beta/search"
PARALLEL_EXTRACT_URL = "https://api.parallel.ai/v1beta/extract"
PARALLEL_BETA_HEADER = "search-extract-2025-10-10"
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"
EXA_SEARCH_URL = "https://api.exa.ai/search"
EXA_CONTENTS_URL = "https://api.exa.ai/contents"

_ARABIC_RE = re.compile(r"[\u0600-\u06FF]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_TRACKING_PARAMS = frozenset(
    {
        "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid", "_hsenc", "_hsmi",
        "igshid", "spm", "scm", "yclid", "dclid", "wbraid", "gbraid",
    }
)

_DEPTH_BUDGETS: dict[str, dict[str, int]] = {
    "simple": {"max_queries": 1, "per_provider": 5, "final_top": 5, "extract_n": 0},
    "normal": {"max_queries": 4, "per_provider": 10, "final_top": 10, "extract_n": 1},
    "deep": {"max_queries": 8, "per_provider": 20, "final_top": 12, "extract_n": 3},
}


# --------------------------------------------------------------------------
# Normalization helpers
# --------------------------------------------------------------------------

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonicalize_url(url: str) -> str:
    """Canonical URL with tracking params + fragments + trailing / stripped.

    Only scheme/host are case-insensitive per RFC 3986: the path keeps its
    original case (paths can be case-sensitive), and the trailing ``.lower()``
    applies to the recomposed ``scheme://netloc`` only. Lowering the path
    used to collapse distinct pages into one dedup bucket.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlparse(raw)
    except Exception:
        return raw.lower()
    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower()
    if not host:
        return raw.lower().rstrip("/")
    port = f":{parts.port}" if parts.port and parts.port not in (80, 443) else ""
    path = parts.path or ""
    if len(path) > 1:
        path = path.rstrip("/")
    kept = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS and not k.lower().startswith("utm_")
    ]
    query = urlencode(kept)
    netloc = f"{host}{port}".lower()
    return urlunparse((scheme, netloc, path, "", query, ""))


def domain_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def detect_language(text: str) -> str:
    """ar/en/other. Reuses the cultural bias helper rule: Arabic regex wins."""
    t = text or ""
    if _ARABIC_RE.search(t):
        return "ar"
    if _LATIN_RE.search(t):
        return "en"
    return "other"


def _normalize_for_hash(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def content_hash_of(title: str, content: str) -> str:
    return hashlib.sha1(_normalize_for_hash(f"{title}\n{content}").encode("utf-8")).hexdigest()


def _word_tokens(text: str) -> set[str]:
    return set(re.findall(r"[\w\u0600-\u06FF]+", (text or "").lower()))


def jaccard_similarity(a: str, b: str) -> float:
    ta, tb = _word_tokens(a), _word_tokens(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _preferred_domains() -> tuple[str, ...]:
    """Reuse cultural institutional list; local fallback avoids a hard import cycle."""
    try:
        from sard.agent.tools.cultural_tools import _PREFERRED_DOMAINS  # lazy: no cycle

        return tuple(_PREFERRED_DOMAINS)
    except Exception:
        return (".gov.sa", ".edu.sa", "unesco.org", "saudipedia.com", "spa.gov.sa")


def institutional_boost(url: str) -> int:
    lowered = (url or "").lower()
    return 1 if any(d in lowered for d in _preferred_domains()) else 0


# --------------------------------------------------------------------------
# Key resolution (env-only, never logged/stored)
# --------------------------------------------------------------------------

def resolve_parallel_key(explicit: Optional[str] = None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    return (os.environ.get("PARALLEL_API_KEY") or "").strip()


def resolve_tavily_key(explicit: Optional[str] = None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    canonical = (os.environ.get("TAVILY_API_KEY") or "").strip()
    if canonical:
        return canonical
    alias = (os.environ.get("TIVALY_API_KEY") or "").strip()
    if alias:
        msg = "TIVALY_API_KEY is deprecated; use TAVILY_API_KEY."
        warnings.warn(msg, DeprecationWarning, stacklevel=2)
        logger.warning(msg)
        return alias
    return ""


def resolve_exa_key(explicit: Optional[str] = None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    return (os.environ.get("EXA_API_KEY") or "").strip()


def _timeout_for(provider: str, settings: Any, default: float) -> float:
    attr = f"search_timeout_s_{provider}"
    if settings is not None and hasattr(settings, attr):
        try:
            return float(getattr(settings, attr))
        except (TypeError, ValueError):
            pass
    raw = (os.environ.get(f"SEARCH_TIMEOUT_S_{provider.upper()}") or "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return default


# --------------------------------------------------------------------------
# SearchResult
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class SearchResult:
    id: str
    provider: str
    query: str
    url: str
    canonical_url: str
    title: str
    snippet: str
    content: str
    published_at: Optional[str]
    retrieved_at: str
    author: str
    domain: str
    language: str
    content_hash: str
    metadata: dict[str, Any] = field(default_factory=dict)


def make_search_result(
    *,
    provider: str,
    query: str,
    url: str,
    title: str = "",
    snippet: str = "",
    content: str = "",
    published_at: Optional[str] = None,
    author: str = "",
    metadata: Optional[dict[str, Any]] = None,
) -> SearchResult:
    body = content or snippet or ""
    clip = (snippet or body)[:400]
    canon = canonicalize_url(url)
    chash = content_hash_of(title or "", body or "")
    rid = hashlib.sha1(f"{canon}|{chash}".encode("utf-8")).hexdigest()
    return SearchResult(
        id=rid,
        provider=provider,
        query=query or "",
        url=url or "",
        canonical_url=canon,
        title=title or "",
        snippet=clip,
        content=body or "",
        published_at=str(published_at) if published_at else None,
        retrieved_at=_utc_now_iso(),
        author=author or "",
        domain=domain_of(url or ""),
        language=detect_language(f"{title} {body}"),
        content_hash=chash,
        metadata=dict(metadata or {}),
    )


def dict_to_search_result(item: dict[str, Any], *, provider: str, query: str) -> SearchResult:
    """Adapt legacy/heterogeneous provider dicts (planner mismatch fix).

    Accepts excerpts/content, publish_date/published_date/publishedDate aliases.
    """
    url = item.get("url") or item.get("link") or ""
    title = item.get("title") or ""
    excerpts = item.get("excerpts")
    if isinstance(excerpts, str):
        excerpts = [excerpts]
    excerpt_text = " ".join([e for e in (excerpts or []) if e]).strip()
    content = (
        item.get("content") or excerpt_text or item.get("snippet")
        or item.get("text") or item.get("markdown") or ""
    )
    snippet = item.get("snippet") or excerpt_text or str(content)[:400]
    published = (
        item.get("published_at") or item.get("published_date") or item.get("publish_date")
        or item.get("publishedDate") or item.get("date") or None
    )
    author = item.get("author") or ""
    score = item.get("score", item.get("provider_score", ""))
    return make_search_result(
        provider=provider,
        query=query,
        url=url,
        title=title,
        snippet=str(snippet or ""),
        content=str(content or ""),
        published_at=published,
        author=str(author or ""),
        metadata={"provider_score": score, "raw_keys": sorted(item.keys())},
    )


def to_legacy_dict(res: SearchResult) -> dict[str, Any]:
    """Legacy adapter: carries BOTH excerpts/publish_date and content/published_date.

    The planner reads ``content``/``published_date`` while the cultural router
    reads ``excerpts`` -- both must be present (field-mismatch fix).
    """
    excerpt_list = [res.snippet] if res.snippet else []
    if res.content and res.content not in excerpt_list:
        excerpt_list.append(res.content[:2000])
    return {
        "url": res.url,
        "title": res.title,
        "excerpts": excerpt_list,
        "content": res.content or res.snippet,
        "snippet": res.snippet,
        "publish_date": res.published_at,
        "published_date": res.published_at,
        "published_at": res.published_at,
        "author": res.author,
        "domain": res.domain,
        "language": res.language,
        "provider": res.provider,
        "id": res.id,
        "canonical_url": res.canonical_url,
        "content_hash": res.content_hash,
        "metadata": dict(res.metadata or {}),
    }


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------

class SearchProvider(Protocol):
    name: str

    def has_key(self) -> bool: ...
    def search(
        self, objective: str, queries: Sequence[str],
        max_results: int = 8, timeout_s: float = 10.0,
    ) -> list[SearchResult]: ...
    def extract(
        self, urls: Sequence[str], objective: str = "",
        timeout_s: float = 15.0,
    ) -> list[dict[str, Any]]: ...


class ParallelProvider:
    """Parallel Search/Extract over httpx (same endpoint/shape as cultural_tools)."""

    name = "parallel"

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None,
                 timeout_s: float = 15.0) -> None:
        self._explicit_key = (api_key or "").strip()
        self._base_url = (base_url or os.environ.get("PARALLEL_SEARCH_BASE_URL")
                          or "https://api.parallel.ai/v1beta").rstrip("/")
        self._timeout_s = timeout_s

    def has_key(self) -> bool:
        return bool(resolve_parallel_key(self._explicit_key or None))

    def _key(self) -> str:
        return resolve_parallel_key(self._explicit_key or None)

    def search(self, objective: str, queries: Sequence[str],
               max_results: int = 8, timeout_s: float = 15.0) -> list[SearchResult]:
        key = self._key()
        if not key:
            return []
        qs = [q.strip() for q in (queries or []) if q and q.strip()] or [objective.strip()]
        body = {
            "objective": objective,
            "search_queries": list(qs),
            "max_results": max_results,
            "max_chars_per_result": 4000,
        }
        headers = {
            "Content-Type": "application/json",
            "x-api-key": key,
            "parallel-beta": PARALLEL_BETA_HEADER,
        }
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.post(f"{self._base_url}/search", headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
        items = data.get("results") or data.get("items") or []
        out: list[SearchResult] = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            out.append(dict_to_search_result(item, provider="parallel", query=qs[0]))
        return out

    def extract(self, urls: Sequence[str], objective: str = "",
                timeout_s: float = 15.0) -> list[dict[str, Any]]:
        key = self._key()
        safe = [u for u in (urls or []) if u and u.strip()][:3]
        if not key or not safe:
            return []
        headers = {
            "Content-Type": "application/json",
            "x-api-key": key,
            "parallel-beta": PARALLEL_BETA_HEADER,
        }
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.post(
                f"{self._base_url}/extract", headers=headers,
                json={"urls": safe, "objective": objective},
            )
            resp.raise_for_status()
            data = resp.json()
        items = data.get("results") or data.get("items") or []
        out: list[dict[str, Any]] = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            md = item.get("markdown") or item.get("content") or item.get("text") or ""
            out.append({
                "url": item.get("url") or "", "title": item.get("title") or "",
                "markdown": md, "content": md,
            })
        return out


class TavilyProvider:
    name = "tavily"

    def __init__(self, api_key: Optional[str] = None, timeout_s: float = 10.0) -> None:
        self._explicit_key = (api_key or "").strip()
        self._timeout_s = timeout_s

    def has_key(self) -> bool:
        return bool(resolve_tavily_key(self._explicit_key or None))

    def search(self, objective: str, queries: Sequence[str],
               max_results: int = 8, timeout_s: float = 10.0) -> list[SearchResult]:
        key = resolve_tavily_key(self._explicit_key or None)
        if not key:
            return []
        qs = [q.strip() for q in (queries or []) if q and q.strip()] or [objective.strip()]
        # Single-query API: fold the objective + variants into one query
        # string (deduped, capped) so every variant reaches the provider
        # without multiplying API spend with one call per variant.
        query_text = " ".join(dict.fromkeys([objective.strip(), *qs]))[:400].strip() or qs[0]
        body = {
            "api_key": key, "query": query_text, "search_depth": "advanced",
            "max_results": max_results, "include_answer": False,
        }
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.post(TAVILY_SEARCH_URL, json=body)
            resp.raise_for_status()
            data = resp.json()
        items = data.get("results") or data.get("items") or []
        return [
            dict_to_search_result(it, provider="tavily", query=qs[0])
            for it in (items if isinstance(items, list) else []) if isinstance(it, dict)
        ]

    def extract(self, urls: Sequence[str], objective: str = "",
                timeout_s: float = 15.0) -> list[dict[str, Any]]:
        key = resolve_tavily_key(self._explicit_key or None)
        safe = [u for u in (urls or []) if u and u.strip()][:3]
        if not key or not safe:
            return []
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.post(
                TAVILY_EXTRACT_URL,
                json={"api_key": key, "urls": safe, "query": objective, "extract_depth": "advanced"},
            )
            resp.raise_for_status()
            data = resp.json()
        items = data.get("results") or data.get("items") or []
        out: list[dict[str, Any]] = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            md = item.get("content") or item.get("markdown") or item.get("text") or ""
            out.append({
                "url": item.get("url") or "", "title": item.get("title") or "",
                "markdown": md, "content": md,
            })
        return out


class ExaProvider:
    name = "exa"

    def __init__(self, api_key: Optional[str] = None, timeout_s: float = 10.0) -> None:
        self._explicit_key = (api_key or "").strip()
        self._timeout_s = timeout_s

    def has_key(self) -> bool:
        return bool(resolve_exa_key(self._explicit_key or None))

    def search(self, objective: str, queries: Sequence[str],
               max_results: int = 8, timeout_s: float = 10.0) -> list[SearchResult]:
        key = resolve_exa_key(self._explicit_key or None)
        if not key:
            return []
        qs = [q.strip() for q in (queries or []) if q and q.strip()] or [objective.strip()]
        # Single-query API: fold the objective + variants into one query
        # string (deduped, capped) so every variant reaches the provider
        # without multiplying API spend with one call per variant.
        query_text = " ".join(dict.fromkeys([objective.strip(), *qs]))[:400].strip() or qs[0]
        headers = {"Content-Type": "application/json", "x-api-key": key}
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.post(
                EXA_SEARCH_URL,
                headers=headers,
                json={"query": query_text, "numResults": max_results, "type": "auto"},
            )
            resp.raise_for_status()
            data = resp.json()
        items = data.get("results") or data.get("items") or []
        return [
            dict_to_search_result(it, provider="exa", query=qs[0])
            for it in (items if isinstance(items, list) else []) if isinstance(it, dict)
        ]

    def extract(self, urls: Sequence[str], objective: str = "",
                timeout_s: float = 15.0) -> list[dict[str, Any]]:
        key = resolve_exa_key(self._explicit_key or None)
        safe = [u for u in (urls or []) if u and u.strip()][:3]
        if not key or not safe:
            return []
        headers = {"Content-Type": "application/json", "x-api-key": key}
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.post(
                EXA_CONTENTS_URL, headers=headers,
                json={"urls": safe, "text": True, "query": objective},
            )
            resp.raise_for_status()
            data = resp.json()
        items = data.get("results") or data.get("items") or []
        out: list[dict[str, Any]] = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            md = item.get("text") or item.get("content") or item.get("markdown") or ""
            out.append({
                "url": item.get("url") or "", "title": item.get("title") or "",
                "markdown": md, "content": md,
            })
        return out


# --------------------------------------------------------------------------
# Fanout orchestration
# --------------------------------------------------------------------------

def _classify_error(exc: BaseException, status_code: Optional[int] = None) -> tuple[str, str]:
    code = status_code
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            code = exc.response.status_code
        except Exception as exc2:
            logger.debug("status-code read skipped (%s)", type(exc2).__name__)
    if code == 429:
        return "http_429", "HTTPStatusError"
    if code == 401:
        return "http_401", "HTTPStatusError"
    if code is not None:
        return f"http_{code}", "HTTPStatusError"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout", type(exc).__name__
    name = type(exc).__name__
    if name in ("JSONDecodeError", "JSONError") or "json" in name.lower():
        return "malformed", name
    if isinstance(exc, ValueError):
        return "malformed", name
    return "exception", name


def _dedup_and_rerank(candidates: list[SearchResult]) -> list[SearchResult]:
    # 1. canonical_url -> keep highest institutional, tie earliest.
    # Hits with an empty canonical URL are kept under a per-URL fallback
    # key (never silently dropped): relative/malformed URLs still carry
    # content, and dropping them loses recall with zero telemetry.
    by_canon: dict[str, SearchResult] = {}
    order: dict[str, int] = {}
    for idx, res in enumerate(candidates):
        key = res.canonical_url or f"nocanon:{(res.url or '').strip().lower() or res.content_hash}"
        prev = by_canon.get(key)
        if prev is None:
            by_canon[key] = res
            order[key] = idx
        elif institutional_boost(res.url) > institutional_boost(prev.url):
            by_canon[key] = res
    stage = [by_canon[k] for k in sorted(by_canon, key=lambda k: order[k])]
    # 2. content_hash
    by_hash: dict[str, SearchResult] = {}
    hash_order: dict[str, int] = {}
    for idx, res in enumerate(stage):
        prev = by_hash.get(res.content_hash)
        if prev is None:
            by_hash[res.content_hash] = res
            hash_order[res.content_hash] = idx
        elif institutional_boost(res.url) > institutional_boost(prev.url):
            by_hash[res.content_hash] = res
    stage = [by_hash[k] for k in sorted(by_hash, key=lambda k: hash_order[k])]
    # 3. near-dup Jaccard >= 0.9 on content
    kept: list[SearchResult] = []
    for res in stage:
        dup_idx: Optional[int] = None
        for i, prev in enumerate(kept):
            if jaccard_similarity(prev.content or prev.snippet, res.content or res.snippet) >= 0.9:
                dup_idx = i
                break
        if dup_idx is None:
            kept.append(res)
        elif institutional_boost(res.url) > institutional_boost(kept[dup_idx].url):
            kept[dup_idx] = res
    # rerank: institutional first, then original order (stable)
    first_seen = {id(r): i for i, r in enumerate(kept)}
    return sorted(kept, key=lambda r: (-institutional_boost(r.url), first_seen[id(r)]))


def is_duplicate_of_seen(
    canonical_url: str,
    content: str,
    snippet: str,
    seen_canonicals: dict[str, list[str]],
) -> bool:
    """True when a web hit duplicates already-kept local evidence.

    Cross-dedup by canonical URL without dropping distinct content: the same
    canonical URL is a duplicate ONLY when its content is also the same
    (identical normalized hash) or near-identical (Jaccard >= 0.9). The same
    URL with distinct content is kept (different evidence, e.g. updated page
    vs cached excerpt).
    """
    if not canonical_url:
        return False
    prior_texts = seen_canonicals.get(canonical_url) or []
    if not prior_texts:
        return False
    body = content or snippet or ""
    if not body.strip():
        # A known URL with an empty web body is NOT a duplicate: the empty
        # snippet adds nothing, but dropping the hit would also skip the
        # extract-enrichment pass that could fill it. Keep it.
        return False
    for prior in prior_texts:
        if not prior:
            continue
        if _normalize_for_hash(body) == _normalize_for_hash(prior):
            return True
        try:
            if jaccard_similarity(prior, body) >= 0.9:
                return True
        except Exception as exc_sim:
            logger.debug("Cross-dedup similarity skipped (%s).", type(exc_sim).__name__)
            continue
    return False


def build_providers(settings: Any = None) -> list[Any]:
    return [
        ParallelProvider(timeout_s=_timeout_for("parallel", settings, 15.0)),
        TavilyProvider(timeout_s=_timeout_for("tavily", settings, 10.0)),
        ExaProvider(timeout_s=_timeout_for("exa", settings, 10.0)),
    ]


def _fanout_deadline(
    deadline: Any = None,
    cancel_event: Any = None,
    reserve_s: float = 0.0,
    label: str = "fanout_search",
) -> tuple[Any, Any]:
    """Coerce hierarchical Deadline/cancel for fanout (never raises)."""
    try:
        from sard.agent.deadline import coerce_deadline as _coerce
    except Exception as exc_import:
        logger.debug("Deadline import skipped (%s).", type(exc_import).__name__)
        return None, cancel_event
    try:
        if deadline is None and cancel_event is None:
            return None, None
        dl = _coerce(deadline, cancel_event=cancel_event, label=label)
        if dl is not None and cancel_event is None:
            try:
                cancel_event = getattr(dl, "cancel_event", None)
            except Exception as exc_cancel:
                logger.debug("Fanout cancel read skipped (%s).", type(exc_cancel).__name__)
        if dl is not None and reserve_s:
            try:
                dl.reserve_s = max(float(dl.reserve_s or 0.0), float(reserve_s))
            except Exception as exc_reserve:
                logger.debug("Fanout reserve set skipped (%s).", type(exc_reserve).__name__)
        return dl, cancel_event
    except Exception as exc_coerce:
        logger.debug("Fanout deadline coerce skipped (%s).", type(exc_coerce).__name__)
        return None, cancel_event


def _fanout_cancelled(cancel_event: Any) -> bool:
    try:
        return bool(cancel_event is not None and cancel_event.is_set())
    except Exception as exc_flag:
        logger.debug("Fanout cancel flag read skipped (%s).", type(exc_flag).__name__)
        return False


def _fanout_reserve_gone(dl: Any) -> bool:
    try:
        return bool(dl is not None and dl.reserve_remaining() <= 0)
    except Exception as exc_budget:
        logger.debug("Fanout budget check skipped (%s).", type(exc_budget).__name__)
        return False


def _provider_timeout_s(default_s: float, dl: Any, providers_left: int) -> float:
    """Per-provider timeout from RESERVE-PROTECTED remaining time.

    ``min(default, reserve_remaining() / providers_left)`` so sequential
    provider timeouts can never consume the terminal reserve. Without a
    Deadline the configured default applies unchanged.
    """
    try:
        default = max(0.05, float(default_s))
    except Exception:
        default = 10.0
    if dl is None:
        return default
    try:
        reserve_left = float(dl.reserve_remaining())
    except Exception as exc_reserve:
        logger.debug("Fanout reserve read skipped (%s).", type(exc_reserve).__name__)
        return default
    if reserve_left <= 0:
        return 0.05
    try:
        left = max(1, int(providers_left))
    except Exception:
        left = 1
    return max(0.05, min(default, reserve_left / left))


def fanout_search(
    objective: str,
    queries: Sequence[str],
    depth: str = "normal",
    settings: Any = None,
    max_results: Optional[int] = None,
    providers: Optional[Sequence[Any]] = None,
    run_extract: bool = True,
    deadline: Any = None,
    cancel_event: Any = None,
    reserve_s: float = 0.0,
) -> tuple[list[SearchResult], list[dict[str, Any]], dict[str, Any]]:
    """Fan out Parallel -> Tavily -> Exa with dedup + rerank + optional extracts.

    Request-aware depth: ``simple``/``normal``/``deep`` selects
    ``max_queries``/``per_provider``/``final_top``/``extract_n`` from
    ``_DEPTH_BUDGETS`` and those budgets actually reach providers
    (multi-query APIs get the sliced variant list; single-query APIs get
    the objective + variants folded into one query string; ``max_results``
    passed through). When callers also cap ``max_results``, providers are
    asked for at most that many each so we never fetch-then-truncate
    (e.g. fetch 10 per provider only to keep 3).

    Hierarchical ``deadline`` (Deadline or absolute monotonic float),
    ``cancel_event``, and ``reserve_s`` bound the chain: no new provider is
    launched once cancelled or once ``remaining - reserve <= 0``, and each
    provider's ``timeout_s`` is ``min(configured, reserve_remaining /
    providers_left)`` so sequential provider timeouts can never consume the
    terminal reserve. Cancellation raises typed ``DeadlineCancelledError``
    (never swallowed as an ordinary provider failure).

    Returns ``(results, telemetry, flags)``. Never raises for provider
    failures; never fabricates results. Telemetry entries carry
    ``{provider, ok, latency_ms, raw_n, kept_n, status, error_class}`` with
    no keys and no URLs.
    """
    from sard.agent.deadline import DeadlineCancelledError as _FanoutCancelled

    dl, cancel_event = _fanout_deadline(deadline, cancel_event, reserve_s)
    depth_norm = (depth or "normal").strip().lower()
    budget = _DEPTH_BUDGETS.get(depth_norm, _DEPTH_BUDGETS["normal"])
    qs = [q.strip() for q in (queries or []) if q and q.strip()]
    if not qs and (objective or "").strip():
        qs = [(objective or "").strip()]
    qs = qs[: budget["max_queries"]]

    # Avoid fetching then truncating: when the caller caps max_results,
    # ask each provider for at most that many (dedup still needs a small
    # pool, but never 10-per-provider when only 3 are wanted).
    per_provider_n = int(budget["per_provider"])
    final_top_n = int(budget["final_top"])
    if max_results is not None:
        try:
            cap = max(0, int(max_results))
        except (TypeError, ValueError):
            cap = final_top_n
        per_provider_n = min(per_provider_n, cap) if cap else 0
        final_top_n = min(final_top_n, cap) if cap else 0

    chain = list(providers) if providers is not None else build_providers(settings)
    timeouts = {
        "parallel": _timeout_for("parallel", settings, 15.0),
        "tavily": _timeout_for("tavily", settings, 10.0),
        "exa": _timeout_for("exa", settings, 10.0),
    }

    def _halt_telemetry(pname: str, status: str, klass: str) -> dict[str, Any]:
        return {
            "provider": pname, "ok": False, "latency_ms": 0.0,
            "raw_n": 0, "kept_n": 0, "status": status, "error_class": klass,
        }

    telemetry: list[dict[str, Any]] = []
    pool: list[SearchResult] = []
    origin_index: dict[str, int] = {}

    if _fanout_cancelled(cancel_event):
        raise _FanoutCancelled("cancelled before fanout_search", stage="fanout_search")

    for index, provider in enumerate(chain):
        pname = getattr(provider, "name", type(provider).__name__)
        # Never launch a new provider on a cancelled or reserve-exhausted
        # budget: halt the chain here instead of overrunning the parent.
        if _fanout_cancelled(cancel_event):
            raise _FanoutCancelled(f"cancelled before provider '{pname}'", stage="fanout_search")
        if _fanout_reserve_gone(dl):
            telemetry.append(_halt_telemetry(pname, "timeout", "DeadlineBudget"))
            break
        t0 = time.monotonic()
        if not provider.has_key():
            telemetry.append({
                "provider": pname, "ok": False, "latency_ms": 0.0,
                "raw_n": 0, "kept_n": 0, "status": "provider_unavailable",
                "error_class": "MissingKey",
            })
            continue
        providers_left = len(chain) - index
        timeout_s = _provider_timeout_s(timeouts.get(pname, 10.0), dl, providers_left)
        try:
            hits = provider.search(
                objective, qs,
                max_results=per_provider_n,
                timeout_s=timeout_s,
            ) or []
            latency = (time.monotonic() - t0) * 1000
            for hit in hits:
                if hit.id not in origin_index:
                    origin_index[hit.id] = len(origin_index)
                pool.append(hit)
            telemetry.append({
                "provider": pname, "ok": True, "latency_ms": round(latency, 1),
                "raw_n": len(hits), "kept_n": 0, "status": "ok", "error_class": "",
            })
        except _FanoutCancelled:
            raise
        except httpx.HTTPStatusError as exc:
            if _fanout_cancelled(cancel_event):
                raise _FanoutCancelled(f"cancelled during provider '{pname}'", stage="fanout_search") from exc
            code: Optional[int] = None
            try:
                code = exc.response.status_code
            except Exception as exc_code:
                logger.debug("Search status-code read skipped (%s).", type(exc_code).__name__)
                code = None
            status, klass = _classify_error(exc, code)
            # Never log URLs or keys -- counts only.
            logger.warning("search provider '%s' HTTP failure: %s", pname, status)
            telemetry.append({
                "provider": pname, "ok": False,
                "latency_ms": round((time.monotonic() - t0) * 1000, 1),
                "raw_n": 0, "kept_n": 0, "status": status, "error_class": klass,
            })
        except Exception as exc:  # timeout / malformed / transport -- continue chain
            if _fanout_cancelled(cancel_event):
                raise _FanoutCancelled(f"cancelled during provider '{pname}'", stage="fanout_search") from exc
            status, klass = _classify_error(exc)
            logger.warning("search provider '%s' failed: %s (%s)", pname, status, klass)
            telemetry.append({
                "provider": pname, "ok": False,
                "latency_ms": round((time.monotonic() - t0) * 1000, 1),
                "raw_n": 0, "kept_n": 0, "status": status, "error_class": klass,
            })

    ranked = _dedup_and_rerank(pool)[:final_top_n]

    # Optional extract enrichment for top-N (depth budgets; never fabricates).
    # Bound by the same remaining budget: skip enrichment entirely when the
    # reserve is gone or cancellation arrived.
    extract_n = budget["extract_n"] if run_extract else 0
    if extract_n and ranked:
        if _fanout_cancelled(cancel_event):
            raise _FanoutCancelled("cancelled before fanout extract", stage="fanout_search")
        if _fanout_reserve_gone(dl):
            extract_n = 0
    if extract_n and ranked:
        targets = [r for r in ranked[:extract_n] if r.url]
        enriched: dict[str, str] = {}
        for index, provider in enumerate(chain):
            if not targets or not provider.has_key():
                continue
            if _fanout_cancelled(cancel_event):
                raise _FanoutCancelled("cancelled during fanout extract", stage="fanout_search")
            if _fanout_reserve_gone(dl):
                break
            try:
                docs = provider.extract(
                    [t.url for t in targets if t.url not in enriched],
                    objective,
                    timeout_s=_provider_timeout_s(
                        timeouts.get(getattr(provider, "name", ""), 10.0), dl, len(chain) - index
                    ),
                ) or []
                for doc in docs:
                    curl = canonicalize_url(doc.get("url", ""))
                    md = (doc.get("markdown") or doc.get("content") or "").strip()
                    if curl and md and curl not in enriched:
                        enriched[curl] = md[:8000]
            except Exception as exc_extract:
                logger.debug("extract via '%s' skipped (%s)",
                             getattr(provider, "name", "?"), type(exc_extract).__name__)
                continue
            if len(enriched) >= len(targets):
                break
        if enriched:
            new_ranked: list[SearchResult] = []
            for res in ranked:
                md = enriched.get(res.canonical_url)
                if md:
                    # Preserve the pre-enrichment identity (id/retrieved_at):
                    # enriching content must not invalidate cached IDs.
                    new_ranked.append(replace(
                        res, content=md,
                        metadata={**(res.metadata or {}), "extract_enriched": True},
                    ))
                else:
                    new_ranked.append(res)
            ranked = new_ranked

    by_provider: dict[str, int] = {}
    for res in ranked:
        by_provider[res.provider] = by_provider.get(res.provider, 0) + 1
    for entry in telemetry:
        entry["kept_n"] = by_provider.get(entry["provider"], 0)

    flags = {
        "provider_unavailable": any(t["status"] == "provider_unavailable" for t in telemetry),
        "web_unavailable_warning": len(ranked) == 0,
        "providers_attempted": [t["provider"] for t in telemetry],
        "depth": depth_norm,
    }
    if not ranked:
        logger.warning("fanout_search: all providers down or unkeyed; returning [] (RAG proceeds).")
    return ranked, telemetry, flags


def fanout_extract(
    urls: Sequence[str],
    objective: str = "",
    settings: Any = None,
    providers: Optional[Sequence[Any]] = None,
) -> list[dict[str, Any]]:
    """First-success extract across the chain (Parallel -> Tavily -> Exa)."""
    chain = list(providers) if providers is not None else build_providers(settings)
    safe = [u for u in (urls or []) if u and u.strip()][:3]
    if not safe:
        return []
    for provider in chain:
        if not provider.has_key():
            continue
        try:
            docs = provider.extract(safe, objective) or []
            if docs:
                return docs
        except Exception as exc:
            logger.debug("fanout_extract via '%s' skipped (%s)",
                         getattr(provider, "name", "?"), type(exc).__name__)
            continue
    return []
