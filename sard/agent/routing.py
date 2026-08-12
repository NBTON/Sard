"""Routing and failure-classification helpers for the agent graph.

Contains the verify -> compose/render conditional decision, retrieval-mode
normalization (Step 3 modes to the required Step 5 set), exception-to-failure
classification, and the deterministic assembly of an honest partial answer.
"""

from __future__ import annotations

from typing import Optional

from sard.agent.events import FailureKind, GraphNodeError
from sard.agent.state import ClaimStatus, GraphState, RAGMode
from sard.rag.fallbacks import FailureCategory, classify_exception


def classify_failure_to_kind(exc: BaseException) -> FailureKind:
    """Map any exception to a fixed :class:`FailureKind`."""
    if isinstance(exc, GraphNodeError):
        return exc.kind
    category = classify_exception(exc)
    mapping = {
        FailureCategory.AUTHENTICATION: FailureKind.AUTH,
        FailureCategory.MODEL_UNAVAILABLE: FailureKind.MODEL_UNAVAILABLE,
        FailureCategory.RATE_LIMIT: FailureKind.RATE_LIMIT,
        FailureCategory.TIMEOUT: FailureKind.TIMEOUT,
        FailureCategory.MALFORMED_OUTPUT: FailureKind.INVALID_STRUCTURED_OUTPUT,
        FailureCategory.INVALID_REQUEST: FailureKind.INVALID_STRUCTURED_OUTPUT,
        FailureCategory.ZVEC_UNAVAILABLE: FailureKind.ZVEC_UNAVAILABLE,
        FailureCategory.EMBEDDING_DIMENSION_MISMATCH: FailureKind.EMBEDDING_MISMATCH,
        FailureCategory.ZVEC_SCHEMA_MISMATCH: FailureKind.EMBEDDING_MISMATCH,
        FailureCategory.CONTEXT_LENGTH: FailureKind.MODEL_UNAVAILABLE,
    }
    return mapping.get(category, FailureKind.MODEL_UNAVAILABLE)


def normalize_retrieval_mode(raw_mode: str, reranker_used: Optional[str]) -> str:
    """Normalize Step 3 retrieval modes to the required Step 5 set.

    ``hybrid`` splits into ``hybrid_reranked`` (an actual model rerank ran)
    or ``hybrid_fused`` (deterministic RRF/dense ranking only); the rest map
    one-to-one onto ``dense_only``, ``full_text_only`` and ``unavailable``.
    """
    base = (raw_mode or "").strip().lower()
    if base == "hybrid":
        if reranker_used and reranker_used.strip().lower() in ("nvidia", "rerank"):
            return RAGMode.HYBRID_RERANKED.value
        return RAGMode.HYBRID_FUSED.value
    if base == "dense_only":
        return RAGMode.DENSE_ONLY.value
    if base == "fts_only_emergency" or base == "full_text_only":
        return RAGMode.FULL_TEXT_ONLY.value
    return RAGMode.UNAVAILABLE.value


_ACCEPTED_STATUSES = {
    ClaimStatus.SUPPORTED,
    ClaimStatus.PARTIALLY_SUPPORTED,
    ClaimStatus.USER_PROVIDED,
    ClaimStatus.EXPLICITLY_UNCERTAIN,
}


def collect_verified_claims(state: dict) -> list:
    """Latest claims whose status is safe to surface to the user."""
    claims = state.get("atomic_claims") or []
    return [claim for claim in claims if claim.status in _ACCEPTED_STATUSES]


def collect_unsupported_claims(state: dict) -> list:
    claims = state.get("atomic_claims") or []
    return [
        claim
        for claim in claims
        if claim.status
        in {
            ClaimStatus.UNSUPPORTED,
            ClaimStatus.CONTRADICTED,
            ClaimStatus.NON_FACTUAL,
        }
    ]


def assemble_partial_answer(state: dict) -> str:
    """Honest partial Arabic answer containing verified claims only."""
    verified = collect_verified_claims(state)
    if not verified:
        return (
            "لم تكتمل الصياغة النهائية: لا تتوفر حقائق مُتحقق منها من المصادر المدخلة. "
            "أُنشئت إجابة جزئية صريحة بدلاً من ادعاء غير مدعوم."
        )
    lines = [
        "توفرت أدلة جزئية فقط. فيما يلي ما يمكن تأكيده من المصادر المتاحة:",
        "",
    ]
    for claim in verified:
        text = claim.text.strip()
        citations = " ".join(f"[{cid}]" for cid in claim.citation_ids) if claim.citation_ids else ""
        lines.append(f"- {text} {citations}".rstrip())
    unsupported = collect_unsupported_claims(state)
    if unsupported:
        dropped = len(unsupported)
        lines.append("")
        lines.append(
            f"ملاحظة: تم استبعاد {dropped} ادعاءً لم تتوفر له تغطية موثوقة في المصادر."
        )
    return "\n".join(lines)


def route_after_verification(state: dict) -> str:
    """Conditional edge target: ``compose`` (retry) or ``render``.

    Retry count is incremented inside the verify node when it fails; routing
    only mirrors that decision so the graph structure stays explicit.
    """
    result = state.get("verification_result")
    if result is not None and result.passed:
        return "render"
    retry_count = int(state.get("compose_retry_count", 0))
    max_retries = int(state.get("compose_max_retries", 0))
    if retry_count <= max_retries:
        return "compose"
    return "render"