"""Claim-level verification node: ``verify``.

Deterministic support checks are authoritative; semantic model output is only
advisory and may narrow (partial) or remove (contradicted/non-factual)
claims.  Unknown citation IDs auto-fail, duplicate citation records and
missing source metadata fail deterministically, and coverage must reach 100%
external factual coverage before the verification passes.  Failure history is
preserved; retries are capped by ``compose_retry_count``.
"""

from __future__ import annotations

import re
import time
from typing import Optional

from sard.agent.events import (
    EVENT_CITATION_COVERAGE_CALCULATED,
    EVENT_COMPLETED,
    EVENT_DEGRADED,
    EVENT_FAILED,
    EVENT_RETRIED,
    EVENT_STARTED,
    FailureKind,
    adapt_fallback_events,
    make_error,
    make_event,
)
from sard.agent.prompts.verify import (
    VERIFY_OUTPUT_KEYS,
    VERIFY_SYSTEM_PROMPT,
    VERIFY_USER_TEMPLATE,
)
from sard.agent.routing import assemble_partial_answer
from sard.agent.state import (
    ClaimRecord,
    ClaimStatus,
    CoverageReport,
    EvidenceItem,
    VerificationResult,
    VerificationRound,
)
from sard.outputs.schemas import INLINE_CITATION_RE

_ACCEPTED = {
    ClaimStatus.SUPPORTED,
    ClaimStatus.PARTIALLY_SUPPORTED,
    ClaimStatus.USER_PROVIDED,
    ClaimStatus.EXPLICITLY_UNCERTAIN,
}
_REMOVED = {
    ClaimStatus.UNSUPPORTED,
    ClaimStatus.CONTRADICTED,
    ClaimStatus.NON_FACTUAL,
}

_USER_TEXT_RE = re.compile(r"(أود|أريد|أخطط|أفضّل|نفَضّل|أبحث|أحتاج|أنا|نحن)")
_UNCERTAIN_RE = re.compile(r"(غير مؤكد|غير معروف|لا أعلم|قد|ربما|غير متأكد|لا تتوفر معلومات|معلومات محدودة|تحتاج إلى تأكيد)")


def _is_valid_http_url(url: str) -> bool:
    """Validate that a source URL is a real HTTP(S) URL (no bare IDs or empty)."""
    if not url or not isinstance(url, str):
        return False
    url = url.strip()
    if " " in url or "\n" in url:
        return False
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc) and "." in parsed.netloc
    except Exception:
        return False


_AR_DIACRITICS_RE = re.compile(r"[\u064B-\u0652\u0670\u0640]")


def _content_tokens(text: str) -> set[str]:
    """Normalize Arabic/Latin text into content tokens for overlap checks."""
    if not text:
        return set()
    cleaned = _AR_DIACRITICS_RE.sub("", text)
    cleaned = re.sub(r"[^\w\u0600-\u06FF]+", " ", cleaned.lower())
    stop = {
        "في", "من", "على", "إلى", "إلي", "عن", "مع", "هذا", "هذه", "ذلك", "التي", "الذي",
        "و", "أو", "أن", "إن", "قد", "لا", "ما", "هو", "هي", "تم", "يتم", "بين", "بعد",
        "قبل", "خلال", "the", "and", "for", "with", "from",
    }
    tokens = {t for t in cleaned.split() if len(t) >= 2 and t not in stop}
    return tokens


