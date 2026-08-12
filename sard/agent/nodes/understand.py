"""Structured request-extraction node: ``understand``.

Semantic extraction only, with bounded model retry (via the injected model
service) and a deterministic Arabic fallback for basic fields.  No itinerary
or evidence is produced here.
"""

from __future__ import annotations

import time

from sard.agent.events import (
    EVENT_COMPLETED,
    EVENT_DEGRADED,
    EVENT_STARTED,
    make_event,
)
from sard.agent.prompts.understand import (
    UNDERSTAND_OUTPUT_KEYS,
    UNDERSTAND_SYSTEM_PROMPT,
    UNDERSTAND_USER_TEMPLATE,
)
from sard.agent.util import coerce_int, deterministic_extraction
from sard.rag.normalize import normalize_arabic


def _str_list(value):
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [item for item in value if isinstance(item, str) and item.strip()]


def _constraints_report(
    destination, duration_days, timing, audience
) -> tuple[list[str], list[str]]:
    missing = []
    if not destination:
        missing.append("وجهة السفر غير محددة")
    if not duration_days:
        missing.append("مدة الرحلة غير محددة")
    if not timing:
        missing.append("تاريخ السفر غير محدد")
    if not audience:
        missing.append("عدد وطبيعة المسافرين غير محددة")
    assumptions = []
    if not duration_days:
        assumptions.append("سنفترض رحلة قصيرة (ليلة واحدة) ما لم يُحدد المستخدم خلاف ذلك")
    if not audience:
        assumptions.append("سنفترض مسافرًا بالغًا واحدًا ما لم يُحدد المستخدم خلاف ذلك")
    if not timing:
        assumptions.append("سنفترض إمكانية السفر في أي وقت ما لم يحدد المستخدم خلاف ذلك")
    return missing, assumptions


def understand(state: dict, deps) -> dict:
    run = state.get("run_id") or ""
    start = time.monotonic()
    request = (state.get("original_request") or "").strip()
    base = deterministic_extraction(request)

    events = [
        make_event(EVENT_STARTED, run, "understand", "started", summary="بدء فهم الطلب")
    ]

    degraded = False
    model_used = None
    structured = dict(base)
    model_service = getattr(deps, "model_service", None)
    if model_service is not None:
        user = UNDERSTAND_USER_TEMPLATE.format(request=request)
        parsed, response = model_service.invoke_json(
            "understand",
            UNDERSTAND_SYSTEM_PROMPT,
            user,
            allowed_keys=UNDERSTAND_OUTPUT_KEYS,
        )
        model_used = response.model_used
        if parsed is not None:
            for key in UNDERSTAND_OUTPUT_KEYS:
                if key in parsed:
                    structured[key] = parsed[key]
        else:
            degraded = True

    destination = structured.get("destination")
    if isinstance(destination, str):
        destination = destination.strip() or None
    duration_days = coerce_int(structured.get("duration_days"))
    timing = structured.get("timing")
    if isinstance(timing, str):
        timing = timing.strip() or None
    audience = _str_list(structured.get("audience"))
    interests = _str_list(structured.get("interests"))
    travel_dates = _str_list(structured.get("travel_dates"))
    timing_constraints = _str_list(structured.get("timing_constraints"))
    accessibility_needs = _str_list(structured.get("accessibility_needs"))
    budget = structured.get("budget")
    if isinstance(budget, str):
        budget = budget.strip() or None
    user_facts = _str_list(structured.get("user_facts"))
    intent = structured.get("intent") or "travel_planning"
    if isinstance(intent, str):
        intent = intent.strip().lower() or "travel_planning"

    missing, assumptions = _constraints_report(
        destination, duration_days, timing, audience
    )

    duration_ms = (time.monotonic() - start) * 1000
    status = "degraded" if degraded else "completed"
    events.append(
        make_event(
            EVENT_COMPLETED if not degraded else EVENT_DEGRADED,
            run,
            "understand",
            status,
            summary="اكتمل فهم الطلب" if not degraded else "فهم الطلب بطريقة حتمية (تدهور)",
            duration_ms=duration_ms,
            degraded=degraded,
        )
    )

    return {
        "normalized_request": normalize_arabic(request) or request,
        "request_language": "ar",
        "intent": intent,
        "destination": destination,
        "duration_days": duration_days,
        "travel_dates": travel_dates,
        "audience": audience,
        "interests": interests,
        "timing": timing,
        "timing_constraints": timing_constraints,
        "accessibility_needs": accessibility_needs,
        "budget": budget,
        "user_facts": user_facts,
        "missing_constraints": missing,
        "assumptions": assumptions,
        "understanding_degraded": degraded,
        "model_routes": {"understand": model_used},
        "timings": {"understand_ms": duration_ms},
        "progress_events": events,
    }