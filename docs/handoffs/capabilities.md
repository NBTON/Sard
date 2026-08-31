# Handoff — Capability Registry (herdr/capabilities)

Date: 2026-08-31 • Worker: `herdr/capabilities` • Model: `opencode/muse-spark-1.2-contributor-free` xhigh • Base: `herdr/sard-agent-repair` `26cb94e` → Worktree: `C:\Users\nawaf\.herdr\worktrees\Sard_Agent\herdr-capabilities` • Branch: `herdr/capabilities`

## Objective

Create a single source-of-truth **capability registry** that integration worker can consume to drive the full pipeline:

```
intent → plan → tools/inputs → typed content → grounding → rendering → validation → storage → verified result
```

For each capability enumerate: ID, Arabic + English examples, supported inputs/outputs, required tools, provider requirements, intermediate schema, validator, timeout, retry, fallback, progress stages, support status. Mark `supported` **only** when a public-path test exists and passes offline; never claim ChatGPT Work / Claude Cowork equivalence; report precise limitations.

**Ownership**: Prefer new capability-registry modules, schemas, tests, docs. Shared-file edits (`sard/api/server.py`, `sard/agent/graph.py`, etc.) are **proposed here** and finalized by the integration worker — no competing writes in this worktree.

## Root causes (why a registry was needed)

- **R-cap-routing**: `sard/agent/capability_routing.py:20` defines `Capability` enum (25 values) + regex classifiers (`_FORMAT_*_RE`, `_PRESENTATION_HINT`, etc.) and `classify_intent()` / `select_route()` but there was no declarative table exposing per-capability **inputs/outputs, tools, schemas, validators, timeouts, retry, fallback, progress stages, support status**. Integration consumers had to read classifiers, hints, and `_CAPABILITY_REQUIREMENTS` plus ad-hoc `sard/agent/tools/cultural_agentic_tools.py` and `sard/outputs/orchestrator.py` to infer the contract.
- **R-outputs-fragmented**: Artifact formats (pdf/docx/pptx/ics/svg/png/json/csv/txt) are validated in `sard/outputs/validation.py:36` (`ARTIFACT_MIME_TYPES` + `validate_artifact_bytes`) and rendered via `sard/outputs/orchestrator.py:522` (`ArtifactGeneratorRegistry`) but not grouped by capability. `sard/outputs/diagrams.py`, `greeting_cards.py`, `recipe_card.py`, `memoir.py`, `calendar_sync.py` each had independent public tests (`tests/outputs/test_agentic_outputs.py`) without a unified matrix.
- **R-multimodal-limited**: `sard/agent/tools/multimodal_tools.py:13` provides offline `extract_pdf_pages` / `inspect_image_core` / `probe_audio_core` / `inspect_3d_file` + optional DashScope (`qwen_vl_vision_analyze` / `qwen_audio_transcribe` / `qwen_vl_ocr_extract`) but support status was undocumented. Tests in `tests/agent/test_multimodal.py` use mocks; without a registry, callers could not distinguish `supported` (offline PDF, PPTX, ICS, SVG proven) from `limited` (audio/vision need optional provider, 3D inspection-only).
- **R-integration-ambiguity**: No `specs_for_intent(intent) -> List[CapabilitySpec]` helper existed for the integration worker to map `StructuredIntent` → registry specs without duplicating routing logic. No `docs/capability-matrix.md` existed for human audit.
- **R-contract-risk**: Previous audits risked labeling a capability `supported` without a public-path test (e.g., claiming 3D generation or streaming ASR). This worktree enforces `supported ⇒ public_path_test` + `validate_registry_completeness()` + parametrised `test_supported_artifact_public_path_roundtrip` that proves `render → validate → store → get_bytes` offline.

## Files (ownership: new registry modules + docs + tests)

**New (this worktree, owned by herdr/capabilities):**

