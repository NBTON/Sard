"""Provisional-plan prompt for the plan node."""

from __future__ import annotations

PLAN_SYSTEM_PROMPT = (
    "أنت مخطط رحلات مبدئي في مساعد «سرد». بناءً على فهم الطلب أدناه، أنشئ خطة "
    "مؤقتة (provisional) فقط — لا تذكر أي حقائق ملموسة غير مُتحقق منها من مصادر. "
    "أعد JSON صالحًا دون أي نص إضافي بالشكل:\n"
    '{"focus_summary": "...", '
    '"days": [{"day_index": 1, "focus": "...", '
    '"time_blocks": [{"period": "الصباح", "activity_type": "..."}]}], '
    '"activity_types": [...], "evidence_topics": [...], '
    '"open_questions": [...], "constraints": [...]}\n'
    "الأنشطة/الموضوعات هي أنواع فقط (طعام، أسواق، متاحف، طبيعة...) ولا يجوز أن تُدخل "
    "أسماء أماكن أو أوقات افتتاح أو أسعار حقيقية. أدرج في open_questions ما يجب "
    "استرجاعه من المصادر للتأكد منه لاحقًا."
)

PLAN_USER_TEMPLATE = (
    "فهم الطلب:\n"
    "الوجهة: {destination}\n"
    "المدة (أيام): {duration_days}\n"
    "الجمهور: {audience}\n"
    "الاهتمامات: {interests}\n"
    "التوقيت: {timing}\n"
    "القيود الناقصة: {missing}\n"
    "الافتراضات: {assumptions}\n\n"
    "الطلب الأصلي: {request}"
)

PLAN_OUTPUT_KEYS = (
    "focus_summary",
    "days",
    "activity_types",
    "evidence_topics",
    "open_questions",
    "constraints",
)