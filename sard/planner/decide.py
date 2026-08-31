"""Decision Engine for Sard's Isnād Planner.

Maps the isnād chain confidence score and conflict state to a concrete action decision:
- generate: strong chain, verified region & official sources, generate authoritative narrative.
- hedge: medium chain, single source or fuzzy dates; state what is verified, label uncertainty honestly.
- ask: low chain where clarifying questions (region, occasion, community) would unlock sources.
- refuse: weak chain, unsupported claims, or unresolvable fabricated lineage; politely decline.
"""

from __future__ import annotations

from typing import Tuple
from sard.schemas.isnad import Confidence, Decision, IsnadChain


def decide_action(chain: IsnadChain, query_text: str = "") -> Tuple[Decision, str]:
    """Determine the planner decision and justification."""
    # Greeting / Self-introduction handling
    if chain.classification == "greeting":
        return "generate", "تحية وترحيب بالضيف والتعريف بمهام سرد الثقافية المعتمدة."

    q_clean = query_text.strip().lower()
    if q_clean in ("من أنت", "من انت", "عرفني بنفسك", "مرحبا", "السلام عليكم", "صباح الخير", "مساء الخير", "هلا", "أهلاً", "hello", "hi", "who are you"):
        return "generate", "تحية وترحيب بالضيف والتعريف بمهام سرد الثقافية المعتمدة."

    # If there are open conflicts
    if chain.conflicts:
        # Check if the conflict is cross-regional (e.g. Asir vs Najd)
        # We hedge to surface the distinct traditions, or refuse if an invented mixed lineage was asked
        if any("تعارض إقليمي" in c for c in chain.conflicts):
            return "hedge", "وجود تباين أو تعارض إقليمي يستدعي توضيح الفوارق بين التقاليد دون دمجها."
        return "refuse", "تعارض في الإسناد أو نسبة تراثية غير دقيقة تستوجب التصحيح والرفض الصريح."

    # If score is high
    if chain.score == "high":
        return "generate", "إسناد قوي ومكتمل الأركان مستند إلى وثائق ومصادر رسمية ومحلية معتمدة."

    # If score is medium
    if chain.score == "medium":
        return "hedge", "إسناد مقبول يستند إلى مصدر معتمد، مع التحوط في التفاصيل غير المكتملة تاريخياً."

    # If score is low
    if chain.score == "low":
        # If user provided vague query without region/occasion, we ask
        if chain.region == "unknown" or len(query_text.strip().split()) <= 3:
            return "ask", "نقص في تحديد المنطقة أو السياق؛ يتطلب طرح سؤال توضيحي لتحديد السند المناسب."
        # Otherwise refuse
        return "refuse", "عدم توفر سلسلة إسناد كافية أو موثقة لدعم الادعاء الثقافي."

    return "refuse", "غياب الإسناد الثقافي المعتمد."