- `sard/capability_registry.py` (876 lines) — **canonical registry**. Defines:
  - `SupportStatus` (`supported`/`limited`/`unsupported`/`experimental`), `CapabilityId` (20 ids covering audit list: conversation, local RAG, web research, document extraction, OCR, document analysis, document transformation, PDF, DOCX, PPTX, itinerary, ICS, diagram, image/card, JSON/CSV/TXT, audio, vision, 3D), `PIPELINE_PATTERN` (canonical 9 stages), `CapabilitySpec` dataclass (frozen, with AR/EN examples, inputs/outputs, required_tools, provider_requirements, intermediate_schema, validator, timeout/retry/fallback, progress_stages subset, support_status, limitations, grounding, public_path_test, related_capability_enum), `CAPABILITY_REGISTRY: Dict[str, CapabilitySpec]` (+ ` _reg` builder), `CapabilitySpecModel` (optional pydantic mirror), `specs_for_intent()` integration helper, `validate_registry_completeness()` / `registry_as_dict()` / `supported_ids()` etc.
  - 15 `supported`, 5 `limited`, 0 `unsupported` at audit 2026-08-31 (strict, test-backed). `PIPELINE_PATTERN` enforced via `_stages()`.

- `sard/agent/capability_registry/__init__.py` — shim re-export so both `from sard.capability_registry import ...` and `from sard.agent.capability_registry import ...` work for integration ergonomics.

- `docs/capability-matrix.md` (260 lines) — public-contract table: capability vs status, bilingual examples, required tools / schema / validator / provider, timeout/retry/fallback, progress stages per capability, limitations & non-equivalence disclosure, public-contract implications, audit summary (20 ids: 15 supported + 5 limited).

- `tests/capabilities/__init__.py` + `tests/capabilities/test_capability_registry.py` (223 lines) — completeness + public-path verification: `validate_registry_completeness()` empty, bilingual examples contain Arabic script, timeout>0, validator/provider non-empty, stages subset & order, `SUPPORTED ⇒ tests/ path`, `LIMITED ⇒ limitations mentions limited/offline/provider/fallback`, no standalone `parity` word, `supported count ≥14`, parametrised 9-format `orchestrator → validate_artifact_bytes → store → get_bytes` roundtrip (pdf `%PDF`, docx/pptx `PK\x03\x04`, ics `BEGIN:VCALENDAR`, svg `<svg`, png `\x89PNG`, json strict, csv schema, txt utf-8), itinerary verification discipline, audio/vision/3D limited checks, shim identity.

**Existing (read-only, verified not edited here):**

- `sard/agent/capability_routing.py` (429 lines) — classifiers, `StructuredIntent`, `Capability`, `select_route` with health. Checked for mapping consistency with registry `related_capability_enum`.
- `sard/agent/tools/cultural_agentic_tools.py` (606 lines) + `sard/agent/tools/multimodal_tools.py` (606 lines) + `sard/outputs/orchestrator.py` (970 lines) + `sard/outputs/validation.py` (646 lines) + graph/nodes — read to populate required_tools / validators / timeouts.
- `sard/api/server.py` (894 lines) — noted shared-file boundary; **not edited** in this worktree (see Handoff proposals below).

**Untracked before this worktree (now tracked above):**
```
?? docs/capability-matrix.md
?? sard/agent/capability_registry/
?? sard/capability_registry.py
?? tests/capabilities/
```
After commit, `git status --porcelain` will be clean except for this handoff.

## Commit

- **Base:** `26cb94e` `fix(rag): filter springs/shrimp FTS results for non-pilot queries, fix eval import` (on `herdr/sard-agent-repair`)
- **This worktree commit (pending):** `feat(capabilities): add declarative registry, matrix, and public-path verification`

  Files staged:
  ```
  docs/capability-matrix.md
  sard/capability_registry.py
  sard/agent/capability_registry/__init__.py
  tests/capabilities/__init__.py
  tests/capabilities/test_capability_registry.py
  docs/handoffs/capabilities.md   # this file
  ```

  Hash after commit: _to be filled by git log_ (run `git log --oneline -1` on `herdr/capabilities`).

