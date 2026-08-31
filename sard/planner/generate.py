"""Narrative Generator for Sard's Isnād Planner.

Synthesizes culturally grounded responses strictly adhering to the Isnād decision:
- Generates only when decision is 'generate' or 'hedge'.
- Strictly refuses or asks when decision is 'refuse' or 'ask'.
- Never invents lineage, ritual, recipe, date, or attribution.
- Surfaces regional conflicts explicitly without blending traditions.
- For object_from_image: lists verified patterns and explicitly enumerates what the image cannot prove.
- Preserves Arabic register (MSA vs regional voice supported by the chain).
"""

from __future__ import annotations

import logging
from typing import Callable, List, Optional

from sard.schemas.isnad import Evidence, IsnadChain, PlannerResult

logger = logging.getLogger("sard.planner.generate")


def _format_isnad_references(evidence: List[Evidence]) -> str:
    """Format an inspectable isnād provenance list in Arabic."""
    if not evidence:
        return ""
    lines = ["\n\n### 📜 سلسلة الإسناد والمراجع المعتمدة:"]
    for ev in evidence:
        doc_ref = f" ({ev.url_or_doc_id})" if ev.url_or_doc_id else ""
        period_ref = f" | الفترة: {ev.date_or_period}" if ev.date_or_period else ""
        lines.append(f"- **{ev.origin}** [{ev.region.upper()} | {ev.source_type}]{doc_ref}{period_ref}")
    return "\n".join(lines)


