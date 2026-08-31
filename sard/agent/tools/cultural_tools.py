"""Cultural retrieval tools for the Sard assistant.

Provides two native retrieval tools and an extractor:
1) ``rag_search``: Retrieves from the curated cultural knowledge base (Zvec/corpus).
2) ``parallel_search``: Live web search via Parallel Search API/SDK with cultural source policies.
3) ``parallel_extract``: URL extraction via Parallel Extract for deep page markdown.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

import httpx
from dotenv import load_dotenv

load_dotenv()

from sard.config.rag import get_rag_settings
from sard.url_policy import is_safe_external_url, safe_external_url

logger = logging.getLogger("sard.tools.cultural")

PARALLEL_BETA_HEADER = "search-extract-2025-10-10"
PARALLEL_API_DEFAULT_BASE = "https://api.parallel.ai/v1beta"
DEFAULT_PARALLEL_API_KEY = "dxl5SMKxtkCCAZjJH_LobPTJ6rGbXYot7YX_JLKK"

# Cultural source preferences & domain filters
_PREFERRED_DOMAINS = (
    ".gov.sa",
    ".edu.sa",
    "saudipedia.com",
    "moc.gov.sa",
    "visitsaudi.com",
    "heritage.moc.gov.sa",
    "culinary.moc.gov.sa",
    "museums.moc.gov.sa",
    "spa.gov.sa",
    "aleqt.com",
    "alriyadh.com",
    "al-jazirah.com",
    "okaz.com.sa",
    "alarabiya.net",
    "unesco.org",
    "islamqa.info",
    "quran.com",
    "gov.qa",
    "visitqatar.com",
    "culture.gov.qa",
    "mcy.gov.ae",
    "abudhabiculture.ae",
    "culture.gov.bh",
    "omaninfo.om",
)

_DISALLOWED_DOMAINS = (
    "pinterest.com",
    "quora.com",
    "reddit.com",
    "tripadvisor.com/showtopic",
    "buzzfeed.com",
    "listverse.com",
    "thetravel.com",
    "culturetrip.com",
    "boredpanda.com",
)


def _infer_cultural_metadata(title: str, content: str, original_topic: str = "") -> dict[str, str]:
    """Infer culture/region and standardized cultural topic from document text."""
    text_lower = f"{title} {content} {original_topic}".lower()

    # Region / Culture
    culture = "سعودية / خليجية"
    region = "المملكة العربية السعودية"
    if any(k in text_lower for k in ["أحساء", "احساء", "الشرقية", "قطيف", "تاروت", "دمام", "ظهران", "eastern province", "al-ahsa"]):
        culture = "سعودية (المنطقة الشرقية)"
        region = "المنطقة الشرقية"
    elif any(k in text_lower for k in ["نجد", "رياض", "درعية", "طريف", "najd", "riyadh", "diriyah"]):
        culture = "سعودية (نجد)"
        region = "منطقة الرياض / نجد"
    elif any(k in text_lower for k in ["حجاز", "جدة", "مكة", "مدينة", "علا", "hijaz", "jeddah", "alula"]):
        culture = "سعودية (الحجاز)"
        region = "منطقة مكة / المدينة / الحجاز"
    elif any(k in text_lower for k in ["عسير", "جنوب", "نجران", "جازان", "abha", "asir", "najran"]):
        culture = "سعودية (الجنوب)"
        region = "عسير / نجران / جازان"
    elif any(k in text_lower for k in ["قطر", "دوحة", "qatar", "doha"]):
        culture = "قطرية / خليجية"
        region = "دولة قطر"
    elif any(k in text_lower for k in ["إمارات", "امارات", "أبوظبي", "دبي", "uae", "dubai", "abu dhabi"]):
        culture = "إماراتية / خليجية"
        region = "دولة الإمارات العربية المتحدة"
    elif any(k in text_lower for k in ["كويت", "kuwait"]):
        culture = "كويتية / خليجية"
        region = "دولة الكويت"
    elif any(k in text_lower for k in ["عمان", "عُمان", "مسقط", "oman"]):
        culture = "عُمانية / خليجية"
        region = "سلطنة عُمان"
    elif any(k in text_lower for k in ["بحرين", "bahrain"]):
        culture = "بحرينية / خليجية"
        region = "مملكة البحرين"

    # Standardized Topic
    topic = "heritage"
    if any(k in text_lower for k in ["آداب", "ضيافة", "مجلس", "تحية", "سلام", "فنجان", "قهوة", "اتيكيت", "إتيكيت", "etiquette", "greeting", "hospitality", "majlis"]):
        topic = "etiquette"
    elif any(k in text_lower for k in ["طعام", "مأكولات", "أكل", "طبخ", "روبيان", "كبسة", "جريش", "مطعم", "cuisine", "food", "shrimp", "dish"]):
        topic = "food"
    elif any(k in text_lower for k in ["دين", "إسلام", "صلاة", "رمضان", "حج", "عمرة", "عيد", "صوم", "فقه", "شريعة", "religion", "islam", "prayer"]):
        topic = "religion"
    elif any(k in text_lower for k in ["لباس", "زي", "ثوب", "بشت", "شماغ", "برقع", "عباية", "سدو", "dress", "attire", "thobe", "bisht"]):
        topic = "dress"
    elif any(k in text_lower for k in ["لغة", "لهجة", "أمثال", "مثل", "شعر", "مصطلحات", "فصحى", "language", "dialect", "proverb"]):
        topic = "language"
    elif any(k in text_lower for k in ["مهرجان", "يوم وطني", "يوم التأسيس", "موسم", "فعالية", "احتفال", "holiday", "festival", "season"]):
        topic = "holidays"
    elif any(k in text_lower for k in ["أسرة", "عائلة", "زواج", "عرس", "تقاليد الأسرة", "family", "wedding"]):
        topic = "family"
    elif any(k in text_lower for k in ["أعمال", "مفاوضات", "اجتماع", "تجارة", "سوق", "business", "meeting", "workplace"]):
        topic = "business"
    elif any(k in text_lower for k in ["ينابيع", "عين", "مياه كبريتية", "استشفاء", "springs"]):
        topic = "springs"

    # Language
    has_arabic = bool(re.search(r"[\u0600-\u06FF]", content))
    language = "ar" if has_arabic else "en"

    return {
        "culture": culture,
        "region": region,
        "topic": topic,
        "language": language,
    }


def rag_search(query: str, k: int = 6) -> list[dict[str, Any]]:
    """Retrieve from our curated cultural knowledge base.

    Returns:
        List of dicts: ``[{"source": str, "title": str, "chunk": str, "score": float, "score_type": str, "metadata": dict}]``
    """
    t0 = time.monotonic()
    query_str = (query or "").strip()
    if not query_str:
        return []

    results: list[dict[str, Any]] = []

    # 1. Try local Zvec repository FTS index first for instant sub-millisecond retrieval
    try:
        from sard.config.rag import get_rag_settings
        from sard.rag.zvec_store import ZvecRepository
        settings = get_rag_settings()
        repo = ZvecRepository.find_existing_for_model(
            settings.zvec_collection_path, settings.embedding_route.primary
        )
        if repo is not None:
            try:
                candidates = repo.fts_search(query_str, topk=k)
                for cand in candidates:
                    score_val = getattr(cand, "confidence_score", None) or getattr(cand, "fts_score", 0.85) or 0.85
                    if float(score_val) < 0.65:
                        continue
                    meta = _infer_cultural_metadata(cand.title, cand.content, cand.topic)
                    meta.update({
                        "source_name": cand.source_name,
                        "source_url": cand.source_url,
                        "citation_id": cand.citation_id,
                        "chunk_id": cand.chunk_id,
                        "publication_date": cand.publication_date or "",
                        "page_number": cand.page_number,
                        "score_type": "fts",
                        "confidence_score": round(min(0.95, float(score_val)), 4),
                    })
                    results.append({
                        "source": cand.source_name or "سرد - قاعدة المعرفة الثقافية",
                        "title": cand.title or "وثيقة تراثية",
                        "chunk": cand.content,
                        "score": round(min(0.95, float(score_val)), 4),
                        "score_type": "fts",
                        "metadata": meta,
                    })
            finally:
                repo.close()
    except Exception as exc:
        logger.debug("ZvecRepository search_fts skipped (%s)", exc)

    # 2. Local corpus scan for data/corpus and data/cultural
    corpus_results = _scan_local_cultural_corpus(query_str, k=k)
    if corpus_results:
        # Merge, prioritizing highest score
        existing_chunks = {r["chunk"][:100] for r in results}
        for cr in corpus_results:
            if cr["chunk"][:100] not in existing_chunks:
                results.append(cr)
                existing_chunks.add(cr["chunk"][:100])

    # Filter strictly to relevant items
    results = [r for r in results if r.get("score", 0.0) >= 0.65]
    results.sort(key=lambda x: x["score"], reverse=True)
    results = results[:k]

    latency_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "rag_search executed: query='%s', count=%d, top_score=%.3f, latency=%.1fms",
        query_str,
        len(results),
        results[0]["score"] if results else 0.0,
        latency_ms,
    )
    return results


def _scan_local_cultural_corpus(query: str, k: int = 6) -> list[dict[str, Any]]:
    """Deterministic local corpus scanner with strict topic and entity relevance calibration."""
    root = Path(__file__).resolve().parents[3]
    corpus_dirs = [root / "data" / "corpus", root / "data" / "cultural"]

    stop_words = {
        "في", "من", "على", "عن", "إلى", "الى", "مع", "كيف", "ما", "ماذا", "هل", "تتم",
        "ممارسة", "طريقة", "ماذا", "هو", "هي", "هذا", "هذه", "التي", "الذي", "الذين",
        "تراث", "تراثية", "التقليدية", "تقليدية", "تاريخ", "تاريخية", "عام", "سنة",
        "the", "a", "an", "in", "of", "and", "how", "what", "is", "are", "for", "to",
    }
    raw_terms = [t for t in re.split(r"[\s,،.?؟]+", query.lower()) if len(t) > 1]

    def _clean_token(t: str) -> str:
        t = re.sub(r"[^\w\u0600-\u06FF]", "", t).strip()
        for prefix in ("بال", "كال", "فال", "لل", "ال", "و", "ب", "ف", "ك", "ل"):
            if len(t) > len(prefix) + 2 and t.startswith(prefix):
                t = t[len(prefix):]
                break
        return t

    cleaned_terms = [_clean_token(t) for t in raw_terms if len(_clean_token(t)) >= 2 and _clean_token(t) not in stop_words]
    terms = cleaned_terms or raw_terms
    if not terms:
        return []

    q_lower = query.lower()

    # Geographical regions mapping to detect cross-region mismatch
    region_clusters = {
        "qassim": ["قصيم", "بريدة", "عنيزة", "رس", "بكرية"],
        "asir": ["عسير", "أبها", "ابها", "رجال ألمع", "المع", "خميس مشيط", "سودة"],
        "hijaz": ["حجاز", "جدة", "مكة", "مدينة", "طائف", "ينبع", "علا"],
        "jouf": ["جوف", "سكاكا", "دومة الجندل", "قريات"],
        "jazan": ["جازان", "جيزان", "فرسان", "صبيا", "أبو عريش"],
        "najd": ["نجد", "رياض", "درعية", "خرج", "وشم", "سدير"],
        "north": ["تبوك", "حائل", "عرعر", "حدود شمالية"],
        "eastern": ["شرقية", "أحساء", "احساء", "قطيف", "تاروت", "دمام", "خبر", "هفوف", "سيهات", "جبيل", "خفجي"],
    }

    query_regions = set()
    for reg_name, kws in region_clusters.items():
        if any(kw in q_lower for kw in kws):
            query_regions.add(reg_name)

    scored_docs: list[dict[str, Any]] = []

    for cdir in corpus_dirs:
        if not cdir.exists():
            continue
        for md_file in cdir.glob("**/*.md"):
            if md_file.name in ("MANIFEST.md", "README.md"):
                continue
            text = md_file.read_text(encoding="utf-8", errors="replace")
            sidecar = md_file.with_name(f"{md_file.name}.meta.json")
            meta_json = {}
            if sidecar.exists():
                try:
                    meta_json = json.loads(sidecar.read_text(encoding="utf-8"))
                except Exception:
                    pass

            title = meta_json.get("title") or md_file.stem.replace("-", " ")
            source_name = meta_json.get("source_name") or "دليل التراث السعودي"
            source_url = meta_json.get("source_url") or f"file://{md_file.name}"
            topic_str = meta_json.get("topic", "")

            # Document region
            doc_text_lower = (text + " " + title + " " + topic_str).lower()
            doc_regions = set()
            for reg_name, kws in region_clusters.items():
                if any(kw in doc_text_lower for kw in kws):
                    doc_regions.add(reg_name)
            if not doc_regions:
                doc_regions.add("eastern")

            # Reject geographic mismatch
            if query_regions and not (query_regions & doc_regions):
                continue

            # Pilot corpus topic specificity checks
            is_springs_doc = (
                "springs" in md_file.parent.name
                or "springs" in topic_str.lower()
                or any(k in doc_text_lower for k in ["ينابيع", "عين حارة", "عيون حارة", "مياه حارة"])
            )
            is_shrimp_doc = (
                "coastal" in md_file.parent.name
                or "shrimp" in topic_str.lower()
                or any(k in doc_text_lower for k in ["روبيان", "ربيان", "تجفيف"])
            )

            is_springs_query = any(k in q_lower for k in ["ينابيع", "عين حارة", "عيون حارة", "عين الحارة", "عيون الأحساء", "عيون الاحساء", "مياه حارة", "مياه كبريتية", "springs", "استشفاء"])
            is_shrimp_query = any(k in q_lower for k in ["روبيان", "ربيان", "تجفيف الروبيان", "تجفيف الربيان", "الروبيان المجفف", "الربيان المجفف", "تاروت", "shrimp"])

            if is_springs_doc and not is_springs_query:
                continue
            if is_shrimp_doc and not is_shrimp_query:
                continue

            content_clean = text.lower()
            matches = sum(1 for term in terms if term in content_clean)
            if matches == 0:
                continue

            match_ratio = matches / max(len(terms), 1)
            score = min(0.95, match_ratio * 0.70 + (0.25 if match_ratio >= 0.5 else 0.10))

            if score < 0.65:
                continue

            inferred = _infer_cultural_metadata(title, text, topic_str)
            inferred.update({
                "source_name": source_name,
                "source_url": source_url,
                "citation_id": f"CIT-CORPUS-{md_file.stem[:8].upper()}",
                "chunk_id": f"CHUNK-{md_file.stem[:8].upper()}",
                "publication_date": meta_json.get("publication_date", ""),
                "score_type": "fts",
                "confidence_score": round(score, 4),
            })
            scored_docs.append({
                "source": source_name,
                "title": title,
                "chunk": text[:1500],
                "score": round(score, 4),
                "score_type": "fts",
                "metadata": inferred,
            })

    scored_docs.sort(key=lambda x: x["score"], reverse=True)
    return scored_docs[:k]


def parallel_search(
    objective: str,
    search_queries: Sequence[str],
    max_results: int = 8,
    api_key: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Live web search via Parallel Search API.

    Args:
        objective: Natural language information need describing the exact cultural context.
        search_queries: 2-5 keyword queries.
        max_results: Max number of returned items (default: 8).
        api_key: Optional override for PARALLEL_API_KEY.

    Returns:
        List of ranked ``{"url": str, "title": str, "excerpts": list[str], "publish_date": str}``.
    """
    t0 = time.monotonic()
    resolved_key = (
        api_key
        or os.environ.get("PARALLEL_API_KEY")
        or DEFAULT_PARALLEL_API_KEY
    ).strip()

    search_queries_list = [q.strip() for q in search_queries if q and q.strip()]
    if not search_queries_list:
        search_queries_list = [objective.strip()]

    # Format cultural query biases (Arabic + English, local institutions)
    sanitized_queries = _bias_queries_for_cultural_sources(search_queries_list, objective)

    results: list[dict[str, Any]] = []
    sdk_used = False

    # 1. Try official Parallel SDK
    try:
        from parallel import Parallel
        client = Parallel(api_key=resolved_key)
        sdk_res = client.search(
            objective=objective,
            search_queries=sanitized_queries,
            max_chars_total=4000 * max_results,
        )
        sdk_used = True
        # Extract items from SDK response
        raw_items = getattr(sdk_res, "results", []) or getattr(sdk_res, "items", []) or []
        for item in raw_items:
            url = getattr(item, "url", "") or getattr(item, "link", "")
            title = getattr(item, "title", "")
            excerpts = getattr(item, "excerpts", []) or [getattr(item, "snippet", "")]
            pub_date = getattr(item, "publish_date", "") or getattr(item, "published_date", "")
            if isinstance(excerpts, str):
                excerpts = [excerpts]
            results.append({
                "url": url,
                "title": title,
                "excerpts": [e for e in excerpts if e],
                "publish_date": str(pub_date) if pub_date else None,
            })
    except Exception as sdk_exc:
        logger.debug("Parallel SDK search failed or not available (%s); falling back to direct HTTP REST.", sdk_exc)

    # 2. HTTP REST Fallback
    if not results:
        headers = {
            "Content-Type": "application/json",
            "x-api-key": resolved_key,
            "parallel-beta": PARALLEL_BETA_HEADER,
        }
        body = {
            "objective": objective,
            "search_queries": sanitized_queries,
            "max_results": max_results,
            "max_chars_per_result": 4000,
        }
        url = f"{PARALLEL_API_DEFAULT_BASE}/search"
        try:
            with httpx.Client(timeout=15.0) as http_client:
                resp = http_client.post(url, headers=headers, json=body)
                if resp.status_code == 200:
                    data = resp.json()
                    raw_items = data.get("results") or data.get("items") or []
                    for item in raw_items:
                        item_url = item.get("url") or item.get("link") or ""
                        title = item.get("title") or ""
                        excerpts = item.get("excerpts") or [item.get("snippet", "")] or [item.get("text", "")]
                        if isinstance(excerpts, str):
                            excerpts = [excerpts]
                        pub_date = item.get("publish_date") or item.get("published_date")
                        results.append({
                            "url": item_url,
                            "title": title,
                            "excerpts": [e for e in excerpts if e],
                            "publish_date": str(pub_date) if pub_date else None,
                        })
                else:
                    logger.warning("Parallel HTTP search returned status %d: %s", resp.status_code, resp.text[:200])
        except Exception as http_exc:
            logger.error("Parallel HTTP search request exception: %s", http_exc)

    # Apply Cultural Source Policy & Safety URL validation
    filtered_results = _apply_cultural_source_policy(results, max_results=max_results)

    latency_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "parallel_search executed: objective='%s', queries=%s, raw_count=%d, filtered_count=%d, latency=%.1fms (SDK=%s)",
        objective,
        sanitized_queries,
        len(results),
        len(filtered_results),
        latency_ms,
        sdk_used,
    )
    return filtered_results


