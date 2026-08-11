"""Golden-set retrieval evaluation, judged independently of answer fluency.

Reads ``evals/golden.json`` (an Arabic answer-quality/hallucination-safety
eval set — see its ``$schema_note``) and evaluates whether RETRIEVAL alone
surfaces plausible supporting evidence for each question, using each
question's own ``retrieval_terms_ar`` and ``topic_ar`` as the relevance
signal, since the golden set does not carry explicit gold chunk/document
IDs.

This module deliberately does not grade the LLM's final answer — a
question can only "pass" here if retrieval itself found term-overlapping
evidence for the right topic in the corpus, per the gate definition:

    "Do not claim the gate passed if the expected evidence is absent from
    the corpus... A fabricated or unverifiable source was used."
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sard.rag.normalize import normalize_arabic
from sard.rag.retrieve import RetrievalService, reciprocal_rank_fusion
from sard.rag.schemas import RetrievedCandidate, RewrittenQuery

DEFAULT_EVAL_K = 6


@dataclass
class GoldenCaseResult:
    case_id: str
    question: str
    topic: str
    passed: bool
    reason: str
    matched_terms: list[str]
    retrieval_terms_total: int
    dense_hit: bool
    fts_hit: bool
    fused_hit: bool
    reranked_hit: bool
    retrieved_source_titles: list[str]
    retrieved_chunk_ids: list[str]
    retrieved_citation_ids: list[str]
    reranker_used: str
    retrieval_mode: str
    latency_ms: float
    mrr_contribution: float
    ndcg_contribution: float  # binary-relevance nDCG of the fused list
    retrieved_source_urls: list[str] = field(default_factory=list)
    dense_evidence: list[dict] = field(default_factory=list)
    fts_evidence: list[dict] = field(default_factory=list)
    fused_evidence: list[dict] = field(default_factory=list)
    reranked_evidence: list[dict] = field(default_factory=list)
    model_routes: dict[str, str | None] = field(default_factory=dict)


@dataclass
class GoldenEvaluationReport:
    total_cases: int
    passed_cases: int
    gate_threshold: int
    gate_passed: bool
    mean_reciprocal_rank: float
    recall_at_k_dense: float
    recall_at_k_fts: float
    recall_at_k_fused: float
    recall_at_k_reranked: float
    mean_ndcg: float
    ndcg_note: str
    mrr_note: str = ""
    mean_reciprocal_rank_all: float = 0.0
    gate_blockers: list[str] = field(default_factory=list)
    embedding_model_used: str = ""
    case_results: list[GoldenCaseResult] = field(default_factory=list)
    questions_needing_improvement: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total_cases": self.total_cases,
            "passed_cases": self.passed_cases,
            "gate_threshold": self.gate_threshold,
            "gate_passed": self.gate_passed,
            "mean_reciprocal_rank": self.mean_reciprocal_rank,
            "recall_at_k_dense": self.recall_at_k_dense,
            "recall_at_k_fts": self.recall_at_k_fts,
            "recall_at_k_fused": self.recall_at_k_fused,
            "recall_at_k_reranked": self.recall_at_k_reranked,
            "mean_ndcg": self.mean_ndcg,
            "ndcg_note": self.ndcg_note,
            "mrr_note": self.mrr_note,
            "mean_reciprocal_rank_all": self.mean_reciprocal_rank_all,
            "gate_blockers": self.gate_blockers,
            "embedding_model_used": self.embedding_model_used,
            "case_results": [
                {
                    "case_id": r.case_id,
                    "question": r.question,
                    "topic": r.topic,
                    "passed": r.passed,
                    "reason": r.reason,
                    "matched_terms": r.matched_terms,
                    "retrieval_terms_total": r.retrieval_terms_total,
                    "dense_hit": r.dense_hit,
                    "fts_hit": r.fts_hit,
                    "fused_hit": r.fused_hit,
                    "reranked_hit": r.reranked_hit,
                    "retrieved_source_titles": r.retrieved_source_titles,
                    "retrieved_chunk_ids": r.retrieved_chunk_ids,
                    "retrieved_citation_ids": r.retrieved_citation_ids,
                    "reranker_used": r.reranker_used,
                    "retrieval_mode": r.retrieval_mode,
                    "latency_ms": r.latency_ms,
                    "mrr_contribution": r.mrr_contribution,
                    "ndcg_contribution": r.ndcg_contribution,
                    "retrieved_source_urls": r.retrieved_source_urls,
                    "dense_evidence": r.dense_evidence,
                    "fts_evidence": r.fts_evidence,
                    "fused_evidence": r.fused_evidence,
                    "reranked_evidence": r.reranked_evidence,
                    "model_routes": r.model_routes,
                }
                for r in self.case_results
            ],
            "questions_needing_improvement": self.questions_needing_improvement,
        }


def load_golden_cases(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return _validate_golden_cases(data.get("golden_cases", []))


def _validate_golden_cases(cases) -> list[dict]:
    if not isinstance(cases, list):
        raise ValueError("golden_cases must be a JSON array.")
    seen_ids: set[str] = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"golden_cases[{index}] must be a JSON object.")
        missing = [key for key in ("id", "question_ar", "retrieval_terms_ar") if key not in case]
        if missing:
            raise ValueError(f"golden_cases[{index}] is missing required field(s): {missing}")
        if not isinstance(case["id"], str) or not case["id"].strip():
            raise ValueError(f"golden_cases[{index}].id must be a non-empty string.")
        if case["id"] in seen_ids:
            raise ValueError(f"Duplicate golden case id: {case['id']}")
        seen_ids.add(case["id"])
        if not isinstance(case["question_ar"], str) or not case["question_ar"].strip():
            raise ValueError(f"golden_cases[{index}].question_ar must be a non-empty string.")
        if not isinstance(case["retrieval_terms_ar"], list) or not all(
            isinstance(term, str) and term.strip() for term in case["retrieval_terms_ar"]
        ):
            raise ValueError(f"golden_cases[{index}].retrieval_terms_ar must be a list of non-empty strings.")
    return cases


def _candidate_summary(candidate: RetrievedCandidate) -> dict:
    return {
        "chunk_id": candidate.chunk_id,
        "citation_id": candidate.citation_id,
        "title": candidate.title,
        "source_name": candidate.source_name,
        "source_url": candidate.source_url,
        "dense_score": candidate.dense_score,
        "fts_score": candidate.fts_score,
        "fused_score": candidate.fused_score,
        "rerank_score": candidate.rerank_score,
        "dense_rank": candidate.dense_rank,
        "fts_rank": candidate.fts_rank,
        "fused_rank": candidate.fused_rank,
        "rerank_rank": candidate.rerank_rank,
    }


def _candidate_evidence_text(candidate: RetrievedCandidate) -> str:
    """Build the weak evidence surface used by the diagnostic metric.

    Retrieved citation metadata matters for source-level relevance when a
    document uses a close lexical variant in its body, such as
    ``العيون الحارة`` versus ``الينابيع الحارة``. This remains a proxy and
    does not prove that every matching term is a supported fact.
    """
    return "\n".join(
        value
        for value in (candidate.content, candidate.title, candidate.topic, candidate.source_name)
        if value
    )


def _term_hits(terms: list[str], candidates: list[RetrievedCandidate]) -> list[str]:
    normalized_terms = [normalize_arabic(t) for t in terms]
    haystack = normalize_arabic(" \n ".join(_candidate_evidence_text(c) for c in candidates))
    return [orig for orig, norm in zip(terms, normalized_terms) if norm and norm in haystack]


def _binary_ndcg(
    candidates: list[RetrievedCandidate], terms: list[str], k: int
) -> float:
    """nDCG@k over the fused ranked list using term-based BINARY relevance.

    A chunk is "relevant" iff its content contains at least one normalized
    retrieval term from the golden case. This is a legitimate but weak
    relevance signal: it is NOT a graded per-chunk gold label, and the
    report's ``ndcg_note`` says so explicitly. Returns 0.0 when no chunk in
    the list is relevant (DCG is then 0) or when the list is empty.
    """
    normalized_terms = [normalize_arabic(t) for t in terms]
    if not normalized_terms or not candidates:
        return 0.0
    rels = [
        any(t and t in normalize_arabic(_candidate_evidence_text(c)) for t in normalized_terms)
        for c in candidates[:k]
    ]
    if not any(rels):
        return 0.0
    dcg = sum(rel / math.log2(i + 2) for i, rel in enumerate(rels))
    ideal = sorted(rels, reverse=True)
    idcg = sum(rel / math.log2(i + 2) for i, rel in enumerate(ideal))
    return dcg / idcg if idcg > 0 else 0.0


def evaluate_case(
    case: dict,
    retrieval_service: RetrievalService,
    rerank_service=None,
    k: int = DEFAULT_EVAL_K,
) -> GoldenCaseResult:
    question = case["question_ar"]
    terms = case.get("retrieval_terms_ar", [])
    topic = case.get("topic_ar", "")

    start = time.monotonic()
    rewritten = RewrittenQuery(
        original_question=question,
        normalized_question=normalize_arabic(question),
        search_variants=[normalize_arabic(question)],
        rewrite_succeeded=False,
    )
    result = retrieval_service.retrieve(rewritten, filters=None)
    latency_ms = (time.monotonic() - start) * 1000

    dense_terms_hit = _term_hits(terms, result.dense_candidates[:k])
    fts_terms_hit = _term_hits(terms, result.fts_candidates[:k])
    fused_terms_hit = _term_hits(terms, result.fused_candidates[:k])

    reranked = result.fused_candidates[:k]
    reranker_used = "none"
    rerank_model_used = None
    if rerank_service is not None and result.fused_candidates:
        outcome = rerank_service.rerank(question, result.fused_candidates, top_n=k)
        reranked = outcome.candidates
        reranker_used = outcome.model_used or outcome.method_used
        rerank_model_used = outcome.model_used
    reranked_terms_hit = _term_hits(terms, reranked)

    passed = len(fused_terms_hit) > 0 or len(reranked_terms_hit) > 0

    # MRR: reciprocal rank of the first fused candidate containing ANY term hit.
    mrr = 0.0
    normalized_terms = [normalize_arabic(t) for t in terms]
    for rank, c in enumerate(result.fused_candidates, start=1):
        evidence_norm = normalize_arabic(_candidate_evidence_text(c))
        if any(t and t in evidence_norm for t in normalized_terms):
            mrr = 1.0 / rank
            break

    ndcg = _binary_ndcg(result.fused_candidates, terms, k)

    reason = (
        "تم العثور على مصطلحات استرجاع مطابقة ضمن القطع المسترجعة."
        if passed
        else "لم يتم العثور على أي مصطلح استرجاع متوقع ضمن القطع المسترجعة "
        "(الأدلة المتوقعة غير موجودة في المجموعة الحالية أو الاسترجاع لم يُصِب)."
    )

    return GoldenCaseResult(
        case_id=case.get("id", question[:24]),
        question=question,
        topic=topic,
        passed=passed,
        reason=reason,
        matched_terms=fused_terms_hit or reranked_terms_hit,
        retrieval_terms_total=len(terms),
        dense_hit=bool(dense_terms_hit),
        fts_hit=bool(fts_terms_hit),
        fused_hit=bool(fused_terms_hit),
        reranked_hit=bool(reranked_terms_hit),
        retrieved_source_titles=list(dict.fromkeys(c.title for c in reranked if c.title)),
        retrieved_chunk_ids=[c.chunk_id for c in reranked],
        retrieved_citation_ids=[c.citation_id for c in reranked],
        reranker_used=reranker_used,
        retrieval_mode=result.mode.value,
        latency_ms=latency_ms,
        mrr_contribution=mrr,
        ndcg_contribution=ndcg,
        retrieved_source_urls=list(dict.fromkeys(c.source_url for c in reranked if c.source_url)),
        dense_evidence=[_candidate_summary(c) for c in result.dense_candidates[:k]],
        fts_evidence=[_candidate_summary(c) for c in result.fts_candidates[:k]],
        fused_evidence=[_candidate_summary(c) for c in result.fused_candidates[:k]],
        reranked_evidence=[_candidate_summary(c) for c in reranked[:k]],
        model_routes={
            "embedding": getattr(retrieval_service._deps, "embedding_model_id", None),
            "rerank": rerank_model_used or reranker_used,
        },
    )


def run_golden_evaluation(
    golden_path: Path,
    retrieval_service: RetrievalService,
    rerank_service=None,
    k: int = DEFAULT_EVAL_K,
    gate_threshold: int = 8,
) -> GoldenEvaluationReport:
    data = json.loads(golden_path.read_text(encoding="utf-8"))
    cases = _validate_golden_cases(data.get("golden_cases", []))
    evaluation_policy = data.get("evaluation_policy") or {}
    if not isinstance(evaluation_policy, dict):
        raise ValueError("evaluation_policy must be a JSON object when present.")
    results = [evaluate_case(c, retrieval_service, rerank_service, k) for c in cases]

    passed_count = sum(1 for r in results if r.passed)
    mrr_all = sum(r.mrr_contribution for r in results) / len(results) if results else 0.0
    hit_mrrs = [r.mrr_contribution for r in results if r.mrr_contribution > 0]
    mrr = sum(hit_mrrs) / len(hit_mrrs) if hit_mrrs else 0.0
    recall_dense = sum(1 for r in results if r.dense_hit) / len(results) if results else 0.0
    recall_fts = sum(1 for r in results if r.fts_hit) / len(results) if results else 0.0
    recall_fused = sum(1 for r in results if r.fused_hit) / len(results) if results else 0.0
    recall_reranked = sum(1 for r in results if r.reranked_hit) / len(results) if results else 0.0
    mean_ndcg = sum(r.ndcg_contribution for r in results) / len(results) if results else 0.0

    gate_blockers = [str(v) for v in evaluation_policy.get("gate_blockers", [])]
    if evaluation_policy.get("gate_eligible") is False:
        gate_blockers.append(
            str(
                evaluation_policy.get(
                    "gate_ineligible_reason",
                    "The golden set is not eligible for an 8/10 gate under its declared evidence policy.",
                )
            )
        )

    return GoldenEvaluationReport(
        total_cases=len(results),
        passed_cases=passed_count,
        gate_threshold=gate_threshold,
        gate_passed=passed_count >= gate_threshold and not gate_blockers,
        mean_reciprocal_rank=mrr,
        recall_at_k_dense=recall_dense,
        recall_at_k_fts=recall_fts,
        recall_at_k_fused=recall_fused,
        recall_at_k_reranked=recall_reranked,
        mean_ndcg=mean_ndcg,
        mrr_note=(
            "mean_reciprocal_rank is conditioned on questions with at least one "
            "retrieved evidence hit; mean_reciprocal_rank_all includes zero-hit questions."
        ),
        mean_reciprocal_rank_all=mrr_all,
        gate_blockers=gate_blockers,
        ndcg_note=(
            "nDCG@K هنا مبني على الصلة الثنائية المستندة إلى المصطلحات "
            "(chunk يُعد ذا صلة إذا احتوى نصه أو عنوانه أو metadata موضوعه على أي مصطلح استرجاع متوقع واحد على "
            "الأقل) — وليس على تصنيفات ملاءمة متدرجة (graded relevance) لكل "
            "معرّف قطعة، لأن evals/golden.json لا يحمل معرّفات قطع ذهبية. "
            "الرقم مؤشر تقريبي ولا يجب اعتباره nDCG حقيقيًا بتصنيفات متدرجة."
        ),
        embedding_model_used=getattr(retrieval_service._deps, "embedding_model_id", ""),
        case_results=results,
        questions_needing_improvement=[
            r.question for r in results if not r.passed
        ],
    )