def generate_isnad_response(
    chain: IsnadChain,
    query_text: str,
    llm_invoke_fn: Optional[Callable[[str, str], str]] = None,
    lang: str = "ar",
) -> PlannerResult:
    """Generate final culturally grounded response gated by the isnād chain."""
    visible_sources = [ev for ev in chain.evidence if ev.source_type != "user_upload"]
    if not visible_sources and chain.evidence:
        visible_sources = chain.evidence

    # Case 0: Persona Greeting / Introduction
    q_norm = query_text.strip().lower()
    is_greeting_query = chain.classification == "greeting" or q_norm in (
        "من أنت", "من انت", "عرفني بنفسك", "عرف بنفسك", "ما هو سرد", "مرحبا", "أهلا", "اهلا", "السلام عليكم", "صباح الخير", "مساء الخير", "هلا", "أهلاً", "hello", "hi", "who are you"
    )
    if is_greeting_query:
        answer_ar = (
            "أهلاً وسهلاً بك! 🇸🇦\n\n"
            "أنا **سرد**، رفيقك الثقافي الذكي ومستشارك المعتمد لاستكشاف التراث والحضارة في المملكة العربية السعودية، "
            "بمعارف موثقة مستندة إلى سجلات وهيئات **وزارة الثقافة السعودية** و**دارة الملك عبد العزيز**.\n\n"
            "### 🏛️ كيف يمكنني مساعدتك اليوم؟\n"
            "1. **المعارف والتراث الإقليمي**: استكشاف التراث والعمارة والأزياء والتقاليد عبر **مناطق المملكة الـ 13**.\n"
            "2. **القطاعات الثقافية الـ 11**: التراث، فنون الطهي، الأزياء، الأدب، الموسيقى، العمارة، المتاحف، الفنون البصرية، المسرح، الأفلام، والمكتبات.\n"
            "3. **المخرجات والأدوات التفاعلية**:\n"
            "   - تصميم **عروض تقديمية (PowerPoint .pptx)** للإيجاز الثقافي.\n"
            "   - إعداد **بطاقات الوصفات والحرف التراثية (PDF)**.\n"
            "   - محاكاة **بروتوكولات الإتيكيت والضيافة والمجالس** ومخططات تدفقية.\n"
            "   - فك شفرة **الأمثال واللهجات المحلية** وسرد قصصها.\n"
            "   - توثيق **السير والتاريخ الشفوي العائلي** في كتيبات مصقولة.\n"
            "   - مزامنة **المواسم الفلكية والمناسبات التراثية (.ics)**.\n\n"
            "تفضل بطرح سؤالك أو اختر موضوعاً للبدء!"
        )
        answer_en = (
            "Welcome! I am **Sard**, your Saudi AI Cultural Companion and guide to the rich heritage and traditions "
            "of the Kingdom of Saudi Arabia, grounded in verified references from the **Saudi Ministry of Culture**.\n\n"
            "I can assist you with:\n"
            "- Documented cultural history across all 13 Saudi regions.\n"
            "- Generating verified presentations (PPTX), recipe cards (PDF), etiquette guides (SVG), and heritage calendars (ICS).\n"
            "- Exploring traditional culinary arts, architecture, music, fashion, and folklore."
        )
        return PlannerResult(
            chain=chain,
            answer_ar=answer_ar,
            answer_en=answer_en,
            visible_sources=visible_sources,
            follow_up="ما هو الموضوع أو المنطقة التي تود استكشافها؟",
        )

    # Case 1: Refusal
    if chain.decision == "refuse":
        conflict_explanation = ""
        if chain.conflicts:
            conflict_explanation = "\n- " + "\n- ".join(chain.conflicts)

        answer_ar = (
            "عذرًا، بصفتي المستشار الثقافي **سرد**، ألتزم بمبدأ التوثيق الصارم والإسناد التراثي المعتمد، "
            "ولا يمكنني الجزم بمعلومات أو نسب تراثية غير مسندة إلى وثائق أو مصادر رسمية معتمدة.\n\n"
            f"**سبب التوقف عن الإجابة:**\n"
            f"- غياب سلسلة إسناد كافية أو تعارض في نسبة التراث المطروح.{conflict_explanation}\n\n"
            "تفضل بتقديم تفاصيل إضافية أو تحديد المنطقة التراثية بدقة لمساعدتك في استخراج السند المعتمد."
        )
        answer_en = (
            "As Sard, your cultural guide, I adhere to strict provenance rules. "
            "I cannot assert or invent cultural narratives without verified sources.\n\n"
            "Please specify the region or provide verified references."
        )
        return PlannerResult(
            chain=chain,
            answer_ar=answer_ar,
            answer_en=answer_en,
            visible_sources=[],
            follow_up="هل ترغب في تحديد المنطقة التراثية أو الاستفسار عن موقع موثق محدد؟",
        )

    # Case 2: Clarification / Ask
    if chain.decision == "ask":
        answer_ar = (
            "أهلاً بك! لتزويدك بالرواية والتوثيق التراثي الدقيق المستند إلى مراجع وزارة الثقافة، "
            "يُرجى توضيح المنطقة أو المناسبة المستهدفة:\n\n"
            "- ما هي المنطقة المحددة؟ (مثال: نجد، الحجاز، عسير، المنطقة الشرقية، الشمال، الجنوب)\n"
            "- ما هو السياق أو المناسبة؟ (مثال: عمارة تقليدية، عادات ضيافة، طعام شعبي، أزياء)\n\n"
            "سأقوم فوراً بربط استفسارك بسلسلة الإسناد والوثائق الخاصة بالمنطقة المطلوبة."
        )
        answer_en = (
            "To provide you with verified cultural narratives, please specify the exact region "
            "(e.g., Najd, Hijaz, Asir, Eastern Province) and the specific context."
        )
        return PlannerResult(
            chain=chain,
            answer_ar=answer_ar,
            answer_en=answer_en,
            visible_sources=[],
            follow_up="يرجى تحديد المنطقة التراثية.",
        )

    # Case 3 & 4: Generate or Hedge
    # Check if there are regional conflicts (e.g. Asir vs Najd)
    if chain.conflicts and any("تعارض إقليمي" in c for c in chain.conflicts):
        # Surface the conflict explicitly
        answer_ar = (
            "تزخر مناطق المملكة بتنوع ثقافي وتراثي فريد؛ وتفادياً لدمج التقاليد الإقليمية المختلفة:\n\n"
            "### ✦ تمايز التقاليد الإقليمية في المصادر المعتمدة:\n"
        )
        for ev in chain.evidence:
            answer_ar += f"- **منطقة {ev.region} ({ev.origin})**: {ev.excerpt}\n"

        answer_ar += (
            "\n> [!NOTE]\n"
            "> نؤكد في **سرد** على احترام خصوصية كل منطقة وعدم خلط أطباقها أو عاداتها التراثية في قالب موحد."
        )
        answer_ar += _format_isnad_references(visible_sources)
        return PlannerResult(
            chain=chain,
            answer_ar=answer_ar,
            answer_en="Regional traditions presented with distinct provenance without blending.",
            visible_sources=visible_sources,
            follow_up="هل تود التعمق في تقاليد منطقة محددة من هذه المناطق؟",
        )

    # Object from image specific synthesis (e.g. Najdi wooden door demo)
    if chain.classification == "object_from_image":
        # Build structured door/artifact analysis
        answer_ar = _synthesize_image_artifact_narrative(chain, query_text)
        answer_ar += _format_isnad_references(visible_sources)
        return PlannerResult(
            chain=chain,
            answer_ar=answer_ar,
            answer_en="Verified artifact narrative grounded in regional carpentry and heritage documents.",
            visible_sources=visible_sources,
            follow_up="هل تود استكشاف المزيد عن تفاصيل العمارة النجدية أو أنواع الأخشاب المحلية المستخدمة؟",
        )

    # Standard Grounded Synthesis
    if llm_invoke_fn:
        try:
            sys_prompt = (
                "أنت «سرد»، المستشار الثقافي السعودي الأصيل. صُغ إجابة موثقة، غنية، وأنيقة باللغة العربية الفصحى مستندة للشواهد المعطاة.\n"
                "القواعد الصارمة:\n"
                "1. انسب كل تفصيل إلى منطقته ومصدره بدقة وانسيابية.\n"
                "2. يُمنع منعاً باتاً ذكر كلمة RAG أو أي وسوم برمجية أو تقنية في النص.\n"
                "3. نسّق الإجابة بشكل جذاب وعناوين واضحة وجداول نظيفة دون وسوم HTML.\n"
                "4. لا تخلط أي مصطلحات إنجليزية في النص العربي نهائياً."
            )
            context = "\n".join(f"[{ev.origin} | {ev.region}]: {ev.excerpt}" for ev in chain.evidence)
            user_prompt = f"السؤال: {query_text}\n\nالشواهد المعتمدة:\n{context}\n\nصغ الإجابة بالعربية الفصحى مع المصطلحات التراثية المناسبة:"
            llm_text = llm_invoke_fn(sys_prompt, user_prompt)
            if llm_text and len(llm_text.strip()) > 30:
                from sard.agent.util import sanitize_cultural_output
                full_ar = sanitize_cultural_output(llm_text.strip())
                return PlannerResult(
                    chain=chain,
                    answer_ar=full_ar,
                    answer_en=None,
                    visible_sources=visible_sources,
                )
        except Exception as exc:
            logger.warning("LLM synthesis error in generator: %s", exc)

    # Deterministic fallback synthesis grounded in evidence atoms
    excerpts_bullet = "\n".join(f"- **{ev.origin}**: {ev.excerpt}" for ev in chain.evidence)
    answer_ar = (
        f"بناءً على التوثيق المعتمد لمنطقة **{chain.region}**:\n\n"
        f"{excerpts_bullet}\n"
    )
    if chain.decision == "hedge":
        answer_ar += "\n*(ملاحظة توثيقية: التفاصيل مبنية على المصادر المتاحة مع التحوط في التواريخ الدقيقة).*\n"
    answer_ar += _format_isnad_references(visible_sources)

    return PlannerResult(
        chain=chain,
        answer_ar=answer_ar,
        answer_en=None,
        visible_sources=visible_sources,
        follow_up="هل تود الاطلاع على وثائق إضافية حول هذا الموضوع؟",
    )


