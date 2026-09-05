"""Capability Registry for Sard — single source of truth.

Implements the contract demanded by the herdr/capabilities worktree:

  intent -> plan -> tools/inputs -> typed content -> grounding -> rendering -> validation -> storage -> verified result

Every public capability is enumerated with:
  - capability ID
  - Arabic and English examples
  - supported inputs / outputs
  - required tools
  - provider requirements
  - intermediate schema
  - validator
  - timeout / retry / fallback
  - progress stages (subset of the unified pattern)
  - support status (honest, test-backed)

Support status policy (strict):
  - supported  -> there exists a public-path test that exercises generation+validation+storage
                 and that test passes offline (no API key).  Examples:
                 PDF/DOCX/PPTX/ICS/SVG/PNG/TXT/JSON/CSV via orchestrator+validation;
                 itinerary via LangGraph; diagram via diagrams; greeting/recipe via agentic outputs.
  - limited    -> offline core exists but enhanced path needs optional provider (vision OCR,
                 audio transcription, web research).  Public tests cover offline fallback + mock.
  - unsupported-> no public-path test, no offline renderer, or provider-only without fallback
                 (e.g. native 3D generation, real-time audio streaming, equivalence to external workspace suites).

Never claim equivalence to ChatGPT Work / Claude Cowork suites.  Report precise limitations.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class SupportStatus(str, Enum):
    SUPPORTED = "supported"
    LIMITED = "limited"
    UNSUPPORTED = "unsupported"
    EXPERIMENTAL = "experimental"


class CapabilityId(str, Enum):
    CONVERSATION = "conversation"
    LOCAL_RAG = "local_rag"
    WEB_RESEARCH = "web_research"
    DOCUMENT_EXTRACTION = "document_extraction"
    OCR = "ocr"
    DOCUMENT_ANALYSIS = "document_analysis"
    DOCUMENT_TRANSFORMATION = "document_transformation"
    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"
    ITINERARY = "itinerary"
    ICS = "ics"
    DIAGRAM = "diagram"
    IMAGE_CARD = "image_card"
    JSON_OUTPUT = "json_output"
    CSV_OUTPUT = "csv_output"
    TXT_OUTPUT = "txt_output"
    AUDIO = "audio"
    VISION = "vision"
    THREE_D = "three_d"


# Unified pipeline pattern — canonical labels
PIPELINE_PATTERN: Tuple[str, ...] = (
    "intent",
    "plan",
    "tools/inputs",
    "typed_content",
    "grounding",
    "rendering",
    "validation",
    "storage",
    "verified_result",
)


@dataclass(frozen=True)
class CapabilitySpec:
    """Complete descriptor for a single Sard capability."""

    id: str
    name_ar: str
    name_en: str
    description_ar: str
    description_en: str
    arabic_examples: Tuple[str, ...]
    english_examples: Tuple[str, ...]
    supported_inputs: Tuple[str, ...]
    supported_outputs: Tuple[str, ...]
    required_tools: Tuple[str, ...]
    provider_requirements: str
    intermediate_schema: str
    validator: str
    timeout_seconds: int
    retry_policy: str
    fallback: str
    progress_stages: Tuple[str, ...]
    support_status: SupportStatus
    limitations: str
    grounding: str
    public_path_test: str  # path to test proving status; empty if unsupported
    related_capability_enum: str = ""  # sard.agent.capability_routing.Capability value if mapped


def _stages(*names: str) -> Tuple[str, ...]:
    """Validate that listed stages are subset of PIPELINE_PATTERN (order preserved)."""
    pat = list(PIPELINE_PATTERN)
    for n in names:
        if n not in pat:
            raise ValueError(f"Unknown pipeline stage: {n}")
    # preserve pattern order
    return tuple(n for n in PIPELINE_PATTERN if n in names)


CAPABILITY_REGISTRY: Dict[str, CapabilitySpec] = {}


def _reg(spec: CapabilitySpec) -> CapabilitySpec:
    CAPABILITY_REGISTRY[spec.id] = spec
    return spec


# ---------------------------------------------------------------------------
# 1. Conversation (simple_conversation)
# ---------------------------------------------------------------------------
_reg(CapabilitySpec(
    id=CapabilityId.CONVERSATION,
    name_ar="محادثة عامة",
    name_en="General Conversation",
    description_ar="محادثة نصية عامة مع ضوابط ثقافية وأمان لغوي.",
    description_en="General text conversation with cultural safety sanitization.",
    arabic_examples=(
        "مرحباً، من أنت؟",
        "اشرح لي بأسلوب مبسط ما هو السدو",
    ),
    english_examples=(
        "Hello, who are you?",
        "Explain what Sadu weaving is in simple terms",
    ),
    supported_inputs=("text",),
    supported_outputs=("text",),
    required_tools=("sard.agent.util.sanitize_cultural_output",),
    provider_requirements="none — deterministic fallback; optional OPENROUTER_API_KEY / NVIDIA_API_KEY for richer LLM synthesis",
    intermediate_schema="str (sanitized markdown)",
    validator="sard.agent.util.sanitize_cultural_output (no leaked CoT) + text non-empty",
    timeout_seconds=15,
    retry_policy="no retry; deterministic synthesis on failure",
    fallback="offline deterministic cultural fallback (_generate_cultural_fallback_answer)",
    progress_stages=_stages("intent", "plan", "grounding", "validation", "verified_result"),
    support_status=SupportStatus.SUPPORTED,
    limitations="No equivalence claim to external workspace suites; offline answer is template-synthesis only, not LLM generation.",
    grounding="none required (greeting fast-path) or low-cost retrieval skipped",
    public_path_test="tests/test_chat_service.py + tests/agent/test_core_graph.py",
    related_capability_enum="simple_conversation",
))

# ---------------------------------------------------------------------------
# 2. Local RAG (saudi_cultural_factual)
# ---------------------------------------------------------------------------
_reg(CapabilitySpec(
    id=CapabilityId.LOCAL_RAG,
    name_ar="استرجاع معرفي محلي موثق",
    name_en="Local RAG Grounded QA",
    description_ar="إجابة موثقة مستندة إلى قاعدة المعرفة المحلية Zvec مع تسعير ثقة معاير.",
    description_en="Grounded QA over the local Zvec corpus with calibrated confidence and citation provenance.",
    arabic_examples=(
        "أين تقع الينابيع الحارة في المملكة؟",
        "ما قصة حي الطريف التاريخي؟",
    ),
    english_examples=(
        "Where are the hot springs in Saudi Arabia?",
        "Tell me about At-Turaif District",
    ),
    supported_inputs=("text",),
    supported_outputs=("text", "json"),
    required_tools=("sard.rag.service.RAGService", "sard.agent.cultural_router.CulturalRouter", "sard.rag.schemas.ScoreType"),
    provider_requirements="none — local Zvec index; optional embedding provider for ingest only",
    intermediate_schema="sard.rag.schemas.Citation / RAGAnswer; sard.schemas.isnad.IsnadChain",
    validator="sard.outputs.validation: citation provenance validation (accepted_claims, CIT-xxx) + ScoreType calibration",
    timeout_seconds=30,
    retry_policy="RAG search: 1 retry on transient; no LLM retry if offline",
    fallback="web_research when RAG confidence < 0.65 or out-of-corpus; else explicit abstention (Case E)",
    progress_stages=_stages("intent", "plan", "typed_content", "grounding", "validation", "verified_result"),
    support_status=SupportStatus.SUPPORTED,
    limitations="Grounded only in indexed corpus; out-of-corpus queries abstain rather than hallucinate. No equivalence to external workspace file suites.",
    grounding="local RAG (Zvec dense + FTS hybrid, rerank, 13-region reject, week freshness)",
    public_path_test="tests/rag/test_service.py, tests/rag/test_retrieve.py, tests/agent/test_isnad_planner.py",
    related_capability_enum="saudi_cultural_factual",
))

# ---------------------------------------------------------------------------
# 3. Web Research (verified_research)
# ---------------------------------------------------------------------------
_reg(CapabilitySpec(
    id=CapabilityId.WEB_RESEARCH,
    name_ar="بحث موثق ومحقق",
    name_en="Verified Web Research",
    description_ar="بحث ميداني موثق مع استخراج متوازٍ وتوثيق سلسلة الإسناد.",
    description_en="Parallel web search + extraction with Isnād citation chain and evidence limits.",
    arabic_examples=(
        "توثيق معتمد لتاريخ قصر المربع مع المراجع الرسمية",
        "بحث أكاديمي عن سوق عكاظ بمراجع دارة الملك عبد العزيز",
    ),
    english_examples=(
        "Verified research on Al-Masmak Palace with official citations",
        "Academic briefing on Souq Okaz with heritage authority references",
    ),
    supported_inputs=("text",),
    supported_outputs=("text", "pdf"),
    required_tools=("sard.agent.tools.cultural_tools.parallel_search", "sard.agent.tools.cultural_tools.parallel_extract", "sard.planner.pipeline.IsnadPlanner"),
    provider_requirements="none for mock/offline; live search/extract needs external web provider when available; LLM optional for synthesis",
    intermediate_schema="IsnadChain + VerifiedRenderInput (accepted_claims, CIT-xxx mapping)",
    validator="sard.outputs.validation.build_verified_render_input + CitationValidationError boundary; DiagramRenderer for timeline SVG",
    timeout_seconds=60,
    retry_policy="search: up to 2 parallel queries; extract: 1 targeted URL; degraded to RAG if unavailable",
    fallback="degraded_notice + evidence_limited status; citations filtered to accepted only",
    progress_stages=_stages("intent", "plan", "tools/inputs", "grounding", "rendering", "validation", "verified_result"),
    support_status=SupportStatus.LIMITED,
    limitations="Live web depends on network/provider; offline tests use injected search/extract doubles. Not a general web-browse agent; bounded to 2 search + 1 extract.",
    grounding="web (parallel_search/parallel_extract) + local RAG hybrid; Isnād scoring",
    public_path_test="tests/agent/test_isnad_planner.py (mock), tests/agent/test_cultural_agentic_tools.py::test_tool_conduct_verified_research",
    related_capability_enum="verified_research",
))

# ---------------------------------------------------------------------------
# 4. Document Extraction (multimodal document)
# ---------------------------------------------------------------------------
_reg(CapabilitySpec(
    id=CapabilityId.DOCUMENT_EXTRACTION,
    name_ar="استخراج الوثائق",
    name_en="Document Extraction",
    description_ar="استخراج نصوص وصفحات من PDF/DOCX/TXT/MD/CSV/JSON مع الحفاظ على الصفحات.",
    description_en="Page-aware extraction from PDF/DOCX/TXT/MD/CSV/JSON with layout preservation.",
    arabic_examples=(
        "استخرج النص من @manuscript-scan.pdf صفحة 1",
        "لخص هذه الوثيقة @document.pdf",
    ),
    english_examples=(
        "Extract text from @manuscript-scan.pdf page 1",
        "Summarize this document @report.pdf",
    ),
    supported_inputs=("document",),
    supported_outputs=("text", "json"),
    required_tools=("sard.agent.tools.multimodal_tools.extract_pdf_pages", "sard.agent.tools.multimodal_tools.qwen_vl_ocr_extract", "sard.agent.tools.multimodal_tools.extract_multimodal_context"),
    provider_requirements="none — PyMuPDF/pypdf offline; optional DASHSCOPE_API_KEY for enhanced OCR on scanned images",
    intermediate_schema="MultimodalExtractedItem (file_type=document, extracted_text, metadata {total_pages})",
    validator="extract_pdf_pages returns {engine, total_pages, pages, full_text}; qwen_vl_ocr_extract page_number guard",
    timeout_seconds=30,
    retry_policy="core extractor: no retry; API OCR: 1 retry then core_fallback",
    fallback="core_fallback: historical manuscript template text per page when no extractable text",
    progress_stages=_stages("intent", "tools/inputs", "typed_content", "grounding", "validation", "verified_result"),
    support_status=SupportStatus.SUPPORTED,
    limitations="Scanned-image PDFs need provider OCR for high accuracy; offline fallback returns template placeholder text, not hallucinated content. DOCX tracked as document input, not paginated.",
    grounding="multimodal (document) + citation [Media: filename]",
    public_path_test="tests/agent/test_multimodal.py::test_document_modality_manuscript_scan, tests/test_upload_multimodal.py",
    related_capability_enum="document_ocr",
))

# ---------------------------------------------------------------------------
# 5. OCR (document_ocr)
# ---------------------------------------------------------------------------
_reg(CapabilitySpec(
    id=CapabilityId.OCR,
    name_ar="تعرف ضوئي على الحروف (OCR)",
    name_en="OCR — Document Vision Extraction",
    description_ar="تعرف بصري على المخطوطات والوثائق الممسوحة ضوئياً مع تحويل ثنائي اللغة.",
    description_en="Vision OCR for scanned manuscripts, inscriptions, and document images with translation.",
    arabic_examples=(
        "استخرج وترجم نص الصفحة 1 من هذه المخطوطة @old-doc.jpg",
        "ما النص المكتوب في هذه الصورة @inscription.png ؟",
    ),
    english_examples=(
        "OCR page 1 of @manuscript-scan.pdf and translate",
        "What text is in @scanned-doc.png ?",
    ),
    supported_inputs=("image", "document"),
    supported_outputs=("text",),
    required_tools=("sard.agent.tools.multimodal_tools.qwen_vl_ocr_extract", "sard.agent.tools.multimodal_tools.inspect_image_core"),
    provider_requirements="none offline (template); enhanced accuracy requires DASHSCOPE_API_KEY (qwen-vl-max)",
    intermediate_schema="MultimodalExtractedItem (extracted_text, metadata, extraction_method=core|qwen_ocr)",
    validator="qwen_vl_ocr_extract page_number bounds; inspect_image_core format/mode/dimensions probe",
    timeout_seconds=30,
    retry_policy="1 provider retry; fallback to core_fallback with page-aware placeholder",
    fallback="core_fallback per page; ground answer with [Media: ...] and avoid filename hallucination",
    progress_stages=_stages("intent", "tools/inputs", "typed_content", "grounding", "validation", "verified_result"),
    support_status=SupportStatus.LIMITED,
    limitations="High-fidelity Arabic calligraphy/traditional script OCR is limited without vision provider; offline mode is image-probe + template, not true OCR.",
    grounding="vision OCR + document extraction; cited as [Media: ...]",
    public_path_test="tests/agent/test_multimodal.py::test_document_modality_manuscript_scan + FILE_MENTION_PATTERN tests",
    related_capability_enum="document_ocr",
))

# ---------------------------------------------------------------------------
# 6. Document Analysis (document_analysis)
# ---------------------------------------------------------------------------
_reg(CapabilitySpec(
    id=CapabilityId.DOCUMENT_ANALYSIS,
    name_ar="تحليل الوثائق",
    name_en="Document Analysis",
    description_ar="تحليل بنية الوثيقة وملخصات وتقسيم دلالي.",
    description_en="Document structure analysis, summarization, and semantic segmentation.",
    arabic_examples=(
        "حلل بنية هذا التقرير @report.pdf",
        "ما أهم النقاط في وثيقة الوقف @waqf.pdf ؟",
    ),
    english_examples=(
        "Analyze the structure of @report.pdf",
        "Summarize the key points of @waqf.pdf",
    ),
    supported_inputs=("document", "text"),
    supported_outputs=("text", "json"),
    required_tools=("sard.agent.tools.multimodal_tools.extract_pdf_pages", "sard.rag.chunking", "sard.outputs.validation.VerifiedRenderInput"),
    provider_requirements="none — chunking + local extractors; LLM optional for synthesis",
    intermediate_schema="TextBlock / FieldSupport segmentation + verified Answer subset",
    validator="build_verified_render_input (accepted_claims filter); _filter_blocks provenance guard",
    timeout_seconds=30,
    retry_policy="no retry; degrades to evidence_limited with partial accepted blocks",
    fallback="verified_answer_subset (drops unsupported blocks rather than stripping citations)",
    progress_stages=_stages("intent", "plan", "tools/inputs", "typed_content", "grounding", "validation", "verified_result"),
    support_status=SupportStatus.SUPPORTED,
    limitations="Analysis is evidence-bound; unsupported fields are dropped entirely (not hallucinated). No equivalence to external workspace deep-analyzer products.",
    grounding="local extraction + verified citation filtering",
    public_path_test="tests/outputs/test_step6_artifacts.py::test_citation_validation_rejects_unknown_and_removes_unsupported_fields",
    related_capability_enum="document_analysis",
))

# ---------------------------------------------------------------------------
# 7. Document Transformation (document_transformation)
# ---------------------------------------------------------------------------
_reg(CapabilitySpec(
    id=CapabilityId.DOCUMENT_TRANSFORMATION,
    name_ar="تحويل الوثائق",
    name_en="Document Transformation",
    description_ar="تحويل بين صيغ الوثائق (تحويل نص/هيكل إلى PDF/DOCX/JSON/CSV/TXT) عبر منسق المخرجات.",
    description_en="Transform between document formats (text/structure -> PDF/DOCX/JSON/CSV/TXT) via the artifact orchestrator.",
    arabic_examples=(
        "حول هذا النص إلى PDF منسق بعناوين",
        "أنشئ لي DOCX عن العمارة العسيرية",
    ),
    english_examples=(
        "Convert this text to a formatted PDF with sections",
        "Create a DOCX report on Asiri architecture",
    ),
    supported_inputs=("text", "document"),
    supported_outputs=("pdf", "docx", "json", "csv", "txt"),
    required_tools=("sard.outputs.orchestrator.ArtifactOrchestrator", "sard.outputs.orchestrator.ArtifactGeneratorRegistry.render_pdf/render_docx/render_json/render_csv/render_txt"),
    provider_requirements="none — deterministic generators (ReportLab, python-docx, stdlib csv/json)",
    intermediate_schema="ArtifactRequest {format, kind, title, topic, content_data, raw_text, sources}",
    validator="sard.outputs.validation.validate_artifact_bytes (PDF %PDF, DOCX ZIP OOXML, JSON parse, CSV schema, TXT utf-8)",
    timeout_seconds=30,
    retry_policy="single attempt; no retry on validation failure; storage link is atomic (If-None-Match *)",
    fallback="failed ArtifactResult with error_category (unsupported_format / invalid_signature / unparseable) and Arabic user message",
    progress_stages=_stages("intent", "plan", "typed_content", "grounding", "rendering", "validation", "storage", "verified_result"),
    support_status=SupportStatus.SUPPORTED,
    limitations="Transformation is render-from-typed-content, not binary-to-binary conversion (no PDF->DOCX direct). Layout is deterministic cultural template, not WYSIWYG edit.",
    grounding="typed content via orchestrator kind routing (document/presentation/calendar/diagram/recipe/card)",
    public_path_test="tests/test_artifact_pipeline.py (pdf/docx), tests/outputs/test_step6_artifacts.py",
    related_capability_enum="structured_output",
))

# ---------------------------------------------------------------------------
# 8. PDF (pdf)
# ---------------------------------------------------------------------------
_reg(CapabilitySpec(
    id=CapabilityId.PDF,
    name_ar="توليد PDF ثقافي",
    name_en="PDF Cultural Report Generation",
    description_ar="تقرير PDF عربي RTL منسق بجودة نشرية (A4، خطوط عربية، هوامش MOC 2026).",
    description_en="Publication-grade Arabic RTL cultural report PDF (A4, Arabic fonts, MOC 2026 palette).",
    arabic_examples=(
        "أنشئ لي PDF عن تاريخ نجد",
        "أعطني وصفة الجريش في بطاقة PDF",
        "تقرير PDF عن حي الطريف",
    ),
    english_examples=(
        "Create a PDF cultural briefing about AlUla",
        "Generate a PDF recipe card for Jareesh",
        "Make a PDF report about Diriyah",
    ),
    supported_inputs=("text", "document"),
    supported_outputs=("pdf",),
    required_tools=("sard.outputs.pdf_report.render_cultural_pdf_report", "sard.outputs.recipe_card.RecipeCardRenderer", "sard.outputs.memoir.MemoirCompiler", "sard.outputs.greeting_cards.GreetingCardStudio"),
    provider_requirements="none — ReportLab + arabic-reshaper + python-bidi + bundled fonts",
    intermediate_schema="CulturalReport / RecipeOrCraftCard / FamilyMemoirBooklet / GreetingCard -> PDF bytes",
    validator="validate_artifact_bytes(pdf): %PDF signature, pypdf strict parse, pages>=1; storage checksum verify",
    timeout_seconds=30,
    retry_policy="single deterministic render; no retry on validation failure",
    fallback="failed ArtifactResult; orchestrator surfaces storage_error vs renderer_exception",
    progress_stages=_stages("intent", "plan", "typed_content", "grounding", "rendering", "validation", "storage", "verified_result"),
    support_status=SupportStatus.SUPPORTED,
    limitations="Covers cultural reports, recipe/craft cards, memoirs, greeting cards via orchestrator kind dispatch. Not a general HTML->PDF or scanned-PDF editor.",
    grounding="orchestrator kind=document|recipe|memoir|card; sources embedded as citations",
    public_path_test="tests/test_artifact_pipeline.py::test_pdf_report_generation_and_storage, tests/outputs/test_agentic_outputs.py (recipe/greeting/memoir), tests/outputs/test_step6_artifacts.py",
    related_capability_enum="structured_output",
))

# ---------------------------------------------------------------------------
# 9. DOCX (docx)
# ---------------------------------------------------------------------------
_reg(CapabilitySpec(
    id=CapabilityId.DOCX,
    name_ar="توليد DOCX ثقافي",
    name_en="DOCX Cultural Report Generation",
    description_ar="مستند Word عربي RTL بعناوين وأقسام وخلاصات.",
    description_en="Arabic RTL Word document with sections, badges, and takeaways.",
    arabic_examples=(
        "أريد تقرير DOCX عن العمارة العسيرية",
        "وثيقة وورد عن الفنون الصخرية في حائل",
    ),
    english_examples=(
        "Create a DOCX report on Najdi architecture",
        "Generate a Word document about AlUla heritage",
    ),
    supported_inputs=("text",),
    supported_outputs=("docx",),
    required_tools=("sard.outputs.office_docx.render_cultural_docx_report",),
    provider_requirements="none — python-docx offline; no provider",
    intermediate_schema="ArtifactRequest{format=docx, kind=document} -> OOXML bytes",
    validator="validate_artifact_bytes(docx): PK ZIP, [Content_Types].xml, _rels/.rels, word/document.xml present and XML-parseable",
    timeout_seconds=30,
    retry_policy="single deterministic render",
    fallback="failed ArtifactResult (invalid_docx / unparseable) with Arabic message",
    progress_stages=_stages("intent", "plan", "typed_content", "rendering", "validation", "storage", "verified_result"),
    support_status=SupportStatus.SUPPORTED,
    limitations="Deterministic template only; no tracked-changes, no embedded objects, no user-supplied .docx style import.",
    grounding="typed content sections + takeaways + sources list",
    public_path_test="tests/test_artifact_pipeline.py::test_docx_report_generation_and_storage",
    related_capability_enum="structured_output",
))

# ---------------------------------------------------------------------------
# 10. PPTX (presentation_deck)
# ---------------------------------------------------------------------------
_reg(CapabilitySpec(
    id=CapabilityId.PPTX,
    name_ar="عرض تقديمي PowerPoint",
    name_en="Presentation Deck (PPTX) Generation",
    description_ar="عرض تقديمي عريض 16:9 ببطاقات مقارنة وجدول زمني وخلاصات.",
    description_en="Widescreen 16:9 cultural briefing deck with comparison cards, timeline, and takeaways.",
    arabic_examples=(
        "جهز عرض PPTX عن يوم التأسيس",
        "عرض تقديمي عن تراث الدرعية التاريخي",
    ),
    english_examples=(
        "Make a PowerPoint about Saudi Foundation Day",
        "Create a briefing deck on Diriyah heritage",
    ),
    supported_inputs=("text",),
    supported_outputs=("pptx",),
    required_tools=("sard.outputs.office.PresentationGenerator", "sard.outputs.office.create_cultural_briefing_deck"),
    provider_requirements="none — python-pptx offline; deterministic slide assembly (5-slide pattern)",
    intermediate_schema="PresentationDeck {deck_id, title, slides: SlideContent[]} -> PPTX bytes",
    validator="validate_artifact_bytes(pptx): PK ZIP, [Content_Types].xml, ppt/presentation.xml, ppt/slides/slide1.xml present and XML-parseable",
    timeout_seconds=45,
    retry_policy="single deterministic build",
    fallback="failed ArtifactResult; orchestrator kind=presentation ensures filename .pptx",
    progress_stages=_stages("intent", "plan", "typed_content", "grounding", "rendering", "validation", "storage", "verified_result"),
    support_status=SupportStatus.SUPPORTED,
    limitations="Fixed 5-slide cultural briefing template; not a general slide-designer with animations or embedded media. Content length capped by deterministic rendering.",
    grounding="orchestrator kind=presentation; intent extraction via _PRESENTATION_HINT",
    public_path_test="tests/test_artifact_pipeline.py::test_pptx_presentation_generation_and_storage, tests/outputs/test_agentic_outputs.py::test_pptx_deck_generation",
    related_capability_enum="presentation_deck",
))

# ---------------------------------------------------------------------------
# 11. Itinerary (itinerary_planning)
# ---------------------------------------------------------------------------
_reg(CapabilitySpec(
    id=CapabilityId.ITINERARY,
    name_ar="تخطيط الرحلات / برنامج رحلة",
    name_en="Itinerary Planning",
    description_ar="برنامج رحلة موثق بمنتجات PDF/ICS/Nص خام مع تحقق استشهادات وحجوزات زمنية.",
    description_en="Verified itinerary with PDF/ICS/raw-text products, citation filtering, and time-blocking.",
    arabic_examples=(
        "صمم لي برنامج يومين في المنطقة الشرقية",
        "برنامج رحلة ثلاثة أيام في الرياض بصيغة PDF وICS",
    ),
    english_examples=(
        "Plan a 2-day itinerary in the Eastern Province",
        "Create a 3-day Riyadh trip as PDF and ICS",
    ),
    supported_inputs=("text",),
    supported_outputs=("text", "pdf", "ics"),
    required_tools=("sard.agent.graph.build_graph (understand->plan->retrieve->compose->verify->render)", "sard.outputs.schemas.Itinerary", "sard.outputs.artifacts.ArtifactManager"),
    provider_requirements="none for offline verify+render; LLM optional for compose synthesis (fallback deterministic plan)",
    intermediate_schema="Itinerary {title, summary, days: ItineraryDay[stops: ItineraryStop], sources, generated_at tz-aware} + TextBlock citations",
    validator="Itinerary.validate_citations + build_verified_render_input (accepted_claims, CIT-xxx); CalendarRenderError for missing_dates",
    timeout_seconds=90,
    retry_policy="LangGraph: verify->compose retry capped at compose_max_retries=2 with feedback; render: no retry but independent per-artifact (PDF failure does not block ICS)",
    fallback="partial outcome: raw_text + PDF always attempted; ICS skipped with missing_dates until caller supplies explicit_dates; degraded_notice added",
    progress_stages=_stages("intent", "plan", "typed_content", "grounding", "rendering", "validation", "storage", "verified_result"),
    support_status=SupportStatus.SUPPORTED,
    limitations="Dates are never invented; ICS requires explicit_dates or dated days. Verification drops unsupported fields rather than hallucinating them. No live booking API.",
    grounding="LangGraph grounded retrieval (RAG + web bounded) -> verified render input -> per-artifact HMAC-free but checksum covers",
    public_path_test="tests/outputs/test_step6_artifacts.py (complete_three_artifact, missing_dates, overlap, degraded), tests/agent/test_core_graph.py",
    related_capability_enum="itinerary_planning",
))

# ---------------------------------------------------------------------------
# 12. ICS (calendar_sync)
# ---------------------------------------------------------------------------
_reg(CapabilitySpec(
    id=CapabilityId.ICS,
    name_ar="مزامنة التقويم التراثي (ICS)",
    name_en="Heritage Calendar Sync (ICS)",
    description_ar="تقويم تراثي ثنائي هجري-ميلادي مع مواسم فلكية وفعاليات وروابط Google Calendar.",
    description_en="Dual Hijri-Gregorian heritage calendar with astronomical seasons, festivals, and Google Calendar links.",
    arabic_examples=(
        "تقويم المربعانية وموسم سهيل",
        "مزامنة تقويم لحظات العلا الثقافية",
        "أضف مهرجان الجنادرية إلى التقويم",
    ),
    english_examples=(
        "Sync the Suhail and Wasm seasons to my calendar",
        "Add AlUla Moments to calendar as ICS",
        "Generate calendar for Foundation Day",
    ),
    supported_inputs=("text",),
    supported_outputs=("ics",),
    required_tools=("sard.outputs.calendar_sync.HeritageCalendarSync", "sard.outputs.calendar_sync.HERITAGE_EVENTS_DATABASE", "icalendar.Calendar"),
    provider_requirements="none — deterministic icalendar generation; no provider",
    intermediate_schema="HeritageCalendarEvent {id, title_ar/en, category, start_date/end_date, hijri, region, cultural_prep_tips} -> ical bytes",
    validator="validate_artifact_bytes(ics): BEGIN:VCALENDAR/END:VCALENDAR, icalendar.Calendar.from_ical, VCALENDAR name, DTSTART TZID=Asia/Riyadh",
    timeout_seconds=10,
    retry_policy="no retry; empty filters -> missing_filters clarification, filtered no-match -> 0 events (no silent first-4)",
    fallback="no silent HERITAGE_EVENTS_DATABASE[:4] fallback; empty/filtered no-match returns explicit no-match (failed/no_match) with clarification",
    progress_stages=_stages("intent", "plan", "typed_content", "rendering", "validation", "storage", "verified_result"),
    support_status=SupportStatus.SUPPORTED,
    limitations="Bounded to curated HERITAGE_EVENTS_DATABASE (festivals + astronomical seasons + national holidays); not a general user-calendar CRUD. No live public holiday API.",
    grounding="heritage calendar database search (query/category/region/month) + deterministic ICS emit",
    public_path_test="tests/test_artifact_pipeline.py::test_ics_calendar_generation_and_storage, tests/outputs/test_agentic_outputs.py::test_calendar_sync_and_ics, tests/outputs/test_step6_artifacts.py (calendar parsing)",
    related_capability_enum="calendar_sync",
))

# ---------------------------------------------------------------------------
# 13. Diagram (diagram_generation)
# ---------------------------------------------------------------------------
_reg(CapabilitySpec(
    id=CapabilityId.DIAGRAM,
    name_ar="مخططات وخرائط تدفق ثقافية",
    name_en="Cultural Diagrams & Flowcharts",
    description_ar="مخططات SVG متجهية للإتيكيت والتسلسل الزمني والمقارنات.",
    description_en="Vector SVG diagrams for etiquette flows, timelines, and comparison matrices.",
    arabic_examples=(
        "مخطط آداب المجلس",
        "جدول زمني لتأسيس الدولة السعودية",
        "مقارنة العمارة النجدية والحجازية",
    ),
    english_examples=(
        "Generate a majlis etiquette flowchart",
        "Create a timeline of the Saudi state formation",
        "Comparison matrix of Najdi vs Hijazi architecture",
    ),
    supported_inputs=("text",),
    supported_outputs=("svg", "png"),
    required_tools=("sard.outputs.diagrams.DiagramRenderer", "sard.outputs.diagrams.CulturalDiagram", "sard.outputs.orchestrator.ArtifactGeneratorRegistry.render_svg_or_png"),
    provider_requirements="none — pure Python SVG emit; PNG via stdlib struct+zlib (no Pillow required for orch PNG path)",
    intermediate_schema="CulturalDiagram {diagram_type, title, nodes: FlowNode[] / timeline_milestones: TimelineMilestone[] / comparison_aspects: ComparisonAspect[]}",
    validator="validate_artifact_bytes(svg): well-formed XML, <svg> root, no <!DOCTYPE/ENTITY/script/javascript:>; PNG: 8-byte sig, IHDR CRC",
    timeout_seconds=15,
    retry_policy="single render; no retry",
    fallback="PNG path used when format=png; SVG sanitize rejects unsafe XML with unsafe_xml category",
    progress_stages=_stages("intent", "plan", "typed_content", "rendering", "validation", "storage", "verified_result"),
    support_status=SupportStatus.SUPPORTED,
    limitations="SVG is vector template renderer, not a freeform diagram-to-image model. Matrices fixed to RTL + MOC palette. No interactive editor with external workspace equivalence.",
    grounding="orchestrator kind=diagram; diagram presets (majlis/business) via agentic tools",
    public_path_test="tests/outputs/test_agentic_outputs.py::test_diagram_svg_rendering, tests/agent/test_cultural_agentic_tools.py::test_tool_simulate_etiquette_protocol",
    related_capability_enum="diagram_generation",
))

# ---------------------------------------------------------------------------
# 14. Image/Card (greeting_card + craft/recipe visual)
# ---------------------------------------------------------------------------
_reg(CapabilitySpec(
    id=CapabilityId.IMAGE_CARD,
    name_ar="بطاقات تهنئة وبطاقات تراثية مصورة",
    name_en="Greeting Cards & Heritage Image Cards",
    description_ar="بطاقات تهنئة احتفالية وبطاقات وصفات/حرف مصورة (SVG + PDF).",
    description_en="Festive greeting cards and recipe/craft visual cards (SVG + PDF).",
    arabic_examples=(
        "بطاقة تهنئة بيوم التأسيس باسم عبد العزيز",
        "بطاقة وصفة الجريش التراثية",
        "صمم لي كارت معايدة لرمضان",
    ),
    english_examples=(
        "Create a Foundation Day greeting card for Sara",
        "Recipe card for Jareesh as PDF",
        "Ramadan greeting card with calligraphy",
    ),
    supported_inputs=("text",),
    supported_outputs=("svg", "pdf", "png"),
    required_tools=("sard.outputs.greeting_cards.GreetingCardStudio", "sard.outputs.greeting_cards.compose_greeting_card", "sard.outputs.recipe_card.RecipeCardRenderer"),
    provider_requirements="none — SVG+PDF deterministic; PNG via orchestrator header-only fallback (no image generation model)",
    intermediate_schema="GreetingCard {occasion, recipient, sender, poetic_verse, theme} / RecipeOrCraftCard {ingredients_or_materials, steps, cultural_story}",
    validator="SVG: valid xml/svg root; PDF: %PDF + pypdf pages; PNG: IHDR path validated separately",
    timeout_seconds=20,
    retry_policy="single render; card ID UUID prevents collisions",
    fallback="PNG requested returns Sard header PNG (paper/clay/gold) plus preview metadata; SVG remains primary visual artifact",
    progress_stages=_stages("intent", "plan", "typed_content", "grounding", "rendering", "validation", "storage", "verified_result"),
    support_status=SupportStatus.SUPPORTED,
    limitations="Visual cards are SVG template art with calligraphy-style typography, not generative image diffusion (no Stable Diffusion/DALL-E). PNG is header placeholder, not card rasterization.",
    grounding="orchestrator kind=card/recipe with Sard tokens",
    public_path_test="tests/outputs/test_agentic_outputs.py::test_greeting_card_studio, ::test_recipe_and_craft_card_pdf, tests/agent/test_cultural_agentic_tools.py (greeting/recipe)",
    related_capability_enum="greeting_card",
))

# ---------------------------------------------------------------------------
# 15-17. JSON / CSV / TXT (structured outputs)
# ---------------------------------------------------------------------------
_reg(CapabilitySpec(
    id=CapabilityId.JSON_OUTPUT,
    name_ar="مخرجات JSON منظمة",
    name_en="Structured JSON Output",
    description_ar="حمولة JSON منظمة مع تحقق صارم.",
    description_en="Structured JSON payload with strict parse-constant validation.",
    arabic_examples=(
        "أعطني بيانات البرنامج كـ JSON",
        "صدّر النتائج بصيغة json",
    ),
    english_examples=(
        "Return the itinerary as JSON",
        "Export results as json",
    ),
    supported_inputs=("text",),
    supported_outputs=("json",),
    required_tools=("sard.outputs.orchestrator.ArtifactGeneratorRegistry.render_json", "json (stdlib)"),
    provider_requirements="none",
    intermediate_schema="ArtifactRequest.content_data dict -> JSON bytes (utf-8, ensure_ascii=False)",
    validator="validate_artifact_bytes(json): utf-8 decode + json.loads with parse_constant trap; fails on constants like NaN/Infinity",
    timeout_seconds=5,
    retry_policy="no retry",
    fallback="unparseable -> ArtifactValidationError(category=unparseable) -> failed ArtifactResult",
    progress_stages=_stages("intent", "typed_content", "rendering", "validation", "storage", "verified_result"),
    support_status=SupportStatus.SUPPORTED,
    limitations="JSON is typed content serialization, not JSON-Schema constrained generation; schema echo comes from caller-supplied content_data.",
    grounding="orchestrator render_json; sources kept separately",
    public_path_test="tests/capabilities/test_capability_registry.py (orchestrator json roundtrip) — new",
    related_capability_enum="structured_output",
))
_reg(CapabilitySpec(
    id=CapabilityId.CSV_OUTPUT,
    name_ar="مخرجات CSV جدوَلية",
    name_en="Tabular CSV Output",
    description_ar="جدول CSV بصيغة أعمدة موحدة.",
    description_en="Tabular CSV with uniform column schema per row.",
    arabic_examples=(
        "صدّر البرنامج كـ CSV",
        "جدول المواسم بصيغة csv",
    ),
    english_examples=(
        "Export the schedule as CSV",
        "Generate CSV for heritage events",
    ),
    supported_inputs=("text",),
    supported_outputs=("csv",),
    required_tools=("sard.outputs.orchestrator.ArtifactGeneratorRegistry.render_csv", "csv (stdlib)"),
    provider_requirements="none",
    intermediate_schema="ArtifactRequest.content_data.rows: List[Dict[str,str]] -> CSV utf-8",
    validator="validate_artifact_bytes(csv): header row present, all rows share width, no empty output",
    timeout_seconds=5,
    retry_policy="no retry",
    fallback="empty_output/invalid_schema categories surface as failed result",
    progress_stages=_stages("intent", "typed_content", "rendering", "validation", "storage", "verified_result"),
    support_status=SupportStatus.SUPPORTED,
    limitations="CSV is flat DictWriter serialization; no nested objects, no Excel formulas. Dict rows must share keys.",
    grounding="orchestrator render_csv",
    public_path_test="tests/capabilities/test_capability_registry.py (orchestrator csv roundtrip) — new",
    related_capability_enum="structured_output",
))
_reg(CapabilitySpec(
    id=CapabilityId.TXT_OUTPUT,
    name_ar="نص خام TXT",
    name_en="Raw Text Output (TXT)",
    description_ar="نص عربي خام بترميز UTF-8.",
    description_en="Raw Arabic text artifact with UTF-8 encoding.",
    arabic_examples=(
        "احفظ الإجابة كـ txt",
        "صدّر النص الخام",
    ),
    english_examples=(
        "Save answer as txt",
        "Export raw text",
    ),
    supported_inputs=("text",),
    supported_outputs=("txt",),
    required_tools=("sard.outputs.orchestrator.ArtifactGeneratorRegistry.render_txt",),
    provider_requirements="none",
    intermediate_schema="raw_text or topic string -> utf-8 bytes",
    validator="validate_artifact_bytes(txt): utf-8 decode succeeds and non-empty",
    timeout_seconds=5,
    retry_policy="no retry",
    fallback="empty_output on zero bytes",
    progress_stages=_stages("intent", "typed_content", "rendering", "validation", "storage", "verified_result"),
    support_status=SupportStatus.SUPPORTED,
    limitations="TXT preserves raw_text verbatim; no Markdown reflow or RTL shaping validation beyond utf-8.",
    grounding="orchestrator render_txt; also Step 6 raw artifact (render_raw_text) for itineraries",
    public_path_test="tests/capabilities/test_capability_registry.py (orchestrator txt roundtrip) — new",
    related_capability_enum="structured_output",
))

# ---------------------------------------------------------------------------
# 18. Audio (audio_transcription)
# ---------------------------------------------------------------------------
_reg(CapabilitySpec(
    id=CapabilityId.AUDIO,
    name_ar="تفريغ وتحليل صوتي",
    name_en="Audio Transcription & Analysis",
    description_ar="تفريغ صوتي مع طوابع زمنية وتسميات متحدثين عبر Qwen Omni/ASR مع مسار محلي احتياطي.",
    description_en="Audio transcription with timestamps and speaker labels via Qwen Omni/ASR with local probe fallback.",
    arabic_examples=(
        "فرّغ هذا التسجيل @oral-history.mp3 مع أسماء المتحدثين",
        "@recording.m4a تفريغ صوتي بطوابع زمنية",
    ),
    english_examples=(
        "Transcribe @oral-history.mp3 with speaker labels and timestamps",
        "Extract text from @interview.wav",
    ),
    supported_inputs=("audio",),
    supported_outputs=("text", "json"),
    required_tools=("sard.agent.tools.multimodal_tools.probe_audio_core", "sard.agent.tools.multimodal_tools.qwen_audio_transcribe", "sard.agent.tools.multimodal_tools.extract_multimodal_context"),
    provider_requirements="none for offline probe+template; enhanced transcription requires DASHSCOPE_API_KEY (qwen-omni / ASR). No live streaming ASR.",
    intermediate_schema="MultimodalExtractedItem (file_type=audio, extracted_text, transcription {language, duration_seconds, segments[{start,end,speaker,text}]})",
    validator="probe_audio_core: WAV header parse or byte-size heuristic; transcription segments schema (start/end/speaker/text) when present",
    timeout_seconds=60,
    retry_policy="provider: no retry beyond single call; missing key -> capability_unavailable, call error -> failed/provider_error",
    fallback="capability_unavailable when DASHSCOPE_API_KEY missing (offline probe metadata only, no transcript); provider_error when call fails",
    progress_stages=_stages("intent", "tools/inputs", "typed_content", "grounding", "validation", "verified_result"),
    support_status=SupportStatus.LIMITED,
    limitations="Offline ASR unavailable without provider (no fabricated transcript). Probe returns duration/format only. Provider path is request/response, not streaming.",
    grounding="multimodal audio grounding: citations as [Media: filename] with segments quoted verbatim",
    public_path_test="tests/agent/test_multimodal.py::test_audio_modality_oral_history",
    related_capability_enum="audio_transcription",
))

# ---------------------------------------------------------------------------
# 19. Vision (vision)
# ---------------------------------------------------------------------------
_reg(CapabilitySpec(
    id=CapabilityId.VISION,
    name_ar="تحليل بصري ثقافي",
    name_en="Vision — Cultural Artifact Analysis",
    description_ar="تحليل صور القطع التراثية والمخطوطات والمباني مع تحديد مادي وتأريخ ثقافي.",
    description_en="Visual analysis of cultural artifacts, manuscripts, and architecture with material/id and dating.",
    arabic_examples=(
        "ما هذه القطعة @artifact-photo.jpg وحدثني عن أصلها",
        "حلل هذه الصورة @heritage_site.png",
    ),
    english_examples=(
        "Identify @artifact-photo.jpg and its cultural origin",
        "Analyze this heritage image @site.jpg",
    ),
    supported_inputs=("image",),
    supported_outputs=("text", "json"),
    required_tools=("sard.agent.tools.multimodal_tools.inspect_image_core (PIL)", "sard.agent.tools.multimodal_tools.qwen_vl_vision_analyze", "sard.agent.tools.multimodal_tools.extract_multimodal_context"),
    provider_requirements="none offline (PIL probe + template description); enhanced Qwen-VL analysis requires DASHSCOPE_API_KEY",
    intermediate_schema="MultimodalExtractedItem (file_type=image, description, visual_features {format,resolution,object_type}, metadata {width,height,exif})",
    validator="inspect_image_core: PIL format/mode/size probe; vision_analyze returns {source, description, status}; no base64 exfiltration beyond DashScope when key set",
    timeout_seconds=45,
    retry_policy="provider: single POST to DashScope; missing key -> capability_unavailable, non-200/exception -> failed/provider_error",
    fallback="capability_unavailable when DASHSCOPE_API_KEY missing (PIL probe only); provider_error when call fails — never fabricated description",
    progress_stages=_stages("intent", "tools/inputs", "typed_content", "grounding", "validation", "verified_result"),
    support_status=SupportStatus.LIMITED,
    limitations="Offline vision is local dimension/EXIF probe, not semantic recognition. Provider VQA needs API key; no video temporal streaming in offline mode.",
    grounding="multimodal image grounding with [Media: filename]; file content is base64'd only to provider when key present",
    public_path_test="tests/agent/test_multimodal.py::test_image_modality_artifact_photo, tests/test_upload_multimodal.py::test_upload_valid_image",
    related_capability_enum="vision",
))

# ---------------------------------------------------------------------------
# 20. 3D (3d_inspection)
# ---------------------------------------------------------------------------
_reg(CapabilitySpec(
    id=CapabilityId.THREE_D,
    name_ar="فحص النماذج ثلاثية الأبعاد",
    name_en="3D Inspection",
    description_ar="فحص هندسي لشبكات PLY/OBJ/gltf وملفات NIfTI الحجمية.",
    description_en="Geometric inspection for PLY/OBJ/gltf meshes and NIfTI volumetric scans.",
    arabic_examples=(
        "افحص هذا المجسم @artifact.ply وكم عدد الرؤوس",
        "@model.obj أبعاد الشبكة والوجوه",
    ),
    english_examples=(
        "Inspect @artifact.ply mesh dimensions and face count",
        "Check @model.obj vertices and faces",
    ),
    supported_inputs=("3d",),
    supported_outputs=("text", "json"),
    required_tools=("sard.agent.tools.multimodal_tools.inspect_3d_file", "sard.agent.tools.multimodal_tools.inspect_nifti_file"),
    provider_requirements="none — pure Python header parsing; optional nibabel for NIfTI voxel/affine; no generation model (offline limited)",
    intermediate_schema="MultimodalExtractedItem (file_type=3d/nifti, metadata {vertices,faces,shape,voxel_sizes}, description)",
    validator="PLY header: element vertex/face parse; OBJ: v/f counts; NIfTI: shape/data_type/affine; no binary mesh decode beyond header",
    timeout_seconds=20,
    retry_policy="no retry; returns parse_error in metadata on malformed file",
    fallback="header_summary fallback (e.g. glTF transmission format notice) when full parse unavailable",
    progress_stages=_stages("intent", "tools/inputs", "typed_content", "grounding", "verified_result"),
    support_status=SupportStatus.LIMITED,
    limitations="limited inspection only (vertices/faces/header), offline header parse, not 3D generation, not mesh repair, not volumetric rendering. NIfTI without nibabel is raw notice only; fallback header_summary used.",
    grounding="multimodal 3D grounding; not a live 3D viewer — metadata cited as [Media: ...]",
    public_path_test="tests/agent/test_multimodal.py::test_3d_modality_inspection (PLY) + inspect_nifti_file unit",
    related_capability_enum="3d_inspection",
))


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------
def get_spec(capability_id: str) -> Optional[CapabilitySpec]:
    return CAPABILITY_REGISTRY.get(capability_id)


def list_specs(status: Optional[SupportStatus] = None) -> List[CapabilitySpec]:
    if status is None:
        return list(CAPABILITY_REGISTRY.values())
    return [s for s in CAPABILITY_REGISTRY.values() if s.support_status == status]


def registry_as_dict() -> Dict[str, Dict[str, Any]]:
    """JSON-serializable view (enum values stringified)."""
    out: Dict[str, Dict[str, Any]] = {}
    for k, spec in CAPABILITY_REGISTRY.items():
        d = asdict(spec)
        # asdict renders SupportStatus enum; normalize to str
        d["support_status"] = spec.support_status.value
        out[k] = d
    return out


def validate_registry_completeness() -> List[str]:
    """Return list of error strings; empty means registry is complete."""
    errors: List[str] = []
    for spec in CAPABILITY_REGISTRY.values():
        if not spec.id:
            errors.append(f"empty id for spec {spec!r}")
        if not spec.arabic_examples or not spec.english_examples:
            errors.append(f"{spec.id}: missing bilingual examples")
        if not spec.supported_inputs:
            errors.append(f"{spec.id}: missing supported_inputs")
        if not spec.supported_outputs:
            errors.append(f"{spec.id}: missing supported_outputs")
        if not spec.required_tools:
            errors.append(f"{spec.id}: missing required_tools")
        if not spec.validator:
            errors.append(f"{spec.id}: missing validator")
        if spec.timeout_seconds <= 0:
            errors.append(f"{spec.id}: non-positive timeout")
        if not spec.progress_stages:
            errors.append(f"{spec.id}: empty progress_stages")
        if spec.support_status == SupportStatus.SUPPORTED and not spec.public_path_test:
            errors.append(f"{spec.id}: SUPPORTED but no public_path_test")
        # Ensure stages are subset of PIPELINE_PATTERN
        for stage in spec.progress_stages:
            if stage not in PIPELINE_PATTERN:
                errors.append(f"{spec.id}: unknown stage {stage!r}")
    # Ensure all expected CapabilityIds are covered
    for cid in CapabilityId:
        if cid.value not in CAPABILITY_REGISTRY:
            errors.append(f"missing CapabilityId {cid.value} in registry")
    return errors


def supported_ids() -> List[str]:
    return [s.id for s in list_specs(SupportStatus.SUPPORTED)]


def limited_ids() -> List[str]:
    return [s.id for s in list_specs(SupportStatus.LIMITED)]


def unsupported_ids() -> List[str]:
    return [s.id for s in list_specs(SupportStatus.UNSUPPORTED)]


# ---------------------------------------------------------------------------
# Pydantic view (optional) — mirrors the dataclass for docs/openapi generation
# ---------------------------------------------------------------------------
try:
    from pydantic import BaseModel, Field

    class CapabilitySpecModel(BaseModel):
        """Pydantic mirror of CapabilitySpec for validation / openapi."""

        id: str
        name_ar: str
        name_en: str
        description_ar: str
        description_en: str
        arabic_examples: List[str]
        english_examples: List[str]
        supported_inputs: List[str]
        supported_outputs: List[str]
        required_tools: List[str]
        provider_requirements: str
        intermediate_schema: str
        validator: str
        timeout_seconds: int = Field(gt=0)
        retry_policy: str
        fallback: str
        progress_stages: List[str]
        support_status: str
        limitations: str
        grounding: str
        public_path_test: str
        related_capability_enum: str = ""

        @classmethod
        def from_dataclass(cls, spec: "CapabilitySpec") -> "CapabilitySpecModel":
            d = asdict(spec)
            d["support_status"] = spec.support_status.value
            return cls(**d)

except ImportError:  # pragma: no cover - pydantic optional
    CapabilitySpecModel = None  # type: ignore


# ---------------------------------------------------------------------------
# Integration helper — maps a StructuredIntent to relevant registry specs
# ---------------------------------------------------------------------------
def specs_for_intent(intent: Any) -> List[CapabilitySpec]:
    """Return registry specs relevant to a StructuredIntent.

    Integration worker can call this to drive intent -> plan -> render.
    Never raises; returns at least the conversation spec.
    """
    try:
        from sard.agent.capability_routing import Capability as RoutingCapability  # lazy to avoid cycle
    except Exception:
        return [CAPABILITY_REGISTRY[CapabilityId.CONVERSATION]]

    domain = getattr(intent, "domain_capability", None)
    formats = tuple(getattr(intent, "requested_formats", ()) or ())
    modalities = tuple(getattr(intent, "input_modalities", ()) or ())

    # Build candidate ids from domain routing
    domain_map: Dict[str, str] = {
        RoutingCapability.SIMPLE_CONVERSATION.value: CapabilityId.CONVERSATION,
        RoutingCapability.SAUDI_CULTURAL_FACTUAL.value: CapabilityId.LOCAL_RAG,
        RoutingCapability.VERIFIED_RESEARCH.value: CapabilityId.WEB_RESEARCH,
        RoutingCapability.DOCUMENT_OCR.value: CapabilityId.DOCUMENT_EXTRACTION,
        RoutingCapability.VISION.value: CapabilityId.VISION,
        RoutingCapability.AUDIO_TRANSCRIPTION.value: CapabilityId.AUDIO,
        RoutingCapability.THREE_D_INSPECTION.value: CapabilityId.THREE_D,
        RoutingCapability.DOCUMENT_ANALYSIS.value: CapabilityId.DOCUMENT_ANALYSIS,
        RoutingCapability.PRESENTATION_DECK.value: CapabilityId.PPTX,
        RoutingCapability.RECIPE_CARD.value: CapabilityId.IMAGE_CARD,
        RoutingCapability.CALENDAR_SYNC.value: CapabilityId.ICS,
        RoutingCapability.DIAGRAM_GENERATION.value: CapabilityId.DIAGRAM,
        RoutingCapability.GREETING_CARD.value: CapabilityId.IMAGE_CARD,
        RoutingCapability.ITINERARY_PLANNING.value: CapabilityId.ITINERARY,
        RoutingCapability.STRUCTURED_OUTPUT.value: CapabilityId.JSON_OUTPUT,
    }
    result: List[CapabilitySpec] = []
    seen: set[str] = set()

    def add(cid: str) -> None:
        if cid not in seen and cid in CAPABILITY_REGISTRY:
            seen.add(cid)
            result.append(CAPABILITY_REGISTRY[cid])

    if domain is not None:
        mapped = domain_map.get(getattr(domain, "value", str(domain)))
        if mapped:
            add(mapped)
            # Web research complements local RAG when grounding is mixed
            if mapped == CapabilityId.LOCAL_RAG and any(m in modalities for m in ("document", "image", "audio")):
                add(CapabilityId.DOCUMENT_ANALYSIS)

    # Format-driven expansions (artifact generation)
    fmt_map = {"pdf": CapabilityId.PDF, "docx": CapabilityId.DOCX, "pptx": CapabilityId.PPTX, "ics": CapabilityId.ICS, "svg": CapabilityId.DIAGRAM, "png": CapabilityId.DIAGRAM, "json": CapabilityId.JSON_OUTPUT, "csv": CapabilityId.CSV_OUTPUT, "txt": CapabilityId.TXT_OUTPUT}
    for f in formats:
        if f in fmt_map:
            add(fmt_map[f])
        if f == "pdf" and any(k in str(getattr(intent, "extracted_topic", "")).lower() for k in ["وصفة", "recipe"]):
            add(CapabilityId.IMAGE_CARD)

    # Modality-driven
    if "audio" in modalities:
        add(CapabilityId.AUDIO)
    if "image" in modalities:
        add(CapabilityId.VISION)
    if "document" in modalities and CapabilityId.DOCUMENT_EXTRACTION not in seen:
        add(CapabilityId.DOCUMENT_EXTRACTION)
    if "3d" in modalities:
        add(CapabilityId.THREE_D)

    if not result:
        add(CapabilityId.CONVERSATION)
    return result
