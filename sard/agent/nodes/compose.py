"""Cited-composition node: ``compose``.

Produces a cited Arabic draft, a typed Step 4 itinerary assembled only from
evidence actually referenced by the draft, and the citation source list.
IDs come exclusively from graph evidence; unknown details are omitted or
qualified.  Deterministic evidence-only/no-evidence fallbacks apply when the
model path degrades.  Prompts/reasoning are never surfaced.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Optional

from sard.agent.events import (
    EVENT_COMPLETED,
    EVENT_DEGRADED,
    EVENT_STARTED,
    make_event,
)
from sard.agent.prompts.compose import (
    COMPOSE_SYSTEM_PROMPT,
    COMPOSE_USER_TEMPLATE,
)
from sard.agent.state import EvidenceItem
from sard.outputs.schemas import (
    INLINE_CITATION_RE,
    CITATION_ID_RE,
    CitationSource,
    Itinerary,
    ItineraryDay,
    ItineraryStop,
    TextBlock,
)

_DRAFT_LIMIT_CHARS = 900


def _safe_source(citation_id: str, item: EvidenceItem) -> Optional[CitationSource]:
    if not CITATION_ID_RE.fullmatch(citation_id):
        return None
    page = item.page_number if isinstance(item.page_number, int) and item.page_number >= 1 else None
    pub_date = None
    raw_date = item.publication_date
    if isinstance(raw_date, str):
        match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", raw_date.strip())
        if match:
            try:
                pub_date = datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).date()
            except ValueError:
                pub_date = None
    return CitationSource(
        citation_id=citation_id,
        title=item.title or "مصدر غير مسمى",
        url=item.source_url,
        page=page,
        section=item.section_heading,
        publication_date=pub_date,
    )


def build_sources(evidence: list[EvidenceItem]) -> list[CitationSource]:
    sources = []
    seen = set()
    for item in evidence:
        source = _safe_source(item.citation_id, item)
        if source is not None and item.citation_id not in seen:
            sources.append(source)
            seen.add(item.citation_id)
    return sources


def _extractive_draft(evidence: list[EvidenceItem]) -> str:
    if not evidence:
        return (
            "لم يتم العثور على أدلة كافية في المصادر المتاحة للإجابة عن هذا الطلب. "
            "يُرجى توضيح الطلب أو إثراء منظومة المصادر."
        )
    lines = [
        "تعذّر إنشاء نص تركيبي تلقائي؛ فيما يلي ملخص مباشر من الأدلة المسترجعة مع الاستشهادات:",
        "",
    ]
    for item in evidence:
        snippet = item.content.strip()
        if len(snippet) > _DRAFT_LIMIT_CHARS:
            snippet = snippet[: _DRAFT_LIMIT_CHARS] + "…"
        lines.append(f"- {snippet} [{item.citation_id}]")
    return "\n".join(lines)


def _repair_citations(text: str, valid_ids: set[str]) -> tuple[str, tuple[str, ...]]:
    referenced: list[str] = []
    seen = set()

    def _sub(match: re.Match) -> str:
        cid = match.group(0)[1:-1]
        if cid in valid_ids:
            if cid not in seen:
                referenced.append(cid)
                seen.add(cid)
            return match.group(0)
        return ""

    repaired = INLINE_CITATION_RE.sub(_sub, text)
    return repaired, tuple(referenced)


def _build_itinerary(
    plan,
    evidence: list[EvidenceItem],
    referenced_ids: tuple[str, ...],
    destination: Optional[str],
    constraints: list[str],
) -> Optional[Itinerary]:
    by_cit: dict[str, EvidenceItem] = {item.citation_id: item for item in evidence}
    referenced = [cid for cid in referenced_ids if cid in by_cit]
    if not referenced:
        return None

    title = f"خطة {destination}" if destination else "خطة الرحلة"
    summary = plan.focus_summary if plan is not None else "خطة مؤقتة مبنية على الأدلة المتاحة."

    days = []
    if plan is not None and plan.days:
        for index, plan_day in enumerate(plan.days):
            blocks = list(plan_day.time_blocks) if plan_day.time_blocks else []
            citations_for_day = referenced[index::max(1, len(plan.days))]
            stops = []
            for block_index, citation_id in enumerate(citations_for_day, start=1):
                item = by_cit[citation_id]
                text = item.content.strip()
                if len(text) > _DRAFT_LIMIT_CHARS:
                    text = text[: _DRAFT_LIMIT_CHARS] + "…"
                period = blocks[block_index - 1].period if block_index - 1 < len(blocks) else "أثناء اليوم"
                stops.append(
                    ItineraryStop(
                        time=period,
                        title=item.title or "محطة",
                        location=item.source_name or "",
                        paragraphs=(TextBlock(text=text, citation_ids=(citation_id,)),),
                    )
                )
            if not stops:
                period = blocks[0].period if blocks else "أثناء اليوم"
                stops.append(
                    ItineraryStop(
                        time=period,
                        title=plan_day.focus or "استكشاف",
                        location="",
                    )
                )
            days.append(
                ItineraryDay(
                    title=plan_day.focus or f"اليوم {plan_day.day_index}",
                    stops=tuple(stops),
                )
            )
    else:
        for index, citation_id in enumerate(referenced, start=1):
            item = by_cit[citation_id]
            text = item.content.strip()
            if len(text) > _DRAFT_LIMIT_CHARS:
                text = text[: _DRAFT_LIMIT_CHARS] + "…"
            stops = [
                ItineraryStop(
                    time="أثناء اليوم",
                    title=item.title or "محطة",
                    location=item.source_name or "",
                    paragraphs=(TextBlock(text=text, citation_ids=(citation_id,)),),
                )
            ]
            days.append(
                ItineraryDay(
                    title=f"اليوم {index}: {item.title or 'استكشاف'}",
                    stops=tuple(stops),
                )
            )

    notes = []
    for constraint in constraints:
        if constraint and constraint.strip():
            notes.append(TextBlock(text=constraint.strip()))

    sources = build_sources(evidence)
    return Itinerary(
        title=title,
        summary=summary,
        days=tuple(days),
        sources=tuple(sources),
        generated_at=datetime.now(timezone.utc),
        notes=tuple(notes),
    )


def compose(state: dict, deps) -> dict:
    run = state.get("run_id") or ""
    start = time.monotonic()
    events = [make_event(EVENT_STARTED, run, "compose", "started", summary="بدء صياغة الإجابة")]

    evidence: list[EvidenceItem] = list(state.get("evidence") or [])
    plan = state.get("plan")
    constraints = list(state.get("missing_constraints") or []) + list(state.get("assumptions") or [])
    destination = state.get("destination")
    verification_feedback = list(state.get("verification_feedback") or [])

    if not evidence:
        draft = (
            "لم يتم العثور على أدلة كافية في المصادر المتاحة للإجابة عن هذا الطلب. "
            "يُرجى توضيح الطلب أو إثراء منظومة المصادر."
        )
        duration_ms = (time.monotonic() - start) * 1000
        events.append(
            make_event(
                EVENT_COMPLETED,
                run,
                "compose",
                "completed",
                summary="لا دليل متاح — إجابة صريحة جزئية",
                duration_ms=duration_ms,
                source_count=0,
            )
        )
        return {
            "draft": draft,
            "itinerary": None,
            "sources": [],
            "model_routes": {"compose": None},
            "timings": {"compose_ms": duration_ms},
            "progress_events": events,
        }

    valid_ids = {item.citation_id for item in evidence}
    sources = build_sources(evidence)
    model_used = None
    degraded = False

    summary_parts = []
    if plan is not None:
        summary_parts.append(f"النطاق: {plan.focus_summary}")
    if plan is not None and plan.open_questions:
        summary_parts.append("أسئلة مفتوحة: " + "؛ ".join(plan.open_questions))
    if constraints:
        summary_parts.append("قيود: " + "؛ ".join(constraints))
    plan_summary = "\n".join(summary_parts) if summary_parts else "لا توجد قيود إضافية."

    draft = None
    model_service = getattr(deps, "model_service", None)
    if model_service is not None:
        context = "\n\n---\n\n".join(
            f"[{item.citation_id}] {item.title} — {item.source_name}\n{item.content}"
            for item in evidence
        )
        user = COMPOSE_USER_TEMPLATE.format(
            plan=plan_summary,
            open_questions="؛ ".join(plan.open_questions) if plan is not None and plan.open_questions else "—",
            constraints="؛ ".join(constraints) if constraints else "—",
            request=state.get("original_request"),
        )
        feedback_section = ""
        if verification_feedback:
            last = verification_feedback[-1]
            feedback_section = (
                "\nملاحظات المراجعة (عالجها):\n" + "\n".join(f"- {line}" for line in last.splitlines())
            )
        response = model_service.invoke(
            "compose",
            COMPOSE_SYSTEM_PROMPT.format(context=context),
            user + feedback_section,
        )
        model_used = response.model_used
        if response.success:
            draft, referenced_ids = _repair_citations(
                response.text, valid_ids
            )
            if not referenced_ids:
                degraded = True
                draft = _extractive_draft(evidence)
        else:
            degraded = True
            draft = _extractive_draft(evidence)

    if draft is None:
        draft = _extractive_draft(evidence)
        degraded = True

    referenced_ids = INLINE_CITATION_RE.findall(draft)
    referenced_ids = tuple(dict.fromkeys(cid for cid in referenced_ids if cid in valid_ids))

    itinerary = _build_itinerary(
        plan, evidence, referenced_ids, destination, constraints
    )

    duration_ms = (time.monotonic() - start) * 1000
    status = "degraded" if degraded else "completed"
    events.append(
        make_event(
            EVENT_COMPLETED if not degraded else EVENT_DEGRADED,
            run,
            "compose",
            status,
            summary="اكتملت الصياغة المسندة"
            if not degraded
            else "صياغة حتمية من الأدلة (تدهور)",
            duration_ms=duration_ms,
            source_count=len(evidence),
        )
    )

    return {
        "draft": draft,
        "itinerary": itinerary,
        "sources": sources,
        "model_routes": {"compose": model_used},
        "timings": {"compose_ms": duration_ms},
        "progress_events": events,
    }