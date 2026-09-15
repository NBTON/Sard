"""Structured request-extraction node: ``understand``.

Semantic extraction only, with bounded model retry (via the injected model
service), deterministic Arabic fallback for basic fields, and multimodal
file reference extraction.
"""

from __future__ import annotations

import logging
import time

from sard.agent.events import (
    EVENT_COMPLETED,
    EVENT_DEGRADED,
    EVENT_STARTED,
    adapt_fallback_events,
    make_event,
)
from sard.agent.prompts.understand import (
    UNDERSTAND_OUTPUT_KEYS,
    UNDERSTAND_SYSTEM_PROMPT,
    UNDERSTAND_USER_TEMPLATE,
)
from sard.agent.state import MultimodalItem
from sard.agent.tools.multimodal_tools import extract_multimodal_context
from sard.agent.util import coerce_int, deterministic_extraction
from sard.rag.normalize import normalize_arabic


def _str_list(value):
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [item for item in value if isinstance(item, str) and item.strip()]


logger = logging.getLogger(__name__)


def _node_deadline(deps, label: str):
    """Hierarchical Deadline/cancel for this node (never raises)."""
    try:
        from sard.agent.deadline import coerce_deadline as _coerce
    except Exception as exc_import:
        logger.debug("Deadline import skipped (%s).", type(exc_import).__name__)
        return None, getattr(deps, "cancel_event", None)
    try:
        raw = getattr(deps, "deadline", None)
        cancel = getattr(deps, "cancel_event", None)
        dl = _coerce(raw, cancel_event=cancel, label=label)
        if dl is not None and cancel is None:
            try:
                cancel = getattr(dl, "cancel_event", None)
            except Exception as exc_cancel:
                logger.debug("Deadline cancel read skipped (%s).", type(exc_cancel).__name__)
        return dl, cancel
    except Exception as exc_coerce:
        logger.debug("Deadline coerce skipped (%s).", type(exc_coerce).__name__)
        return None, getattr(deps, "cancel_event", None)


def _raise_if_cancelled(cancel, stage: str) -> None:
    """Raise typed cancellation before starting model work (never degrade).

    A set cancel flag must propagate to the graph guard as
    :class:`DeadlineCancelledError` so cancellation stays distinguishable
    from timeout and can never continue as an ordinary degraded failure.
    """
    flagged = False
    try:
        flagged = bool(cancel is not None and cancel.is_set())
    except Exception as exc_flag:
        logger.debug("Cancel flag read skipped (%s).", type(exc_flag).__name__)
    if not flagged:
        return
    try:
        from sard.agent.deadline import DeadlineCancelledError as _DC
    except Exception as exc_import:
        logger.debug("Deadline import skipped (%s).", type(exc_import).__name__)
        raise TimeoutError(f"cancelled before node '{stage}'")
    raise _DC(f"cancelled before node '{stage}'", stage=stage)


def _budget_exhausted(dl) -> bool:
    try:
        return bool(dl is not None and dl.reserve_remaining() <= 0)
    except Exception as exc_budget:
        logger.debug("Budget check skipped (%s).", type(exc_budget).__name__)
        return False


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
    # Cancel gate at node entry (before ANY work, model or deterministic):
    # a set cancel flag raises typed and never degrades/continues.
    _, _entry_cancel = _node_deadline(deps, "understand")
    _raise_if_cancelled(_entry_cancel, "understand")
    request = (state.get("original_request") or "").strip()
    base = deterministic_extraction(request)

    events = [
        make_event(EVENT_STARTED, run, "understand", "started", summary="بدء فهم الطلب")
    ]

    # Extract any multimodal file references in the request
    mm_extracted = extract_multimodal_context(request)
    multimodal_items = [
        MultimodalItem(
            filename=item.filename,
            file_type=item.file_type,
            extracted_text=item.extracted_text,
            description=item.description,
            source_path=item.source_path,
            extraction_method=item.extraction_method,
            metadata=item.metadata,
        )
        for item in mm_extracted
    ]

    degraded = False
    model_used = None
    fallback_events = []
    structured = dict(base)
    model_service = getattr(deps, "model_service", None)
    if model_service is not None:
        dl, cancel_event = _node_deadline(deps, "understand")
        _raise_if_cancelled(cancel_event, "understand")
        if _budget_exhausted(dl):
            degraded = True
        else:
            user = UNDERSTAND_USER_TEMPLATE.format(request=request)
            parsed, response = model_service.invoke_json(
                "understand",
                UNDERSTAND_SYSTEM_PROMPT,
                user,
                allowed_keys=UNDERSTAND_OUTPUT_KEYS,
                deadline=dl,
                cancel_event=cancel_event,
            )
            model_used = response.model_used
            fallback_events = adapt_fallback_events(response.events)
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
        "multimodal_inputs": multimodal_items,
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
        "fallback_events": fallback_events,
        "timings": {"understand_ms": duration_ms},
        "progress_events": events,
    }