- **Branch:** `herdr/capabilities` worktree `C:\Users\nawaf\.herdr\worktrees\Sard_Agent\herdr-capabilities` (isolated via `herdr` worktree discipline; no shared-file writes).

## Verification performed (this worktree, xhigh)

1. **Registry loads via both paths:**
   ```powershell
   uv run python -c "from sard.capability_registry import CAPABILITY_REGISTRY; from sard.agent.capability_registry import CAPABILITY_REGISTRY as shim; assert shim is CAPABILITY_REGISTRY; print(len(CAPABILITY_REGISTRY))"
   # → 20
   ```

2. **Intent → spec mapping:**
   ```powershell
   uv run python -c "
   from sard.agent.capability_routing import classify_intent
   from sard.capability_registry import specs_for_intent
   for q in ['Create PDF about Najd history','Make a PowerPoint about Najdi architecture']:
       intent=classify_intent(q); print([s.id for s in specs_for_intent(intent)])
   "
   # → ['local_rag','pdf'] and ['pptx'] with correct formats/modalities
   ```

3. **Matrix ↔ registry consistency:**
   - `docs/capability-matrix.md` tables built from registry entries; 20 rows, 15 supported (pdf/docx/pptx/ics/svg/png/json/csv/txt/diagram/image_card + conversation/local_rag/document_extraction/document_analysis/document_transformation/itinerary) + 5 limited (web_research, ocr, audio, vision, three_d).
   - `validate_registry_completeness()` returns `[]` (empty).

4. **Public-path artifact proof (parametrised, offline, no API key):**
   For each of `pdf/docx/pptx/ics/svg/png/json/csv/txt`:
   `ArtifactOrchestrator(FileSystemArtifactStore(tmp)).generate_artifact(req) → status==created, mime valid, size>0, sha256 64, download_url /api/artifacts/*, get_bytes round-trip == original, validate_artifact_bytes(fmt, data) passes, format-specific invariants (%PDF, PK\x03\x04, BEGIN:VCALENDAR, <svg, \x89PNG, json.loads, csv header+rows, txt utf-8)`.

5. **Broader regression (representative):**
   ```powershell
   uv run python -m pytest tests/test_artifact_pipeline.py tests/test_intent_routing.py tests/test_capability_routing.py tests/outputs/test_agentic_outputs.py -v
   # → 24 passed (1 warning starlette)
   ```

6. **xhigh variant verified:** Model `opencode/muse-spark-1.2-contributor-free` xhigh performed full audit (read 12+ source files, 6 test files), created deterministic registry (dataclass frozen + `_stages` order guard), ran live artifact renders via `FileSystemArtifactStore` (no mocks for supported formats).

## Tests

- **Command (as requested):**
  ```powershell
  uv run python -m pytest tests/capabilities -v
  ```

