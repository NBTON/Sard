"""Verified render boundary shared by the three artifact renderers."""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Iterable, Optional

from sard.outputs.schemas import (
    CITATION_ID_RE,
    CitationSource,
    FieldSupport,
    Itinerary,
    ItineraryDay,
    ItineraryStop,
    TextBlock,
    VerificationStatus,
    INLINE_CITATION_RE,
)


ACCEPTED_CLAIM_STATUSES = {"supported", "partially_supported", "user_provided", "explicitly_uncertain"}
REMOVED_CLAIM_STATUSES = {"unsupported", "contradicted", "non_factual"}
DEGRADED_RETRIEVAL_MODES = {"dense_only", "full_text_only", "unavailable"}


class CitationValidationError(ValueError):
    """Structured output cannot be safely rendered."""

    def __init__(self, message: str, *, raw_answer: str = ""):
        super().__init__(message)
        self.raw_answer = raw_answer


@dataclass(frozen=True)
class VerifiedRenderInput:
    run_id: str
    final_answer: str
    itinerary: Optional[Itinerary]
    sources: tuple[CitationSource, ...]
    accepted_citation_ids: frozenset[str]
    verification_status: VerificationStatus
    retrieval_mode: str
    model_fallback_used: bool
    warnings: tuple[str, ...]
    degraded_notice: Optional[str]
    validation_error: Optional[str] = None

    def __post_init__(self) -> None:
        if isinstance(self.verification_status, str):
            object.__setattr__(self, "verification_status", VerificationStatus(self.verification_status))

    @property
    def source_map(self) -> dict[str, CitationSource]:
        return {source.citation_id: source for source in self.sources}


def _claim_status(claim) -> str:
    raw = getattr(claim, "status", "")
    return getattr(raw, "value", raw)


def accepted_claims(claims: Iterable) -> tuple:
    return tuple(claim for claim in claims or () if _claim_status(claim) in ACCEPTED_CLAIM_STATUSES)


def verified_answer_subset(
    answer: str,
    claims: Iterable,
    allowed_citation_ids: Optional[Iterable[str]] = None,
) -> str:
    """Rebuild only accepted claim text when the composed answer is unsafe."""

    accepted = accepted_claims(claims)
    allowed = None if allowed_citation_ids is None else set(allowed_citation_ids)
    if not accepted:
        return "لم تتوفر حقائق موثقة قابلة للعرض من المصادر المتاحة."
    lines = ["فيما يلي المعلومات التي أمكن التحقق منها:", ""]
    for claim in accepted:
        text = str(getattr(claim, "text", "") or "").strip()
        ids = tuple(getattr(claim, "citation_ids", ()) or ())
        if allowed is not None:
            ids = tuple(cid for cid in ids if cid in allowed)
            text = INLINE_CITATION_RE.sub(
                lambda match: match.group(0) if match.group(1) in allowed else "",
                text,
            ).strip()
        if text:
            lines.append(f"- {text} {' '.join(f'[{cid}]' for cid in ids)}".rstrip())
    return "\n".join(lines)


def _citations_from_supports(supports: Iterable[FieldSupport]) -> set[str]:
    result: set[str] = set()
    for support in supports or ():
        result.update(support.citation_ids)
    return result


def _degraded_notice(status: VerificationStatus, retrieval_mode: str, model_fallback_used: bool) -> Optional[str]:
    notices: list[str] = []
    if status is VerificationStatus.EVIDENCE_LIMITED:
        notices.append("النتيجة محدودة بالأدلة المتاحة")
    if retrieval_mode in DEGRADED_RETRIEVAL_MODES:
        notices.append("تم استخدام وضع استرجاع محدود")
    if model_fallback_used:
        notices.append("تم استخدام مسار نموذج احتياطي")
    return "؛ ".join(notices) + "." if notices else None


def _fallback_used(state: dict) -> bool:
    return bool(
        state.get("model_fallback_used")
        or any(
            getattr(event, "degraded", False)
            or getattr(event, "quality_degraded", False)
            or getattr(event, "selected_fallback", None) not in {None, "", "primary"}
            for event in state.get("fallback_events") or ()
        )
    )


def _validate_ids(ids: Iterable[str], source_map: dict[str, CitationSource], accepted: set[str], label: str) -> None:
    _validate_known_ids(ids, source_map, label)
    for citation_id in ids or ():
        if citation_id not in accepted:
            raise CitationValidationError(f"Citation is not supported on {label}: {citation_id}")


