# Sard Capability Matrix — Public Contract

> Source of truth: `sard/capability_registry.py` (canonical) + `sard/agent/capability_registry/__init__.py` (shim).
> Pattern: `intent → plan → tools/inputs → typed content → grounding → rendering → validation → storage → verified result`
> Policy: **Never label `supported` without a public-path test** that exercises `generation + validation + storage` offline and passes.
> No claim of ChatGPT Work / Claude Cowork parity.  All limitations are precise and test-backed.

## Support status legend

| Status | Meaning |
|--------|---------|
| `supported` | Offline deterministic path exists and a public-path test proves it (render+validate+store). |
| `limited` | Offline probe/fallback exists; enhanced accuracy needs optional provider (`DASHSCOPE_API_KEY` or web provider). Public tests cover fallback/mock. |
| `unsupported` | No offline renderer and no public-path test. |
| `experimental` | Behind flag, not part of verified public contract. |

## Capability vs. status

| ID | Name (AR / EN) | Inputs → Outputs | Support | Timeout | Validator | Public-path test | Routing enum |
|----|----------------|------------------|---------|---------|-----------|------------------|--------------|
| `conversation` | محادثة عامة / General Conversation | `text` → `text` | `supported` | 15s | `sanitize_cultural_output` | `tests/test_chat_service.py`, `tests/agent/test_core_graph.py` | `simple_conversation` |
| `local_rag` | استرجاع محلي موثق / Local RAG | `text` → `text,json` | `supported` | 30s | `build_verified_render_input`, CIT-xxx | `tests/rag/test_service.py`, `tests/rag/test_retrieve.py` | `saudi_cultural_factual` |
| `web_research` | بحث موثق / Verified Research | `text` → `text,pdf` | `limited` | 60s | `build_verified_render_input` + timeline SVG | `tests/agent/test_isnad_planner.py` (mock), `::test_tool_conduct_verified_research` | `verified_research` |
| `document_extraction` | استخراج الوثائق / Document Extraction | `document` → `text,json` | `supported` | 30s | `extract_pdf_pages` page guard | `tests/agent/test_multimodal.py::test_document_modality`, `tests/test_upload_multimodal.py` | `document_ocr` |
| `ocr` | تعرف ضوئي OCR / OCR | `image,document` → `text` | `limited` | 30s | `qwen_vl_ocr_extract` page bounds + PIL probe | `tests/agent/test_multimodal.py::test_document_modality` | `document_ocr` |
| `document_analysis` | تحليل الوثائق / Document Analysis | `document,text` → `text,json` | `supported` | 30s | `_filter_blocks` + `accepted_claims` | `tests/outputs/test_step6_artifacts.py::test_citation_validation_*` | `document_analysis` |
| `document_transformation` | تحويل الوثائق / Document Transformation | `text,document` → `pdf,docx,json,csv,txt` | `supported` | 30s | `validate_artifact_bytes` per format | `tests/test_artifact_pipeline.py`, `tests/outputs/test_step6_artifacts.py` | `structured_output` |
| `pdf` | PDF ثقافي / PDF Report | `text,document` → `pdf` | `supported` | 30s | `validate_artifact_bytes(pdf)` `%PDF` + pypdf pages | `tests/test_artifact_pipeline.py::test_pdf_*`, `tests/outputs/test_agentic_outputs.py::*pdf` | `structured_output` |
| `docx` | DOCX ثقافي / DOCX Report | `text` → `docx` | `supported` | 30s | `validate_artifact_bytes(docx)` ZIP OOXML | `tests/test_artifact_pipeline.py::test_docx_*` | `structured_output` |
| `pptx` | عرض تقديمي / PPTX Deck | `text` → `pptx` | `supported` | 45s | `validate_artifact_bytes(pptx)` ZIP + `ppt/slides/slide1.xml` | `tests/test_artifact_pipeline.py::test_pptx_*`, `tests/outputs/test_agentic_outputs.py::test_pptx_*` | `presentation_deck` |
| `itinerary` | برنامج رحلة / Itinerary | `text` → `text,pdf,ics` | `supported` | 90s | `Itinerary.validate_citations` + `CalendarRenderError` | `tests/outputs/test_step6_artifacts.py` (complete/missing_dates/overlap/degraded) | `itinerary_planning` |
| `ics` | تقويم تراثي ICS / Heritage ICS | `text` → `ics` | `supported` | 10s | `validate_artifact_bytes(ics)` `BEGIN:VCALENDAR` + `icalendar` parse | `tests/test_artifact_pipeline.py::test_ics_*`, `tests/outputs/test_agentic_outputs.py::test_calendar_*` | `calendar_sync` |
| `diagram` | مخططات ثقافية / Diagrams | `text` → `svg,png` | `supported` | 15s | `validate_artifact_bytes(svg)` XML + `unsafe_xml` guard | `tests/outputs/test_agentic_outputs.py::test_diagram_*`, `tests/agent/test_cultural_agentic_tools.py::test_tool_simulate_*` | `diagram_generation` |
| `image_card` | بطاقات مصورة / Image Cards | `text` → `svg,pdf,png` | `supported` | 20s | SVG XML + PDF pypdf | `tests/outputs/test_agentic_outputs.py::test_greeting_*`, `::test_recipe_*` | `greeting_card` |
| `json_output` | JSON منظم / JSON | `text` → `json` | `supported` | 5s | `validate_artifact_bytes(json)` strict parse | `tests/capabilities/test_capability_registry.py` (new) | `structured_output` |
| `csv_output` | CSV جدولي / CSV | `text` → `csv` | `supported` | 5s | `validate_artifact_bytes(csv)` schema width | `tests/capabilities/test_capability_registry.py` (new) | `structured_output` |
| `txt_output` | نص خام TXT / TXT | `text` → `txt` | `supported` | 5s | `validate_artifact_bytes(txt)` utf-8 | `tests/capabilities/test_capability_registry.py` (new) | `structured_output` |
| `audio` | تفريغ صوتي / Audio | `audio` → `text,json` | `limited` | 60s | `probe_audio_core` WAV + segments schema | `tests/agent/test_multimodal.py::test_audio_*` | `audio_transcription` |
| `vision` | تحليل بصري / Vision | `image` → `text,json` | `limited` | 45s | `inspect_image_core` PIL probe | `tests/agent/test_multimodal.py::test_image_*`, `tests/test_upload_multimodal.py` | `vision` |
| `three_d` | فحص 3D / 3D Inspection | `3d` → `text,json` | `limited` | 20s | PLY/OBJ header parse; NIfTI shape | `tests/agent/test_multimodal.py::test_3d_*` | `3d_inspection` |