def _excerpt_supports_claim(claim_text: str, evidence_contents: list[str]) -> bool:
    """Require lexical grounding: claim shares content tokens with cited excerpts.

    A claim is only SUPPORTED when at least one cited excerpt contains
    overlapping substantive tokens (or the claim is near-verbatim contained).
    This prevents marking answers verified merely because citation IDs exist.
    """
    claim_norm = (claim_text or "").strip()
    if not claim_norm:
        return False
    claim_tokens = _content_tokens(claim_norm)
    if not claim_tokens:
        return False
    for excerpt in evidence_contents:
        if not excerpt:
            continue
        excerpt_norm = excerpt.strip()
        # Near-verbatim containment (either direction, length-guarded)
        if len(claim_norm) >= 12 and claim_norm in excerpt_norm:
            return True
        if len(excerpt_norm) >= 12 and excerpt_norm in claim_norm:
            return True
        excerpt_tokens = _content_tokens(excerpt_norm)
        if not excerpt_tokens:
            continue
        shared = claim_tokens & excerpt_tokens
        if len(shared) >= 2:
            return True
        # Single strong token + length overlap for very short claims
        smaller = min(len(claim_tokens), len(excerpt_tokens))
        if smaller and (len(shared) / smaller) >= 0.5 and len(shared) >= 1 and len(claim_tokens) <= 4:
            return True
        if smaller and (len(shared) / smaller) >= 0.34 and len(shared) >= 2:
            return True
    return False


def _split_claims(draft: str) -> list[str]:
    segments = [segment.strip() for segment in re.split(r"(?<=[.!؟؟\u2026])\s+|\n+", draft) if segment.strip()]
    return segments


def _status_choice(claim_id: str, deterministic_status: ClaimStatus, model_status: Optional[ClaimStatus]) -> ClaimStatus:
    if deterministic_status is ClaimStatus.UNSUPPORTED:
        return ClaimStatus.UNSUPPORTED
    if model_status in _REMOVED:
        return model_status
    if model_status is ClaimStatus.PARTIALLY_SUPPORTED:
        return ClaimStatus.PARTIALLY_SUPPORTED
    return ClaimStatus.SUPPORTED


