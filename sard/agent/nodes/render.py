"""Output validation node: ``render``.

Normally validates the composed output only: final answer, typed Step 4
itinerary, citation map/source list and render warnings.  Rejects
unsupported factual structured fields (dropping the itinerary rather than
emitting dubious data).  An injected Step 4 renderer smoke path may run only
when explicitly enabled; nothing renders by default.
"""

from __future__ import annotations

import time
from typing import Optional

from sard.agent.events import (
    EVENT_COMPLETED,
    EVENT_GRAPH_COMPLETED,
    EVENT_STARTED,
    FailureKind,
    make_error,
    make_event,
)
from sard.agent.routing import collect_verified_claims
from sard.agent.state import GraphOutcome, RenderedArtifactInfo
from sard.outputs.schemas import INLINE_CITATION_RE

_ACCEPTED = {"supported", "partially_supported", "user_provided", "explicitly_uncertain"}


def _itinerary_referenced_citations(itinerary) -> tuple[str, ...]:
    cited = []
    seen = set()
    for block in itinerary.all_text_blocks():
        for cid in block.citation_ids:
            if cid not in seen:
                seen.add(cid)
                cited.append(cid)
        for cid in INLINE_CITATION_RE.findall(block.text):
            if cid not in seen:
                seen.add(cid)
                cited.append(cid)
    return tuple(cited)


def render(state: dict, deps) -> dict:
    run = state.get("run_id") or ""
    start = time.monotonic()
    events = [make_event(EVENT_STARTED, run, "render", "started", summary="بدء التحقق من المخرجات")]

    warning_updates = []
    error_updates = []
    node_failures = list(state.get("node_failures") or [])

    final_answer = state.get("final_answer")
    if not final_answer:
        draft = state.get("draft")
        final_answer = draft if draft else (
            "لم يتوفر دليل ولا مسودة قابلة للتحقق؛ لا يمكن تقديم إجابة موثوقة."
        )

    itinerary = state.get("itinerary")
    sources = list(state.get("sources") or [])

    atomic_claims = list(state.get("atomic_claims") or [])
    supported_citations = set()
    for claim in atomic_claims:
        status = getattr(claim, "status", None)
        if status is not None and status.value in _ACCEPTED:
            supported_citations.update(claim.citation_ids or ())

    if itinerary is not None:
        try:
            itinerary.validate_citations()
        except Exception as exc:
            warning_updates.append(f"تم رفض جدول الرحلة: {str(exc)}")
            error_updates.append(
                make_error(
                    run,
                    "render",
                    FailureKind.RENDERING_VALIDATION,
                    "جدول الرحلة يتضمن استشهادات غير صالحة ورُفض.",
                    retryable=False,
                )
            )
            itinerary = None
        else:
            referenced = _itinerary_referenced_citations(itinerary)
            unsupported = [cid for cid in referenced if cid not in supported_citations]
            if unsupported:
                warning_updates.append(
                    "تم رفض الحقول الواقعية غير المدعومة من جدول الرحلة."
                )
                error_updates.append(
                    make_error(
                        run,
                        "render",
                        FailureKind.RENDERING_VALIDATION,
                        "جدول الرحلة يضم بنودًا واقعية غير مدعومة من الادعاءات الموثقة.",
                        retryable=False,
                    )
                )
                itinerary = None

    artifacts = []
    render_smoke = getattr(deps, "render_smoke", None)
    render_enabled = bool(getattr(deps, "render_artifacts", False))
    if render_smoke is not None and render_enabled and itinerary is not None:
        try:
            artifact = render_smoke(itinerary)
            filename = getattr(artifact, "filename", "")
            path = getattr(artifact, "path", "")
            artifacts.append(
                RenderedArtifactInfo(
                    filename=filename,
                    path=str(path),
                    mime_type=getattr(artifact, "mime_type", ""),
                    size_bytes=int(getattr(artifact, "size_bytes", 0)),
                    warnings=tuple(getattr(artifact, "warnings", ()) or ()),
                )
            )
        except Exception as exc:
            warning_updates.append(f"فشل تجربة العرض الإضافية: {type(exc).__name__}")

    outcome = GraphOutcome.COMPLETED.value
    if node_failures:
        outcome = GraphOutcome.FAILED.value
    elif state.get("verification_exhausted"):
        outcome = GraphOutcome.PARTIAL.value
    elif not state.get("evidence"):
        outcome = GraphOutcome.PARTIAL.value
    elif not collect_verified_claims(state):
        outcome = GraphOutcome.PARTIAL.value

    duration_ms = (time.monotonic() - start) * 1000
    events.append(
        make_event(
            EVENT_GRAPH_COMPLETED,
            run,
            "render",
            outcome,
            summary=f"اكتمل التشغيل بنتيجة {outcome}",
            duration_ms=duration_ms,
            source_count=len(sources),
            coverage=getattr(state.get("coverage"), "coverage_ratio", None),
            degraded=False,
        )
    )
    events.append(
        make_event(
            EVENT_COMPLETED,
            run,
            "render",
            "completed",
            summary="اكتمل التحقق من المخرجات",
            duration_ms=duration_ms,
        )
    )

    return {
        "final_answer": final_answer,
        "itinerary": itinerary,
        "sources": sources,
        "rendered_artifacts": artifacts,
        "graph_outcome": outcome,
        "errors": error_updates,
        "warnings": warning_updates,
        "progress_events": events,
        "timings": {"render_ms": duration_ms},
    }