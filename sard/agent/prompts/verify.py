"""Constrained-entailment prompt for the verify node (workstream G).

Deterministic layers (L1-L6) remain authoritative; the model (L7) is invoked
ONLY for L4-L6 disagreements and for SUPPORTED high-risk claims, with
temperature 0 and no chain-of-thought.  Output is strict JSON per claim:

    {"claims": [{"claim_id": "...", "verdict": "SUPPORTED|PARTIAL|UNSUPPORTED",
                 "reason_codes": [...], "correction": "...?"}]}

Reason codes: unknown_citation, missing_provenance, lexical_gap,
entity_mismatch, contradiction, partial_scope, non_factual.
"""

from __future__ import annotations

VERIFY_SYSTEM_PROMPT = (
    "أنت مدقق استلزام مقيّد في مساعد «سرد». درجة الحرارة 0 ولا تُخرج أي تفكير متسلسل.\n"
    "الفحوص الحتمية (L1-L6) حاسمة؛ أنت طبقة L7 فقط لحالات الخلاف L4-L6 وكل الادعاءات عالية المخاطر.\n"
    "لا توسّع الحكم أبدًا: يمكنك فقط التضييق (SUPPORTED→PARTIAL→UNSUPPORTED).\n"
    "فئات الادعاءات: factual/high_risk_factual/non_factual/interpretive/user_provided/uncertain.\n"
    "القواعد:\n"
    "1) non_factual (انتقالات/صياغة تنظيمية/توصيات/أسلوب/رأي) لا يتطلب استشهادًا أبدًا.\n"
    "2) interpretive يحتاج استشهادًا + صياغة متحفظة (قد/ربما/يبدو) لا إزالة.\n"
    "3) high_risk (سلامة/ساعات/أسعار/قانوني/تأشيرة/طبي/مالي/ديني/تواريخ/مسمّيات) يتطلب L4+(ضمن أعلى-3 في L6 أو SUPPORTED في L7) وإلا UNSUPPORTED + تعليم الصف.\n"
    "4) أعد JSON صالحًا فقط دون أي نص إضافي بالشكل:\n"
    '\'{"claims": [{"claim_id": "...", "verdict": "SUPPORTED|PARTIAL|UNSUPPORTED", '
    '"reason_codes": ["lexical_gap"], "correction": "..."}]}\n'
    "رموز الأسباب المسموحة حصرًا: unknown_citation, missing_provenance, lexical_gap, "
    "entity_mismatch, contradiction, partial_scope, non_factual.\n"
    "لا تُعدّل ادعاءً مدعومًا بحرية ولا تتجاهل أي claim_id.\n\n"
    "الأدلة:\n{evidence}"
)

VERIFY_USER_TEMPLATE = (
    "تحقق من الادعاءات التالية باستلزام مقيّد (SUPPORTED|PARTIAL|UNSUPPORTED + reason_codes):\n{claims}"
)

VERIFY_OUTPUT_KEYS = ("claims",)

# Back-compat: legacy callers map verdict<->status.
VERDICT_TO_STATUS = {
    "SUPPORTED": "supported",
    "PARTIAL": "partially_supported",
    "UNSUPPORTED": "unsupported",
}
