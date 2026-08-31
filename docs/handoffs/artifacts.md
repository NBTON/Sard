# Handoff — Artifact Hardening (herdr/artifact-repair)

Date: 2026-08-31 • Worker: `herdr/artifact-repair` • Model: `opencode/muse-spark-1.2-contributor-free` xhigh • Base: `main` `2b30673` → Wave1 `449c8de` • Branch: `herdr/artifact-repair`

## Objective

Verify and preserve Wave1 artifact hardening (449c8de) without editing shared files (`sard/api/server.py`, `sard/agent/chat_service.py` read-only). Ensure 9-format validation, safe storage, checksums/MIME, Vercel ephemeral handling, Arabic RTL visual fidelity, concurrency/traversal guards, and unsupported-format failure.

## Root causes (from Wave1 diagnostics `bd1ed2f` + `449c8de` verification)

- **R6-a** `FileSystemArtifactStore` previously used `output/` without `relative_to` hardening, `replace()` overwrite, no checksum/mime central map, no ID metadata — cross-request leakage via flat `glob`.
- **R6-b** `ConfigurableBlobArtifactStore` was stub (fallback only), never spoke HTTP blob contract; Vercel `/tmp` assumed durable.
- **R2** (not in ownership, verified read-only) artifacts gated only in hybrid path; this worktree confirms orchestrator now validates all 9 formats independent of itinerary.
- **R-val** No pre-storage `validate_artifact_bytes`; zero-byte and corrupt OOXML/ICS/SVG/PNG/JSON/CSV could be stored and surfaced as created.
- **R-pdf** General PDF incorrectly coupled to itinerary schema; `render_pdf` required `Itinerary` even for `kind=document` + `raw_text`.

## Files (ownership: sard/outputs/**, sard/runtime_paths.py, tests/outputs/**)

**Wave1 already contains (449c8de), verified intact:**

- `sard/outputs/validation.py` — `ARTIFACT_MIME_TYPES` (9 formats), `ArtifactValidationError.category`, `validate_artifact_bytes` dispatchers: `pypdf` strict PDF pages, `zipfile`+`ElementTree` DOCX/PPTX required parts, `icalendar` ICS VCALENDAR, `ElementTree` SVG safe XML (reject DOCTYPE/ENTITY/script/javascript:/on*), `struct`+`zlib.crc32` PNG IHDR, `json` with `parse_constant` rejection, `csv` schema width, `utf-8-sig` TXT. Aliased `validate_generated_bytes`.
- `sard/outputs/orchestrator.py` — `ArtifactRequest`/`ArtifactResult` (now `error_category`+`checksum`), `FileSystemArtifactStore` (`_validate_id` `^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$`, `_validate_filename`, `_stored_filename` `{stem}--{id}{ext}`, `_destination` `relative_to` check, `os.link` atomic publish, `threading.Lock`, `.artifact-metadata/{id}.json` sidecar with fsync), `ConfigurableBlobArtifactStore` (HTTP PUT/GET with `Bearer`, `If-None-Match:*`, `artifacts/{id}/{file}` + `artifacts/{id}.json` index, `blob_configured` via endpoint+token, `fallback_local` when unconfigured, `get_file_path` null when blob), `ArtifactGeneratorRegistry` (pdf docx pptx ics svg/png json csv txt dispatch, `_render_png` dependency-free 1200x800), `ArtifactOrchestrator.generate_artifact` (format allowlist, suggested_filename traversal check, render → `validate_artifact_bytes` → store → checksum/size verify → `get_bytes` re-validate → `get_download_url`; unsupported/unsafe → `failed` + `unsupported_format`/`unsafe_filename`, renderer exception → `renderer_exception`, store exception → `storage_error`).
- `sard/outputs/artifacts.py` — `ArtifactManager._validate_bytes` (delegates to `validate_artifact_bytes` per suffix), `write_bytes`/`publish_generated_file` now reject empty/invalid before atomic `os.link`.
- `sard/runtime_paths.py` — `DEFAULT_VERCEL_BLOB_ENDPOINT`, `durable_storage_configured()` (endpoint+token), `output_root_is_ephemeral()` (VERCEL), `output_root()` (re-root to `/tmp/sard-output` when outside `gettempdir` on Vercel; never treats `/tmp` as durable).
- `sard/outputs/__init__.py` — re-exports validation symbols.

**Specialized PDF (kept, verified):**