def _validate_known_ids(ids: Iterable[str], source_map: dict[str, CitationSource], label: str) -> None:
    for citation_id in ids or ():
        if not CITATION_ID_RE.fullmatch(citation_id):
            raise CitationValidationError(f"Invalid citation ID on {label}: {citation_id}")
        if citation_id not in source_map:
            raise CitationValidationError(f"Unknown citation ID on {label}: {citation_id}")


def _filter_blocks(
    blocks: Iterable[TextBlock],
    source_map: dict[str, CitationSource],
    accepted: set[str],
    label: str,
    field_citation_ids: tuple[str, ...] = (),
    allow_uncited: bool = False,
) -> tuple[TextBlock, ...]:
    kept: list[TextBlock] = []
    for block in blocks or ():
        inline = set(re.findall(r"\[(CIT-[A-Za-z0-9_-]{3,60})\]", block.text))
        ids = tuple(dict.fromkeys((*block.citation_ids, *inline, *field_citation_ids)))
        if not ids:
            if allow_uncited:
                kept.append(block)
            continue
        _validate_known_ids(ids, source_map, label)
        if any(cid not in accepted for cid in ids):
            # A known source that failed verification cannot support this
            # factual block.  Drop the whole block instead of retaining an
            # unverified sentence with its citation stripped.
            continue
        missing_inline = tuple(cid for cid in field_citation_ids if f"[{cid}]" not in block.text and cid not in block.citation_ids)
        kept.append(replace(block, citation_ids=tuple(dict.fromkeys((*block.citation_ids, *inline, *missing_inline)))))
    return tuple(kept)


def _filter_supports(
    supports: Iterable[FieldSupport],
    source_map: dict[str, CitationSource],
    accepted: set[str],
    label: str,
) -> tuple[FieldSupport, ...]:
    filtered: list[FieldSupport] = []
    for support in supports or ():
        _validate_known_ids(support.citation_ids, source_map, f"{label}.{support.field_name}")
        accepted_ids = tuple(cid for cid in support.citation_ids if cid in accepted)
        if support.citation_ids and len(accepted_ids) != len(support.citation_ids):
            if support.provenance == "verified":
                continue
            filtered.append(replace(support, citation_ids=accepted_ids))
            continue
        filtered.append(support)
    return tuple(filtered)


def _filter_stop(stop: ItineraryStop, source_map: dict[str, CitationSource], accepted: set[str], day_label: str, stop_index: int) -> ItineraryStop:
    label = f"{day_label}.stop{stop_index}"
    _validate_known_ids(stop.citation_ids, source_map, label)
    supports = _filter_supports(stop.field_support, source_map, accepted, label)
    support_fields = {support.field_name for support in supports}

    def support_ids(field_name: str) -> tuple[str, ...]:
        support = next((item for item in supports if item.field_name == field_name), None)
        if support is None:
            return ()
        return support.citation_ids

    def allow_uncited(field_name: str) -> bool:
        support = next((item for item in supports if item.field_name == field_name), None)
        return bool(support and support.provenance in {"user_provided", "uncertain"})

    def is_supported(field_name: str) -> bool:
        support = next((item for item in supports if item.field_name == field_name), None)
        return bool(support and (support.citation_ids or support.provenance in {"user_provided", "uncertain"}))

    def supported_optional(field_name: str, value) -> object:
        if value is None or value == "" or value == ():
            return value
        support = next((item for item in supports if item.field_name == field_name), None)
        if support is None:
            return None
        if support.provenance in {"user_provided", "uncertain"}:
            return value
        return value if support.citation_ids else None

    address = supported_optional("address", stop.address)
    coordinates = supported_optional("coordinates", stop.coordinates)
    description = _filter_blocks(
        stop.effective_description,
        source_map,
        accepted,
        f"{label}.description",
        support_ids("description"),
        allow_uncited=allow_uncited("description"),
    )
    practical = _filter_blocks(
        stop.effective_practical_notes,
        source_map,
        accepted,
        f"{label}.practical_notes",
        support_ids("practical_notes"),
        allow_uncited=allow_uncited("practical_notes"),
    )
    accessibility = _filter_blocks(
        stop.effective_accessibility_notes,
        source_map,
        accepted,
        f"{label}.accessibility_notes",
        support_ids("accessibility_notes"),
        allow_uncited=allow_uncited("accessibility_notes"),
    )
    notes = _filter_blocks(
        stop.notes,
        source_map,
        accepted,
        f"{label}.notes",
        support_ids("notes"),
        allow_uncited=allow_uncited("notes"),
    )
    title = stop.title if is_supported("title") else "محطة"
    location = stop.location if is_supported("location") else ""
    time_text = stop.time if is_supported("time") else ""
    start_time = stop.start_time if is_supported("time") else None
    end_time = stop.end_time if is_supported("time") else None
    return replace(
        stop,
        title=title,
        location=location,
        location_name=location or None,
        time=time_text,
        start_time=start_time,
        end_time=end_time,
        address=address,
        coordinates=coordinates,
        description=description,
        practical_notes=practical,
        accessibility_notes=accessibility,
        paragraphs=description,
        bullets=practical,
        notes=notes,
        citation_ids=tuple(cid for cid in stop.citation_ids if cid in accepted),
        field_support=supports,
    )


