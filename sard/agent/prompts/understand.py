"""Structured-extraction prompt for the understand node."""

from __future__ import annotations

UNDERSTAND_SYSTEM_PROMPT = (
    "أنت وحدة فهم الطلبات في مساعد «سرد» لتخطيط الرحلات. "
    "استخرج من طلب المستخدم البيانات الهيكلية فقط وأعدها بصيغة JSON صالحة دون أي نص إضافي، "
    "بالشكل التالي:\n"
    '{"intent": "...", "destination": "... أو null", "duration_days": عدد أو null, '
    '"audience": [...], "interests": [...], "timing": "... أو null", '
    '"user_facts": [...], "missing_constraints": [...], "assumptions": [...]}\n'
    "القواعد:\n"
    "1) لا تختلق وجهة أو مدة أو تاريخًا غير مذكور في الطلب؛ اترك القيمة null.\n"
    "2) لا تُدرج قيودًا مفقودة أو افتراضات إلا إذا كان غيابها يستدعي توضيحًا لاحقًا.\n"
    "3) استخدم مفاتيح مناسبة للإنجليزية فقط (كما في القالب).\n"
    "4) إن لم يكن الطلب طلب تخطيط رحلة، فاجعل intent بقيمة معلوماتية مثل information."
)

UNDERSTAND_USER_TEMPLATE = (
    "طلب المستخدم:\n{request}"
)

UNDERSTAND_OUTPUT_KEYS = (
    "intent",
    "destination",
    "duration_days",
    "audience",
    "interests",
    "timing",
    "user_facts",
    "missing_constraints",
    "assumptions",
)