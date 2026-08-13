"""Provisional-planning node: ``plan``.

Produces days/time-block/activity-type shapes, evidence topics, open
questions and constraints — never unverified concrete facts.
"""

from __future__ import annotations

import time
from typing import Optional

from sard.agent.events import (
    EVENT_COMPLETED,
    EVENT_DEGRADED,
    EVENT_STARTED,
    adapt_fallback_events,
    make_event,
)
from sard.agent.prompts.plan import (
    PLAN_OUTPUT_KEYS,
    PLAN_SYSTEM_PROMPT,
    PLAN_USER_TEMPLATE,
)
from sard.agent.state import ItineraryPlan, PlanDay, PlanTimeBlock


def _plan_from_dict(payload: dict, duration_hint: Optional[int]) -> ItineraryPlan:
    days_raw = payload.get("days") or []
    if not isinstance(days_raw, list):
        days_raw = []
    if not days_raw and duration_hint:
        days_raw = [{"day_index": i + 1, "focus": "", "time_blocks": []} for i in range(duration_hint)]

    days = []
    for raw in days_raw:
        if not isinstance(raw, dict):
            continue
        blocks = []
        for block in raw.get("time_blocks") or []:
            if isinstance(block, dict):
                blocks.append(
                    PlanTimeBlock(
                        period=str(block.get("period") or "أثناء اليوم"),
                        activity_type=str(block.get("activity_type") or "استكشاف"),
                    )
                )
        days.append(
            PlanDay(
                day_index=int(raw.get("day_index", 1)),
                focus=str(raw.get("focus") or ""),
                time_blocks=tuple(blocks),
            )
        )
    if not days:
        days = [PlanDay(day_index=1, focus="استكشاف عام")]

    def _tuple_of_strs(value) -> tuple[str, ...]:
        if not isinstance(value, list):
            return ()
        return tuple(str(x) for x in value if str(x).strip())

    return ItineraryPlan(
        focus_summary=str(payload.get("focus_summary") or "خطة استكشاف مؤقتة"),
        days=tuple(days),
        activity_types=_tuple_of_strs(payload.get("activity_types")),
        evidence_topics=_tuple_of_strs(payload.get("evidence_topics")),
        open_questions=_tuple_of_strs(payload.get("open_questions")),
        constraints=_tuple_of_strs(payload.get("constraints")),
    )


def _deterministic_plan(state: dict) -> ItineraryPlan:
    destination = state.get("destination")
    interests = state.get("interests") or []
    duration = state.get("duration_days") or 1
    evidence_topics = list(interests) if interests else ([destination] if destination else [])
    activity_types = list(interests) if interests else ["استكشاف"]
    days = tuple(
        PlanDay(day_index=i + 1, focus="استكشاف عام")
        for i in range(max(1, int(duration)))
    )
    return ItineraryPlan(
        focus_summary=f"خطة تأسيسية لرحلة إلى {destination}" if destination else "خطة تأسيسية",
        days=days,
        activity_types=tuple(activity_types),
        evidence_topics=tuple(evidence_topics),
        open_questions=("ما الأدلة المتوفرة عن الأنشطة المقترحة؟",),
        constraints=tuple(state.get("missing_constraints") or []) + tuple(state.get("assumptions") or []),
    )


def plan(state: dict, deps) -> dict:
    run = state.get("run_id") or ""
    start = time.monotonic()
    events = [
        make_event(EVENT_STARTED, run, "plan", "started", summary="بدء التخطيط المؤقت")
    ]

    degraded = False
    model_used = None
    fallback_events = []
    fallback_plan = _deterministic_plan(state)
    final_plan = fallback_plan

    model_service = getattr(deps, "model_service", None)
    if model_service is not None:
        user = PLAN_USER_TEMPLATE.format(
            destination=state.get("destination") or "غير محددة",
            duration_days=state.get("duration_days") or "غير معروف",
            audience=state.get("audience") or [],
            interests=state.get("interests") or [],
            timing=state.get("timing") or "غير محدد",
            missing=state.get("missing_constraints") or [],
            assumptions=state.get("assumptions") or [],
            request=state.get("original_request"),
        )
        parsed, response = model_service.invoke_json(
            "plan",
            PLAN_SYSTEM_PROMPT,
            user,
            allowed_keys=PLAN_OUTPUT_KEYS,
        )
        model_used = response.model_used
        fallback_events = adapt_fallback_events(response.events)
        if parsed is not None:
            final_plan = _plan_from_dict(parsed, state.get("duration_days"))
        else:
            degraded = True

    duration_ms = (time.monotonic() - start) * 1000
    events.append(
        make_event(
            EVENT_COMPLETED if not degraded else EVENT_DEGRADED,
            run,
            "plan",
            "degraded" if degraded else "completed",
            summary="اكتمل التخطيط المؤقت" if not degraded else "خطة حتمية مؤقتة (تدهور)",
            duration_ms=duration_ms,
            degraded=degraded,
        )
    )

    return {
        "plan": final_plan,
        "model_routes": {"plan": model_used},
        "fallback_events": fallback_events,
        "timings": {"plan_ms": duration_ms},
        "progress_events": events,
    }
