"""RAG CLI: ingestion, retrieval, reranking, answering, evaluation, doctor.

Run via:

    uv run python -m sard.cli.rag <command> [args]

Commands:
    create-collection           Create (or verify) the versioned Zvec collection.
    ingest <dir>                Ingest all supported files under <dir>.
    resume-ingest <dir>         Alias for `ingest` (ingestion is idempotent/resumable).
    info                        Show collection stats and active configuration.
    list-sources                List ingested documents from the ingestion manifest.
    dense-search "<query>"      Dense-only vector search.
    fts-search "<query>"        Full-text-only search.
    hybrid-search "<query>"     Dense + FTS + RRF fusion.
    rerank-preview "<query>"    Hybrid retrieval, then show reranked order.
    ask "<question>"            Full cited RAG answer.
    evaluate <golden.json>      Run the golden retrieval evaluation + report the 8/10 gate.
    models                      Attempt live NVIDIA model discovery per route.
    doctor                      Check the complete fallback/model/collection configuration.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from sard.config.rag import (
    NVIDIAConfigError,
    get_rag_settings,
    list_available_models,
)
from sard.rag.embeddings import EmbeddingService
from sard.rag.fallbacks import AllCandidatesFailedError, CircuitBreaker, FallbackClassifiedError
from sard.rag.ingest import ingest_directory
from sard.rag.normalize import normalize_arabic
from sard.rag.retrieve import RetrievalDependencies, RetrievalService
from sard.rag.rerank import RerankService
from sard.rag.schemas import RetrievalFilters, RewrittenQuery
from sard.rag.service import RAGService, RAGServiceUnavailableError
from sard.rag.zvec_store import (
    SCHEMA_VERSION,
    ZvecRepository,
)
from sard.rag.chunking import CHUNKING_VERSION
from sard.rag.normalize import NORMALIZATION_VERSION


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def _print_json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _open_or_create_repository(settings, embedding_service, model_id) -> ZvecRepository:
    existing = ZvecRepository.find_existing_for_model(settings.zvec_collection_path, model_id)
    if existing is not None:
        return existing
    print(f"لا توجد مجموعة سابقة لنموذج {model_id}؛ يتم اكتشاف بُعد التضمين عبر استدعاء تجريبي...")
    dimension = embedding_service.discover_dimension(model_id)
    print(f"تم اكتشاف بُعد التضمين: {dimension}")
    return ZvecRepository.open_or_create(settings.zvec_collection_path, model_id, dimension)


def cmd_create_collection(args: argparse.Namespace) -> int:
    settings = get_rag_settings()
    breaker = CircuitBreaker()
    embedding_service = EmbeddingService(settings=settings, circuit_breaker=breaker)
    model_id = settings.embedding_route.primary
    try:
        repo = _open_or_create_repository(settings, embedding_service, model_id)
    except (NVIDIAConfigError, AllCandidatesFailedError) as exc:
        print(f"تعذّر إنشاء المجموعة: {exc}", file=sys.stderr)
        return 1
    stats = repo.stats
    _print_json(
        {
            "path": stats.path,
            "embedding_model": stats.embedding_model,
            "embedding_dimension": stats.embedding_dimension,
            "schema_version": SCHEMA_VERSION,
            "normalization_version": NORMALIZATION_VERSION,
            "chunking_version": CHUNKING_VERSION,
            "doc_count": stats.doc_count,
        }
    )
    repo.close()
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    settings = get_rag_settings()
    breaker = CircuitBreaker()
    embedding_service = EmbeddingService(settings=settings, circuit_breaker=breaker)
    model_id = settings.embedding_route.primary
    try:
        repo = _open_or_create_repository(settings, embedding_service, model_id)
    except (NVIDIAConfigError, AllCandidatesFailedError) as exc:
        print(f"تعذّر فتح/إنشاء المجموعة: {exc}", file=sys.stderr)
        return 1

    try:
        report = ingest_directory(Path(args.corpus_dir), repo, embedding_service, model_id)
        _print_json(report.to_dict())
    finally:
        repo.close()
    return 0 if report.documents_failed == 0 else 2


def cmd_info(args: argparse.Namespace) -> int:
    settings = get_rag_settings()
    model_id = settings.embedding_route.primary
    repo = ZvecRepository.find_existing_for_model(settings.zvec_collection_path, model_id)
    if repo is None:
        print("لا توجد مجموعة مفهرسة بعد لهذا النموذج.", file=sys.stderr)
        return 1
    stats = repo.stats
    _print_json(
        {
            "path": stats.path,
            "embedding_model": stats.embedding_model,
            "embedding_dimension": stats.embedding_dimension,
            "schema_version": SCHEMA_VERSION,
            "normalization_version": NORMALIZATION_VERSION,
            "chunking_version": CHUNKING_VERSION,
            "doc_count": stats.doc_count,
            "dense_candidates": settings.dense_candidates,
            "fts_candidates": settings.fts_candidates,
            "final_top_k": settings.final_top_k,
            "enable_fts": settings.enable_fts,
            "enable_rerank": settings.enable_rerank,
            "enable_query_rewrite": settings.enable_query_rewrite,
        }
    )
    repo.close()
    return 0


def cmd_list_sources(args: argparse.Namespace) -> int:
    settings = get_rag_settings()
    model_id = settings.embedding_route.primary
    repo = ZvecRepository.find_existing_for_model(settings.zvec_collection_path, model_id)
    if repo is None:
        print("لا توجد مجموعة مفهرسة بعد.", file=sys.stderr)
        return 1
    manifest_path = repo.path / "ingestion_manifest.json"
    if not manifest_path.exists():
        print("لا يوجد سجل ingestion_manifest.json بعد.", file=sys.stderr)
        repo.close()
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _print_json(
        [
            {
                "document_id": doc_id,
                "source_path": entry["source_path"],
                "chunk_count": len(entry["chunk_ids"]),
                "ingested_at": entry["ingested_at"],
            }
            for doc_id, entry in manifest.items()
        ]
    )
    repo.close()
    return 0


def _rewritten_from_raw(question: str) -> RewrittenQuery:
    normalized = normalize_arabic(question)
    return RewrittenQuery(
        original_question=question,
        normalized_question=normalized,
        search_variants=[normalized],
        rewrite_succeeded=False,
    )


def _filters_from_args(args) -> Optional[dict]:
    """Build a retrieval-filters dict from CLI flags, if any were given."""
    filters = {}
    for key in ("topic", "source_name", "language", "publication_date"):
        value = getattr(args, key, None)
        if value:
            filters[key] = value
    return filters or None


def _retrieval_filters_from_args(args) -> Optional[RetrievalFilters]:
    values = _filters_from_args(args)
    return RetrievalFilters(**values) if values else None


def _candidate_to_dict(c) -> dict:
    return {
        "chunk_id": c.chunk_id,
        "citation_id": c.citation_id,
        "title": c.title,
        "source_name": c.source_name,
        "source_url": c.source_url,
        "topic": c.topic,
        "page_number": c.page_number,
        "dense_score": c.dense_score,
        "dense_rank": c.dense_rank,
        "fts_score": c.fts_score,
        "fts_rank": c.fts_rank,
        "fused_score": c.fused_score,
        "fused_rank": c.fused_rank,
        "rerank_score": c.rerank_score,
        "rerank_rank": c.rerank_rank,
        "content_preview": c.content[:200],
    }


def _build_retrieval_service(settings) -> tuple[RetrievalService, ZvecRepository]:
    model_id = settings.embedding_route.primary
    repo = ZvecRepository.find_existing_for_model(settings.zvec_collection_path, model_id)
    if repo is None:
        raise RAGServiceUnavailableError("لا توجد مجموعة مفهرسة بعد. شغّل أمر ingest أولًا.")
    breaker = CircuitBreaker()
    embedding_service = EmbeddingService(settings=settings, circuit_breaker=breaker)
    service = RetrievalService(
        RetrievalDependencies(repository=repo, embedding_model_id=model_id, embedding_service=embedding_service),
        settings=settings,
        circuit_breaker=breaker,
    )
    return service, repo


def cmd_dense_search(args: argparse.Namespace) -> int:
    settings = get_rag_settings()
    model_id = settings.embedding_route.primary
    repo = ZvecRepository.find_existing_for_model(settings.zvec_collection_path, model_id)
    if repo is None:
        print("لا توجد مجموعة مفهرسة بعد لهذا النموذج.", file=sys.stderr)
        return 1
    breaker = CircuitBreaker()
    embedding_service = EmbeddingService(settings=settings, circuit_breaker=breaker)
    try:
        outcome = embedding_service.embed_query(
            model_id,
            normalize_arabic(args.query),
            expected_dim=repo.embedding_dimension,
        )
        candidates = repo.dense_search(
            outcome.vectors[0], topk=args.k, filters=_retrieval_filters_from_args(args)
        )
        warnings = []
        mode = "dense_only"
        exit_code = 0
    except (NVIDIAConfigError, AllCandidatesFailedError, FallbackClassifiedError) as exc:
        print(str(exc), file=sys.stderr)
        candidates = []
        warnings = ["Dense retrieval unavailable; no FTS fallback was used by dense-search."]
        mode = "unavailable"
        exit_code = 2
    finally:
        repo.close()
    _print_json({"mode": mode, "warnings": warnings, "candidates": [_candidate_to_dict(c) for c in candidates]})
    return exit_code


def cmd_fts_search(args: argparse.Namespace) -> int:
    settings = get_rag_settings()
    model_id = settings.embedding_route.primary
    repo = ZvecRepository.find_existing_for_model(settings.zvec_collection_path, model_id)
    if repo is None:
        print("لا توجد مجموعة مفهرسة بعد لهذا النموذج.", file=sys.stderr)
        return 1
    try:
        candidates = repo.fts_search(
            normalize_arabic(args.query), topk=args.k, filters=_retrieval_filters_from_args(args)
        )
    finally:
        repo.close()
    _print_json({"mode": "fts_only", "warnings": [], "candidates": [_candidate_to_dict(c) for c in candidates]})
    return 0


def cmd_hybrid_search(args: argparse.Namespace) -> int:
    settings = get_rag_settings()
    try:
        service, repo = _build_retrieval_service(settings)
    except RAGServiceUnavailableError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    rewritten = _rewritten_from_raw(args.query)
    result = service.retrieve(rewritten, _filters_from_args(args))
    _print_json(
        {
            "mode": result.mode.value,
            "warnings": result.warnings,
            "fused_candidates": [_candidate_to_dict(c) for c in result.fused_candidates[: args.k]],
        }
    )
    repo.close()
    return 0


def cmd_rerank_preview(args: argparse.Namespace) -> int:
    settings = get_rag_settings()
    try:
        service, repo = _build_retrieval_service(settings)
    except RAGServiceUnavailableError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    rewritten = _rewritten_from_raw(args.query)
    result = service.retrieve(rewritten, _filters_from_args(args))
    rerank_service = RerankService(settings=settings)
    outcome = rerank_service.rerank(args.query, result.fused_candidates, top_n=args.k)
    _print_json(
        {
            "method_used": outcome.method_used,
            "model_used": outcome.model_used,
            "candidates": [_candidate_to_dict(c) for c in outcome.candidates],
        }
    )
    repo.close()
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    settings = get_rag_settings()
    try:
        service = RAGService.open_readonly(settings)
    except RAGServiceUnavailableError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    try:
        result = service.answer(args.question, _filters_from_args(args))
        _print_json(
            {
                "question": result.question,
                "rewritten_queries": result.rewritten_queries,
                "answer_text": result.answer_text,
                "citations": [c.__dict__ for c in result.citations],
                "model_route": result.model_route,
                "fallback_events": [
                    {
                        "use_case": e.use_case,
                        "requested_model": e.requested_model,
                        "resolved_model": e.resolved_model,
                        "endpoint_type": e.endpoint_type,
                        "attempt": e.attempt,
                        "failure_category": e.failure_category.value if e.failure_category else None,
                        "selected_fallback": e.selected_fallback,
                        "quality_degraded": e.quality_degraded,
                        "latency_ms": e.latency_ms,
                        "outcome": e.outcome,
                    }
                    for e in result.fallback_events
                ],
                "retrieval_mode": result.retrieval_mode,
                "reranker_used": result.reranker_used,
                "timings_ms": result.timings_ms,
                "warnings": result.warnings,
            }
        )
    finally:
        service.close()
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    from sard.rag.evaluate import run_golden_evaluation

    settings = get_rag_settings()
    try:
        service, repo = _build_retrieval_service(settings)
    except RAGServiceUnavailableError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    rerank_service = RerankService(settings=settings)
    report = run_golden_evaluation(Path(args.golden_path), service, rerank_service, k=args.k)
    _print_json(report.to_dict())
    print(
        f"\nGATE: {report.passed_cases}/{report.total_cases} "
        f"(threshold {report.gate_threshold}) -> "
        f"{'PASSED' if report.gate_passed else 'NOT PASSED'}",
        file=sys.stderr,
    )
    print(
        f"MRR@K={report.mean_reciprocal_rank:.3f} "
        f"nDCG@K={report.mean_ndcg:.3f} "
        f"Recall@K dense={report.recall_at_k_dense:.2f} fts={report.recall_at_k_fts:.2f} "
        f"fused={report.recall_at_k_fused:.2f} reranked={report.recall_at_k_reranked:.2f}",
        file=sys.stderr,
    )
    repo.close()
    return 0 if report.gate_passed else 3


def cmd_models(args: argparse.Namespace) -> int:
    settings = get_rag_settings()
    if not settings.nvidia_api_key and not (settings.chat_base_url or settings.embedding_base_url or settings.rerank_base_url):
        print(
            "لا يوجد مفتاح NVIDIA_API_KEY ولا رابط قاعدة ذاتي (self-hosted). "
            "مطلوب أحدهما لاكتشاف النماذج الفعلي؛ أُعيد الإبلاغ عن المعرّفات "
            "المنطقية المهيّأة فقط دون اكتشاف مباشر.",
            file=sys.stderr,
        )
        return 1
    output = {}
    unavailable_count = 0
    for kind, route in (
        ("chat", settings.chat_route),
        ("embedding", settings.embedding_route),
        ("rerank", settings.rerank_route),
    ):
        discovered = list_available_models(kind, settings)
        if not discovered:
            unavailable_count += 1
        entry = {
            "note": (
                "المعرّفات المهيّأة أسماء منطقية (logical names)؛ المعرّف "
                "الفعلي في كتالوج NVIDIA/نشر NIM قد يختلف. تحقق عبر models "
                "أو كتالوج NVIDIA."
            ),
            "configured_primary": route.primary,
            "configured_fallbacks": list(route.fallbacks),
        }
        if not discovered:
            entry["primary_resolvable"] = None
            entry["discovery"] = "unavailable (لا يوجد مفتاح/رابط، أو فشل الاكتشاف)"
        else:
            entry["primary_resolvable"] = route.primary in discovered
            entry["fallbacks_resolvable"] = [fb in discovered for fb in route.fallbacks]
            entry["discovery"] = "live_catalog"
            entry["discovered_models_sample"] = discovered[:15]
        output[kind] = entry
    _print_json(output)
    return 2 if unavailable_count == 3 else 0


def cmd_doctor(args: argparse.Namespace) -> int:
    settings = get_rag_settings()
    report: dict = {}

    report["nvidia_api_key_present"] = bool(settings.nvidia_api_key)
    report["chat_base_url"] = settings.chat_base_url or "(hosted default)"
    report["embedding_base_url"] = settings.embedding_base_url or "(hosted default)"
    report["rerank_base_url"] = settings.rerank_base_url or "(hosted default)"

    try:
        import zvec

        version_value = getattr(zvec, "__version__", None) or getattr(zvec, "version", "unknown")
        report["zvec_version"] = version_value() if callable(version_value) else version_value
        report["zvec_importable"] = True
    except ImportError:
        report["zvec_importable"] = False

    try:
        import langchain_nvidia_ai_endpoints

        report["langchain_nvidia_ai_endpoints_importable"] = True
        report["langchain_nvidia_ai_endpoints_version"] = getattr(
            langchain_nvidia_ai_endpoints, "__version__", "unknown"
        )
    except ImportError:
        report["langchain_nvidia_ai_endpoints_importable"] = False

    model_id = settings.embedding_route.primary
    repo = ZvecRepository.find_existing_for_model(settings.zvec_collection_path, model_id)
    if repo is not None:
        stats = repo.stats
        report["collection"] = {
            "exists": True,
            "path": stats.path,
            "doc_count": stats.doc_count,
            "embedding_model": stats.embedding_model,
            "embedding_dimension": stats.embedding_dimension,
            "schema_version": SCHEMA_VERSION,
            "normalization_version": NORMALIZATION_VERSION,
            "chunking_version": CHUNKING_VERSION,
        }
        repo.close()
    else:
        report["collection"] = {
            "exists": False,
            "expected_embedding_model": model_id,
            "incompatible_collections": ZvecRepository.inspect_collections_for_model(
                settings.zvec_collection_path, model_id
            ),
        }

    report["routes"] = {
        "generation": {"primary": settings.chat_route.primary, "fallbacks": list(settings.chat_route.fallbacks)},
        "query_rewrite": {"primary": settings.query_route.primary, "fallbacks": list(settings.query_route.fallbacks)},
        "embedding": {"primary": settings.embedding_route.primary, "fallback_separate_collection": settings.embedding_fallback_model},
        "rerank": {"primary": settings.rerank_route.primary},
        "vision": {"primary": settings.vision_route.primary, "fallbacks": list(settings.vision_route.fallbacks)},
        "translation": {"primary": settings.translation_route.primary, "fallbacks": list(settings.translation_route.fallbacks)},
        "safety": {"primary": settings.safety_route.primary, "fallbacks": list(settings.safety_route.fallbacks)},
    }
    report["retrieval_config"] = {
        "dense_candidates": settings.dense_candidates,
        "fts_candidates": settings.fts_candidates,
        "fused_candidates": settings.fused_candidates,
        "final_top_k": settings.final_top_k,
        "enable_query_rewrite": settings.enable_query_rewrite,
        "enable_fts": settings.enable_fts,
        "enable_rerank": settings.enable_rerank,
        "request_timeout_seconds": settings.request_timeout_seconds,
        "max_retries": settings.max_retries,
    }
    _print_json(report)
    return 0


def _add_filter_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--topic", help="حصر النتائج على موضوع معيّن (topic) من الـ metadata.")
    parser.add_argument("--source-name", dest="source_name", help="حصر النتائج على مصدر معيّن.")
    parser.add_argument("--language", help="حصر النتائج على لغة معيّنة (مثل ar).")
    parser.add_argument("--publication-date", dest="publication_date", help="حصر النتائج على تاريخ نشر محدد.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sard.cli.rag", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-collection")
    p.set_defaults(func=cmd_create_collection)

    p = sub.add_parser("ingest")
    p.add_argument("corpus_dir")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("resume-ingest")
    p.add_argument("corpus_dir")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("info")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("list-sources")
    p.set_defaults(func=cmd_list_sources)

    p = sub.add_parser("dense-search")
    p.add_argument("query")
    p.add_argument("--k", type=int, default=10)
    _add_filter_flags(p)
    p.set_defaults(func=cmd_dense_search)

    p = sub.add_parser("fts-search")
    p.add_argument("query")
    p.add_argument("--k", type=int, default=10)
    _add_filter_flags(p)
    p.set_defaults(func=cmd_fts_search)

    p = sub.add_parser("hybrid-search")
    p.add_argument("query")
    p.add_argument("--k", type=int, default=10)
    _add_filter_flags(p)
    p.set_defaults(func=cmd_hybrid_search)

    p = sub.add_parser("rerank-preview")
    p.add_argument("query")
    p.add_argument("--k", type=int, default=8)
    _add_filter_flags(p)
    p.set_defaults(func=cmd_rerank_preview)

    p = sub.add_parser("ask")
    p.add_argument("question")
    _add_filter_flags(p)
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("evaluate")
    p.add_argument("golden_path")
    p.add_argument("--k", type=int, default=6)
    p.set_defaults(func=cmd_evaluate)

    p = sub.add_parser("models")
    p.set_defaults(func=cmd_models)

    p = sub.add_parser("doctor")
    p.set_defaults(func=cmd_doctor)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, NotADirectoryError) as exc:
        print(f"تعذّر الوصول إلى الملف أو المجلد المطلوب: {exc.filename}", file=sys.stderr)
        return 1
    except (
        NVIDIAConfigError,
        RAGServiceUnavailableError,
        AllCandidatesFailedError,
        FallbackClassifiedError,
        ValueError,
    ) as exc:
        print(f"تعذّر تنفيذ الأمر: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
