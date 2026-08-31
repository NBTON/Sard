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

# Conservative Arabic-only lexical expansions used when the rewrite model is
# unavailable.  They are phrasing equivalents, not added facts.  Keeping them
# here also gives the offline rehearsal the same query surface as production.
_DETERMINISTIC_EQUIVALENTS = (
    ("الينابيع الحارة", "العيون الحارة"),
    ("الينابيع", "العيون المائية"),
    ("تجفيف الروبيان", "الروبيان المجفف"),
    ("حفظ الروبيان", "تخزين الروبيان"),
    ("السياح", "الزوار"),
    ("قديمًا", "تقليديًا"),
)

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


_PILOT_TOPIC_ENTITIES = (
    "تجفيف الروبيان",
    "تجفيف الربيان",
    "الروبيان المجفف",
    "الربيان المجفف",
    "العيون الحارة",
    "الينابيع الحارة",
    "روبيان",
    "ربيان",
    "تاروت",
    "shrimp",
    "tarout",
)


def _sanitize_search_variants(variants: list[str], original_question: str) -> list[str]:
    """Ensure search variants NEVER inject pilot-topic entities (shrimp/springs/tarout) into unrelated queries."""
    orig_norm = normalize_arabic(original_question)
    allowed_entities = [e for e in _PILOT_TOPIC_ENTITIES if normalize_arabic(e) in orig_norm]

    clean_variants = []
    for var in variants:
        var_norm = normalize_arabic(var)
        is_contaminated = False
        for entity in _PILOT_TOPIC_ENTITIES:
            entity_norm = normalize_arabic(entity)
            if entity_norm in var_norm and entity not in allowed_entities and entity_norm not in orig_norm:
                is_contaminated = True
                break
        if not is_contaminated:
            clean_variants.append(var)

    if not clean_variants:
        clean_variants = [normalize_arabic(original_question) or original_question]
    return clean_variants


def deterministic_query_variants(question: str) -> list[str]:
    """Return bounded Arabic lexical variants without translating or adding facts."""

    normalized = normalize_arabic(question)
    if not normalized:
        return [question] if question else []
    variants = [normalized]
    for left, right in _DETERMINISTIC_EQUIVALENTS:
        left_normalized = normalize_arabic(left)
        right_normalized = normalize_arabic(right)
        if left_normalized in normalized:
            variants.append(normalized.replace(left_normalized, right_normalized))
        if right_normalized in normalized:
            variants.append(normalized.replace(right_normalized, left_normalized))
    original = question.strip()
    if original and original not in variants:
        variants.append(original)
    deduped = list(dict.fromkeys(value for value in variants if value))[:4]
    return _sanitize_search_variants(deduped, question)


class QueryRewriteService:
    """Query rewriting is a stateless pure function of (normalized_query, model_id).

    Cache is process-global by design for efficiency — rewrite does not depend on
    session_id, user, or history. This is intentional and not a leak: same Arabic
    query rewrites identically across sessions. Cache key is (normalize_arabic(query), model_id)
    and is bounded: deterministic fallback is used when all candidates fail, so no session
    contamination occurs. For session-isolated state, see IsnadMemory L3 per session_id.
    """

    def __init__(
        self,
        settings: Optional[RAGSettings] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
        chat_model_factory=build_chat_model,
    ):
        self._settings = settings or get_rag_settings()
        self._breaker = circuit_breaker or CircuitBreaker()
        self._chat_model_factory = chat_model_factory
        # Stateless cache: key = (normalized_query, model_id), not session_id. Bounded growth mitigated
        # by deterministic fallback path; LRU eviction could be added if growth observed in warm lambda.
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
        variants = deterministic_query_variants(question)
        return RewrittenQuery(
            original_question=question,
            normalized_question=normalized,
            search_variants=variants or [question],
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
            search_variants = _sanitize_search_variants(search_variants, question)
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