def build_verified_render_input(
    state: dict,
    *,
    allow_partial: bool = True,
) -> VerifiedRenderInput:
    run_id = str(state.get("run_id") or "")
    answer = str(state.get("final_answer") or state.get("draft") or "")
    raw_sources = tuple(state.get("sources") or ())
    source_map: dict[str, CitationSource] = {}
    for source in raw_sources:
        if source.citation_id in source_map:
            raise CitationValidationError(f"Duplicate citation ID: {source.citation_id}", raw_answer=answer)
        source_map[source.citation_id] = source
    claims = accepted_claims(state.get("atomic_claims") or ())
    accepted = {citation_id for claim in claims for citation_id in getattr(claim, "citation_ids", ())}
    itinerary = state.get("itinerary")
    warnings = list(state.get("warnings") or ())
    validation_error: Optional[str] = None
    verification = state.get("verification_result")
    status = VerificationStatus.VERIFIED
    if state.get("verification_exhausted") or not getattr(verification, "passed", True):
        status = VerificationStatus.EVIDENCE_LIMITED
    model_fallback_used = _fallback_used(state)
    retrieval_mode = str(state.get("retrieval_mode") or "")
    answer_citations = tuple(dict.fromkeys(INLINE_CITATION_RE.findall(answer)))
    try:
        _validate_known_ids(
            tuple(
                citation_id
                for claim in claims
                for citation_id in getattr(claim, "citation_ids", ())
            ),
            source_map,
            "verified claims",
        )
        _validate_ids(answer_citations, source_map, accepted, "final answer")
    except CitationValidationError as exc:
        if not allow_partial:
            raise CitationValidationError(str(exc), raw_answer=verified_answer_subset(answer, state.get("atomic_claims") or ())) from exc
        warnings.append("تمت إزالة الاستشهادات غير الصالحة من النص الخام وحُفظ الجزء الموثق فقط.")
        answer = verified_answer_subset(
            answer,
            state.get("atomic_claims") or (),
            (set(source_map) & accepted),
        )
        validation_error = str(exc)
    try:
        if itinerary is not None:
            _validate_known_ids(itinerary.citation_ids, source_map, "itinerary")
            _validate_known_ids(_citations_from_supports(itinerary.field_support), source_map, "itinerary fields")
            itinerary_supports = _filter_supports(itinerary.field_support, source_map, accepted, "itinerary")
            itinerary_support = {support.field_name: support for support in itinerary_supports}
            for block in itinerary.all_text_blocks():
                _filter_blocks((block,), source_map, accepted, "itinerary text")
            filtered_days = []
            for day_index, day in enumerate(itinerary.days, start=1):
                filtered_day_supports = _filter_supports(day.field_support, source_map, accepted, f"day{day_index}")
                day_support = {support.field_name: support for support in filtered_day_supports}
                day_title = day.title if (day_support.get("title") and (day_support["title"].citation_ids or day_support["title"].provenance != "verified")) else f"اليوم {day_index}"
                date_support = day_support.get("date")
                day_date = (
                    day.date
                    if day.date is not None
                    and date_support is not None
                    and (date_support.citation_ids or date_support.provenance in {"user_provided", "uncertain"})
                    else None
                )
                day_note_support = day_support.get("notes")
                day_notes = _filter_blocks(
                    day.notes,
                    source_map,
                    accepted,
                    f"day{day_index}.notes",
                    allow_uncited=bool(
                        day_note_support
                        and day_note_support.provenance in {"user_provided", "uncertain"}
                    ),
                )
                filtered_stops = []
                for stop_index, stop in enumerate(day.stops, start=1):
                    filtered_stop = _filter_stop(stop, source_map, accepted, f"day{day_index}", stop_index)
                    has_field_content = bool(
                        filtered_stop.effective_description
                        or filtered_stop.effective_practical_notes
                        or filtered_stop.effective_accessibility_notes
                        or filtered_stop.notes
                        or filtered_stop.title
                        or filtered_stop.address
                        or filtered_stop.coordinates
                        or filtered_stop.effective_location_name
                        or filtered_stop.time
                    )
                    has_supported_field = any(
                        support.citation_ids or support.provenance in {"user_provided", "uncertain"}
                        for support in filtered_stop.field_support
                    )
                    if has_field_content and has_supported_field:
                        filtered_stops.append(filtered_stop)
                filtered_days.append(
                    replace(
                        day,
                        title=day_title,
                        date=day_date,
                        notes=day_notes,
                        stops=tuple(filtered_stops),
                        field_support=filtered_day_supports,
                    )
                )
            title_support = itinerary_support.get("title")
            summary_support = itinerary_support.get("summary")
            title = itinerary.title
            summary = itinerary.summary
            if title_support is None or (not title_support.citation_ids and title_support.provenance == "verified"):
                title = "خطة الرحلة"
                itinerary_supports = tuple(
                    support for support in itinerary_supports if support.field_name != "title"
                ) + (FieldSupport("title", provenance="user_provided"),)
            if summary_support is None or (not summary_support.citation_ids and summary_support.provenance == "verified"):
                summary = "ملخص موثق غير متاح بالكامل من الأدلة الحالية."
                itinerary_supports = tuple(
                    support for support in itinerary_supports if support.field_name != "summary"
                ) + (FieldSupport("summary", provenance="uncertain"),)
            itinerary = replace(
                itinerary,
                title=title,
                summary=summary,
                citation_ids=tuple(cid for cid in itinerary.citation_ids if cid in accepted),
                run_id=run_id or itinerary.run_id,
                verification_status=status,
                retrieval_mode=retrieval_mode or itinerary.retrieval_mode,
                model_fallback_used=model_fallback_used,
                warnings=tuple(warnings) + tuple(itinerary.warnings),
                degraded_notice=_degraded_notice(status, retrieval_mode or itinerary.retrieval_mode, model_fallback_used),
                days=tuple(filtered_days),
                notes=_filter_blocks(
                    itinerary.notes,
                    source_map,
                    accepted,
                    "itinerary.notes",
                    allow_uncited=bool(
                        itinerary_support.get("notes")
                        and itinerary_support["notes"].provenance in {"user_provided", "uncertain"}
                    ),
                ),
                field_support=itinerary_supports,
            )
            itinerary.validate_citations()
    except Exception as exc:
        if not allow_partial:
            raise CitationValidationError(str(exc), raw_answer=answer) from exc
        warnings.append(f"تم استبعاد الجدول المنظم بسبب فشل تحقق الاستشهادات: {type(exc).__name__}.")
        validation_error = validation_error or str(exc)
        itinerary = None
    if not accepted and itinerary is None and not answer:
        raise CitationValidationError("لا توجد إجابة أو حقائق موثقة قابلة للرندر.", raw_answer=answer)
    answer_ids = set(INLINE_CITATION_RE.findall(answer))
    used_source_ids = accepted | answer_ids
    filtered_sources = tuple(source for source in raw_sources if source.citation_id in used_source_ids)
    if itinerary is not None:
        # The renderer must never expose an unreferenced source from the raw
        # graph state.  Keep the source map aligned with the verified subset.
        itinerary = replace(itinerary, sources=filtered_sources)
        itinerary.validate_citations()
    return VerifiedRenderInput(
        run_id=run_id,
        final_answer=answer,
        itinerary=itinerary,
        sources=filtered_sources,
        accepted_citation_ids=frozenset(accepted),
        verification_status=status,
        retrieval_mode=str(state.get("retrieval_mode") or ""),
        model_fallback_used=model_fallback_used,
        warnings=tuple(warnings),
        degraded_notice=_degraded_notice(status, retrieval_mode, model_fallback_used),
        validation_error=validation_error,
    )
