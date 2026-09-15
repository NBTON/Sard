"""Claim-level verification node: ``verify`` (workstream G).

Per-scope survival replaces the old whole-document gate.  Deterministic
layers L1-L6 are authoritative and short-circuit cheap->expensive; the
constrained model (L7) runs ONLY for L4-L6 disagreements and all high-risk
claims and may only narrow (see :func:`_status_choice`).
"""

from __future__ import annotations

import hashlib
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
    ClaimStatus.NON_FACTUAL,
}
_REMOVED = {
    ClaimStatus.UNSUPPORTED,
    ClaimStatus.CONTRADICTED,
}
_EXEMPT_STATUSES = {
    ClaimStatus.USER_PROVIDED,
    ClaimStatus.EXPLICITLY_UNCERTAIN,
    ClaimStatus.NON_FACTUAL,
}

CLASS_FACTUAL = "factual"
CLASS_HIGH_RISK = "high_risk_factual"
CLASS_NON_FACTUAL = "non_factual"
CLASS_INTERPRETIVE = "interpretive"
CLASS_USER = "user_provided"
CLASS_UNCERTAIN = "uncertain"

REASON_CODES = (
    "unknown_citation",
    "missing_provenance",
    "lexical_gap",
    "entity_mismatch",
    "contradiction",
    "partial_scope",
    "non_factual",
)

_USER_TEXT_RE = re.compile(r"(أود|أريد|أخطط|أفضّل|نفَضّل|أبحث|أحتاج|أنا|نحن)")
_UNCERTAIN_RE = re.compile(
    r"(غير مؤكد|غير معروف|لا أعلم|غير متأكد|لا تتوفر معلومات|معلومات محدودة|تحتاج إلى تأكيد|"
    r"(?<![\w\u0600-\u06FF])قد(?![\w\u0600-\u06FF])|(?<![\w\u0600-\u06FF])ربما(?![\w\u0600-\u06FF]))"
)
_NON_FACTUAL_RE = re.compile(
    r"(فيما يلي|أولاً|ثانياً|ثانيًا|أخيراً|أخيرًا|باختصار|في الختام|ملاحظة عامة|"
    r"نوصي|نقترح|يُنصح|من الأفضل|يُفضل generally|رائع|جميل|ممتع|"
    r"أتمنى|نأمل|مع أطيب|شكراً|شكرًا|مرحباً|مرحبًا|أهلاً|بالإضافة|علاوة على|"
    r"من الجدير|استمتع|نتمنى لك)"
)
_INTERPRETIVE_RE = re.compile(r"(يشير إلى|يدل على|يعكس|يمكن تفسير|يبدو أن|من المحتمل|يوحي|يُظهر أن|يُشير|يُقصد)")
_HEDGE_RE = re.compile(
    r"((?<![\w\u0600-\u06FF])قد(?![\w\u0600-\u06FF])|(?<![\w\u0600-\u06FF])ربما(?![\w\u0600-\u06FF])|"
    r"يبدو|محتمل|غالبًا|غالبا|نسبيًا|نسبيا|إلى حد)"
)
_HIGH_RISK_RE = re.compile(
    r"(سلامة|خطر|تحذير|طوارئ|إسعاف|ساعة|مواعيد|دوام|يفتح|يغلق|ساعات العمل|"
    r"ريال|دولار|رسوم|سعر|تكلفة|أسعار|التكلفة|الدخول|تذكرة|"
    r"قانون|تأشيرة|فيزا|إقامة|جواز|تصريح|غرامة|مخالفة|"
    r"طبي|دواء|علاج|صحة|مستشفى|عيادة|لقاح|"
    r"بنك|استثمار|قرض|مالي|مالية|تمويل|"
    r"مسجد|صلاة|حج|عمرة|ديني|دينية|فتوى|حلال|حرام|"
    r"\d{4}-\d{2}-\d{2}|\d+\s?/\s?\d+|يناير|فبراير|مارس|أبريل|مايو|يونيو|يوليو|"
    r"أغسطس|سبتمبر|أكتوبر|نوفمبر|ديسمبر|محرم|صفر|رمضان|شوال)"
)