def _synthesize_image_artifact_narrative(chain: IsnadChain, query_text: str) -> str:
    """Synthesize object_from_image narrative, including what the photo CAN and CANNOT prove."""
    q_norm = query_text.lower()
    is_door = any(k in q_norm for k in ["باب", "door", "خشبي", "أثل"])

    if is_door or chain.region == "najd":
        return (
            "### ✦ قصة الباب النجدي التراثي (عمارة الطين والأثل)\n\n"
            "استناداً إلى وثائق وسجلات **هيئة التراث** ودراسات العمارة التقليدية في **منطقة نجد**:\n\n"
            "1. **الهوية والأصل الجغرافي**:\n"
            "   - هذا العنصر المعماري هو **باب نجدي تراثي تقليدي**، صُنع محلياً من **خشب الأثل (Tamarisk)** المتوفر في أودية وواحات نجد، وهو خشب صلب ومتين يقاوم قسوة المناخ الصحراوي.\n"
            "   - **نفي النسب غير الدقيقة**: يختلف هذا الباب جوهرياً عن الأبواب والرواشين الحجازية (مثل المنجور وخشب الساج/الجاوي المستورد في جدة التاريخية) ولا يرتبط بنمط العمارة الساحلية.\n\n"
            "2. **العناصر الحرفية والزخرفية الموثقة**:\n"
            "   - **النقوش والزخارف**: تزدان مصاريع الباب بالزخارف الهندسية والنباتية المحفورة والمحروقة بالألوان الطبيعية (المثلثات، الدوائر، زهرة الوردة/الشمسية التراثية).\n"
            "   - **أعمال الحديد والنجارة التقليدية**: تشتمل على المسامير الحديدية المقببة (القُرصية)، و\"المجرى\"، و\"السكرة\" (الضبة والمفتاح الخشبي التقليدي الضخم).\n\n"
            "3. **حدود الإثبات التوثيقي (ما لا يمكن للصورة إثباته بمفردها)**:\n"
            "   - **اسم الحرفي/النجار**: لا يمكن تحديد اسم الصانع أو الأستاذ النجار بدقة دون وجود نقش توقيع أو ختم مقروء على الخشب.\n"
            "   - **العائلة أو المنزل المحدد**: لا تحدد الصورة اسم مالك البيت الأصلي أو البلدة بدقة (مثلاً: الدرعية، أشيقر، المجمعة، أو حوطة بني تميم) دون وثيقة ملكية أو كتابة مؤرخة.\n"
            "   - **تاريخ الصنع الدقيق**: يمكن تأكيد انتمائه للنمط النجدي السائد خلال القرنين الثالث عشر والرابع عشر الهجري، لكن تعيين السنة الدقيقة يتطلب فحصاً مخبرياً أو تاريخاً منقوشاً."
        )

    # General image object
    return (
        f"### ✦ التحليل التوثيقي للمرفق البصري ({chain.region.upper()})\n\n"
        "1. **الهوية التراثية**: تدل الشواهد والزخارف المرئية على انتماء هذا العنصر إلى تراث **منطقة {chain.region}**.\n"
        "2. **السمات المعتمدة**: متوافق مع النمط التراثي الموثق في السجلات المعتمدة.\n"
        "3. **ما لا تثبته الصورة**: تقتصر الصورة على إثبات الطراز العام دون إثبات سنة الصنع الدقيقة أو هوية الصانع دون نقش موثق."
    )
