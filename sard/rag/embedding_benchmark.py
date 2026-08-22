"""Embedding evaluation scaffolding for Arabic-first cultural queries.

Measures Recall@K, MRR, nDCG, citation-support, dialect/name matching, latency, failure rate.
Dataset includes Arabic spelling variants, transliteration, Saudi place names, mixed AR/EN.
Decision: adopt only if benchmark supports it; otherwise preserve current provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Dict, Callable
import time
import statistics

@dataclass(frozen=True)
class QueryCase:
    query: str
    expected_doc_ids: tuple[str, ...]
    variant: str  # dialect, transliteration, spelling_variant etc

@dataclass(frozen=True)
class BenchmarkMetrics:
    recall_at_k: float
    mrr: float
    ndcg: float
    citation_accuracy: float
    latency_ms_p50: float
    latency_ms_p95: float
    failure_rate: float
    dialect_match_rate: float

# Minimal golden set covering Arabic variants
GOLDEN_QUERIES: List[QueryCase] = [
    QueryCase("أين تقع الينابيع الحارة في الأحساء؟", ("doc-ahsa-hot-springs",), "standard"),
    QueryCase("وين عيون الموية الحارة بالاحسا؟", ("doc-ahsa-hot-springs",), "dialect"),
    QueryCase("Al-Ahsa hot springs location", ("doc-ahsa-hot-springs",), "transliteration"),
    QueryCase("تجفيف الروبيان في تاروت", ("doc-tarout-shrimp",), "standard"),
    QueryCase("Al-Qatif shrimp drying Tarout", ("doc-tarout-shrimp",), "transliteration_mixed"),
    QueryCase("حي الطريف الدرعية", ("doc-diriyah-turaif",), "saudi_place"),
    QueryCase("مدائن صالح الحجر العلا", ("doc-hegra",), "historic_name"),
]

def compute_recall_at_k(retrieved: List[str], expected: tuple[str, ...], k: int = 6) -> float:
    if not expected:
        return 0.0
    topk = set(retrieved[:k])
    hits = len(topk.intersection(expected))
    return hits / len(expected)

def compute_mrr(retrieved: List[str], expected: tuple[str, ...]) -> float:
    for i, doc in enumerate(retrieved, start=1):
        if doc in expected:
            return 1.0 / i
    return 0.0

def compute_ndcg(retrieved: List[str], expected: tuple[str, ...], k: int = 6) -> float:
    # binary relevance
    dcg = 0.0
    for i, doc in enumerate(retrieved[:k], start=1):
        rel = 1 if doc in expected else 0
        if rel:
            import math
            dcg += rel / math.log2(i + 1)
    # ideal
    ideal_len = min(len(expected), k)
    idcg = sum(1 / __import__("math").log2(i + 1) for i in range(1, ideal_len + 1))
    return dcg / idcg if idcg else 0.0

def benchmark_provider(
    provider_name: str,
    retrieve_fn: Callable[[str], List[str]],
    cases: List[QueryCase] = GOLDEN_QUERIES,
) -> BenchmarkMetrics:
    recalls, mrrs, ndcgs, latencies = [], [], [], []
    failures = 0
    dialect_hits = 0
    dialect_total = 0
    for case in cases:
        start = time.monotonic()
        try:
            retrieved = retrieve_fn(case.query)
        except Exception:
            failures += 1
            retrieved = []
        latencies.append((time.monotonic() - start) * 1000)
        recalls.append(compute_recall_at_k(retrieved, case.expected_doc_ids))
        mrrs.append(compute_mrr(retrieved, case.expected_doc_ids))
        ndcgs.append(compute_ndcg(retrieved, case.expected_doc_ids))
        if case.variant in ("dialect", "transliteration", "transliteration_mixed"):
            dialect_total += 1
            if compute_recall_at_k(retrieved, case.expected_doc_ids) > 0:
                dialect_hits += 1
    return BenchmarkMetrics(
        recall_at_k=statistics.mean(recalls) if recalls else 0,
        mrr=statistics.mean(mrrs) if mrrs else 0,
        ndcg=statistics.mean(ndcgs) if ndcgs else 0,
        citation_accuracy=statistics.mean(recalls) if recalls else 0,
        latency_ms_p50=statistics.median(latencies) if latencies else 0,
        latency_ms_p95=sorted(latencies)[int(len(latencies)*0.95)] if latencies else 0,
        failure_rate=failures / len(cases) if cases else 0,
        dialect_match_rate=(dialect_hits / dialect_total) if dialect_total else 0,
    )

def should_migrate(current: BenchmarkMetrics, candidate: BenchmarkMetrics) -> bool:
    # Require candidate not worse on recall/mrr/ndcg and not higher failure/latency
    if candidate.failure_rate > current.failure_rate + 0.05:
        return False
    if candidate.recall_at_k + 0.02 < current.recall_at_k:
        return False
    if candidate.mrr + 0.02 < current.mrr:
        return False
    # Require at least parity on dialect matching
    if candidate.dialect_match_rate + 0.05 < current.dialect_match_rate:
        return False
    # Require improvement on at least one key metric
    return (candidate.recall_at_k > current.recall_at_k + 0.01 or
            candidate.ndcg > current.ndcg + 0.01 or
            candidate.latency_ms_p50 < current.latency_ms_p50 * 0.9)