def _is_valid_http_url(url: str) -> bool:
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
    if not text:
        return set()
    cleaned = _AR_DIACRITICS_RE.sub("", text)
    cleaned = re.sub(r"[^\w\u0600-\u06FF]+", " ", cleaned.lower())
    stop = {
        "في", "من", "على", "إلى", "إلي", "عن", "مع", "هذا", "هذه", "ذلك", "التي", "الذي",
        "و", "أو", "أن", "إن", "قد", "لا", "ما", "هو", "هي", "تم", "يتم", "بين", "بعد",
        "قبل", "خلال", "the", "and", "for", "with", "from",
    }
    return {t for t in cleaned.split() if len(t) >= 2 and t not in stop}


def _excerpt_supports_claim(claim_text: str, evidence_contents: list[str]) -> bool:
    """L4 lexical grounding: claim shares content tokens with cited excerpts."""
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
        smaller = min(len(claim_tokens), len(excerpt_tokens))
        if smaller and (len(shared) / smaller) >= 0.5 and len(shared) >= 1 and len(claim_tokens) <= 4:
            return True
        if smaller and (len(shared) / smaller) >= 0.34 and len(shared) >= 2:
            return True
    return False


_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}\s?/\s?\d{1,2}(?:\s?/\s?\d{2,4})?")


def _entity_sets(text: str) -> dict[str, set[str]]:
    cleaned = _AR_DIACRITICS_RE.sub("", text or "")
    numbers = set(_NUMBER_RE.findall(cleaned))
    dates = set(_DATE_RE.findall(cleaned))
    tokens = _content_tokens(cleaned)
    names = {t for t in tokens if len(t) >= 4}
    return {"numbers": numbers, "dates": dates, "names": names}


def _entity_overlap_pass(claim_text: str, evidence_contents: list[str]) -> bool:
    """L4 entity-set pass: names/dates/numbers in claim must appear in evidence."""
    claim_ents = _entity_sets(claim_text)
    claim_numbers = claim_ents["numbers"] | claim_ents["dates"]
    if not claim_numbers and len(claim_ents["names"]) < 2:
        return True
    combined = " ".join(evidence_contents or "")
    ev_ents = _entity_sets(combined)
    ev_numbers = ev_ents["numbers"] | ev_ents["dates"]
    if claim_numbers and not (claim_numbers & ev_numbers):
        if claim_numbers:
            return False
    if len(claim_ents["names"]) >= 2:
        ev_tokens = _content_tokens(combined)
        if not (claim_ents["names"] & ev_tokens):
            return False
    return True


def _semantic_similarity(claim_text: str, evidence_contents: list[str]) -> float:
    """L5 deterministic semantic proxy: max Jaccard over content tokens."""
    claim_tokens = _content_tokens(claim_text or "")
    if not claim_tokens:
        return 0.0
    best = 0.0
    for excerpt in evidence_contents or ():
        ev_tokens = _content_tokens(excerpt or "")
        if not ev_tokens:
            continue
        inter = len(claim_tokens & ev_tokens)
        union = len(claim_tokens | ev_tokens)
        if union:
            best = max(best, inter / union)
    return round(best, 4)


def _reranker_topk(
    claim_text: str, evidence: list[EvidenceItem], k: int = 3
) -> list[EvidenceItem]:
    """L6 reranker top-k: prefer rerank/fused/dense scores, fallback lexical."""

    def _score(item: EvidenceItem) -> float:
        for value in (item.rerank_score, item.fused_score, item.dense_score):
            if isinstance(value, (int, float)):
                return float(value)
        return -1.0

    scored = sorted(evidence, key=_score, reverse=True)
    if scored and _score(scored[0]) >= 0:
        return scored[: max(1, k)]
    ranked = sorted(
        evidence,
        key=lambda item: _semantic_similarity(claim_text, [item.content or ""]),
        reverse=True,
    )
    return ranked[: max(1, k)]


def stable_evidence_id(source_id: str, chunk_id: str, content: str) -> str:
    """Stable evidence ID: ``{source_id}:{chunk_id}:{hash}``.

    The hash binds the ID to content so edits change the ID, while reorder
    of the evidence list never invalidates ``citation_ids``.
    """
    digest = hashlib.sha1((content or "").encode("utf-8")).hexdigest()[:8]
    return f"{source_id}:{chunk_id}:{digest}"


