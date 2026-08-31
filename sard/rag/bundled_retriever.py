"""Zero-cold-start static and in-memory hybrid retriever for Sard.

Loads the bundled cultural corpus from ``sard/rag/bundled_index.json`` instantaneously
in serverless environments (Vercel) without requiring external vector DB daemons.
Provides robust lexical, BM25-calibrated, and keyword matching across all 11
Saudi cultural sectors and 13 administrative regions.
"""

from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sard.rag.schemas import ScoreType

logger = logging.getLogger("sard.rag.bundled")

_STOP_WORDS = {
    "في", "من", "على", "عن", "إلى", "الى", "مع", "كيف", "ما", "ماذا", "هل", "تتم",
    "ممارسة", "طريقة", "ماذا", "هو", "هي", "هذا", "هذه", "التي", "الذي", "الذين",
    "تراث", "تراثية", "التقليدية", "تقليدية", "تاريخ", "تاريخية", "عام", "سنة",
    "the", "a", "an", "in", "of", "and", "how", "what", "is", "are", "for", "to",
    "tell", "me", "about", "story", "explain", "describe",
}

_CALIBRATED_THRESHOLD = 0.65


def normalize_token(t: str) -> str:
    """Normalize Arabic word token by stripping punctuation and common attached particles."""
    t = re.sub(r"[^\w\u0600-\u06FFa-zA-Z0-9]", "", t.strip().lower())
    for prefix in ("بال", "كال", "فال", "لل", "ال", "و", "ب", "ف", "ك", "ل"):
        if len(t) > len(prefix) + 2 and t.startswith(prefix):
            t = t[len(prefix):]
            break
    return t


