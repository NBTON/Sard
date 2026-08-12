"""Step 5 Agent CLI.

Runs the agent pipeline and prints a sanitized trace. Supports offline `--demo` mode.
"""

import argparse
import sys
import json
from dataclasses import dataclass, field

from sard.agent.graph import GraphDependencies, default_dependencies, run_pipeline
from sard.agent.models import AgentModelService
from sard.rag.schemas import RAGAnswer, RetrievedCandidate, Citation, RetrievalMode


@dataclass
class _FakeChatModel:
    scripts: list[str] = field(default_factory=list)
    model_id: str = "fake-chat"

    def invoke(self, messages, **kwargs):
        content = self.scripts.pop(0) if self.scripts else ""
        return _Reply(content)

@dataclass
class _Reply:
    content: str


class _FakeRAGService:
    def answer(self, question, filters=None):
        riy01 = RetrievedCandidate(
            chunk_id="CHUNK-CIT-DEMO01",
            document_id="DOC-DEMO",
            citation_id="CIT-DEMO01",
            content="المنطقة الشرقية غنية بالتراث الأصيل وتمتلك مواقع أثرية فريدة.",
            title="تراث الشرقية",
            source_name="دليل التراث",
            source_url="https://example.org",
            topic="تراث",
            language="ar",
            publication_date="2025-01-01",
            page_number=1,
            dense_score=0.9,
            dense_rank=1,
        )
        return RAGAnswer(
            question=question,
            rewritten_queries=[question],
            dense_candidates=[riy01],
            fts_candidates=[],
            fused_candidates=[riy01],
            selected_context=[riy01],
            answer_text="",
            citations=[
                Citation(citation_id="CIT-DEMO01", title="تراث الشرقية", source_name="دليل التراث",
                         source_url="https://example.org", chunk_id="CHUNK-CIT-DEMO01")
            ],
            model_route={"embedding": "fake-embed", "query_rewrite": None, "rerank": None, "generation": "fake-chat"},
            fallback_events=[],
            retrieval_mode=RetrievalMode.DENSE_ONLY.value,
            reranker_used=None,
            timings_ms={"total_ms": 10.0},
            warnings=[],
        )


def _demo_dependencies() -> GraphDependencies:
    scripts = [
        # Understand
        '{"intent":"travel_planning","destination":"المنطقة الشرقية","duration_days":2,"audience":["بالغون"],"interests":["تراث"],"timing":null,"user_facts":[],"missing_constraints":[],"assumptions":[]}',
        # Plan
        '{"focus_summary":"استكشاف التراث","days":[{"day_index":1,"focus":"تراث الشرقية","time_blocks":[{"period":"الصباح","activity_type":"تراث"}]}],"activity_types":["تراث"],"evidence_topics":["تراث"],"open_questions":[],"constraints":[]}',
        # Compose
        "المنطقة الشرقية غنية بالتراث الأصيل [CIT-DEMO01].",
        # Verify
        '{"claims":[{"claim_id":"CLAIM-01-001","status":"supported","correction":"","note":""}]}',
    ]
    from sard.config.rag import RAGSettings, ModelRoute
    settings = RAGSettings(
        nvidia_api_key="demo",
        chat_base_url=None,
        embedding_base_url=None,
        rerank_base_url=None,
        chat_route=ModelRoute("generation", "fake-chat", ()),
        query_route=ModelRoute("query", "fake-query", ()),
        embedding_route=ModelRoute("embed", "fake-embed", ()),
        rerank_route=ModelRoute("rerank", "fake-rerank", ()),
        vision_route=ModelRoute("vision", "fake-vision", ()),
        translation_route=ModelRoute("translate", "fake-translate", ()),
        safety_route=ModelRoute("safety", "fake-safety", ()),
        request_timeout_seconds=2.0,
        max_retries=1,
        zvec_collection_path="demo",
        embedding_fallback_model="nv-embed",
        dense_candidates=5,
        fts_candidates=5,
        fused_candidates=5,
        final_top_k=4,
        enable_query_rewrite=False,
        enable_fts=True,
        enable_rerank=False,
    )
    model = _FakeChatModel(scripts=scripts)
    from sard.rag.fallbacks import CircuitBreaker
    service = AgentModelService(
        settings=settings,
        chat_model_factory=lambda model_id, s: model,
        circuit_breaker=CircuitBreaker(),
        max_retries_per_candidate=1,
    )
    return GraphDependencies(
        rag_service=_FakeRAGService(),
        model_service=service,
        settings=settings,
        compose_max_retries=2,
    )


def main():
    parser = argparse.ArgumentParser(description="Sard Step 5 Agent CLI")
    parser.add_argument("--demo", action="store_true", help="Run in offline deterministic demo mode")
    parser.add_argument("query", nargs="?", default="أنشئ برنامجًا سياحيًا تراثيًا لمدة يومين في المنطقة الشرقية")
    args = parser.parse_args()

    deps = _demo_dependencies() if args.demo else default_dependencies(open_rag=True)

    result = run_pipeline(args.query, dependencies=deps, run_id="cli-run")

    print(f"Query: {args.query}")
    print("Mode: " + ("DEMO (Offline Fake)" if args.demo else "NORMAL (Real Dependencies)"))
    
    events = result.get("progress_events", [])
    node_events = {}
    for ev in events:
        kind = getattr(ev, "kind", "") if not isinstance(ev, dict) else ev.get("kind", "")
        node = getattr(ev, "node", "") if not isinstance(ev, dict) else ev.get("node", "")
        status = getattr(ev, "status", "") if not isinstance(ev, dict) else ev.get("status", "")
        duration = getattr(ev, "duration_ms", None) if not isinstance(ev, dict) else ev.get("duration_ms", None)
        
        if node and node != "pipeline":
            if node not in node_events:
                node_events[node] = {"status": "started", "duration": 0}
            if kind == "completed" or kind == "degraded" or kind == "failed":
                node_events[node]["status"] = status
                if duration:
                    node_events[node]["duration"] = duration

    node_seq = []
    for node, info in node_events.items():
        node_seq.append(f"{node} ({info['status']})")
    
    print(f"Node Sequence: {' -> '.join(node_seq)}")
    print(f"Retrieval Mode: {result.get('retrieval_mode', 'unavailable')}")
    print(f"Source Count: {len(result.get('sources', []))}")
    print(f"Model Routes: {json.dumps(result.get('model_routes', {}))}")
    print(f"Fallback Activations: {len(result.get('fallback_events', []))}")
    
    vr = result.get("verification_result")
    vr_status = getattr(vr, "passed", False) if vr else False
    print(f"Verification Passed: {vr_status}")
    
    cov = result.get("coverage")
    cov_ratio = getattr(cov, "coverage_ratio", 0.0) if cov else 0.0
    print(f"Citation Coverage: {cov_ratio:.0%}")
    print(f"Retry Count: {result.get('compose_retry_count', 0)}")
    
    total_ms = 0.0
    timings = result.get("timings", {})
    for k, v in timings.items():
        if k.endswith("_ms") and isinstance(v, (int, float)):
             total_ms += float(v)
    print(f"Total Latency: {total_ms:.1f}ms")

if __name__ == "__main__":
    main()