def parallel_extract(
    urls: Sequence[str],
    objective: str,
    api_key: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Fetch clean markdown content for 1-3 top URLs via Parallel Extract.

    Args:
        urls: List of 1 to 3 URLs.
        objective: Natural language extraction focus.
        api_key: Optional override for PARALLEL_API_KEY.

    Returns:
        List of dicts: ``[{"url": str, "title": str, "markdown": str, "content": str}]``.
    """
    t0 = time.monotonic()
    safe_urls = [safe_external_url(u) for u in urls if is_safe_external_url(u)][:3]
    if not safe_urls:
        return []

    resolved_key = (
        api_key
        or os.environ.get("PARALLEL_API_KEY")
        or DEFAULT_PARALLEL_API_KEY
    ).strip()

    results: list[dict[str, Any]] = []

    # 1. Try official Parallel SDK
    try:
        from parallel import Parallel
        client = Parallel(api_key=resolved_key)
        sdk_res = client.extract(
            urls=safe_urls,
            objective=objective,
        )
        raw_items = getattr(sdk_res, "results", []) or getattr(sdk_res, "items", []) or []
        for item in raw_items:
            u = getattr(item, "url", "")
            t = getattr(item, "title", "")
            md = getattr(item, "markdown", "") or getattr(item, "content", "")
            results.append({
                "url": u,
                "title": t,
                "markdown": md,
                "content": md,
            })
    except Exception as sdk_exc:
        logger.debug("Parallel SDK extract failed or not available (%s); falling back to direct HTTP REST.", sdk_exc)

    # 2. HTTP REST Fallback
    if not results:
        headers = {
            "Content-Type": "application/json",
            "x-api-key": resolved_key,
            "parallel-beta": PARALLEL_BETA_HEADER,
        }
        body = {
            "urls": safe_urls,
            "objective": objective,
        }
        url = f"{PARALLEL_API_DEFAULT_BASE}/extract"
        try:
            with httpx.Client(timeout=20.0) as http_client:
                resp = http_client.post(url, headers=headers, json=body)
                if resp.status_code == 200:
                    data = resp.json()
                    raw_items = data.get("results") or data.get("items") or []
                    for item in raw_items:
                        item_url = item.get("url") or ""
                        title = item.get("title") or ""
                        md = item.get("markdown") or item.get("content") or item.get("text") or ""
                        results.append({
                            "url": item_url,
                            "title": title,
                            "markdown": md,
                            "content": md,
                        })
                else:
                    logger.warning("Parallel HTTP extract returned status %d: %s", resp.status_code, resp.text[:200])
        except Exception as http_exc:
            logger.error("Parallel HTTP extract request exception: %s", http_exc)

    latency_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "parallel_extract executed: urls=%s, objective='%s', count=%d, latency=%.1fms",
        safe_urls,
        objective,
        len(results),
        latency_ms,
    )
    return results


def _bias_queries_for_cultural_sources(queries: list[str], objective: str) -> list[str]:
    """Ensure search queries include both Arabic and English forms with local institutional context."""
    out_queries: list[str] = []
    seen = set()

    for q in queries:
        clean = q.strip()
        if clean and clean not in seen:
            out_queries.append(clean)
            seen.add(clean)

    # If Arabic query, add an English variant if helpful; if English, add Arabic keyword
    obj_lower = objective.lower()
    has_arabic = bool(re.search(r"[\u0600-\u06FF]", objective))

    if has_arabic and len(out_queries) < 4:
        # Check if Saudi / Qatar / Gulf specific
        if "قطر" in objective or "دوحة" in objective:
            q_extra = "Qatari etiquette business Doha official"
            if q_extra not in seen:
                out_queries.append(q_extra)
        elif "سعود" in objective or "ثقافة" in objective:
            q_extra = "Saudi cultural traditions ministry heritage"
            if q_extra not in seen:
                out_queries.append(q_extra)

    return out_queries[:5]


def _apply_cultural_source_policy(results: list[dict[str, Any]], max_results: int = 8) -> list[dict[str, Any]]:
    """Filter out spam/clickbait listicles and boost primary/institutional sources."""
    preferred: list[dict[str, Any]] = []
    standard: list[dict[str, Any]] = []

    for item in results:
        raw_url = item.get("url", "")
        if not is_safe_external_url(raw_url):
            continue
        safe_url = safe_external_url(raw_url)
        item["url"] = safe_url

        url_lower = safe_url.lower()

        # Check for disallowed / clickbait domains
        if any(d in url_lower for d in _DISALLOWED_DOMAINS):
            continue

        # Check for preferred local / institutional domains
        if any(d in url_lower for d in _PREFERRED_DOMAINS):
            preferred.append(item)
        else:
            standard.append(item)

    # Ranked: preferred institutional sources first, followed by safe general sources
    ranked = preferred + standard
    return ranked[:max_results]
