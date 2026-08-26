"""Central RAG + NVIDIA NIM configuration and model-instance factory.

Mirrors the pattern in :mod:`sard.config.models`: this is the ONLY module
that should import ``langchain_nvidia_ai_endpoints``. Every other RAG module
(embeddings, reranking, query rewriting, generation) asks this module for a
LangChain-compatible model instance and never imports the NVIDIA SDK/
integration package directly.

NVIDIA NIM supports both the hosted API Catalog and self-hosted
deployments. Chat, embedding, and rerank NIMs may live on different hosts,
so each has its own optional base-URL override; when unset, the hosted
NVIDIA API Catalog default is used.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

DEFAULT_HOSTED_BASE_URL = "https://integrate.api.nvidia.com/v1"


class NVIDIAConfigError(Exception):
    """Raised for invalid/incomplete NVIDIA or RAG configuration.

    Messages are safe to surface to end users (no secrets, no raw SDK
    payloads).
    """


@dataclass(frozen=True)
class ModelRoute:
    """An ordered logical model route: primary then fallbacks."""

    use_case: str
    primary: str
    fallbacks: tuple[str, ...] = ()

    @property
    def ordered(self) -> tuple[str, ...]:
        return (self.primary, *self.fallbacks)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise NVIDIAConfigError(f"{name} must be a valid number.")


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise NVIDIAConfigError(f"{name} must be a valid integer.")


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise NVIDIAConfigError(f"{name} must be one of true/false, 1/0, yes/no, or on/off.")



def _env_tuple(name: str) -> tuple[str, ...]:
    raw = _env(name)
    return tuple(v.strip() for v in raw.split(",") if v.strip()) if raw else ()


@dataclass(frozen=True)
class RAGSettings:
    nvidia_api_key: str

    chat_base_url: Optional[str]
    embedding_base_url: Optional[str]
    rerank_base_url: Optional[str]

    chat_route: ModelRoute
    query_route: ModelRoute
    embedding_route: ModelRoute
    embedding_fallback_model: str  # separate-collection fallback (nv-embed-v1)
    rerank_route: ModelRoute
    vision_route: ModelRoute
    translation_route: ModelRoute
    safety_route: ModelRoute

    request_timeout_seconds: float
    max_retries: int

    zvec_collection_path: str
    dense_candidates: int
    fts_candidates: int
    fused_candidates: int
    final_top_k: int
    enable_query_rewrite: bool
    enable_fts: bool
    enable_rerank: bool
    parallel_api_key: str = ""
    parallel_search_base_url: str = "https://api.parallel.ai/v1beta"

    def validate(self) -> "RAGSettings":
        """Validate settings that would otherwise fail much later in a request.

        This intentionally validates shape and safe local bounds only.  It does
        not claim that a configured NVIDIA model ID exists in a catalog or that
        a deployment is reachable; ``models`` and the live smoke test handle
        those checks explicitly.
        """
        routes = (
            self.chat_route,
            self.query_route,
            self.embedding_route,
            self.rerank_route,
            self.vision_route,
            self.translation_route,
            self.safety_route,
        )
        for route in routes:
            if not route.primary.strip() or any(not model.strip() for model in route.fallbacks):
                raise NVIDIAConfigError(
                    f"معرّف نموذج فارغ في مسار {route.use_case}. راجع إعدادات NVIDIA_*_MODEL_*."
                )
        if not self.embedding_fallback_model.strip():
            raise NVIDIAConfigError("NVIDIA_EMBEDDING_MODEL_FALLBACK لا يجوز أن يكون فارغًا.")
        if self.request_timeout_seconds <= 0:
            raise NVIDIAConfigError("NVIDIA_REQUEST_TIMEOUT_SECONDS يجب أن يكون أكبر من صفر.")
        if self.max_retries < 1:
            raise NVIDIAConfigError("NVIDIA_MAX_RETRIES يجب أن يكون 1 أو أكثر.")
        for name, value in (
            ("RAG_DENSE_CANDIDATES", self.dense_candidates),
            ("RAG_FTS_CANDIDATES", self.fts_candidates),
            ("RAG_FUSED_CANDIDATES", self.fused_candidates),
            ("RAG_FINAL_TOP_K", self.final_top_k),
        ):
            if value < 1:
                raise NVIDIAConfigError(f"{name} يجب أن يكون 1 أو أكثر.")
        if not self.zvec_collection_path.strip():
            raise NVIDIAConfigError("ZVEC_COLLECTION_PATH لا يجوز أن يكون فارغًا.")
        return self


def get_rag_settings() -> RAGSettings:
    """Read RAG + NVIDIA configuration from environment variables.

    Does not validate network reachability or API-key validity — only
    that required *shape* (non-empty model IDs) is present when a route is
    actually used. Individual factories below raise :class:`NVIDIAConfigError`
    for missing credentials at the point of use.
    """
    settings = RAGSettings(
        nvidia_api_key=_env("NVIDIA_API_KEY"),
        chat_base_url=_env("NVIDIA_CHAT_BASE_URL") or None,
        embedding_base_url=_env("NVIDIA_EMBEDDING_BASE_URL") or None,
        rerank_base_url=_env("NVIDIA_RERANK_BASE_URL") or None,
        chat_route=ModelRoute(
            use_case="generation",
            primary=_env("NVIDIA_CHAT_MODEL_PRIMARY", "nemotron-3-super-120b-a12b"),
            fallbacks=(
                _env("NVIDIA_CHAT_MODEL_FALLBACK_1", "qwen3-next-80b-a3b-instruct"),
                _env("NVIDIA_CHAT_MODEL_FALLBACK_2", "llama-3.3-70b-instruct"),
            ),
        ),
        query_route=ModelRoute(
            use_case="query_rewrite",
            primary=_env("NVIDIA_QUERY_MODEL_PRIMARY", "nemotron-3-nano-30b-a3b"),
            fallbacks=(
                _env("NVIDIA_QUERY_MODEL_FALLBACK_1", "nvidia-nemotron-nano-9b-v2"),
                _env("NVIDIA_QUERY_MODEL_FALLBACK_2", "llama-3.1-8b-instruct"),
            ),
        ),
        embedding_route=ModelRoute(
            use_case="embedding",
            primary=_env("NVIDIA_EMBEDDING_MODEL_PRIMARY", "nemotron-3-embed-1b"),
            fallbacks=(),  # the fallback model builds a SEPARATE collection; see embeddings.py
        ),
        embedding_fallback_model=_env("NVIDIA_EMBEDDING_MODEL_FALLBACK", "nv-embed-v1"),
        rerank_route=ModelRoute(
            use_case="rerank",
            primary=_env("NVIDIA_RERANK_MODEL_PRIMARY", "rerank-qa-mistral-4b"),
            fallbacks=(),
        ),
        vision_route=ModelRoute(
            use_case="vision",
            primary=_env("NVIDIA_VISION_MODEL_PRIMARY", "llama-3.1-nemotron-nano-vl-8b-v1"),
            fallbacks=(
                _env("NVIDIA_VISION_MODEL_FALLBACK_1", "nemotron-nano-12b-v2-vl"),
                _env("NVIDIA_VISION_MODEL_FALLBACK_2", "muse-glimmer-30b"),
            ),
        ),
        translation_route=ModelRoute(
            use_case="translation",
            primary=_env("NVIDIA_TRANSLATE_MODEL_PRIMARY", "riva-translate-4b-instruct-v2"),
            fallbacks=(_env("NVIDIA_TRANSLATE_MODEL_FALLBACK", "riva-translate-4b-instruct-v1_1"),),
        ),
        safety_route=ModelRoute(
            use_case="safety",
            primary=_env("NVIDIA_SAFETY_MODEL_PRIMARY", "nemotron-3.5-content-safety"),
            fallbacks=(
                _env("NVIDIA_SAFETY_MODEL_FALLBACK_1", "llama-3.1-nemotron-safety-guard-8b-v3"),
                _env("NVIDIA_SAFETY_MODEL_FALLBACK_2", "llama-guard-4-12b"),
            ),
        ),
        request_timeout_seconds=_env_float("NVIDIA_REQUEST_TIMEOUT_SECONDS", 30.0),
        max_retries=_env_int("NVIDIA_MAX_RETRIES", 2),
        zvec_collection_path=_env("ZVEC_COLLECTION_PATH", "data/zvec/sard"),
        dense_candidates=_env_int("RAG_DENSE_CANDIDATES", 30),
        fts_candidates=_env_int("RAG_FTS_CANDIDATES", 30),
        fused_candidates=_env_int("RAG_FUSED_CANDIDATES", 20),
        final_top_k=_env_int("RAG_FINAL_TOP_K", 6),
        enable_query_rewrite=_env_bool("RAG_ENABLE_QUERY_REWRITE", True),
        enable_fts=_env_bool("RAG_ENABLE_FTS", True),
        enable_rerank=_env_bool("RAG_ENABLE_RERANK", True),
        parallel_api_key=_env("PARALLEL_API_KEY"),
        parallel_search_base_url=_env("PARALLEL_SEARCH_BASE_URL", "https://api.parallel.ai/v1beta"),
    )
    return settings.validate()


def _require_api_key_if_hosted(base_url: Optional[str], api_key: str) -> None:
    if base_url is None and not api_key:
        raise NVIDIAConfigError(
            "مفتاح NVIDIA_API_KEY غير موجود. مطلوب عند استخدام NVIDIA API "
            "Catalog المستضاف. إذا كنت تشغّل NIM ذاتيًا، حدّد رابط القاعدة "
            "(base URL) الخاص به بدلاً من ذلك."
        )


def build_chat_model(model_id: str, settings: Optional[RAGSettings] = None):
    """Return a LangChain ``ChatNVIDIA`` instance for the given model ID.

    Callers depend only on LangChain's ``BaseChatModel`` interface — this
    is the sole place ``langchain_nvidia_ai_endpoints`` is imported for chat.
    """
    settings = settings or get_rag_settings()
    _require_api_key_if_hosted(settings.chat_base_url, settings.nvidia_api_key)

    try:
        from langchain_nvidia_ai_endpoints import ChatNVIDIA
    except ImportError as exc:
        raise NVIDIAConfigError(
            "حزمة langchain-nvidia-ai-endpoints غير مثبّتة. ثبّتها عبر: "
            "uv sync --extra nvidia"
        ) from exc

    kwargs = {"model": model_id, "timeout": settings.request_timeout_seconds}
    if settings.nvidia_api_key:
        kwargs["api_key"] = settings.nvidia_api_key
    if settings.chat_base_url:
        kwargs["base_url"] = settings.chat_base_url
    return ChatNVIDIA(**kwargs)



def build_embeddings_model(model_id: str, settings: Optional[RAGSettings] = None):
    """Return a LangChain ``NVIDIAEmbeddings`` instance for the given model ID."""
    settings = settings or get_rag_settings()
    _require_api_key_if_hosted(settings.embedding_base_url, settings.nvidia_api_key)

    try:
        from langchain_nvidia_ai_endpoints import NVIDIAEmbeddings
    except ImportError as exc:
        raise NVIDIAConfigError(
            "حزمة langchain-nvidia-ai-endpoints غير مثبّتة. ثبّتها عبر: "
            "uv sync --extra nvidia"
        ) from exc

    kwargs = {"model": model_id, "timeout": settings.request_timeout_seconds}
    if settings.nvidia_api_key:
        kwargs["api_key"] = settings.nvidia_api_key
    if settings.embedding_base_url:
        kwargs["base_url"] = settings.embedding_base_url
    return NVIDIAEmbeddings(**kwargs)


def build_rerank_model(model_id: str, settings: Optional[RAGSettings] = None):
    """Return a LangChain ``NVIDIARerank`` instance for the given model ID."""
    settings = settings or get_rag_settings()
    _require_api_key_if_hosted(settings.rerank_base_url, settings.nvidia_api_key)

    try:
        from langchain_nvidia_ai_endpoints import NVIDIARerank
    except ImportError as exc:
        raise NVIDIAConfigError(
            "حزمة langchain-nvidia-ai-endpoints غير مثبّتة. ثبّتها عبر: "
            "uv sync --extra nvidia"
        ) from exc

    kwargs = {"model": model_id, "timeout": settings.request_timeout_seconds}
    if settings.nvidia_api_key:
        kwargs["api_key"] = settings.nvidia_api_key
    if settings.rerank_base_url:
        kwargs["base_url"] = settings.rerank_base_url
    return NVIDIARerank(**kwargs)


def list_available_models(
    kind: str, settings: Optional[RAGSettings] = None
) -> list[str]:
    """Best-effort live model discovery via the NVIDIA integration.

    ``kind`` is one of "chat", "embedding", "rerank". Returns an empty list
    (never raises) if discovery is unavailable — callers must fall back to
    explicitly configured model IDs in that case, per the "doctor" command
    and the fail-fast-with-actionable-message requirement.
    """
    settings = settings or get_rag_settings()
    try:
        from langchain_nvidia_ai_endpoints import ChatNVIDIA, NVIDIAEmbeddings, NVIDIARerank
    except ImportError:
        return []

    cls = {"chat": ChatNVIDIA, "embedding": NVIDIAEmbeddings, "rerank": NVIDIARerank}.get(kind)
    if cls is None:
        return []

    try:
        kwargs = {}
        if settings.nvidia_api_key:
            kwargs["api_key"] = settings.nvidia_api_key
        base_url = {
            "chat": settings.chat_base_url,
            "embedding": settings.embedding_base_url,
            "rerank": settings.rerank_base_url,
        }[kind]
        if base_url:
            kwargs["base_url"] = base_url
        models = cls.get_available_models(**kwargs)
        return [getattr(m, "id", str(m)) for m in models]
    except Exception:
        return []