## Bilingual examples (representative)

| ID | Arabic example | English example |
|----|----------------|-----------------|
| `conversation` | "مرحباً، من أنت؟" | "Hello, who are you?" |
| `local_rag` | "أين تقع الينابيع الحارة؟" | "Where are the hot springs in Saudi Arabia?" |
| `web_research` | "توثيق معتمد لتاريخ قصر المربع مع المراجع" | "Verified research on Al-Masmak with citations" |
| `document_extraction` | "استخرج النص من @manuscript-scan.pdf صفحة 1" | "Extract text from @manuscript-scan.pdf page 1" |
| `ocr` | "استخرج وترجم نص هذه المخطوطة @old-doc.jpg" | "OCR page 1 of @manuscript-scan.pdf and translate" |
| `pdf` | "أنشئ لي PDF عن تاريخ نجد" | "Create a PDF briefing about AlUla" |
| `docx` | "أريد تقرير DOCX عن العمارة العسيرية" | "Create a DOCX on Najdi architecture" |
| `pptx` | "جهز عرض PPTX عن يوم التأسيس" | "Make a PowerPoint about Foundation Day" |
| `itinerary` | "برنامج رحلة يومين في الشرقية مع PDF و ICS" | "Plan a 2-day itinerary in Eastern Province" |
| `ics` | "مزامنة تقويم لحظات العلا" | "Sync AlUla Moments to calendar as ICS" |
| `diagram` | "مخطط آداب المجلس" | "Generate a majlis etiquette flowchart" |
| `image_card` | "بطاقة تهنئة بيوم التأسيس باسم سارة" | "Recipe card for Jareesh as PDF" |
| `audio` | "فرّغ @oral-history.mp3 مع أسماء المتحدثين" | "Transcribe @oral-history.mp3 with speaker labels" |
| `vision` | "ما هذه القطعة @artifact-photo.jpg" | "Identify @artifact-photo.jpg" |
| `three_d` | "@artifact.ply كم عدد الرؤوس" | "Inspect @artifact.ply mesh" |