def evidence_ordinals(evidence: list) -> dict[str, int]:
    """Render-time ordinal map: citation_id -> [1],[2],[3] display number."""
    ordinals: dict[str, int] = {}
    for index, item in enumerate(evidence or (), start=1):
        cid = getattr(item, "citation_id", "") or ""
        if cid and cid not in ordinals:
            ordinals[cid] = index
    return ordinals


def classify_claim(text: str) -> str:
    """Regex+NER-list classifier run BEFORE any support checks."""
    normalized = (text or "").strip()
    if not normalized:
        return CLASS_FACTUAL
    if _USER_TEXT_RE.search(normalized):
        return CLASS_USER
    if _UNCERTAIN_RE.search(normalized):
        return CLASS_UNCERTAIN
    if _HIGH_RISK_RE.search(normalized):
        return CLASS_HIGH_RISK
    if _NON_FACTUAL_RE.search(normalized):
        return CLASS_NON_FACTUAL
    if _INTERPRETIVE_RE.search(normalized):
        return CLASS_INTERPRETIVE
    return CLASS_FACTUAL


def _split_claims(draft: str) -> list[str]:
    segments = [segment.strip() for segment in re.split(r"(?<=[.!؟؟\u2026])\s+|\n+", draft) if segment.strip()]
    return segments


def _claim_scope(draft: str, segment: str, seq: int) -> str:
    """Per-scope bucket: paragraph ordinal (tables: each row is its own scope)."""
    paragraphs = [p for p in re.split(r"\n\s*\n|\n", draft or "") if p.strip()]
    for index, para in enumerate(paragraphs):
        if segment and segment[:40] in para:
            stripped = para.strip()
            if stripped.startswith("|"):
                return f"table:r{index}"
            return f"answer:p{index}"
    return f"answer:p{seq % 3}"


def _build_scope_survival(records: list[ClaimRecord]) -> dict[str, dict]:
    survival: dict[str, dict] = {}
    for record in records:
        scope = getattr(record, "scope", "answer") or "answer"
        bucket = survival.setdefault(scope, {"total": 0, "survived": 0, "survived_ids": [], "unsupported_ids": []})
        bucket["total"] += 1
        if record.status in _ACCEPTED:
            bucket["survived"] += 1
            bucket["survived_ids"].append(record.claim_id)
        else:
            bucket["unsupported_ids"].append(record.claim_id)
    for bucket in survival.values():
        total = bucket["total"] or 1
        bucket["survival_ratio"] = round(bucket["survived"] / total, 4)
        bucket["wiped"] = bucket["survived"] == 0
    return survival


def _is_isolated_failure(total_external: int, removed_count: int) -> bool:
    """Single unsupported claim among >=4 verifiable claims: no recompose loop."""
    return removed_count == 1 and total_external >= 4


def _status_choice(claim_id: str, deterministic_status: ClaimStatus, model_status: Optional[ClaimStatus]) -> ClaimStatus:
    if deterministic_status is ClaimStatus.UNSUPPORTED:
        return ClaimStatus.UNSUPPORTED
    if model_status in _REMOVED:
        return model_status
    if model_status is ClaimStatus.PARTIALLY_SUPPORTED:
        return ClaimStatus.PARTIALLY_SUPPORTED
    return ClaimStatus.SUPPORTED


def _parse_model_verdict(entry: dict) -> tuple[Optional[ClaimStatus], tuple[str, ...], str]:
    if not isinstance(entry, dict):
        return None, (), ""
    raw = entry.get("verdict", entry.get("status", ""))
    status: Optional[ClaimStatus] = None
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        mapping = {
            "supported": ClaimStatus.SUPPORTED,
            "partial": ClaimStatus.PARTIALLY_SUPPORTED,
            "partially_supported": ClaimStatus.PARTIALLY_SUPPORTED,
            "unsupported": ClaimStatus.UNSUPPORTED,
            "contradicted": ClaimStatus.CONTRADICTED,
        }
        status = mapping.get(normalized)
    codes: list[str] = []
    for key in ("reason_codes", "reasonCodes", "reasons"):
        value = entry.get(key)
        if isinstance(value, list):
            for code in value:
                if isinstance(code, str) and code in REASON_CODES and code not in codes:
                    codes.append(code)
    correction = entry.get("correction") if isinstance(entry.get("correction"), str) else ""
    return status, tuple(codes), correction