def verify(state: dict, deps) -> dict:
    run = state.get("run_id") or ""
    start = time.monotonic()
    events = [
        make_event(EVENT_STARTED, run, "verify", "started", summary="بدء التحقق من الادعاءات")
    ]

    draft = state.get("draft")
    evidence: list[EvidenceItem] = list(state.get("evidence") or [])
    round_index = int(state.get("compose_retry_count", 0)) + 1
    semantic_model_used = None
    semantic_degraded = False
    fallback_events = []

    if not draft or not draft.strip() or not evidence:
        from sard.agent.routing import assemble_partial_answer as _assemble_partial

        coverage = CoverageReport(
            total_claims=0,
            external_claims=0,
            covered_claims=0,
            coverage_ratio=0.0,
            note="لا توجد أدلة موثقة؛ لا يمكن منح صفة التحقق.",
        )
        feedback = "لا توجد أدلة مسترجعة لدعم الإجابة — يلزم الامتناع أو طلب التوضيح."
        result = VerificationResult(
            passed=False,
            verified_claim_ids=(),
            unsupported_claim_ids=(),
            feedback=feedback,
        )
        events.append(
            make_event(
                EVENT_CITATION_COVERAGE_CALCULATED,
                run,
                "verify",
                "completed",
                summary="لا أدلة — لا يمكن التحقق",
                coverage=0.0,
            )
        )
        # Terminal (no retry): recomposing without evidence cannot ground new
        # claims, so mark exhaustion now. The router then goes to render
        # instead of looping compose↔verify forever.
        max_retries = int(state.get("compose_max_retries", 0))
        exhausted_count = max_retries + 1
        events.append(
            make_event(EVENT_COMPLETED, run, "verify", "failed", summary="تعذر التحقق: لا أدلة موثقة — إجابة جزئية صريحة", duration_ms=(time.monotonic() - start) * 1000)
        )
        partial_state = {**state, "atomic_claims": []}
        # Preserve an honest compose abstention verbatim when one exists;
        # otherwise fall back to the explicit partial template.
        existing_draft = (draft or "").strip()
        final_text = existing_draft or _assemble_partial(partial_state)
        return {
            "atomic_claims": [],
            "claim_citation_mapping": {},
            "unsupported_claims": [],
            "coverage": coverage,
            "verification_result": result,
            "verification_feedback": [feedback],
            "verification_history": [VerificationRound(round_index, False, (), ())],
            "verification_exhausted": True,
            "compose_retry_count": exhausted_count,
            "final_answer": final_text,
            "graph_outcome": "partial",
            "model_routes": {"verify": semantic_model_used},
            "fallback_events": fallback_events,
            "timings": {"verify_ms": (time.monotonic() - start) * 1000},
            "progress_events": events,
        }

    valid_cits = {item.citation_id for item in evidence}
    chunk_by_cit: dict[str, str] = {item.citation_id: item.chunk_id for item in evidence}
    item_by_cit: dict[str, EvidenceItem] = {item.citation_id: item for item in evidence}
    duplicate_cits = {
        item.citation_id
        for item in evidence
        if [other.citation_id for other in evidence].count(item.citation_id) > 1
    }

    claim_records: list[ClaimRecord] = []
    model_suggestions: dict[str, ClaimStatus] = {}
    model_corrections: dict[str, str] = {}

    model_service = getattr(deps, "model_service", None)
    segments = _split_claims(draft)
    pending_records: list[ClaimRecord] = []
    for seq, segment in enumerate(segments, start=1):
        claim_id = f"CLAIM-{round_index:02d}-{seq:03d}"
        citation_ids = tuple(dict.fromkeys(INLINE_CITATION_RE.findall(segment)))
        text = INLINE_CITATION_RE.sub("", segment).strip()

        deterministic_status = ClaimStatus.SUPPORTED
        explanation = ""
        if not citation_ids:
            if _USER_TEXT_RE.search(text):
                deterministic_status = ClaimStatus.USER_PROVIDED
            elif _UNCERTAIN_RE.search(text):
                deterministic_status = ClaimStatus.EXPLICITLY_UNCERTAIN
            else:
                deterministic_status = ClaimStatus.UNSUPPORTED
                explanation = "لا يوجد أي معرّف استشهاد يدعم هذا الادعاء."
        elif any(cid not in valid_cits for cid in citation_ids):
            deterministic_status = ClaimStatus.UNSUPPORTED
            unknown = [cid for cid in citation_ids if cid not in valid_cits]
            explanation = f"معرّفات استشهاد غير معروفة في الأدلة: {', '.join(unknown)}"
        elif any(cid in duplicate_cits for cid in citation_ids):
            deterministic_status = ClaimStatus.UNSUPPORTED
            explanation = "سجل استشهاد مكرر في الأدلة لهذا الادعاء."
        else:
            has_provenance_problem = False
            cited_contents: list[str] = []
            for cid in citation_ids:
                item = item_by_cit.get(cid)
                if item is None or not (item.title or "").strip() or not _is_valid_http_url(item.source_url or ""):
                    deterministic_status = ClaimStatus.UNSUPPORTED
                    explanation = "بيانات مصدر ناقصة (عنوان أو رابط HTTP(S) صالح) لهذا الاستشهاد."
                    has_provenance_problem = True
                    break
                if not (item.chunk_id or "").strip():
                    deterministic_status = ClaimStatus.UNSUPPORTED
                    explanation = "معرّف مقطع (chunk) مفقود لهذا الاستشهاد — لا يمكن ربط الادعاء بالمصدر."
                    has_provenance_problem = True
                    break
                if not (item.content or "").strip() or len((item.content or "").strip()) < 20:
                    deterministic_status = ClaimStatus.UNSUPPORTED
                    explanation = "المقتطف المصدر فارغ أو قصير جدًا — لا يمكن التحقق من دعم الادعاء."
                    has_provenance_problem = True
                    break
                cited_contents.append(item.content or "")
            if not has_provenance_problem:
                # Trustworthy behavior: citation existence alone is never enough.
                # Require relevant captured excerpts to lexically support the claim.
                if not _excerpt_supports_claim(text, cited_contents):
                    deterministic_status = ClaimStatus.UNSUPPORTED
                    explanation = "الاستشهادات موجودة لكن مقتطفات المصادر لا تدعم نص هذا الادعاء."

        supporting_chunks = tuple(
            dict.fromkeys(chunk_by_cit[cid] for cid in citation_ids if cid in chunk_by_cit)
        )
        claim_records.append(
            ClaimRecord(
                claim_id=claim_id,
                text=text,
                citation_ids=citation_ids,
                supporting_chunk_ids=supporting_chunks,
                status=deterministic_status,
                explanation=explanation or "",
            )
        )
        if deterministic_status is ClaimStatus.SUPPORTED:
            pending_records.append(claim_records[-1])

    if pending_records and model_service is not None:
        evidence_text = "\n\n".join(
            f"[{item.citation_id}] {item.title} — {item.source_name}\n{item.content[:700]}"
            for item in evidence
        )
        claims_text = "\n".join(f"- {record.claim_id}: {record.text}" for record in pending_records)
        user = VERIFY_USER_TEMPLATE.format(claims=claims_text)
        parsed, response = model_service.invoke_json(
            "verify",
            VERIFY_SYSTEM_PROMPT.format(evidence=evidence_text),
            user,
            allowed_keys=VERIFY_OUTPUT_KEYS,
        )
        fallback_events = adapt_fallback_events(response.events)
        if parsed is not None and isinstance(parsed.get("claims"), list):
            for entry in parsed["claims"]:
                if not isinstance(entry, dict):
                    continue
                claim_id = entry.get("claim_id")
                raw_status = entry.get("status")
                if isinstance(raw_status, str):
                    try:
                        model_suggestions[claim_id] = ClaimStatus(raw_status)
                    except ValueError:
                        pass
                if isinstance(entry.get("correction"), str):
                    model_corrections[claim_id] = entry["correction"]
            semantic_model_used = response.model_used
        elif not response.success:
            semantic_degraded = True

    final_records: list[ClaimRecord] = []
    for record in claim_records:
        if record.status is ClaimStatus.SUPPORTED:
            suggested = model_suggestions.get(record.claim_id)
            status = _status_choice(record.claim_id, record.status, suggested)
            correction = model_corrections.get(record.claim_id, "")
            final_records.append(
                ClaimRecord(
                    claim_id=record.claim_id,
                    text=record.text,
                    citation_ids=record.citation_ids,
                    supporting_chunk_ids=record.supporting_chunk_ids,
                    status=status,
                    explanation=record.explanation,
                    correction=correction,
                )
            )
        else:
            final_records.append(record)

    external = [record for record in final_records if record.status is not ClaimStatus.USER_PROVIDED]
    verified = [record for record in final_records if record.status in _ACCEPTED]
    removed = [record for record in final_records if record.status in _REMOVED]
    external_good = [record for record in external if record.status in _ACCEPTED]

    total_external = len(external)
    covered = len(external_good)
    coverage_ratio = (covered / total_external) if total_external else 1.0
    passed = len(removed) == 0

    coverage = CoverageReport(
        total_claims=len(final_records),
        external_claims=total_external,
        covered_claims=covered,
        coverage_ratio=round(coverage_ratio, 4),
        uncovered_claim_ids=tuple(record.claim_id for record in removed),
        model_used=semantic_model_used,
        note="" if passed else "تغطية خارجية أقل من 100% — توجد ادعاءات غير مدعومة.",
    )

    claim_citation_mapping = {
        record.claim_id: list(record.citation_ids) for record in final_records
    }

    events.append(
        make_event(
            EVENT_CITATION_COVERAGE_CALCULATED,
            run,
            "verify",
            "completed",
            summary=f"تغطية الاستشهادات: {coverage_ratio:.0%}"
            if total_external
            else "لا توجد ادعاءات خارجية للتغطية",
            coverage=coverage_ratio,
            source_count=len(evidence),
        )
    )

    if passed:
        result = VerificationResult(
            passed=True,
            verified_claim_ids=tuple(record.claim_id for record in verified),
            unsupported_claim_ids=(),
            feedback="",
        )
        duration_ms = (time.monotonic() - start) * 1000
        events.append(
            make_event(
                EVENT_COMPLETED,
                run,
                "verify",
                "completed",
                summary="تحقق ناجح: التغطية كاملة",
                duration_ms=duration_ms,
                coverage=coverage_ratio,
            )
        )
        if semantic_degraded:
            events.append(
                make_event(
                    EVENT_DEGRADED,
                    run,
                    "verify",
                    "degraded",
                    summary="التحقق الدلالي غير متاح — الاعتماد على الفحوص الحتمية فقط",
                    degraded=True,
                )
            )
        return {
            "atomic_claims": final_records,
            "claim_citation_mapping": claim_citation_mapping,
            "unsupported_claims": [],
            "coverage": coverage,
            "verification_result": result,
            "verification_history": [
                VerificationRound(
                    round_index,
                    True,
                    tuple(record.claim_id for record in verified),
                    (),
                )
            ],
            "model_routes": {"verify": semantic_model_used},
            "fallback_events": fallback_events,
            "timings": {"verify_ms": duration_ms},
            "progress_events": events,
            "warnings": (["التحقق الدلالي غير متاح؛ الفحوص الحتمية وحدها حاسمة."] if semantic_degraded else []),
        }

    feedback_lines = []
    for record in removed:
        feedback_lines.append(
            f"- الادعاء «{record.text}» غير مدعوم من الأدلة ({record.status.value}). "
            f"أعِد صياغته بالاعتماد حصرًا على المعرفات المدعومة أو انقله خارج الإجابة."
        )
    feedback = "\n".join(feedback_lines) or "يجب معالجة التغطية إلى 100%."

    max_retries = int(state.get("compose_max_retries", 0))
    retry_count = int(state.get("compose_retry_count", 0)) + 1
    # The configured cap counts re-compositions after the initial attempt.
    exhausted = retry_count > max_retries

    result = VerificationResult(
        passed=False,
        verified_claim_ids=tuple(record.claim_id for record in verified),
        unsupported_claim_ids=tuple(record.claim_id for record in removed),
        feedback=feedback,
    )

    updates: dict = {
        "atomic_claims": final_records,
        "claim_citation_mapping": claim_citation_mapping,
        "unsupported_claims": [record.claim_id for record in removed],
        "coverage": coverage,
        "verification_result": result,
        "verification_feedback": [feedback],
        "verification_history": [
            VerificationRound(
                round_index,
                False,
                tuple(record.claim_id for record in verified),
                tuple(record.claim_id for record in removed),
            )
        ],
    }

    if not exhausted:
        updates["compose_retry_count"] = retry_count
        events.append(
            make_event(
                EVENT_RETRIED,
                run,
                "verify",
                "retried",
                summary=f"إعادة الصياغة بعد فشل التغطية (محاولة {retry_count})",
                retry=retry_count,
                coverage=coverage_ratio,
                degraded=True,
            )
        )
        status = "completed"
        summary = "فشل التحقق — إعادة التوجيه إلى الصياغة"
    else:
        updates["verification_exhausted"] = True
        updates["compose_retry_count"] = retry_count
        partial_state = {**state, "atomic_claims": final_records}
        updates["final_answer"] = assemble_partial_answer(partial_state)
        updates["graph_outcome"] = "partial"
        events.append(
            make_event(
                EVENT_FAILED,
                run,
                "verify",
                "failed",
                summary="استُنفدت محاولات المراجعة — إجابة جزئية صريحة فقط",
                coverage=coverage_ratio,
                retry=retry_count,
                degraded=True,
            )
        )
        updates["errors"] = [
            make_error(
                run,
                "verify",
                FailureKind.VERIFICATION_EXHAUSTED,
                "استُنفدت محاولات التغطية؛ أُنتجت إجابة جزئية من الادعاءات الموثوقة فقط.",
                retryable=False,
            )
        ]
        status = "failed"
        summary = "استُنفدت التغطية — إجابة جزئية"

    duration_ms = (time.monotonic() - start) * 1000
    events.append(
        make_event(
            EVENT_COMPLETED,
            run,
            "verify",
            status,
            summary=summary,
            duration_ms=duration_ms,
            coverage=coverage_ratio,
        )
    )
    updates["model_routes"] = {"verify": semantic_model_used}
    updates["fallback_events"] = fallback_events
    updates["timings"] = {"verify_ms": duration_ms}
    updates["progress_events"] = events
    return updates
