"""Routing and failure-classification helpers for the agent graph.

Contains the verify -> compose/render conditional decision, retrieval-mode
normalization (Step 3 modes to the required Step 5 set), exception-to-failure
classification, and the deterministic assembly of an honest partial answer.
"""

from __future__ import annotations

from typing import Optional

from sard.agent.events import FailureKind, GraphNodeError
from sard.agent.state import ClaimStatus, RAGMode
from sard.rag.fallbacks import FailureCategory, classify_exception


def classify_failure_to_kind(exc: BaseException) -> FailureKind:
    """Map any exception to a fixed :class:`FailureKind`."""
    if isinstance(exc, GraphNodeError):
        return exc.kind
    # Cancellation is typed, never a timeout: callers match on kind, not on
    # message substrings.
    try:
        from sard.agent.deadline import DeadlineCancelledError as _Cancelled

        if isinstance(exc, _Cancelled):
            return FailureKind.CANCELLED
    except Exception:
        pass
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
    ClaimStatus.NON_FACTUAL,
}


def stable_evidence_id_local(source_id: str, chunk_id: str, content: str) -> str:
    """Stable evidence ID (routing-side copy to avoid a verify import cycle)."""
    import hashlib

    digest = hashlib.sha1((content or "").encode("utf-8")).hexdigest()[:8]
    return f"{source_id}:{chunk_id}:{digest}"


def evidence_ordinals_local(evidence) -> dict[str, int]:
    """Render-time ordinal map (citation_id -> [1],[2],[3]) without mutating IDs."""
    ordinals: dict[str, int] = {}
    for index, item in enumerate(evidence or (), start=1):
        cid = getattr(item, "citation_id", "") or ""
        if cid and cid not in ordinals:
            ordinals[cid] = index
    return ordinals


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
    """Honest partial Arabic answer containing verified claims only, per-scope.

    Claims are grouped by their ``scope`` (paragraph / table-row bucket from
    the verify node) so a single unsupported claim strips only its own claim
    text while its scope-mates survive.  Citation IDs stay stable; callers may
    map them to render-time ordinals via :func:`evidence_ordinals_local`.
    """
    verified = collect_verified_claims(state)
    if not verified:
        return (
            "لم تكتمل الصياغة النهائية: لا تتوفر حقائق مُتحقق منها من المصادر المدخلة. "
            "أُنشئت إجابة جزئية صريحة بدلاً من ادعاء غير مدعوم."
        )
    scope_survival = state.get("scope_survival") or {}
    by_scope: dict[str, list] = {}
    for claim in verified:
        scope = getattr(claim, "scope", "answer") or "answer"
        by_scope.setdefault(scope, []).append(claim)
    lines = [
        "توفرت أدلة جزئية فقط. فيما يلي ما يمكن تأكيده من المصادر المتاحة:",
        "",
    ]
    for scope in sorted(by_scope):
        bucket = scope_survival.get(scope) if isinstance(scope_survival, dict) else None
        claims = by_scope[scope]
        if len(by_scope) > 1:
            if bucket:
                lines.append(f"[{scope}: نجاة {bucket.get('survived', len(claims))}/{bucket.get('total', len(claims))}]")
            else:
                lines.append(f"[{scope}]")
        for claim in claims:
            text = claim.text.strip()
            citations = " ".join(f"[{cid}]" for cid in claim.citation_ids) if claim.citation_ids else ""
            flagged = " (صف مُعلَّم عالي المخاطر)" if getattr(claim, "flagged_row", False) else ""
            lines.append(f"- {text} {citations}{flagged}".rstrip())
        lines.append("")
    unsupported = collect_unsupported_claims(state)
    if unsupported:
        dropped = len(unsupported)
        lines.append(
            f"ملاحظة: تم استبعاد {dropped} ادعاءً لم تتوفر له تغطية موثوقة في المصادر."
        )
        per_scope = {}
        for claim in unsupported:
            scope = getattr(claim, "scope", "answer") or "answer"
            per_scope[scope] = per_scope.get(scope, 0) + 1
        if len(per_scope) > 1 or len(by_scope) > 1:
            detail = "؛ ".join(f"{scope}: {count}" for scope, count in sorted(per_scope.items()))
            lines.append(f"التوزيع per-scope للفقرات المستبعدة: {detail}.")
    return "\n".join(lines).rstrip()


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