## Required tools / intermediate schema / validator (condensed)

| ID | Required tools | Intermediate schema | Validator | Provider |
|----|----------------|---------------------|-----------|----------|
| `pdf` | `pdf_report.render_cultural_pdf_report`, `RecipeCardRenderer`, `MemoirCompiler`, `GreetingCardStudio` | `CulturalReport / RecipeOrCraftCard / FamilyMemoirBooklet` | `validate_artifact_bytes(pdf)` `%PDF` + pypdf strict | none (ReportLab offline) |
| `docx` | `office_docx.render_cultural_docx_report` | `ArtifactRequest(docx)` | ZIP `[Content_Types].xml` + `word/document.xml` | none (python-docx) |
| `pptx` | `office.PresentationGenerator` | `PresentationDeck` | ZIP `ppt/presentation.xml` + `ppt/slides/slide1.xml` | none (python-pptx) |
| `ics` | `calendar_sync.HeritageCalendarSync` | `HeritageCalendarEvent[]` | `validate_artifact_bytes(ics)` + icalendar parse | none (icalendar) |
| `diagram` | `diagrams.DiagramRenderer` | `CulturalDiagram` | `validate_artifact_bytes(svg)` no `<!DOCTYPE/ENTITY/script:` | none |
| `image_card` | `greeting_cards.GreetingCardStudio` | `GreetingCard` | SVG XML + PDF pypdf | none; PNG is header placeholder |
| `json/csv/txt` | `orchestrator.ArtifactGeneratorRegistry` | `ArtifactRequest.content_data` | `validate_artifact_bytes(json/csv/txt)` | none |
| `document_extraction` | `multimodal_tools.extract_pdf_pages` | `MultimodalExtractedItem(document)` | page guard + full_text | none; OCR optional DASHSCOPE |
| `audio` | `multimodal_tools.probe_audio_core` + `qwen_audio_transcribe` | `MultimodalExtractedItem(audio)` | WAV header / segments schema | limited: `DASHSCOPE_API_KEY` for real ASR |
| `vision` | `inspect_image_core` + `qwen_vl_vision_analyze` | `MultimodalExtractedItem(image)` | PIL format/mode/size | limited: `DASHSCOPE_API_KEY` for VQA |
| `three_d` | `inspect_3d_file` + `inspect_nifti_file` | `MultimodalExtractedItem(3d)` | PLY vertex/face counts | none; nibabel optional |

## Timeout / Retry / Fallback

| ID | Timeout | Retry | Fallback |
|----|---------|-------|----------|
| `conversation` | 15s | none | deterministic cultural fallback |
| `local_rag` | 30s | 1× RAG transient | abstain (Case E) or trigger `web_research` |
| `web_research` | 60s | 2× search + 1× extract | `evidence_limited` + filtered citations |
| `document_extraction` / `ocr` | 30s | 1× provider then truthful failure | missing key → `capability_unavailable`; call error → `failed/provider_error` (no fabricated text) |
| `pdf/docx/pptx` | 30–45s | single deterministic build | `failed ArtifactResult` with Arabic message + `error_category` |
| `itinerary` | 90s | verify→compose ×2 | partial: `raw_text+PDF` survive; `ICS` skipped with `missing_dates` |
| `ics` | 10s | none; empty filters → `missing_filters`, filtered no-match → 0 events | no silent `HERITAGE_EVENTS_DATABASE[:4]`; explicit clarification |
| `diagram/image_card` | 15–20s | none | `unsafe_xml` reject or PNG header fallback |
| `json/csv/txt` | 5s | none; chat fast-path skips RAG/web | `unparseable/empty_output/invalid_schema` as failed result; pure data requests render in ms |
| `audio` | 60s | none; missing key → `capability_unavailable` | offline probe metadata only (no transcript); call error → `failed/provider_error` |
| `vision` | 45s | single DashScope POST; missing key → `capability_unavailable` | PIL probe only offline; call error → `failed/provider_error` (never fabricated) |
| `three_d` | 20s | none | `header_summary` notice |