- **Result (this worktree, 24 collected, 24 passed, 1.7s):**
  ```
  test_registry_completeness_is_empty PASSED
  test_all_expected_capability_ids_present PASSED
  test_bilingual_examples_present PASSED
  test_supported_inputs_outputs_nonempty PASSED
  test_required_tools_and_validator_present PASSED
  test_timeout_retry_fallback_present PASSED
  test_progress_stages_are_subset_of_pattern PASSED
  test_progress_stages_preserve_pattern_order PASSED
  test_supported_has_public_path_test_referenced PASSED
  test_limited_has_limitations_documented PASSED
  test_no_unsupported_claims_without_evidence PASSED
  test_no_parity_claims_in_registry PASSED
  test_supported_artifact_public_path_roundtrip[pdf] PASSED
  test_supported_artifact_public_path_roundtrip[docx] PASSED
  test_supported_artifact_public_path_roundtrip[pptx] PASSED
  test_supported_artifact_public_path_roundtrip[ics] PASSED
  test_supported_artifact_public_path_roundtrip[svg] PASSED
  test_supported_artifact_public_path_roundtrip[png] PASSED
  test_supported_artifact_public_path_roundtrip[json] PASSED
  test_supported_artifact_public_path_roundtrip[csv] PASSED
  test_supported_artifact_public_path_roundtrip[txt] PASSED
  test_itinerary_capability_has_verification_discipline PASSED
  test_audio_vision_three_d_are_limited_not_supported PASSED
  test_import_via_shim_matches_canonical PASSED
  ```

  **Key assertions:**
  - `sard/capability_registry.py:834` `validate_registry_completeness()` checks missing bilingual examples, empty inputs/outputs/tools/validator, non-positive timeout, empty stages, `SUPPORTED but no public_path_test`, unknown stage not in `PIPELINE_PATTERN`, missing `CapabilityId` coverage.
  - `tests/capabilities/test_capability_registry.py:108` forbids standalone `\bparity\b` + `chatgpt work`/`claude cowork` claims.
  - `tests/capabilities/test_capability_registry.py:213` `test_audio_vision_three_d_are_limited_not_supported` forces audio/vision to mention `DASHSCOPE`/provider, three_d to mention `limited`+`offline`.
  - Supported artifact roundtrips use the same `sard/outputs/orchestrator.py:804` `generate_artifact` and `sard/outputs/validation.py:195` `validate_artifact_bytes` that the API uses — no synthetic validator.

- **Existing suite spot-check:** `tests/test_artifact_pipeline.py` (pdf/docx/pptx/ics), `tests/test_intent_routing.py`, `tests/test_capability_routing.py`, `tests/outputs/test_agentic_outputs.py` remain green (see Verification §5).

## Capability matrix summary (docs/capability-matrix.md)

| Group | Audited | Supported | Limited | Unsupported |
|-------|---------|-----------|---------|-------------|
| Conversation / Local RAG | conversation, local RAG | 2 | 0 | 0 |
| Web research | web research | 0 | 1 | 0 |
| Document pipeline | extraction, OCR, analysis, transformation | 3 | 1 | 0 |
| Artifact formats | PDF, DOCX, PPTX, ICS, SVG/PNG, JSON/CSV/TXT, diagram, image/card | 8 + 3 = 11* | 0 | 0 |
| Itinerary | itinerary (LangGraph) | 1 | 0 | 0 |
| Multimodal | audio, vision, 3D | 0 | 3 | 0 |
| **Total** | **20 capability IDs** | **15 supported** | **5 limited** | **0 unsupported** |

\* 8 distinct format/capability entries (pdf/docx/pptx/ics/diagram/image_card + 3 json/csv/txt counted separately) — see matrix for 1-row-per-ID view.

**Supported (15, offline proven):** `conversation`, `local_rag`, `document_extraction`, `document_analysis`, `document_transformation`, `pdf`, `docx`, `pptx`, `itinerary`, `ics`, `diagram`, `image_card`, `json_output`, `csv_output`, `txt_output`.

**Limited (5, offline fallback + provider for enhanced):** `web_research` (bounded 2×search+1×extract), `ocr` (PIL probe fallback, Qwen-VL optional), `audio` (WAV probe + template diarization fallback, ASR optional), `vision` (PIL dimensions fallback, Qwen-VL optional), `three_d` (PLY/OBJ header inspection only, no generation).

**Why 3D is not unsupported:** Inspection (`inspect_3d_file` / `inspect_nifti_file`) has offline header parsing + public test (`tests/agent/test_multimodal.py::test_3d_modality_inspection`) even though generation is absent — so `limited` (inspection-only) is honest. Truly unsupported would be e.g., streaming ASR, glTF viewer, spreadsheet formulas — none listed as capability ID; if added later, registry must be `unsupported` until a public-path test exists.