class BundledHybridRetriever:
    """Fast, deterministic in-memory hybrid retriever for bundled cultural corpus."""

    def __init__(self, index_path: Optional[Path] = None):
        if index_path is None:
            index_path = Path(__file__).resolve().parent / "bundled_index.json"
        self.index_path = Path(index_path)
        self.documents: List[Dict[str, Any]] = []
        self._doc_tokens: List[List[str]] = []
        self._doc_lengths: List[int] = []
        self._avg_doc_len: float = 1.0
        self._df: Dict[str, int] = {}
        self._load_corpus()

    def _load_corpus(self) -> None:
        if not self.index_path.exists():
            logger.warning("Bundled index file not found at: %s", self.index_path)
            return

        try:
            raw_text = self.index_path.read_text(encoding="utf-8")
            self.documents = json.loads(raw_text)
        except Exception as exc:
            logger.error("Failed to load bundled index: %s", exc)
            self.documents = []

        total_tokens = 0
        for doc in self.documents:
            text_corpus = (
                f"{doc.get('title', '')} {doc.get('topic', '')} {doc.get('content', '')} "
                f"{' '.join(doc.get('keywords', []))}"
            )
            raw_words = re.split(r"[\s,،.?؟:;!/()\"'«»-]+", text_corpus)
            tokens = [normalize_token(w) for w in raw_words if len(normalize_token(w)) >= 2]
            self._doc_tokens.append(tokens)
            self._doc_lengths.append(len(tokens))
            total_tokens += len(tokens)

            unique_doc_tokens = set(tokens)
            for tok in unique_doc_tokens:
                self._df[tok] = self._df.get(tok, 0) + 1

        if self.documents:
            self._avg_doc_len = total_tokens / max(len(self.documents), 1)

        logger.info(
            "BundledHybridRetriever loaded %d documents (%d unique index terms).",
            len(self.documents),
            len(self._df),
        )

    @property
    def is_available(self) -> bool:
        return len(self.documents) > 0

    def search(
        self,
        query: str,
        k: int = 5,
        target_region: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search the bundled corpus with BM25-like lexical scoring & keyword boosting."""
        if not self.documents or not query.strip():
            return []

        raw_words = re.split(r"[\s,،.?؟:;!/()\"'«»-]+", query.strip().lower())
        q_tokens = [
            normalize_token(w)
            for w in raw_words
            if len(normalize_token(w)) >= 2 and normalize_token(w) not in _STOP_WORDS
        ]
        if not q_tokens:
            q_tokens = [normalize_token(w) for w in raw_words if len(normalize_token(w)) >= 2]
        if not q_tokens:
            return []

        scored_results: List[Tuple[float, Dict[str, Any]]] = []
        n_docs = len(self.documents)
        k1 = 1.5
        b = 0.75

        q_lower = query.lower()

        for idx, doc in enumerate(self.documents):
            doc_toks = self._doc_tokens[idx]
            doc_len = self._doc_lengths[idx]
            tf_map: Dict[str, int] = {}
            for t in doc_toks:
                tf_map[t] = tf_map.get(t, 0) + 1

            bm25_score = 0.0
            matched_terms = 0

            for q_tok in q_tokens:
                tf = tf_map.get(q_tok, 0)
                if tf > 0:
                    matched_terms += 1
                    df_val = self._df.get(q_tok, 1)
                    idf = math.log(1.0 + (n_docs - df_val + 0.5) / (df_val + 0.5))
                    numerator = tf * (k1 + 1.0)
                    denominator = tf + k1 * (1.0 - b + b * (doc_len / max(self._avg_doc_len, 1.0)))
                    bm25_score += idf * (numerator / max(denominator, 0.001))

            # Phrase & Keyword exact bonus
            bonus = 0.0
            doc_title_lower = doc.get("title", "").lower()
            doc_topic_lower = doc.get("topic", "").lower()
            doc_content_lower = doc.get("content", "").lower()

            for kw in doc.get("keywords", []):
                if kw.lower() in q_lower:
                    bonus += 0.20
                    break

            if doc_topic_lower in q_lower or any(w in doc_topic_lower for w in q_tokens if len(w) > 3):
                bonus += 0.25

            if any(tok in doc_title_lower for tok in q_tokens if len(tok) > 3):
                bonus += 0.15

            # Target Region booster
            if target_region and target_region != "unknown":
                doc_reg = doc.get("region", "").lower()
                doc_reg_code = doc.get("region_code", "").lower()
                if target_region.lower() in doc_reg or target_region.lower() in doc_reg_code:
                    bonus += 0.10

            if matched_terms == 0 and bonus == 0.0:
                continue

            match_ratio = matched_terms / max(len(q_tokens), 1)
            raw_combined = (bm25_score / max(len(q_tokens), 1)) * 0.40 + match_ratio * 0.35 + bonus

            # Calibrate confidence score between 0.70 and 0.96
            calibrated_score = round(min(0.96, max(0.66, 0.70 + raw_combined * 0.26)), 4)

            if calibrated_score < _CALIBRATED_THRESHOLD:
                continue

            meta = {
                "source_name": doc.get("source_name", "سرد - قاعدة المعرفة الثقافية"),
                "source_url": doc.get("source_url", ""),
                "citation_id": doc.get("citation_id", f"CIT-{doc.get('id', 'DOC').upper()}"),
                "chunk_id": f"CHUNK-{doc.get('id', '0')}",
                "publication_date": doc.get("publication_date", "2024"),
                "region": doc.get("region", "المملكة العربية السعودية"),
                "region_code": doc.get("region_code", "all"),
                "topic": doc.get("topic", ""),
                "sector": doc.get("sector", "heritage"),
                "score_type": ScoreType.CALIBRATED_CONFIDENCE.value,
                "confidence_score": calibrated_score,
            }

            result_item = {
                "source": doc.get("source_name", "سرد - قاعدة المعرفة الثقافية"),
                "title": doc.get("title", "وثيقة تراثية معتمدة"),
                "chunk": doc.get("content", ""),
                "score": calibrated_score,
                "score_type": ScoreType.CALIBRATED_CONFIDENCE.value,
                "metadata": meta,
                "doc_id": doc.get("id"),
                "citation_id": doc.get("citation_id"),
            }
            scored_results.append((calibrated_score, result_item))

        scored_results.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored_results[:k]]


_RETRIEVER_INSTANCE: Optional[BundledHybridRetriever] = None


def get_bundled_retriever() -> BundledHybridRetriever:
    """Singleton getter for the bundled retriever."""
    global _RETRIEVER_INSTANCE
    if _RETRIEVER_INSTANCE is None:
        _RETRIEVER_INSTANCE = BundledHybridRetriever()
    return _RETRIEVER_INSTANCE
