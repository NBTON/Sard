"""Offline (network-free) graph and render fixtures shared by Step 7 state tests.

These builders deliberately mirror the established patterns from
``tests/agent/test_core_graph.py`` (fake chat model + fake RAG service) and
``tests/outputs/test_step6_artifacts.py`` (render-node state/deps) so the Step 7
state tests exercise the real graph and render node without NVIDIA or network.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, time as time_type
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from sard.agent.graph import GraphDependencies, run_pipeline
from sard.agent.models import AgentModelService
from sard.agent.state import ClaimRecord, ClaimStatus, VerificationResult
from sard.config.rag import ModelRoute, RAGSettings
from sard.rag.fallbacks import CircuitBreaker, FallbackEvent
from sard.rag.schemas import Citation, RAGAnswer, RetrievedCandidate, RetrievalMode
from sard.outputs.schemas import (
    CitationSource,
    Coordinates,
    FieldSupport,
    Itinerary,
    ItineraryDay,
    ItineraryStop,
    TextBlock,
)


# ---------------------------------------------------------------------------
# Fake chat model + fake RAG service (Step 5 harness)
# ---------------------------------------------------------------------------


@dataclass
class FakeChatModel:
    scripts: list[str] = field(default_factory=list)
    model_id: str = "fake-chat"

    def invoke(self, messages, **kwargs):
        content = self.scripts.pop(0)
        return _Reply(content)

    @property
    def remaining(self) -> int:
        return len(self.scripts)


@dataclass
class _Reply:
    content: str


def _settings() -> RAGSettings:
    return RAGSettings(
        nvidia_api_key="nvapi-test",
        chat_base_url=None,
        embedding_base_url=None,
        rerank_base_url=None,
        chat_route=ModelRoute("generation", "fake-chat", ()),
        query_route=ModelRoute("query_rewrite", "fake-query", ()),
        embedding_route=ModelRoute("embedding", "fake-embed", ()),
        embedding_fallback_model="nv-embed-v1",
        rerank_route=ModelRoute("rerank", "fake-rerank", ()),
        vision_route=ModelRoute("vision", "fake-vision", ()),
        translation_route=ModelRoute("translation", "fake-translate", ()),
        safety_route=ModelRoute("safety", "fake-safety", ()),
        request_timeout_seconds=2.0,
        max_retries=1,
        zvec_collection_path="data/zvec/test",
        dense_candidates=5,
        fts_candidates=5,
        fused_candidates=5,
        final_top_k=4,
        enable_query_rewrite=False,
        enable_fts=True,
        enable_rerank=False,
    )


def make_offline_deps(
    scripts: list[str],
    rag_service=None,
    max_retries: int = 2,
    *,
    render_artifacts: bool = False,
    output_root: Optional[str] = None,
) -> GraphDependencies:
    """Offline dependencies with a deterministic fake chat model + fake RAG."""
    model = FakeChatModel(scripts=scripts)
    settings = _settings()
    service = AgentModelService(
        settings=settings,
        chat_model_factory=lambda model_id, s: model,
        circuit_breaker=CircuitBreaker(),
        max_retries_per_candidate=1,
    )
    return GraphDependencies(
        rag_service=rag_service,
        model_service=service,
        settings=settings,
        compose_max_retries=max_retries,
        render_artifacts=render_artifacts,
        output_root=output_root,
    )


def _candidate(citation_id: str, content: str, **extra) -> RetrievedCandidate:
    fields = dict(
        chunk_id=f"CHUNK-{citation_id}",
        document_id="DOC-RIY",
        citation_id=citation_id,
        content=content,
        title="مصدر الرياض",
        source_name="دليل الرياض",
        source_url="https://example.org/riyadh",
        topic="طعام",
        language="ar",
        publication_date="2025-01-01",
        page_number=3,
    )
    fields.update(extra)
    return RetrievedCandidate(**fields)


class FakeRAGService:
    def __init__(self, answer: RAGAnswer):
        self._answer = answer
        self.calls: list[tuple] = []

    def answer(self, question, filters=None):
        self.calls.append((question, filters))
        return self._answer


class ExplodingRAG:
    def answer(self, question, filters=None):
        raise RuntimeError("backend exploded")

    def close(self):
        pass


def evidence_answer() -> RAGAnswer:
    riy01 = _candidate("CIT-RIY01", "تُعد الأسواق الشعبية في الرياض وجهة بارزة للزوار.")
    riy02 = _candidate("CIT-RIY02", "يُفضّل زيارة الأسواق في ساعات المساء خلال الصيف.")
    return RAGAnswer(
        question="خطة لرحلة إلى الرياض",
        rewritten_queries=["خطة لرحلة إلى الرياض"],
        dense_candidates=[riy01, riy02],
        fts_candidates=[riy01, riy02],
        fused_candidates=[riy01, riy02],
        selected_context=[riy01, riy02],
        answer_text="",
        citations=[
            Citation(
                citation_id="CIT-RIY01",
                title="مصدر الرياض",
                source_name="دليل الرياض",
                source_url="https://example.org/riyadh",
                chunk_id="CHUNK-CIT-RIY01",
            ),
            Citation(
                citation_id="CIT-RIY02",
                title="مصدر الرياض",
                source_name="دليل الرياض",
                source_url="https://example.org/riyadh",
                chunk_id="CHUNK-CIT-RIY02",
            ),
        ],
        model_route={
            "embedding": "fake-embed",
            "query_rewrite": None,
            "rerank": "fake-rerank",
            "generation": "fake-chat",
        },
        fallback_events=[],
        retrieval_mode=RetrievalMode.HYBRID.value,
        reranker_used="nvidia",
        timings_ms={"total_ms": 12.0},
        warnings=[],
    )


def fallback_answer() -> RAGAnswer:
    """Evidence answer plus a real degraded provider fallback that succeeded."""
    answer = evidence_answer()
    answer.fallback_events = [
        FallbackEvent(
            use_case="generation",
            requested_model="nv-chat",
            resolved_model="nv-chat-lite",
            endpoint_type="hosted",
            attempt=1,
            failure_category=None,
            selected_fallback="fallback_1",
            quality_degraded=True,
            latency_ms=5.0,
            outcome="success",
        )
    ]
    answer.retrieval_mode = RetrievalMode.DENSE_ONLY.value
    answer.reranker_used = ""
    return answer


def success_scripts() -> list[str]:
    return [
        '{"intent":"travel_planning","destination":"الرياض","duration_days":2,'
        '"audience":["بالغون"],"interests":["طعام","أسواق"],"timing":null,'
        '"user_facts":[],"missing_constraints":[],"assumptions":[]}',
        '{"focus_summary":"استكشاف الأسواق","days":[{"day_index":1,"focus":"أسواق الرياض",'
        '"time_blocks":[{"period":"المساء","activity_type":"أسواق"}]}],'
        '"activity_types":["أسواق"],"evidence_topics":["طعام"],'
        '"open_questions":["ما أشهر الأسواق؟"],"constraints":[]}',
        "الأسواق الشعبية في الرياض وجهة بارزة للزوار [CIT-RIY01]. "
        "يُفضّل زيارتها مساءً في الصيف [CIT-RIY02].",
        '{"claims":[{"claim_id":"CLAIM-01-001","status":"supported","correction":"","note":""},'
        '{"claim_id":"CLAIM-01-002","status":"supported","correction":"","note":""}]}',
    ]


def offline_runner(deps: GraphDependencies) -> Callable:
    """Return a ``callable(request) -> final graph state dict`` using ``deps``."""

    def run(request) -> dict:
        from tests.helpers.step7_contracts import UIRunRequest

        return run_pipeline(
            request.query,
            dependencies=deps,
            run_id=request.run_id,
            caller_dates=list(request.trip_dates),
            preview_calendar=False,
        )

    return run


# ---------------------------------------------------------------------------
# Render-node state/deps builders (Step 6 harness)
# ---------------------------------------------------------------------------

CID_ONE = "CIT-STEP6-001"
CID_TWO = "CIT-STEP6-002"


def _sources() -> tuple[CitationSource, ...]:
    return (
        CitationSource(
            CID_ONE,
            "دليل الواحة",
            r"https://example.org/a,b;section?x=1\\2",
            page=4,
            section="المسار؛ الشرقي",
        ),
        CitationSource(CID_TWO, "دليل السوق", "https://example.com/market?lang=ar"),
    )


def _stop(citation_id: str = CID_ONE, *, start: time_type = time_type(9, 0), end: time_type = time_type(10, 0)) -> ItineraryStop:
    description = TextBlock(
        "وصف موثق؛ يتضمن فاصلة، وفاصلة منقوطة؛ ومسار\\خاص\nوسطراً ثانياً "
        f"[{citation_id}]",
        (citation_id,),
    )
    return ItineraryStop(
        time="09:00 - 10:00",
        title="المحطة الأولى",
        location="الموقع، شارع؛ مبنى\\1",
        paragraphs=(description,),
        bullets=(TextBlock("ملاحظة عملية موثقة، احفظ هذه الجملة.", (citation_id,)),),
        notes=(),
        stop_id="stop-one",
        start_time=start,
        end_time=end,
        location_name="الموقع، شارع؛ مبنى\\1",
        address="العنوان المدعوم، مبنى ١",
        coordinates=Coordinates(24.7136, 46.6753),
        description=(description,),
        practical_notes=(),
        accessibility_notes=(),
        citation_ids=(citation_id,),
        field_support=(
            FieldSupport("title", (citation_id,)),
            FieldSupport("location", (citation_id,)),
            FieldSupport("time", (citation_id,)),
        ),
    )


def render_itinerary(*, dated: bool = True, explicit_dates: tuple[date, ...] = ()) -> Itinerary:
    first = _stop()
    second = replace(
        _stop(CID_TWO, start=time_type(11, 0), end=time_type(12, 0)),
        time="11:00 - 12:00",
        stop_id="stop-two",
        title="المحطة الثانية",
        location="السوق القديم",
        location_name="السوق القديم",
    )
    day = ItineraryDay(
        "اليوم الأول",
        date(2026, 11, 1) if dated else None,
        (first, second),
        notes=(TextBlock("ملاحظة اليوم موثقة.", (CID_ONE,)),),
        relative_day_number=1,
        field_support=(FieldSupport("title", (CID_ONE,)), FieldSupport("date", provenance="user_provided")),
    )
    return Itinerary(
        title="رحلة التحقق العربية",
        summary="ملخص موثق للرحلة [CIT-STEP6-001]",
        days=(day,),
        sources=_sources(),
        generated_at=datetime(2026, 8, 13, 9, 30, tzinfo=ZoneInfo("Asia/Riyadh")),
        notes=(),
        run_id="step6-state-run",
        explicit_dates=explicit_dates,
        citation_ids=(),
        field_support=(
            FieldSupport("title", provenance="user_provided"),
            FieldSupport("summary", (CID_ONE,)),
        ),
    )


def render_state(
    itinerary: Optional[Itinerary] = None,
    *,
    answer: str = "هذه إجابة عربية موثقة [CIT-STEP6-001] و[CIT-STEP6-002].",
    run_id: str = "step6-state-run",
    caller_dates: list[str] | None = None,
    fallback_events: tuple = (),
    verification_passed: bool = True,
) -> dict:
    return {
        "run_id": run_id,
        "final_answer": answer,
        "draft": answer,
        "itinerary": itinerary,
        "sources": list(_sources()),
        "atomic_claims": [
            ClaimRecord("claim-1", "حقيقة أولى", (CID_ONE,), status=ClaimStatus.SUPPORTED),
            ClaimRecord("claim-2", "حقيقة ثانية", (CID_TWO,), status=ClaimStatus.SUPPORTED),
        ],
        "verification_result": VerificationResult(verification_passed, ("claim-1", "claim-2"), (), ""),
        "verification_exhausted": not verification_passed,
        "retrieval_mode": "hybrid_reranked",
        "fallback_events": list(fallback_events),
        "model_fallback_used": bool(fallback_events),
        "warnings": [],
        "caller_dates": caller_dates or [],
        "travel_dates": [],
    }


def render_deps(tmp_path: Path, **kwargs):
    values = {
        "render_artifacts": True,
        "output_root": str(tmp_path),
        "render_checksums": True,
        "preview_calendar": False,
    }
    values.update(kwargs)
    return SimpleNamespace(**values)


def created_artifact(result: dict, artifact_type: str):
    return next(item for item in result["rendered_artifacts"] if item.artifact_type == artifact_type)