## Progress stages per capability (subset of unified pattern)

Unified pattern: `intent → plan → tools/inputs → typed content → grounding → rendering → validation → storage → verified result`

- `conversation`: `intent → plan → grounding → validation → verified_result`
- `local_rag`: `intent → plan → typed_content → grounding → validation → verified_result`
- `web_research`: `intent → plan → tools/inputs → grounding → rendering → validation → verified_result`
- `document_extraction/ocr/audio/vision/three_d`: `intent → tools/inputs → typed_content → grounding → validation → verified_result` (plus `rendering/storage` if artifact requested)
- `document_analysis`: `intent → plan → tools/inputs → typed_content → grounding → validation → verified_result`
- `pdf/docx/pptx/ics/diagram/image_card/json/csv/txt`: full `intent → plan → typed_content → grounding → rendering → validation → storage → verified_result` via orchestrator
- `itinerary`: full pipeline via LangGraph `understand→plan→retrieve→compose→verify→render` then artifact manager validation+storage

## Precise limitations & non-parity disclosure

- **No ChatGPT Work / Claude Cowork parity claimed.** Sard is a curated cultural assistant with deterministic artifact templates (MOC 2026 palette), not a general workspace with live file editing, spreadsheets, or collaborative canvas.
- **Web research bounded:** at most `2× search + 1× extract`; no autonomous browsing, no JavaScript rendering.
- **OCR/audio/vision** are `limited` offline: enhanced accuracy requires `DASHSCOPE_API_KEY` (`qwen-vl-max` / Qwen-Omni ASR). Offline mode is probe+template, not production STT/VQA.
- **3D** is inspection only (PLY/OBJ headers, NIfTI voxel header). No generation, mesh repair, or viewer.
- **DOCX/PPTX** are deterministic templates, not WYSIWYG round-trippers; no track-changes or media embedding.
- **PNG** card raster is header-only placeholder; primary visual is SVG.
- **Itinerary ICS** never invents dates; missing dates → `skipped` with `missing_dates`.
- All `supported` claims are tied to a listed public-path test that passes offline. No provider-only capability is marked `supported`.
- Inputs/outputs outside listed sets (e.g., `wav` streaming ASR, `gltf` live render, `xlsx` finance) are `unsupported`.

## Public-contract implications (integration worker)

- Integration worker **consumes** `sard.capability_registry` (or shim) to drive `intent→plan→render`. It must not duplicate the table — always import the registry as source of truth.
- Do **not** make competing edits to shared files (`sard/api/server.py`, `sard/agent/graph.py`, etc.). Instead, propose required shared-file deltas via `docs/handoffs/capabilities.md` and let the integration worker finalize.
- Before marking a capability `supported`, add a public-path test under `tests/capabilities/` or existing `tests/` that validates `orchestrator.generate_artifact` + `validate_artifact_bytes` + `store.get_bytes` and include it in `CapabilitySpec.public_path_test`.
- `supported` promotion requires offline green `uv run pytest tests/capabilities -v` and the referenced artifact bytes pass `validation.py` validators.

## Audit summary (2026-08-31, herdr/capabilities @ 26cb94e)

| Group | Audited | Supported | Limited | Unsupported |
|-------|---------|-----------|---------|-------------|
| Conversation / Local RAG | conversation, local RAG | 2 | 0 | 0 |
| Web research | web research | 0 | 1 | 0 |
| Document pipeline | extraction, OCR, analysis, transformation | 3 | 1 | 0 |
| Artifact formats | PDF, DOCX, PPTX, ICS, SVG/PNG, JSON/CSV/TXT, diagram, image/card | 8 + 3 = 11 | 0 | 0 |
| Itinerary | itinerary (LangGraph) | 1 | 0 | 0 |
| Multimodal | audio, vision, 3D | 0 | 3 | 0 |
| **Total** | **20 capability IDs** | **15 supported** | **5 limited** | **0 unsupported (none currently hidden; audio/vision/3D are limited not unsupported)** |

> If a future audit finds a capability with no offline renderer and no fallback, mark it `unsupported` and document why in this file plus the registry `limitations`.