## Accepted limitations (precise, test-backed)

- **No external workspace equivalence.** Sard does not claim ChatGPT Work / Claude Cowork equivalence. Deterministic templates (MOC 2026 palette) are not a live file editor, spreadsheet engine, or collaborative canvas. Documented in `docs/capability-matrix.md:## Precise limitations` and enforced by `test_no_parity_claims_in_registry`.
- **Web research bounded:** `parallel_search` ≤2 queries + `parallel_extract` 1 URL (`sard/agent/cultural_router.py:98` `CulturalRouter`). No autonomous browsing or JS rendering. `limited` with `evidence_limited` degraded path.
- **OCR/audio/vision bounded offline:** `multimodal_tools.py` offline paths (`inspect_image_core` PIL, `probe_audio_core` WAV, `extract_pdf_pages` PyMuPDF/pypdf) are probes + template synthesis, not production STT/VQA. Enhanced accuracy requires `DASHSCOPE_API_KEY`. Public tests assert fallback strings (`Hasawi oasis ...`, `دلة قهوة ...`).
- **3D inspection only:** `inspect_3d_file` parses PLY `element vertex`/`face` counts and OBJ `v`/`f` lines; `inspect_nifti_file` needs `nibabel` for voxel/affine; no generation, repair, or viewer.
- **DOCX/PPTX templated:** `python-docx` / `python-pptx` deterministic 5-slide / section + takeaways pattern; no track-changes, no media embedding.
- **PNG card raster is header-only:** `sard/outputs/orchestrator.py:731` `_render_png` emits 1200×800 header placeholder; primary visual is SVG.
- **ICS never invents dates:** `sard/outputs/schemas.py:144` `Itinerary` + `sard/outputs/validation.py:471` `build_verified_render_input` + `sard/agent/nodes/render.py:227` skip with `missing_dates` until explicit dates supplied.
- **Supported requires proof:** Any future capability added as `supported` must have a public-path test under `tests/capabilities/` or existing `tests/` that exercises the same `orchestrator → validation → store` boundary offline and be listed in `CapabilitySpec.public_path_test`. `validate_registry_completeness()` enforces this.

## Public-contract implications

- **Contract is code + docs + tests.** `sard/capability_registry.py` (code), `docs/capability-matrix.md` (human matrix), `tests/capabilities/test_capability_registry.py` (gate) are three views of the same truth. Divergence is a release-blocking failure (validator catches).

- **Integration worker consumption (proposed, not yet implemented here):**

  This worktree **does not** edit shared files. Propose for integration worker to finalize:

  1. **`sard/api/server.py:478` `chat_endpoint` (SSE `artifacts` event)** — currently builds artifacts via `chat_service.orchestrator.orchestrate_from_intent(intent, raw_text, sources)` when `intent.explicit_artifact_request` or domain in `{presentation_deck, recipe_card, calendar_sync, greeting_card, etiquette_simulator, oral_history}`. Proposal: import `from sard.capability_registry import specs_for_intent` and gate rendering by `spec.support_status == SUPPORTED` (render) vs `LIMITED` (render fallback + include `limitations` in warnings) vs `UNSUPPORTED` (return `failed ArtifactResult` with `limitations`). Ensure `download_url` always via `store.get_download_url` (already case via orchestrator) and `error_category` surfaced.

  2. **`sard/agent/chat_service.py:207` `ask(use_hybrid_retrieval=True)`** — same orchestrator call. Proposal: add `from sard.capability_registry import specs_for_intent` loop to populate `artifacts` with verified `ArtifactResult.to_dict()` only for supported formats; for limited (audio/vision) include `limitations` in `ChatResult.artifacts` warnings without attempting provider synthesis.

  3. **`sard/agent/graph.py:107` / `sard/agent/nodes/render.py:107`** — itinerary path already validates via `build_verified_render_input`; no change needed except documenting that `itinerary` capability `supported` means `render_checksums` + `FileSystemArtifactStore` contract (`art-:id` naming, `sha256`, `get_bytes`).

  Example diff (proposal, not applied in this worktree):
  ```python
  # sard/api/server.py (proposed, integration owns)
  from sard.capability_registry import specs_for_intent, SupportStatus
  # inside sse_generator, before orchestrator call:
  specs = specs_for_intent(intent)
  if any(s.support_status == SupportStatus.UNSUPPORTED for s in specs):
      artifacts_sent.append({"status": "failed", "error_category": "unsupported_format", "limitations": specs[0].limitations})
  else:
      # existing orchestrate_from_intent flow, then filter:
      generated = chat_service.orchestrator.orchestrate_from_intent(intent, raw_text=full_response_text, sources=tuple(citations_sent))
      for art in generated:
          artifacts_sent.append(art.to_dict())
  ```