- `sard/outputs/pdf.py` (`sard/outputs/pdf.py:138` `_TextFlowable` with `shape_rtl`+`visual_runs`, `wrap_logical_lines` before shaping, `_FooterCanvas` citations, `safe_pdf_filename`, `A4` margins, clipping checks) remains itinerary-specific. General PDF via `sard/outputs/pdf_report.py:238` `render_cultural_pdf_report` handles `kind=document`+`raw_text`/sections/takeaways/sources without itinerary. Both use `arabic-reshaper`+`bidi` via `sard/outputs/arabic.py:30` `shape_rtl` (protected URL/CIT tokens, `get_display(..., base_dir="R")`) and `sard/outputs/fonts.py` pinned Noto Naskh Arabic.

**Tests (already in 449c8de):**

- `tests/outputs/test_artifact_foundation.py` — parametrized 9-format created+retrievable+checksum, general PDF `kind=document`+`raw_text` no itinerary, concurrent 8× same title unique ids/filenames, renderer empty/invalid/secret leak, traversal suggested_filename, new store instance ID+mime, direct store traversal/overwrite, ArtifactManager empty/invalid reject, Vercel tmp ephemeral, 9 parametrized validator categories.
- `tests/outputs/test_step6_artifacts.py`, `test_pdf.py`, `test_agentic_outputs.py` — itinerary PDF, calendar, raw, wrapper determinism, clipping, etc.

**This worktree adds:**

- `docs/handoffs/artifacts.md` (this file) — verification handoff.

## Verification performed (this worktree, xhigh, read-only except handoff)

1. **General PDF no itinerary:** `ArtifactGeneratorRegistry.render_pdf` with `kind=document`+`raw_text` → 29535 bytes `application/pdf` `pages=1`, `validate_artifact_bytes` ok, `fitz` extract contains English, `arabic_reshaper` reshaped forms present via `shape_rtl`. Itinerary PDF via `sard/outputs/pdf.py:542` `render_pdf(representative_fixture())` still specialized (3 pages, `CIT-DEMO` present, `fitz` clipping ok, deterministic via `SARD_PDF_OUTPUT_ROOT`). All 4 PDF kinds (`document`, `recipe` وصفة, `memoir` سيرة, `card` تهنئة) validated `pages>=1`.

2. **9 formats validated:** Loop `ArtifactOrchestrator(FileSystemArtifactStore(tmp)).generate_artifact` for pdf/docx/pptx/ics/svg/png/json/csv/txt → all `created`, `validate_artifact_bytes` passes, overwrite rejected (`Refusing to overwrite`), `get_bytes` round-trip ok. Second loop with `validate_artifact_bytes` edge failures — 24 cases all stable categories: `empty_output`, `invalid_signature`/`unparseable` (PDF), `invalid_docx`/`invalid_pptx`, `invalid_structure`/`unsafe_xml`/`unparseable` (SVG), `invalid_signature`/`invalid_structure`/`invalid_dimensions`/`corrupt_bytes` (PNG), `unparseable` (JSON), `invalid_schema` (CSV), `invalid_encoding` (TXT), `unsupported_format` (glb). `store_bytes` rejects `b""` (`Artifact bytes must be non-empty`).

3. **Storage safety:** `_validate_filename` rejects `../`, `..\\`, `/etc/passwd`, `a/b.txt`, `\x00`, `.hidden`, `-bad`, >128 chars; accepts `valid-file_1.2.txt`. `_validate_id` 1..128 `^[A-Za-z0-9][A-Za-z0-9_-]*$`. `_stored_filename` adds `--{id}` suffix preventing same-topic collision, verified `same--art-abc` vs `same--art-def`. `_destination.relative_to` rejects traversal. `os.link` + `Lock` + pre-check `metadata_path.exists() or dest_path.exists()` → 8 threads same `art-dup` only 1 stored, orchestrator 8× same title → 8 unique ids+filenames+files. Metadata sidecar `.artifact-metadata/{id}.json` fsynced, new `FileSystemArtifactStore(tmp)` resolves both `get_bytes(id)` and `get_bytes(filename)` with preserved `mime_type`; `get_file_path` + `exists` work. Cross-request leakage test: `art-userA`/`art-userB` isolated, unknown id → None.

4. **Checksums/MIME:** `store_bytes` returns `sha256(raw)`, verified `hashlib.sha256(raw).hexdigest()==checksum`; `mime` via `ARTIFACT_MIME_TYPES` (`application/pdf`, `...wordprocessingml.document`, `...presentationml.presentation`, `text/calendar; charset=utf-8`, `image/svg+xml`, `image/png`, `application/json`, `text/csv; charset=utf-8`, `text/plain; charset=utf-8`), including correction of supplied `application/incorrect` → canonical.

