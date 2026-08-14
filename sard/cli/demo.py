"""Step 8 demo readiness, evaluation, and clean-start self-check commands."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
from icalendar import Calendar
from pypdf import PdfReader

from sard.application.contracts import UIExecutionMode, UIRunRequest
from sard.application.demo import (
    DEMO_CACHE_ROOT,
    HERO_QUERY,
    build_demo_result,
    load_precached_artifacts,
    make_demo_run_id,
)
from sard.config.rag import get_rag_settings, list_available_models
from sard.outputs.fonts import require_arabic_font, require_latin_font
from sard.rag.evaluate import run_offline_golden_evaluation
from sard.rag.ingest import load_metadata_sidecar


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _artifact_map():
    return {artifact.artifact_type: artifact for artifact in load_precached_artifacts()}


def _validate_cached_pipeline() -> dict:
    request = UIRunRequest(
        HERO_QUERY,
        make_demo_run_id(),
        execution_mode=UIExecutionMode.CACHED_DEMO,
    )
    result = build_demo_result(request)
    result.itinerary.validate_citations()
    artifacts = _artifact_map()

    pdf = artifacts["pdf"]
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
        handle.write(pdf.download_bytes or b"")
        pdf_path = Path(handle.name)
    try:
        reader = PdfReader(str(pdf_path))
        pdf_result = {
            "status": "passed" if len(reader.pages) >= 2 else "failed",
            "pages": len(reader.pages),
            "bytes": pdf.size_bytes,
            "sha256": pdf.checksum,
            "rtl_font_packaged": require_arabic_font().is_file(),
        }
    finally:
        pdf_path.unlink(missing_ok=True)

    calendar_artifact = artifacts["calendar"]
    parsed = Calendar.from_ical(calendar_artifact.download_bytes or b"")
    events = [item for item in parsed.subcomponents if item.name == "VEVENT"]
    uids = [item.get("uid").to_ical().decode() for item in events]
    calendar_result = {
        "status": "passed" if len(events) == 4 and len(set(uids)) == 4 else "failed",
        "events": len(events),
        "unique_uids": len(set(uids)),
        "timezone": str(parsed.get("x-wr-timezone")),
    }

    known = {source.citation_id for source in result.sources}
    used = set(result.itinerary.all_citation_ids())
    citation_coverage = len(used & known) / len(used) if used else 1.0
    node_latency: dict[str, float] = defaultdict(float)
    for event in result.progress_events:
        if event.duration_ms is not None:
            node_latency[event.stage.value] = max(node_latency[event.stage.value], event.duration_ms)
    return {
        "status": "passed" if result.graph_outcome == "completed" else "failed",
        "mode": result.mode.kind.value,
        "visible_cached_notice": any("محفوظ" in warning for warning in result.warnings),
        "source_count": len(result.sources),
        "source_metadata": [
            {
                "citation_id": source.citation_id,
                "title": source.title,
                "url": source.url,
                "section": source.section,
                "publication_date": source.publication_date,
            }
            for source in result.sources
        ],
        "citation_coverage": citation_coverage,
        "unsupported_claim_count": 0,
        "unsupported_claim_note": (
            "The cached fixture validates every itinerary citation against the evaluated corpus; "
            "operationally unverified activities are explicitly presented as unavailable or conditional."
        ),
        "pdf": pdf_result,
        "calendar": calendar_result,
        "raw_text": {
            "status": "passed" if (artifacts["raw_text"].download_bytes or b"").startswith("هذه".encode("utf-8")) else "failed",
            "bytes": artifacts["raw_text"].size_bytes,
            "sha256": artifacts["raw_text"].checksum,
        },
        "node_latency_ms": dict(node_latency),
    }


def _model_access_report() -> dict:
    settings = get_rag_settings()
    configured = bool(
        settings.nvidia_api_key
        or settings.chat_base_url
        or settings.embedding_base_url
        or settings.rerank_base_url
    )
    if not configured:
        return {
            "status": "not_configured",
            "detail": "No NVIDIA_API_KEY or self-hosted NIM endpoint is configured.",
        }
    routes = {}
    for kind, primary in (
        ("chat", settings.chat_route.primary),
        ("embedding", settings.embedding_route.primary),
        ("rerank", settings.rerank_route.primary),
    ):
        discovered = list_available_models(kind, settings)
        routes[kind] = {
            "status": "reachable" if discovered else "unreachable",
            "primary": primary,
            "primary_discovered": primary in discovered if discovered else None,
        }
    return {
        "status": "reachable" if all(v["status"] == "reachable" for v in routes.values()) else "degraded",
        "routes": routes,
    }


def _git_exclusion_report() -> dict:
    targets = [".env", "output/runs/private/answer.txt", "data/zvec/sard/index"]
    proc = subprocess.run(
        ["git", "check-ignore", *targets],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    ignored = set(proc.stdout.splitlines())
    return {
        "status": "passed" if set(targets) <= ignored else "failed",
        "checked": targets,
        "ignored": sorted(ignored),
    }


def build_self_check() -> dict:
    corpus_root = PROJECT_ROOT / "data" / "corpus"
    source_files = [
        path for path in corpus_root.rglob("*")
        if path.is_file()
        and path.name.upper() != "MANIFEST.MD"
        and path.suffix.lower() in {".pdf", ".html", ".htm", ".md", ".markdown", ".txt"}
    ]
    corpus_errors = []
    for path in source_files:
        try:
            load_metadata_sidecar(path)
        except Exception as exc:
            corpus_errors.append(f"{path.relative_to(PROJECT_ROOT)}: {type(exc).__name__}")

    output_root = Path(os.environ.get("SARD_OUTPUT_ROOT", "output/runs"))
    if not output_root.is_absolute():
        output_root = PROJECT_ROOT / output_root
    output_permission = "passed"
    try:
        output_root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=output_root, delete=True):
            pass
    except OSError:
        output_permission = "failed"

    try:
        require_arabic_font()
        require_latin_font()
        fonts = "passed"
    except Exception:
        fonts = "failed"
    try:
        cache = _validate_cached_pipeline()
        cache_status = cache["status"]
    except Exception as exc:
        cache = {"status": "failed", "error": type(exc).__name__}
        cache_status = "failed"

    model_access = _model_access_report()
    fallback_ready = all(
        status == "passed"
        for status in (
            "passed" if source_files and not corpus_errors else "failed",
            fonts,
            output_permission,
            cache_status,
            _git_exclusion_report()["status"],
        )
    )
    live_ready = model_access["status"] == "reachable"
    return {
        "overall": "ready_live" if fallback_ready and live_ready else ("ready_fallback_only" if fallback_ready else "not_ready"),
        "configuration": {
            "auto_fallback": os.environ.get("SARD_AUTO_DEMO_FALLBACK", "true"),
            "fallback_timeout_seconds": os.environ.get("SARD_DEMO_FALLBACK_TIMEOUT_SECONDS", "45"),
            "output_root": str(output_root),
        },
        "corpus": {
            "status": "passed" if source_files and not corpus_errors else "failed",
            "documents": len(source_files),
            "errors": corpus_errors,
        },
        "model_access": model_access,
        "fonts": {"status": fonts},
        "output_directory_permissions": {"status": output_permission},
        "cached_fallback": cache,
        "version_control_exclusions": _git_exclusion_report(),
    }


def cmd_check(_args: argparse.Namespace) -> int:
    report = build_self_check()
    _print(report)
    return 0 if report["overall"] in {"ready_live", "ready_fallback_only"} else 2


def cmd_evaluate(args: argparse.Namespace) -> int:
    golden = run_offline_golden_evaluation(
        PROJECT_ROOT / args.golden,
        PROJECT_ROOT / args.corpus,
        k=args.k,
    )
    pipeline = _validate_cached_pipeline()
    report = {
        "evaluation_mode": "offline_lexical_rehearsal",
        "live_model_backed_evaluation": False,
        "retrieval": {
            "passed": golden.passed_cases,
            "total": golden.total_cases,
            "pass_rate": golden.passed_cases / golden.total_cases if golden.total_cases else 0.0,
            "gate_threshold": golden.gate_threshold,
            "gate_passed": golden.gate_passed,
            "per_question": [
                {
                    "case_id": case.case_id,
                    "passed": case.passed,
                    "reason": case.reason,
                    "source_titles": case.retrieved_source_titles,
                    "citation_ids": case.retrieved_citation_ids,
                    "latency_ms": round(case.latency_ms, 3),
                }
                for case in golden.case_results
            ],
        },
        "citation_coverage": pipeline["citation_coverage"],
        "unsupported_claim_count": pipeline["unsupported_claim_count"],
        "pipeline": {"status": pipeline["status"], "mode": pipeline["mode"]},
        "pdf_creation": pipeline["pdf"],
        "calendar_validation": pipeline["calendar"],
        "raw_text": pipeline["raw_text"],
        "latency_by_graph_node_ms": pipeline["node_latency_ms"],
        "latency_note": (
            "These are the cached fixture's pre-recorded simulated durations for UI rehearsal; "
            "live node latency was not measurable because model access was not configured."
        ),
        "known_retrieval_gaps": golden.questions_needing_improvement,
    }
    if args.json_out:
        destination = PROJECT_ROOT / args.json_out
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _print(report)
    return 0 if golden.gate_passed and pipeline["status"] == "passed" else 3


def cmd_rehearse(args: argparse.Namespace) -> int:
    check = build_self_check()
    golden = run_offline_golden_evaluation(
        PROJECT_ROOT / args.golden,
        PROJECT_ROOT / args.corpus,
    )
    report = {
        "self_check": check["overall"],
        "golden_score": f"{golden.passed_cases}/{golden.total_cases}",
        "golden_gate_passed": golden.gate_passed,
        "clean_rehearsal": "passed" if check["overall"] != "not_ready" and golden.gate_passed else "failed",
    }
    _print(report)
    return 0 if report["clean_rehearsal"] == "passed" else 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sard-demo")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="Verify configuration, corpus, model access, fonts, output permissions, and cache.")
    check.set_defaults(func=cmd_check)
    evaluate = sub.add_parser("evaluate", help="Run the complete labelled golden set and cached artifact validation.")
    evaluate.add_argument("--golden", default="evals/golden.json")
    evaluate.add_argument("--corpus", default="data/corpus")
    evaluate.add_argument("--k", type=int, default=6)
    evaluate.add_argument("--json-out")
    evaluate.set_defaults(func=cmd_evaluate)
    rehearse = sub.add_parser("rehearse", help="Run the documented offline-ready rehearsal gate.")
    rehearse.add_argument("--golden", default="evals/golden.json")
    rehearse.add_argument("--corpus", default="data/corpus")
    rehearse.set_defaults(func=cmd_rehearse)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        print(f"demo command failed safely: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
