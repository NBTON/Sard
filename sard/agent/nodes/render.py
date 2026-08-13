"""Provider-free Step 6 artifact rendering node.

The node receives only the final graph state.  It validates the supplied
verified subset once, then fans out to three independent render/publish paths.
No model, retriever, reranker, Zvec, or network dependency is available here.
"""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path

from sard.agent.events import (
    EVENT_COMPLETED,
    EVENT_DEGRADED,
    EVENT_GRAPH_COMPLETED,
    EVENT_STARTED,
    FailureKind,
    make_error,
    make_event,
)
from sard.agent.routing import collect_verified_claims
from sard.agent.state import GraphOutcome, RenderedArtifactInfo
from sard.outputs.artifacts import (
    ArtifactError,
    ArtifactManager,
    ArtifactWriteResult,
    failed_artifact,
    skipped_artifact,
)
from sard.outputs.calendar import CalendarRenderError, render_calendar
from sard.outputs.pdf import render_pdf
from sard.outputs.pdf_environment import locked_pdf_output_root
from sard.outputs.raw import render_raw_text
from sard.outputs.validation import CitationValidationError, VerifiedRenderInput, build_verified_render_input, verified_answer_subset


def _parse_explicit_dates(values) -> tuple[date, ...]:
    result: list[date] = []
    for value in values or ():
        if isinstance(value, date) and not hasattr(value, "hour"):
            result.append(value)
            continue
        if isinstance(value, str):
            try:
                result.append(date.fromisoformat(value.strip()))
            except ValueError:
                continue
    return tuple(result)


def _manifest_info(result: ArtifactWriteResult) -> RenderedArtifactInfo:
    return RenderedArtifactInfo(
        filename=result.filename,
        path=result.absolute_path or "",
        absolute_path=result.absolute_path or "",
        mime_type=result.mime_type,
        size_bytes=result.size_bytes,
        warnings=result.warnings,
        artifact_type=result.artifact_type,
        display_label=result.display_label,
        checksum=result.checksum,
        creation_status=result.creation_status,
        error_category=result.error_category,
    )


def _artifact_failure(artifact_type: str, label: str, filename: str, mime: str, exc: Exception) -> ArtifactWriteResult:
    category = getattr(exc, "category", None) or type(exc).__name__.lower()
    return failed_artifact(
        artifact_type=artifact_type,
        display_label=label,
        filename=filename,
        mime_type=mime,
        category=category,
        warning=f"تعذر إنشاء {label}: {category}.",
    )


def _render_pdf_artifact(manager: ArtifactManager, verified: VerifiedRenderInput) -> ArtifactWriteResult:
    if verified.itinerary is None:
        return skipped_artifact(
            artifact_type="pdf",
            display_label="برنامج الرحلة PDF",
            filename="itinerary.pdf",
            mime_type="application/pdf",
            category="no_verified_itinerary",
            warning="تم تخطي PDF لعدم توفر جدول رحلة منظم موثق.",
        )
    temporary = manager.temporary_path(".pdf")
    try:
        with locked_pdf_output_root(manager.run_dir):
            render_pdf(verified.itinerary, temporary)
        return manager.publish_generated_file(
            temporary,
            filename="itinerary.pdf",
            artifact_type="pdf",
            display_label="برنامج الرحلة PDF",
            mime_type="application/pdf",
            warnings=verified.warnings,
        )
    finally:
        temporary.unlink(missing_ok=True)