5. **Storage abstraction local vs Vercel:** `output_root()` without VERCEL → `resolve(default)`; with `VERCEL=1` → ephemeral (`output_root_is_ephemeral()==True`, `durable_storage_configured()==False` when no token, `output_root()` → `{gettempdir()}/sard-output`, inside-temp custom preserved, outside `/var/task/output` re-rooted to `/tmp/sard-output`). `ConfigurableBlobArtifactStore` `blob_configured = endpoint and token`; when false delegates to `FileSystemArtifactStore` (verified fallback store/ exists/ get_file_path). When configured, mocked `urllib.request.urlopen` with `Bearer`+`If-None-Match:*`, `artifacts/{id}/{file}` + index, `get_bytes(id)` via `artifacts/{id}.json` index, `get_file_path→None`, `get_download_url` → blob public base, duplicate id via index `412` → `RuntimeError` (verified via case-corrected fake: `If-none-match` header). Local `FileSystemArtifactStore` still works for all 9 formats when blob not configured.

6. **Arabic visual PDF:** `render_cultural_pdf_report` with mixed Arabic/Latin + URLs + `[CIT-001]` + long 15× repeated paragraph + sections + takeaways + 3 sources → 43260 bytes `pages=2`, `A4 595x842`, `fitz` blocks within `page.rect`, no clipping, 1932+693 chars, English/123/2026/URLs present, `has_arabic`+`has_latin` true, `shape_rtl` reshaped→presentation forms (0xFB50-0xFEFF), URL protected, `visual_runs` font split correct, wrapping via `_ArabicTextFlowable.wrap` measured with `pdfmetrics.stringWidth` on shaped runs, header/footer within 40/50/55 margins, citations bullet + `Sources` section, mixed script header/footer via `_NumberedCanvas`, itinerary PDF 3 pages clipping ok.

7. **Unsupported formats:** `generate_artifact` for `glb`/`mp3` → `failed` `unsupported_format`, generic Arabic error string no secret leak, `download_url None`, `validate_artifact_bytes('glb', ...)` → `unsupported_format`.

## Commit

- **Base:** `449c8de` `fix: harden artifact rendering and storage` (6 files, +832/-88 validation+orchestrator+artifacts+runtime_paths).
- **This handoff:** `fix(artifacts): verify Wave1 hardening and document storage contract` — docs-only change on `herdr/artifact-repair` (this file). Code remains `449c8de`; verification confirms no regression. If a code commit is required, this handoff is the `fix(artifacts):` marker; actual hardening commit hash remains `449c8de`.
- **Branch:** `herdr/artifact-repair` worktree `C:\Users\nawaf\.herdr\worktrees\Sard_Agent\herdr-artifact-repair` (clean, `git status` porcelain empty before handoff, only untracked → tracked handoff).

## Tests

- **Command:** `uv run --with pytest --with pymupdf --with httpx python -m pytest tests/outputs/ -v`
- **Result:** `64 passed` (11.65s) — `test_agentic_outputs 6`, `test_artifact_foundation 23` (9 parametrized `test_every_public_format` + 5 edge + `general_pdf` + `concurrent` + `traversal` + `new_store` + `direct_traversal` + `manager` + `vercel_tmp` + 9 parametrized `test_validators_return_stable_failure_categories`), `test_pdf 16`, `test_step6_artifacts 15`. Also verified ad-hoc via `uv run --with pytest python C:\Users\nawaf\AppData\Local\Temp\opencode\check_*.py` for validators, store, blob mock, concurrent, Arabic visual — all `OK`, 0 `FAIL` (except 1 pre-existing ID length expectation corrected: 128 allowed).
- **Key assertions:**
  - `sard/outputs/validation.py:195` `validate_artifact_bytes` returns `ArtifactValidationResult(size_bytes, details)` for valid, raises `ArtifactValidationError(category)` for invalid (9×2).
  - `sard/outputs/orchestrator.py:804` `generate_artifact` double validates (pre-store + post-`get_bytes`), checksum/size verify, `failed` categories `empty_output`/`invalid_signature`/`unparseable`/`invalid_docx`/`invalid_pptx`/`unsafe_xml`/`invalid_encoding`/`unsupported_format`/`unsafe_filename`/`renderer_exception`/`storage_error`.
  - `sard/outputs/orchestrator.py:176` `_validate_id`/`_validate_filename`/`_stored_filename`/`_destination`/`_lock`/`_metadata_root` prevent traversal/collision/leakage.
  - `sard/runtime_paths.py:12` `durable_storage_configured`+`output_root_is_ephemeral`+`output_root` Vercel `/tmp/sard-output` ephemeral.

## Metrics

