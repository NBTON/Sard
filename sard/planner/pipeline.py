"""Isnād Planner Orchestration Pipeline for Sard.

Implements the end-to-end provenance planning loop:
1. classify -> 2. locate -> 3. retrieve -> 4. assemble_isnad -> 5. score_chain -> 6. decide -> 7. generate

Updates the IsnadCanvas task graph at every stage and provides event streaming
for UI status progression (waving 13-region strips).
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

from sard.memory import IsnadMemory
from sard.memory.canvas import IsnadCanvas
from sard.planner.assemble_isnad import IsnadAssembler
from sard.planner.classify import classify_request
from sard.planner.decide import decide_action
from sard.planner.generate import generate_isnad_response
from sard.planner.locate import locate_cultural_context
from sard.planner.retrieve import GroundedRetriever
from sard.planner.score_chain import score_isnad_chain
from sard.schemas.isnad import IsnadChain, PlannerResult

logger = logging.getLogger("sard.planner.pipeline")


class IsnadPlanner:
    """End-to-end Isnād Provenance Planner for Sard."""

    def __init__(
        self,
        memory: Optional[IsnadMemory] = None,
        retriever: Optional[GroundedRetriever] = None,
    ):
        self.memory = memory or IsnadMemory()
        self.retriever = retriever or GroundedRetriever(l0_store=self.memory.l0)
        self.assembler = IsnadAssembler(l1_store=self.memory.l1)

    def plan_and_execute(
        self,
        query: str,
        session_id: Optional[str] = None,
        mock_multimodal_files: Optional[Dict[str, Any]] = None,
        llm_invoke_fn: Optional[Callable[[str, str], str]] = None,
        status_callback: Optional[Callable[[str, str], None]] = None,
        lang: str = "ar",
    ) -> PlannerResult:
        """Synchronous execution of the Isnād planning loop."""
        # Scope guardrail first (do not let retrieval override confident out-of-scope)
        try:
            from sard.agent.scope_guard import check_scope_before_retrieval
            should_block, scope_text = check_scope_before_retrieval(query, lang=lang)
            if should_block:
                from sard.schemas.isnad import IsnadChain
                chain = IsnadChain(request_id=f"req-{uuid.uuid4().hex[:8]}", classification="other", region="unknown", evidence=[], atoms=[], conflicts=[], score="low", decision="refuse", missing=["out_of_scope"])
                from sard.schemas.isnad import Evidence
                # Return a PlannerResult that surfaces the scope message directly
                # Use language-appropriate field
                if lang == "en":
                    return __import__("sard.schemas.isnad", fromlist=["PlannerResult"]).PlannerResult(chain=chain, answer_ar=scope_text, answer_en=scope_text, visible_sources=[], follow_up="Please add a Saudi heritage angle if you wish.")
                else:
                    return __import__("sard.schemas.isnad", fromlist=["PlannerResult"]).PlannerResult(chain=chain, answer_ar=scope_text, answer_en=scope_text, visible_sources=[], follow_up="هل تود إضافة جانب سعودي للمقارنة؟")
        except Exception:
            pass

        req_id = f"req-{uuid.uuid4().hex[:8]}"
        canvas = self.memory.create_canvas(req_id)

        def _notify(stage: str, msg_ar: str):
            if status_callback:
                # Localize status if lang is English
                if lang == "en":
                    # Simple mapping for key statuses
                    en_map = {
                        "جارٍ تصنيف الاستفسار وفحص المرفقات والوسائط...": "Classifying query and checking attachments...",
                        "جارٍ تحديد المنطقة والسياق التراثي وهوية السائل...": "Locating cultural region and context...",
                        "جارٍ استرجاع الشواهد من موسوعة المعارف والوثائق المعتمدة...": "Retrieving evidence from verified heritage records...",
                        "جارٍ تجميع سلسلة الإسناد وتدقيق نسبة الشواهد...": "Assembling provenance chain...",
                        "جارٍ احتساب درجة الإسناد وفحص موثوقية الأصول...": "Scoring provenance confidence...",
                        "جارٍ اتخاذ القرار التوثيقي (توليد / تحوط / رفض)...": "Making documentation decision...",
                        "جارٍ صياغة الرواية المعتمدة مع إظهار الإسناد والمصادر...": "Composing verified narrative with sources...",
                    }
                    msg_ar = en_map.get(msg_ar, msg_ar)
                status_callback(stage, msg_ar)

        # Stage 1: Classify
        canvas.set_stage_status("classify", "running")
        _notify("classify", "جارٍ تصنيف الاستفسار وفحص المرفقات والوسائط...")
        classification, cls_conf = classify_request(query, has_media=bool(mock_multimodal_files))
        canvas.set_stage_status(
            "classify",
            "completed",
            details={"classification": classification, "confidence": cls_conf},
        )

        # Stage 2: Locate
        canvas.set_stage_status("locate", "running")
        _notify("locate", "جارٍ تحديد المنطقة والسياق التراثي وهوية السائل...")
        location = locate_cultural_context(query)
        if session_id:
            self.memory.l3.update_interaction(
                session_id=session_id,
                stance=location.user_stance,
                region=location.region,
                topic=query[:40],
            )
        canvas.set_stage_status(
            "locate",
            "completed",
            details={"region": location.region, "stance": location.user_stance, "occasion": location.occasion},
        )

        # Stage 3: Retrieve
        canvas.set_stage_status("retrieve", "running")
        _notify("retrieving", "جارٍ استرجاع الشواهد من موسوعة المعارف والوثائق المعتمدة...")
        evidence, logs = self.retriever.retrieve(
            query=query,
            target_region=location.region,
            mock_multimodal_files=mock_multimodal_files,
        )
        # Dialect/proverb weak-evidence filter: require lexical overlap, otherwise treat as no evidence to force clarification
        if classification == "dialect":
            import re as _re2
            proverb_terms = [t for t in _re2.findall(r"[\u0600-\u06FF]+", query) if len(t) > 2]
            if proverb_terms:
                has_match = False
                for ev in evidence:
                    low = (ev.excerpt or "").lower()
                    for term in proverb_terms:
                        if term.lower() in low:
                            has_match = True
                            break
                    if has_match:
                        break
                if not has_match:
                    # Keep logs but clear evidence to trigger low confidence ask flow without irrelevant citations
                    logs.append("تضارب معجمي للمثل: لا تطابق لفظي في الشواهد المسترجعة — تم تصفية الأدلة وطلب توضيح.")
                    evidence = []
        ev_ids = [e.source_id for e in evidence]
        canvas.set_stage_status(
            "retrieve",
            "completed",
            evidence_ids=ev_ids,
            details={"evidence_count": len(evidence), "logs": logs},
        )

        # Stage 4: Assemble Isnād Chain
        canvas.set_stage_status("assemble_isnad", "running")
        _notify("assembling_isnad", "جارٍ تجميع سلسلة الإسناد وتدقيق نسبة الشواهد...")
        chain = self.assembler.assemble(
            request_id=req_id,
            classification=classification,
            primary_region=location.region,
            evidence=evidence,
        )
        atom_ids = [a.claim_id for a in chain.atoms]
        canvas.set_stage_status(
            "assemble_isnad",
            "completed",
            atom_ids=atom_ids,
            details={"atoms_count": len(chain.atoms), "conflicts_count": len(chain.conflicts)},
        )

        # Stage 5: Score Chain
        canvas.set_stage_status("score_chain", "running")
        _notify("scoring", "جارٍ احتساب درجة الإسناد وفحص موثوقية الأصول...")
        score = score_isnad_chain(chain)
        chain.score = score
        canvas.set_stage_status(
            "score_chain",
            "completed",
            details={"score": score},
        )

        # Stage 6: Decide
        canvas.set_stage_status("decide", "running")
        _notify("deciding", "جارٍ اتخاذ القرار التوثيقي (توليد / تحوط / رفض)...")
        decision, reason = decide_action(chain, query_text=query)
        chain.decision = decision
        canvas.set_stage_status(
            "decide",
            "completed",
            details={"decision": decision, "reason": reason},
        )

        # Stage 7: Generate
        canvas.set_stage_status("generate", "running")
        _notify("generating", "جارٍ صياغة الرواية المعتمدة مع إظهار الإسناد والمصادر...")
        result = generate_isnad_response(
            chain=chain,
            query_text=query,
            llm_invoke_fn=llm_invoke_fn,
            lang=lang,
        )
        canvas.set_stage_status(
            "generate",
            "completed",
            details={"decision_executed": decision, "visible_sources_count": len(result.visible_sources)},
        )

        return result

    async def plan_and_execute_stream(
        self,
        query: str,
        session_id: Optional[str] = None,
        mock_multimodal_files: Optional[Dict[str, Any]] = None,
        llm_invoke_fn: Optional[Callable[[str, str], str]] = None,
        lang: str = "ar",
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Asynchronous streaming generator yielding status events and final PlannerResult."""
        events: List[Dict[str, Any]] = []

        def _collector(stage: str, message: str):
            events.append({"stage": stage, "message": message})

        # Run synchronously in caller or wrapper
        result = self.plan_and_execute(
            query=query,
            session_id=session_id,
            mock_multimodal_files=mock_multimodal_files,
            llm_invoke_fn=llm_invoke_fn,
            status_callback=_collector,
            lang=lang,
        )

        for ev in events:
            yield {"event": "status", "data": ev}

        yield {"event": "result", "data": result}