def render(state: dict, deps) -> dict:
    run = state.get("run_id") or ""
    start = time.monotonic()
    events = [make_event(EVENT_STARTED, run, "render", "started", summary="بدء إنشاء المخرجات")]
    if not bool(getattr(deps, "render_artifacts", False)):
        outcome = GraphOutcome.COMPLETED.value
        if state.get("node_failures") or state.get("verification_exhausted") or not state.get("evidence") or not collect_verified_claims(state):
            outcome = GraphOutcome.PARTIAL.value
        compatibility_warnings = []
        if state.get("itinerary") is not None and not collect_verified_claims(state):
            compatibility_warnings.append("تم رفض الحقول الواقعية غير المدعومة من جدول الرحلة.")
        duration_ms = (time.monotonic() - start) * 1000
        return {
            "final_answer": state.get("final_answer") or state.get("draft") or "",
            "itinerary": state.get("itinerary") if collect_verified_claims(state) else None,
            "sources": list(state.get("sources") or []),
            "rendered_artifacts": [],
            "graph_outcome": outcome,
            "warnings": list(state.get("warnings") or ()) + compatibility_warnings,
            "progress_events": events + [
                make_event(EVENT_GRAPH_COMPLETED, run, "render", outcome, summary="تم التحقق من المخرجات دون إنشاء ملفات", duration_ms=duration_ms, degraded=outcome != GraphOutcome.COMPLETED.value),
                make_event(EVENT_COMPLETED, run, "render", "completed", summary="اكتمل التحقق من المخرجات", duration_ms=duration_ms),
            ],
            "timings": {"render_ms": duration_ms},
        }

    warnings: list[str] = []
    errors = []
    try:
        verified = build_verified_render_input(state)
    except CitationValidationError as exc:
        source_map = {
            getattr(source, "citation_id", ""): source
            for source in state.get("sources") or ()
            if getattr(source, "citation_id", "")
        }
        accepted_ids = {
            citation_id
            for claim in state.get("atomic_claims") or ()
            if getattr(getattr(claim, "status", None), "value", getattr(claim, "status", None))
            in {"supported", "partially_supported", "user_provided", "explicitly_uncertain"}
            for citation_id in getattr(claim, "citation_ids", ())
        }
        safe_answer = verified_answer_subset(
            str(state.get("final_answer") or state.get("draft") or ""),
            state.get("atomic_claims") or (),
            set(source_map) & accepted_ids,
        )
        verified = VerifiedRenderInput(
            run_id=run,
            final_answer=safe_answer,
            itinerary=None,
            sources=tuple(source for citation_id, source in source_map.items() if citation_id in accepted_ids),
            accepted_citation_ids=frozenset(set(source_map) & accepted_ids),
            verification_status="evidence_limited",
            retrieval_mode=str(state.get("retrieval_mode") or ""),
            model_fallback_used=bool(state.get("model_fallback_used")),
            warnings=("فشل تحقق الاستشهادات؛ حُفظ النص المتاح فقط.",),
            degraded_notice="تعذر التحقق الكامل من بعض المخرجات.",
            validation_error=str(exc),
        )
        errors.append(make_error(run, "render", FailureKind.RENDERING_VALIDATION, "فشل تحقق الاستشهادات؛ حُفظ النص المتاح فقط.", retryable=False))
        warnings.extend(verified.warnings)
    else:
        warnings.extend(verified.warnings)
        if verified.validation_error:
            errors.append(make_error(run, "render", FailureKind.RENDERING_VALIDATION, "فشل تحقق الاستشهادات؛ حُفظ الجزء الموثق فقط.", retryable=False))

    explicit_dates = _parse_explicit_dates(state.get("caller_dates")) or _parse_explicit_dates(state.get("travel_dates"))
    if explicit_dates and verified.itinerary is not None:
        from dataclasses import replace

        verified = replace(verified, itinerary=replace(verified.itinerary, explicit_dates=explicit_dates))
    if bool(getattr(deps, "preview_calendar", False)) and explicit_dates:
        warnings.append("التقويم المعروض معاينة مبنية على تواريخ زوّد بها المستدعي.")

    try:
        manager = ArtifactManager(
            getattr(deps, "output_root", None) or state.get("output_root"),
            run,
            checksums=bool(getattr(deps, "render_checksums", False) or state.get("render_checksums", False)),
        )
    except Exception as exc:
        warnings.append("تعذر فتح مجلد المخرجات الآمن.")
        errors.append(make_error(run, "render", FailureKind.RENDERING_VALIDATION, "تعذر فتح مجلد المخرجات الآمن.", retryable=False))
        duration_ms = (time.monotonic() - start) * 1000
        return {
            "final_answer": verified.final_answer,
            "itinerary": verified.itinerary,
            "sources": list(verified.sources),
            "rendered_artifacts": [],
            "graph_outcome": GraphOutcome.FAILED.value,
            "errors": errors,
            "warnings": warnings,
            "progress_events": events + [make_event(EVENT_GRAPH_COMPLETED, run, "render", "failed", summary="فشل مجلد المخرجات", duration_ms=duration_ms, degraded=True)],
            "timings": {"render_ms": duration_ms},
        }

    results: list[ArtifactWriteResult] = []
    # Raw text is independent and is attempted first so it survives structured
    # PDF/calendar failures.
    try:
        raw = render_raw_text(
            verified.final_answer,
            verified.sources,
            verification_status=verified.verification_status,
            retrieval_mode=verified.retrieval_mode,
            warnings=verified.warnings,
            degraded_notice=verified.degraded_notice,
        )
        results.append(manager.write_bytes(raw.data, filename="answer.txt", artifact_type="raw_text", display_label="الإجابة العربية الخام", mime_type="text/plain; charset=utf-8", warnings=raw.warnings))
    except Exception as exc:
        results.append(_artifact_failure("raw_text", "الإجابة العربية الخام", "answer.txt", "text/plain; charset=utf-8", exc))

    try:
        results.append(_render_pdf_artifact(manager, verified))
    except Exception as exc:
        results.append(_artifact_failure("pdf", "برنامج الرحلة PDF", "itinerary.pdf", "application/pdf", exc))

    if verified.itinerary is None:
        results.append(skipped_artifact(artifact_type="calendar", display_label="تقويم الرحلة", filename="itinerary.ics", mime_type="text/calendar; charset=utf-8", category="no_verified_itinerary", warning="تم تخطي التقويم لعدم توفر جدول رحلة موثق."))
    elif not (
        explicit_dates
        or verified.itinerary.explicit_dates
        or any(day.date for day in verified.itinerary.days)
    ):
        results.append(skipped_artifact(artifact_type="calendar", display_label="تقويم الرحلة", filename="itinerary.ics", mime_type="text/calendar; charset=utf-8", category="missing_dates", warning="أدخل تواريخ صريحة قبل إنشاء التقويم؛ لم تُخترع تواريخ."))
    else:
        try:
            calendar = render_calendar(
                verified.itinerary,
                preview=bool(getattr(deps, "preview_calendar", False)),
            )
            results.append(manager.write_bytes(calendar.data, filename="itinerary.ics", artifact_type="calendar", display_label="تقويم الرحلة", mime_type="text/calendar; charset=utf-8", warnings=calendar.warnings))
        except CalendarRenderError as exc:
            results.append(skipped_artifact(artifact_type="calendar", display_label="تقويم الرحلة", filename="itinerary.ics", mime_type="text/calendar; charset=utf-8", category=exc.category, warning="\n".join((str(exc), *exc.warnings))))
        except Exception as exc:
            results.append(_artifact_failure("calendar", "تقويم الرحلة", "itinerary.ics", "text/calendar; charset=utf-8", exc))

    for result in results:
        warnings.extend(result.warnings)
    artifacts = [_manifest_info(result) for result in results]
    successes = sum(result.creation_status == "created" for result in results)
    failures = sum(result.creation_status in {"failed", "skipped"} for result in results)
    outcome = (
        GraphOutcome.COMPLETED.value
        if successes == len(results)
        and not state.get("verification_exhausted")
        and verified.verification_status.value == "verified"
        else GraphOutcome.PARTIAL.value
    )
    if successes == 0:
        outcome = GraphOutcome.FAILED.value
    duration_ms = (time.monotonic() - start) * 1000
    events.append(make_event(EVENT_GRAPH_COMPLETED, run, "render", outcome, summary=f"اكتملت المخرجات بنتيجة {outcome}", duration_ms=duration_ms, source_count=len(verified.sources), degraded=bool(verified.degraded_notice or failures)))
    events.append(make_event(EVENT_DEGRADED if outcome != GraphOutcome.COMPLETED.value else EVENT_COMPLETED, run, "render", outcome, summary="تم إنشاء المخرجات" if outcome == GraphOutcome.COMPLETED.value else "تم إنشاء مخرجات جزئية", duration_ms=duration_ms, degraded=outcome != GraphOutcome.COMPLETED.value))
    return {
        "final_answer": verified.final_answer,
        "itinerary": verified.itinerary,
        "sources": list(verified.sources),
        "rendered_artifacts": artifacts,
        "model_fallback_used": verified.model_fallback_used,
        "graph_outcome": outcome,
        "errors": errors,
        "warnings": warnings,
        "progress_events": events,
        "timings": {"render_ms": duration_ms},
    }
