"""Arabic query rewriting via ChatNVIDIA, with deterministic fallback.

Route (per spec):

1. Primary: ``NVIDIA_QUERY_MODEL_PRIMARY``
2. Fallback 1: ``NVIDIA_QUERY_MODEL_FALLBACK_1``
3. Fallback 2: ``NVIDIA_QUERY_MODEL_FALLBACK_2``
4. Final fallback: deterministic normalization of the original question
   (:func:`sard.rag.normalize.normalize_arabic`) — retrieval must keep
   working even if every rewrite model is unavailable.

The rewriter never translates the query, never adds facts absent from the
question, and always includes the normalized original query among the
search variants. Successful rewrites are cached by (normalized query,
resolved model ID).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

from sard.config.rag import RAGSettings, build_chat_model, get_rag_settings
from sard.rag.fallbacks import (
    AllCandidatesFailedError,
    CircuitBreaker,
    FailureCategory,
    FallbackClassifiedError,
    FallbackEvent,
    ModelCandidate,
    run_with_fallback,
)
from sard.rag.normalize import normalize_arabic
from sard.rag.schemas import RewrittenQuery

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)

_SYSTEM_PROMPT = (
    "أنت أداة إعادة صياغة استعلامات بحث عربية لنظام استرجاع معلومات (RAG). "
    "لا تُجب عن السؤال، ولا تضف معلومات غير موجودة فيه، ولا تترجمه إلى لغة "
    "أخرى. أعد فقط كائن JSON صالحًا بالحقول التالية:\n"
    '{"normalized_question": "...", "search_variants": ["...", "..."], '
    '"entities": ["..."], "topic_filter": "..." or null, '
    '"exact_phrases": ["..."]}\n'
    "قدّم من اثنين إلى أربعة صيغ بحث عربية بديلة (search_variants) تحافظ على "
    "المعنى الأصلي دون ترجمة أو اختلاق حقائق جديدة."
)


@dataclass
class _RewriteCacheKey:
    normalized_query: str
    model_version: str


class QueryRewriteService:
    def __init__(
        self,
        settings: Optional[RAGSettings] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
        chat_model_factory=build_chat_model,
    ):
        self._settings = settings or get_rag_settings()
        self._breaker = circuit_breaker or CircuitBreaker()
        self._chat_model_factory = chat_model_factory
        self._cache: dict[tuple[str, str], RewrittenQuery] = {}

    def _candidates(self) -> list[ModelCandidate]:
        route = self._settings.query_route
        endpoint_type = "self_hosted" if self._settings.chat_base_url else "hosted"
        candidates = [
            ModelCandidate(model_id=route.primary, endpoint_type=endpoint_type, label="primary")
        ]
        for i, fb in enumerate(route.fallbacks, start=1):
            candidates.append(
                ModelCandidate(model_id=fb, endpoint_type=endpoint_type, label=f"fallback_{i}", degraded=True)
            )
        return candidates

    def _deterministic_fallback(self, question: str) -> RewrittenQuery:
        normalized = normalize_arabic(question)
        variants = [normalized]
        if normalized != question.strip():
            variants.append(question.strip())
        return RewrittenQuery(
            original_question=question,
            normalized_question=normalized,
            search_variants=variants[:4] or [question],
            entities=[],
            topic_filter=None,
            exact_phrases=[],
            rewrite_succeeded=False,
            model_used=None,
        )

    def rewrite(self, question: str) -> tuple[RewrittenQuery, list[FallbackEvent]]:
        if not question or not question.strip():
            return self._deterministic_fallback(question), []

        normalized_cache_key = normalize_arabic(question)

        if not self._settings.enable_query_rewrite:
            return self._deterministic_fallback(question), []

        candidates = self._candidates()

        def call(candidate: ModelCandidate) -> RewrittenQuery:
            cache_key = (normalized_cache_key, candidate.model_id)
            if cache_key in self._cache:
                return self._cache[cache_key]

            model = self._build_chat_model(candidate)
            from langchain_core.messages import HumanMessage, SystemMessage

            response = model.invoke(
                [SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=question)]
            )
            content = getattr(response, "content", "")
            if not isinstance(content, str):
                content = str(content)

            match = _JSON_BLOCK_RE.search(content)
            if not match:
                raise FallbackClassifiedError(
                    FailureCategory.MALFORMED_OUTPUT,
                    "Query rewrite model did not return a JSON object.",
                )
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError as exc:
                raise FallbackClassifiedError(
                    FailureCategory.MALFORMED_OUTPUT, f"Query rewrite JSON parse failed: {exc}"
                ) from exc

            normalized_question = normalize_arabic(
                str(data.get("normalized_question") or normalized_cache_key)
            ) or normalized_cache_key
            search_variants = [
                normalize_arabic(str(v))
                for v in (data.get("search_variants") or [])
                if str(v).strip()
            ]
            search_variants = [v for v in search_variants if v]
            if normalized_cache_key not in search_variants:
                search_variants = [normalized_cache_key, *search_variants]
            search_variants = search_variants[:4] or [normalized_cache_key]

            result = RewrittenQuery(
                original_question=question,
                normalized_question=normalized_question,
                search_variants=search_variants,
                entities=[str(e) for e in (data.get("entities") or [])],
                topic_filter=(str(data["topic_filter"]) if data.get("topic_filter") else None),
                exact_phrases=[str(p) for p in (data.get("exact_phrases") or [])],
                rewrite_succeeded=True,
                model_used=candidate.model_id,
            )
            self._cache[cache_key] = result
            return result

        try:
            result, events = run_with_fallback(
                "query_rewrite",
                candidates,
                call,
                max_retries_per_candidate=max(1, self._settings.max_retries),
                circuit_breaker=self._breaker,
            )
            return result, events
        except AllCandidatesFailedError as exc:
            return self._deterministic_fallback(question), exc.events

    def _build_chat_model(self, candidate: ModelCandidate):
        settings = self._settings
        if candidate.endpoint_type == "hosted":
            effective_settings = RAGSettings(**{**settings.__dict__, "chat_base_url": None})
        else:
            effective_settings = settings
        return self._chat_model_factory(candidate.model_id, effective_settings)
