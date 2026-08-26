"""Grounded Retrieval Layer for Sard's Isnād Planner.

Executes retrieval before drafting:
1. Curated RAG corpus first.
2. Parallel Search second, strictly for dated/public authoritative sources.
3. Multimodal extraction & visual inspection if media files or images are attached.

Transforms every retrieved document chunk, search hit, or extracted media trace
into an immutable L0 Evidence record with a durable source_id.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from sard.agent.tools.cultural_tools import parallel_search, rag_search
from sard.agent.tools.multimodal_tools import (
    MultimodalExtractedItem,
    extract_multimodal_context,
)
from sard.memory.l0_evidence import L0EvidenceStore
from sard.schemas.isnad import Evidence, Region, SourceType

logger = logging.getLogger("sard.planner.retrieve")


def _classify_source_type(url_or_name: str, origin: str) -> SourceType:
    """Classify the source type based on domain, title, or authority name."""
    s = f"{url_or_name} {origin}".lower()
    if any(k in s for k in ["moc.gov.sa", "heritage.moc", "culinary.moc", "museums.moc", "وزارة الثقافة", "هيئة التراث", "هيئة فنون الطهي", "دارة الملك عبدالعزيز", "دار الملك"]):
        return "ministry"
    if any(k in s for k in ["museum", "متحف", "متحف الوطني", "إثراء", "ithra", "قصر المربع", "قصر المصمك"]):
        return "museum"
    if any(k in s for k in ["saudipedia", "spa.gov.sa", "unesco.org", "وكالة الأنباء السعودية"]):
        return "news"
    if any(k in s for k in ["وثيقة", "مخطوطة", "تاريخ نجد", "تاريخ الحجاز", "عنوان المجد", "معجم"]):
        return "dated_text"
    if any(k in s for k in ["رواية شفهية", "شهادة حية", "مقابلة", "حرفي", "صانع تقليدي"]):
        return "oral_account"
    if any(k in s for k in ["@photo", "@image", "صورة", "مرفق", "upload", "user"]):
        return "user_upload"
    return "unknown"


def _infer_region_from_text(text: str) -> Region:
    """Infer region from text contents."""
    t_lower = text.lower()
    if any(k in t_lower for k in ["نجد", "رياض", "درعية", "طريف", "سدير", "قصيم", "وشم", "أثل", "najd"]):
        return "najd"
    if any(k in t_lower for k in ["حجاز", "جدة", "مكة", "مدينة", "علا", "رواشين", "منجور", "hijaz"]):
        return "hijaz"
    if any(k in t_lower for k in ["عسير", "أبها", "قط عسيري", "رجال ألمع", "asir"]):
        return "asir"
    if any(k in t_lower for k in ["شرقية", "أحساء", "احساء", "قطيف", "تاروت", "eastern"]):
        return "eastern"
    if any(k in t_lower for k in ["حائل", "تبوك", "جوف", "عرعر", "north"]):
        return "north"
    if any(k in t_lower for k in ["نجران", "جازان", "south"]):
        return "south"
    return "unknown"


class GroundedRetriever:
    """Orchestrates retrieval and converts all findings into L0 Evidence."""

    def __init__(
        self,
        l0_store: L0EvidenceStore,
        rag_search_fn: Callable[[str, int], List[Dict[str, Any]]] = rag_search,
        parallel_search_fn: Callable[..., List[Dict[str, Any]]] = parallel_search,
        multimodal_extract_fn: Callable[..., List[MultimodalExtractedItem]] = extract_multimodal_context,
    ):
        self.l0 = l0_store
        self.rag_search = rag_search_fn
        self.parallel_search = parallel_search_fn
        self.multimodal_extract = multimodal_extract_fn

    def retrieve(
        self,
        query: str,
        target_region: Optional[Region] = None,
        mock_multimodal_files: Optional[Dict[str, Any]] = None,
        allow_web_search: bool = True,
    ) -> Tuple[List[Evidence], List[str]]:
        """Retrieve evidence across RAG, Web, and Multimodal extractors."""
        evidence_list: List[Evidence] = []
        retrieval_logs: List[str] = []

        # 1. Multimodal media inspection
        media_items = self.multimodal_extract(
            query,
            mock_files=mock_multimodal_files,
        )
        for m in media_items:
            origin = f"المرفق البصري/المعرف ({m.filename})"
            excerpt = m.description or m.extracted_text or f"ملف مرئي من نوع {m.file_type}: {m.filename}"
            ev = self.l0.store_evidence(
                excerpt=excerpt,
                origin=origin,
                region=target_region or "unknown",
                source_type="user_upload",
                url_or_doc_id=m.filename,
                raw_data=getattr(m, "__dict__", {}),
                prefix="media",
            )
            evidence_list.append(ev)
            retrieval_logs.append(f"تم فحص المرفق {m.filename} وتوثيقه بسند {ev.source_id}")

        # 2. Curated RAG Search
        rag_hits = self.rag_search(query, 5)
        for h in rag_hits:
            text = h.get("text", "")
            title = h.get("title", "")
            doc_id = h.get("doc_id") or h.get("citation_id", "")
            region = _infer_region_from_text(f"{title} {text}") or target_region or "unknown"
            origin = h.get("source_name") or title or "موسوعة المعارف الثقافية المعتمدة (سرد)"
            stype = _classify_source_type(doc_id, origin)
            if stype == "unknown":
                stype = "ministry"  # Curated RAG is trusted

            ev = self.l0.store_evidence(
                excerpt=text,
                origin=origin,
                region=region,
                source_type=stype,
                date_or_period=h.get("date_or_period") or "تراث موثق",
                url_or_doc_id=doc_id,
                raw_data=h,
                prefix="rag",
            )
            evidence_list.append(ev)
            retrieval_logs.append(f"تم استرجاع وثيقة RAG: {origin} [{region}] -> {ev.source_id}")

        # 3. Parallel Search (Web) - invoked if RAG hits are low or time-sensitive/fresh
        if allow_web_search and len(rag_hits) < 3:
            try:
                web_hits = self.parallel_search(
                    query=query,
                    max_results=3,
                    domains_filter=["saudipedia.com", "moc.gov.sa", "spa.gov.sa", "heritage.moc.gov.sa", "unesco.org"],
                )
                for wh in web_hits:
                    title = wh.get("title", "")
                    content = wh.get("content", "")
                    url = wh.get("url", "")
                    region = _infer_region_from_text(f"{title} {content}") or target_region or "unknown"
                    origin = title or url
                    stype = _classify_source_type(url, origin)

                    ev = self.l0.store_evidence(
                        excerpt=content,
                        origin=origin,
                        region=region,
                        source_type=stype,
                        date_or_period=wh.get("published_date"),
                        url_or_doc_id=url,
                        raw_data=wh,
                        prefix="web",
                    )
                    evidence_list.append(ev)
                    retrieval_logs.append(f"تم استرجاع مصدر ويب: {origin} -> {ev.source_id}")
            except Exception as exc:
                logger.warning("Parallel search skipped or failed: %s", exc)

        return evidence_list, retrieval_logs