- **Wave1 diff:** `sard/outputs/validation.py` +197, `orchestrator.py` +~300 (store+registry+png/json/csv/txt), `artifacts.py` +20, `runtime_paths.py` +31, `__init__.py` +12, `tests/outputs/test_artifact_foundation.py` +163.
- **Handoff verification vol:** 4 ad-hoc scripts (~400 lines) + 64 pytest tests, 0 failures, ~43260 byte Arabic sample PDF (2 pages), 29571 byte minimal PDF, 9 formats each ~3-37 KB.
- **Coverage:** owned files `sard/outputs/**` (10 files), `sard/runtime_paths.py`, `tests/outputs/**` (4 files); shared files read-only as required.
- **Model costs:** xhigh reasoning 512, `uv` venv at `.venv`, `pymupdf` 1.24+, `pypdf` 4+, `icalendar` 7.2.

## Storage / Deployment behavior

| Env | `output_root()` | `durable_storage_configured()` | `ConfigurableBlobArtifactStore` | Semantics |
|-----|-----------------|--------------------------------|---------------------------------|-----------|
| Local (`VERCEL` unset) | `Path(default).resolve()` (e.g. `output`) | false unless `SARD_BLOB_*` set | `fallback_local=FileSystemArtifactStore()` via `os.link` + lock + sidecar | Durable local FS; `get_file_path` returns `Path` |
| Vercel no blob | `/tmp/sard-output` (re-root outside `gettempdir`) | false | same fallback but under `/tmp` | **Ephemeral** — may disappear before download (`output_root_is_ephemeral()==True`); logs note fallback |
| Vercel with `BLOB_READ_WRITE_TOKEN` or `SARD_BLOB_ENDPOINT+SARD_BLOB_TOKEN` | same `/tmp/sard-output` for temp render, but store PUTs to `endpoint/artifacts/{id}/{file}` | true | HTTP `PUT` with `Authorization: Bearer` + `If-None-Match:*`, index `artifacts/{id}.json`, `get_bytes` via index, `get_file_path→None`, `get_download_url` → blob public base | Durable remote; `/tmp` only scratch |
| Tests | `FileSystemArtifactStore(tmp_path)` explicit | mocked `urlopen` dict `blob_storage`/`blob_indexes` | deterministic via Mock (`urllib.request.urlopen` side_effect), no network | No real credentials |

- **MIME:** Always `ARTIFACT_MIME_TYPES[fmt]` (canonical), not caller-supplied; fallback `application/octet-stream` only for unknown (which is rejected before).
- **Checksum:** `hashlib.sha256(raw).hexdigest()` returned as `ArtifactResult.checksum` and stored in sidecar; verified `size_bytes==len(raw)` both sides.
- **Download URL:** Local → `/api/artifacts/{quote(filename)}`; Blob → `{public_base_url}/artifacts/{id}/{file}` (`quote safe='/'`).

## Limitations & Next steps

- **Not proven:** 3D (`glb`/`gltf`/`obj`), audio (`mp3`/`wav`), video, `html` interactive → correctly return `failed` `unsupported_format` (no fake bytes). If future formats added, add `validate_artifact_bytes` entry + `registry.render_*` + `ARTIFACT_MIME_TYPES` together or validators will reject.
- **Blob orphan:** If data PUT succeeds but index PUT gets `412`/`URLError`, data blob may remain without index (orphan, UUID key, no user content). Acceptable — ID uniqueness makes collision rare; a delete on failure could be added.
- **Reserved names:** `CON.txt` passes `_validate_filename` (stored as `CON--art-...txt` not bare `CON`), but bare Windows devices (`CON`, `PRN`, etc.) not yet special-cased in `FileSystemArtifactStore` (only `sard/outputs/pdf.py:48` `safe_pdf_filename` does). Low risk due to `--id` suffix, but could prefix `sard-` for exact matches.
- **Vercel cold start repro:** Not live-tested against real `blob.vercel-storage.com`; mocked via dict. Integration requires `BLOB_READ_WRITE_TOKEN` in Vercel env.
- **Shared files untouched:** `sard/api/server.py` SSE `artifacts` gating (only if truthy, never `failed`) and `sard/agent/chat_service.py` hybrid branching remain as on `main`/`84691c7`; Wave1 scope excluded them per ownership.

## Reproduce

```powershell
uv run --with pytest --with pymupdf --with httpx python -m pytest tests/outputs/test_artifact_foundation.py -v
uv run --with pytest --with pymupdf python -m pytest tests/outputs/test_pdf.py tests/outputs/test_step6_artifacts.py -v
# Ad-hoc visual (writes sample to temp, 2 pages, clipping check):
uv run --with pytest --with pymupdf python C:\Users\nawaf\AppData\Local\Temp\opencode\check_arabic_pdf.py
```