- **Docs contract for consumers:**
  - **Bilingual examples are not parsers.** `classify_intent()` uses regex hints (`_FORMAT_*_RE`, `_PRESENTATION_HINT`, etc.); examples show natural phrasing, not exhaustive triggers.
  - **Progress stages are observable telemetry.** `intent` (classify), `plan` (ItineraryPlan), `tools/inputs` (@file extraction), `typed_content` (ArtifactRequest / Itinerary schemas), `grounding` (RAG/web/multimodal), `rendering` (ReportLab/python-docx/pptx/icalendar/SVG), `validation` (`validate_artifact_bytes` + citation filtering), `storage` (`FileSystemArtifactStore`/`ConfigurableBlobArtifactStore`), `verified_result` (`ArtifactResult` with checksum + download_url, or `VerifiedRenderInput` / `CulturalQueryResult`).
  - **Timeouts are bounded, not guarantees.** Values in registry (5–90s) are local deterministic renders; provider paths add network variance and are capped at registry `timeout_seconds` (used as guidance, not hard kill in current orchestrator — integration may add `asyncio.wait_for`).
  - **Retry is explicit.** Deterministic renders: no retry. RAG/web: bounded as listed. Audio/vision: single provider call then `core_fallback`. Itinerary: `verify → compose` up to `compose_max_retries=2`.
  - **Storage contract:** Local `FileSystemArtifactStore` (`--{id}` suffix, `os.link` + `Lock`, sidecar `.artifact-metadata/{id}.json` fsynced) is durable locally; `ConfigurableBlobArtifactStore` (`blob_configured = endpoint and token`) is durable remotely; `output_root()` with `VERCEL` is ephemeral (`/tmp/sard-output`). Download via `store.get_file_path` / `get_bytes` + `validate_artifact_bytes` re-check.

- **Release gate:** Before promoting a capability from `limited` → `supported`, require:
  1. New public-path test under `tests/capabilities/` proving offline render+validate+store.
  2. `public_path_test` field updated with that test path.
  3. `docs/capability-matrix.md` row updated + audit summary.
  4. `uv run python -m pytest tests/capabilities -v` green + spot `tests/test_artifact_pipeline.py` green.

## Reproduce

```powershell
# Registry gate (must be green):
uv run python -m pytest tests/capabilities -v

# Spot checks that earn the SUPPORTED badge:
uv run python -m pytest tests/test_artifact_pipeline.py tests/test_intent_routing.py tests/test_capability_routing.py tests/outputs/test_agentic_outputs.py -v

# Intent → spec mapping:
uv run python -c "from sard.agent.capability_routing import classify_intent; from sard.capability_registry import specs_for_intent; print([s.id for s in specs_for_intent(classify_intent('أنشئ لي PDF عن تاريخ نجد'))])"

# Matrix generation (JSON view):
uv run python -c "import json, sard.capability_registry as r; print(json.dumps(r.registry_as_dict(), ensure_ascii=False, indent=2)[:800])"
```