def _claim_evidence_ids(citation_ids: tuple[str, ...], item_by_cit: dict) -> tuple[str, ...]:
    out: list[str] = []
    for cid in citation_ids or ():
        item = item_by_cit.get(cid)
        if item is None:
            continue
        out.append(stable_evidence_id(cid, item.chunk_id or "", item.content or ""))
    return tuple(out)


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
        max_retries = int(state.get("compose_max_retries", 0))
        exhausted_count = max_retries + 1
        events.append(
            make_event(EVENT_COMPLETED, run, "verify", "failed", summary="تعذر التحقق: لا أدلة موثقة — إجابة جزئية صريحة", duration_ms=(time.monotonic() - start) * 1000)
        )
        partial_state = {**state, "atomic_claims": []}
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
            "scope_survival": {},
            "evidence_ordinals": evidence_ordinals(evidence),
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
    entail_candidates: list[ClaimRecord] = []
    l4_disagreement: set[str] = set()
    segments = _split_claims(draft)
    for seq, segment in enumerate(segments, start=1):
        claim_id = f"CLAIM-{round_index:02d}-{seq:03d}"
        citation_ids = tuple(dict.fromkeys(INLINE_CITATION_RE.findall(segment)))
        text = INLINE_CITATION_RE.sub("", segment).strip()
        claim_class = classify_claim(text)
        scope = _claim_scope(draft, segment, seq)

        if claim_class == CLASS_USER:
            claim_records.append(ClaimRecord(claim_id, text, citation_ids,
                tuple(chunk_by_cit[c] for c in citation_ids if c in chunk_by_cit),
                ClaimStatus.USER_PROVIDED, "", "", CLASS_USER, scope,
                _claim_evidence_ids(citation_ids, item_by_cit), ()))
            continue
        if claim_class == CLASS_UNCERTAIN:
            claim_records.append(ClaimRecord(claim_id, text, citation_ids,
                tuple(chunk_by_cit[c] for c in citation_ids if c in chunk_by_cit),
                ClaimStatus.EXPLICITLY_UNCERTAIN, "", "", CLASS_UNCERTAIN, scope,
                _claim_evidence_ids(citation_ids, item_by_cit), ()))
            continue
        if claim_class == CLASS_NON_FACTUAL:
            claim_records.append(ClaimRecord(claim_id, text, citation_ids,
                tuple(chunk_by_cit[c] for c in citation_ids if c in chunk_by_cit),
                ClaimStatus.NON_FACTUAL, "صياغة غير واقعية معفاة من الاستشهاد.",
                "", CLASS_NON_FACTUAL, scope,
                _claim_evidence_ids(citation_ids, item_by_cit), ("non_factual",)))
            continue
        if claim_class == CLASS_INTERPRETIVE:
            if not citation_ids:
                claim_records.append(ClaimRecord(claim_id, text, citation_ids, (),
                    ClaimStatus.PARTIALLY_SUPPORTED,
                    "تأويلي بلا استشهاد — يلزم استشهاد وصياغة متحفظة لا إزالة.",
                    "أضف استشهادًا وصياغة متحفظة (قد/ربما).",
                    CLASS_INTERPRETIVE, scope, (), ("partial_scope",)))
                continue
            if not _HEDGE_RE.search(text):
                claim_records.append(ClaimRecord(claim_id, text, citation_ids,
                    tuple(chunk_by_cit[c] for c in citation_ids if c in chunk_by_cit),
                    ClaimStatus.PARTIALLY_SUPPORTED,
                    "تأويلي بلا تحفظ — يُحفظ مع تنبيه لا إزالة.",
                    "أضف تحفظًا (قد/ربما/يبدو).",
                    CLASS_INTERPRETIVE, scope,
                    _claim_evidence_ids(citation_ids, item_by_cit), ("partial_scope",)))
                continue

        deterministic_status = ClaimStatus.SUPPORTED
        reason_codes: list[str] = []
        explanation = ""
        flagged = False
        if not citation_ids:
            deterministic_status = ClaimStatus.UNSUPPORTED
            reason_codes.append("unknown_citation")
            explanation = "لا يوجد أي معرّف استشهاد يدعم هذا الادعاء."
        elif any(cid not in valid_cits for cid in citation_ids):
            deterministic_status = ClaimStatus.UNSUPPORTED
            reason_codes.append("unknown_citation")
            unknown = [cid for cid in citation_ids if cid not in valid_cits]
            explanation = f"معرّفات استشهاد غير معروفة في الأدلة: {', '.join(unknown)}"
        elif any(cid in duplicate_cits for cid in citation_ids):
            deterministic_status = ClaimStatus.UNSUPPORTED
            reason_codes.append("unknown_citation")
            explanation = "سجل استشهاد مكرر في الأدلة لهذا الادعاء."
        else:
            has_provenance_problem = False
            cited_contents: list[str] = []
            for cid in citation_ids:
                item = item_by_cit.get(cid)
                if item is None or not (item.title or "").strip() or not _is_valid_http_url(item.source_url or ""):
                    deterministic_status = ClaimStatus.UNSUPPORTED
                    reason_codes.append("missing_provenance")
                    explanation = "بيانات مصدر ناقصة (عنوان أو رابط HTTP(S) صالح) لهذا الاستشهاد."
                    has_provenance_problem = True
                    break
                if not (item.chunk_id or "").strip():
                    deterministic_status = ClaimStatus.UNSUPPORTED
                    reason_codes.append("missing_provenance")
                    explanation = "معرّف مقطع (chunk) مفقود لهذا الاستشهاد."
                    has_provenance_problem = True
                    break
                if not (item.content or "").strip() or len((item.content or "").strip()) < 20:
                    deterministic_status = ClaimStatus.UNSUPPORTED
                    reason_codes.append("missing_provenance")
                    explanation = "المقتطف المصدر فارغ أو قصير جدًا."
                    has_provenance_problem = True
                    break
                cited_contents.append(item.content or "")
            if not has_provenance_problem:
                lexical_ok = _excerpt_supports_claim(text, cited_contents)
                entity_ok = _entity_overlap_pass(text, cited_contents)
                l4_ok = bool(lexical_ok and entity_ok)
                if not lexical_ok and "lexical_gap" not in reason_codes:
                    reason_codes.append("lexical_gap")
                if not entity_ok and "entity_mismatch" not in reason_codes:
                    reason_codes.append("entity_mismatch")
                sem = _semantic_similarity(text, cited_contents)
                l5_ok = sem >= 0.18 or (len(_content_tokens(text)) <= 4 and sem >= 0.12)
                topk = _reranker_topk(text, [item_by_cit[c] for c in citation_ids if c in item_by_cit] + [i for i in evidence if i.citation_id not in citation_ids], k=3)
                top1 = topk[0].citation_id if topk else None
                l6_ok = top1 in citation_ids if top1 else True
                if l4_ok != l5_ok or (l4_ok and not l6_ok):
                    l4_disagreement.add(claim_id)
                if not l4_ok:
                    if claim_class == CLASS_HIGH_RISK:
                        deterministic_status = ClaimStatus.UNSUPPORTED
                        flagged = True
                        explanation = "ادعاء عالي المخاطر بلا تطابق معجمي/كياني — غير مدعوم والصف مُعلَّم."
                    else:
                        deterministic_status = ClaimStatus.UNSUPPORTED
                        explanation = "الاستشهادات موجودة لكن مقتطفات المصادر لا تدعم نص هذا الادعاء."
                elif claim_class == CLASS_HIGH_RISK and not l6_ok:
                    deterministic_status = ClaimStatus.UNSUPPORTED
                    flagged = True
                    reason_codes.append("entity_mismatch")
                    explanation = "ادعاء عالي المخاطر خارج أعلى-1 في إعادة الترتيب — غير مدعوم والصف مُعلَّم."

        supporting_chunks = tuple(dict.fromkeys(chunk_by_cit[c] for c in citation_ids if c in chunk_by_cit))
        record = ClaimRecord(
            claim_id=claim_id, text=text, citation_ids=citation_ids,
            supporting_chunk_ids=supporting_chunks, status=deterministic_status,
            explanation=explanation or "", correction="", claim_class=claim_class,
            scope=scope, evidence_ids=_claim_evidence_ids(citation_ids, item_by_cit),
            reason_codes=tuple(reason_codes), flagged_row=flagged,
        )
        claim_records.append(record)
        needs_l7 = (claim_id in l4_disagreement) or (claim_class == CLASS_HIGH_RISK and deterministic_status is ClaimStatus.SUPPORTED)
        if needs_l7 and deterministic_status is ClaimStatus.SUPPORTED:
            entail_candidates.append(record)

    model_suggestions: dict[str, ClaimStatus] = {}
    model_corrections: dict[str, str] = {}
    model_reason_codes: dict[str, tuple[str, ...]] = {}
    model_service = getattr(deps, "model_service", None)
    if entail_candidates and model_service is not None:
        evidence_text = "\n\n".join(
            f"[{item.citation_id}] {item.title} — {item.source_name}\n{item.content[:700]}"
            for item in evidence
        )
        claims_text = "\n".join(f"- {r.claim_id} [{r.claim_class}]: {r.text}" for r in entail_candidates)
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
                status, codes, correction = _parse_model_verdict(entry)
                if status is not None:
                    model_suggestions[claim_id] = status
                    if codes:
                        model_reason_codes[claim_id] = codes
                if correction:
                    model_corrections[claim_id] = correction
            semantic_model_used = response.model_used
        elif not response.success:
            semantic_degraded = True

    final_records: list[ClaimRecord] = []
    for record in claim_records:
        if record.status is ClaimStatus.SUPPORTED and record.claim_id in model_suggestions:
            suggested = model_suggestions.get(record.claim_id)
            status = _status_choice(record.claim_id, record.status, suggested)
            correction = model_corrections.get(record.claim_id, "")
            codes = model_reason_codes.get(record.claim_id, record.reason_codes)
            if status is ClaimStatus.PARTIALLY_SUPPORTED and "partial_scope" not in codes:
                codes = tuple([*codes, "partial_scope"])
            final_records.append(ClaimRecord(
                claim_id=record.claim_id, text=record.text, citation_ids=record.citation_ids,
                supporting_chunk_ids=record.supporting_chunk_ids, status=status,
                explanation=record.explanation, correction=correction,
                claim_class=record.claim_class, scope=record.scope,
                evidence_ids=record.evidence_ids, reason_codes=codes,
                flagged_row=record.flagged_row))
        else:
            final_records.append(record)

    verifiable = [r for r in final_records if r.claim_class in (CLASS_FACTUAL, CLASS_HIGH_RISK, CLASS_INTERPRETIVE)]
    verified = [r for r in final_records if r.status in _ACCEPTED]
    removed = [r for r in final_records if r.status in _REMOVED]
    external_good = [r for r in verifiable if r.status in _ACCEPTED]

    total_external = len(verifiable)
    covered = len(external_good)
    coverage_ratio = (covered / total_external) if total_external else 1.0
    scope_survival = _build_scope_survival(final_records)
    ordinals = evidence_ordinals(evidence)
    isolated = _is_isolated_failure(total_external, len([r for r in removed if r.claim_class in (CLASS_FACTUAL, CLASS_HIGH_RISK, CLASS_INTERPRETIVE)]))

    claim_citation_mapping = {r.claim_id: list(r.citation_ids) for r in final_records}
    events.append(make_event(EVENT_CITATION_COVERAGE_CALCULATED, run, "verify", "completed",
        summary=f"تغطية الاستشهادات: {coverage_ratio:.0%}" if total_external else "لا توجد ادعاءات خارجية للتغطية",
        coverage=coverage_ratio, source_count=len(evidence)))

    if not removed or isolated:
        passed = True
        if isolated:
            feedback = (
                f"نجاة per-scope: {covered}/{total_external} ادعاءات قابلة للتحقق مدعومة؛ "
                f"استُبعد ادعاء معزول واحد ({removed[0].claim_id}) دون إعادة صياغة."
            )
            warnings = [feedback]
        else:
            feedback = ""
            warnings = []
        result = VerificationResult(passed=True,
            verified_claim_ids=tuple(r.claim_id for r in verified),
            unsupported_claim_ids=tuple(r.claim_id for r in removed),
            feedback=feedback)
        coverage = CoverageReport(total_claims=len(final_records), external_claims=total_external,
            covered_claims=covered, coverage_ratio=round(coverage_ratio, 4),
            uncovered_claim_ids=tuple(r.claim_id for r in removed),
            model_used=semantic_model_used,
            note="" if not removed else "فشل معزول واحد — نجاة per-scope مع تحذير.")
        duration_ms = (time.monotonic() - start) * 1000
        events.append(make_event(EVENT_COMPLETED, run, "verify", "completed",
            summary="تحقق ناجح: نجاة per-scope" if isolated else "تحقق ناجح: التغطية كاملة",
            duration_ms=duration_ms, coverage=coverage_ratio))
        if semantic_degraded:
            events.append(make_event(EVENT_DEGRADED, run, "verify", "degraded",
                summary="التحقق الدلالي غير متاح — الاعتماد على الفحوص الحتمية فقط", degraded=True))
            warnings.append("التحقق الدلالي غير متاح؛ الفحوص الحتمية وحدها حاسمة.")
        return {
            "atomic_claims": final_records,
            "claim_citation_mapping": claim_citation_mapping,
            "unsupported_claims": [r.claim_id for r in removed],
            "coverage": coverage,
            "verification_result": result,
            "verification_history": [VerificationRound(round_index, True,
                tuple(r.claim_id for r in verified), tuple(r.claim_id for r in removed))],
            "scope_survival": scope_survival,
            "evidence_ordinals": ordinals,
            "model_routes": {"verify": semantic_model_used},
            "fallback_events": fallback_events,
            "timings": {"verify_ms": duration_ms},
            "progress_events": events,
            "warnings": warnings,
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
    exhausted = retry_count > max_retries
    result = VerificationResult(passed=False,
        verified_claim_ids=tuple(r.claim_id for r in verified),
        unsupported_claim_ids=tuple(r.claim_id for r in removed), feedback=feedback)
    coverage = CoverageReport(total_claims=len(final_records), external_claims=total_external,
        covered_claims=covered, coverage_ratio=round(coverage_ratio, 4),
        uncovered_claim_ids=tuple(r.claim_id for r in removed),
        model_used=semantic_model_used,
        note="تغطية خارجية أقل من 100% — توجد ادعاءات غير مدعومة.")
    updates: dict = {
        "atomic_claims": final_records,
        "claim_citation_mapping": claim_citation_mapping,
        "unsupported_claims": [r.claim_id for r in removed],
        "coverage": coverage,
        "verification_result": result,
        "verification_feedback": [feedback],
        "verification_history": [VerificationRound(round_index, False,
            tuple(r.claim_id for r in verified), tuple(r.claim_id for r in removed))],
        "scope_survival": scope_survival,
        "evidence_ordinals": ordinals,
    }
    if not exhausted:
        updates["compose_retry_count"] = retry_count
        events.append(make_event(EVENT_RETRIED, run, "verify", "retried",
            summary=f"إعادة الصياغة بعد فشل التغطية (محاولة {retry_count})",
            retry=retry_count, coverage=coverage_ratio, degraded=True))
        status = "completed"
        summary = "فشل التحقق — إعادة التوجيه إلى الصياغة"
    else:
        updates["verification_exhausted"] = True
        updates["compose_retry_count"] = retry_count
        partial_state = {**state, "atomic_claims": final_records, "scope_survival": scope_survival}
        updates["final_answer"] = assemble_partial_answer(partial_state)
        updates["graph_outcome"] = "partial"
        events.append(make_event(EVENT_FAILED, run, "verify", "failed",
            summary="استُنفدت محاولات المراجعة — إجابة جزئية صريحة فقط",
            coverage=coverage_ratio, retry=retry_count, degraded=True))
        updates["errors"] = [make_error(run, "verify", FailureKind.VERIFICATION_EXHAUSTED,
            "استُنفدت محاولات التغطية؛ أُنتجت إجابة جزئية من الادعاءات الموثوقة فقط.", retryable=False)]
        status = "failed"
        summary = "استُنفدت التغطية — إجابة جزئية"
    duration_ms = (time.monotonic() - start) * 1000
    events.append(make_event(EVENT_COMPLETED, run, "verify", status, summary=summary,
        duration_ms=duration_ms, coverage=coverage_ratio))
    updates["model_routes"] = {"verify": semantic_model_used}
    updates["fallback_events"] = fallback_events
    updates["timings"] = {"verify_ms": duration_ms}
    updates["progress_events"] = events
    return updates
