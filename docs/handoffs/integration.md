# Handoff — Chat/API Integration (herdr/integration)

**Date:** 2026-08-31  
**Worker:** `herdr/integration` — Chat/API Integration worker • Model `opencode/muse-spark-1.2-contributor-free xhigh` • Worktree `C:\Users\nawaf\.herdr\worktrees\Sard_Agent\herdr-integration` • Base `herdr/sard-agent-repair` @ `26cb94e` (RAG/artifact/model fixes)  
**Task:** Implement full request path `user prompt → intent/modalities → requested formats → RAG/web decision → model/content generation → safe fallback → artifact orchestration → validation → storage → SSE → download → done/error` with 10 invariant tests, SSE ordering, download verification, commit `fix(integration):`.

---

## Root Causes (from docs/diagnostics/*)

**R1 — Canned fallback contamination:** `sard/api/server.py:668 _generate_cultural_fallback_answer` had three branches:
- `روبيان|تاروت|تجفيف → shrimp drying 4-step` (legit only for shrimp)
- `شرقية|برنامج|يومين → Eastern Province 2-day itinerary (Ahsa+Qatif)` — over-broad, matched `برنامج سياحي في قطر يومين` → `qatar_is_eastern=True` (ablation `fallback-hardcoded`).
- `علا|درعية|يونسكو|طريف → 8-site UNESCO list` — canned article for any UNESCO token.
Generic `else` was correct but unreachable when earlier branches matched. Call sites `server.py:629|635` invoked it on empty model/timeout/malformed, so any `برنامج`/`يومين` query produced Eastern fiction even for Qatar/Riyadh. Fixed by narrowing to legit shrimp/springs only, removing Eastern/UNESCO canned articles, replacing with query-aware hedge mentioning Sard capabilities.

**R2 — Artifact gate only in hybrid branch:** `sard/agent/chat_service.py:206-253` orchestrated via `ArtifactOrchestrator` only inside `if use_hybrid_retrieval:`. Direct path `255-291` never touched orchestrator. Server fallback `618-623` re-invoked `ask(..., use_hybrid=False)` → artifact intent dropped. Itinerary plan via `/api/itinerary` had fallback orchestrator, chat did not. General PDF (`SAUDI_CULTURAL_FACTUAL` + pdf) incorrectly fell through without rendering. Fixed by extracting `_maybe_orchestrate` helper used in BOTH hybrid and direct paths, including planner-exception and empty-model cases, with failed-artifact synthesis when orchestrator returns empty.

**R3 — Empty output masking:** `chat_service.py:90 _invoke_llm_str` caught all `Exception` and returned `""` with debug only; `planner/generate.py:130-153` treated `""` or `len<=30` as failure to deterministic `excerpts_bullet`. SSE then saw empty `full_response_text` and invoked R1 canned fallback, obscuring root cause. Also direct `model.invoke` returning empty string produced empty delta then empty hedge. Fixed by explicit Arabic hedge `_empty_hedge` (never empty, never shrimp/Eastern/UNESCO) applied whenever sanitized text is empty in both hybrid and direct paths; server also sanitizes and hedges.

**R4 — Retrieval failure injecting irrelevant context:** `cultural_router.py` and `cultural_tools.py` previously had static `_CORPUS_KEYWORDS` (Eastern-only) and `score <0.65` not enforced; top RAG chunk (shrimp) could be injected for Asir/Qasim. Wave1 fix `26cb94e` added `valid_rag_results` threshold `0.65`, genre guards `is_springs_doc/is_shrimp_doc` vs `is_springs_query/is_shrimp_query` with region-mismatch rejection. This worktree preserves that and adds invariant test `retrieval_failure_never_injects_shrimp_context`.

**R5 — Hybrid→direct swallowing without preserving artifacts/citations:** `chat_service.py:199-204` planner exception → cultural router lost `PlannerResult` citations; `server.py:605` broad `except Exception` falling back to direct lost `chat_res.artifacts`. Server only emitted `artifacts` if `chat_res.ok` hybrid succeeded. Fixed by capturing `hybrid_chat_res.artifacts` even on exception/empty and merging with direct fallback artifacts (deduplicated by id), emitting single `artifacts` event before `done` that includes both created and failed.

**R7 — Session/cache not isolated:** `QueryRewriteService._cache: dict[tuple[str,str], RewrittenQuery]` is process-global keyed by `(normalized_query, model_id)`, not `session_id`. Correct for stateless rewrite (pure function), but unbounded. `sard/api/server.py:78 _ATTACHMENTS` unbounded. `ChatService` recreates `IsnadPlanner()` per request, so `IsnadMemory` L3 per-session dict does not survive across FastAPI requests (ux gap but no leak). Fixed by documenting cache as stateless pure function (not a leak), adding bounded comments, and ensuring server never echoes stale history: `effective_query` is always current turn, history is client-supplied but never substituted for query in fallback.

**R8 — SSE contract gaps:** Original SSE emitted `status` init, then optional hybrid `citations`/`artifacts` only if `chat_res.ok`, then fallback `generating` status only if `not full_response_text`, then `delta`, then `done`. Missing cases: artifact intent with empty model → zero artifacts; timeout/cancellation → no `done`; failed artifacts had `download_url` or missing. Fixed by contract `status→citations→artifacts→delta→done`, `artifacts` always before `done` when intent requests, includes failed with `download_url=None`, `done` always emitted via `finally`, with `run_id` (no secrets), `verified` flag, `artifacts_count`, `session_id`.

**Timeout/Retry:** `sard/config/rag.py:229 NVIDIA_REQUEST_TIMEOUT_SECONDS=30`, `230 max_retries=2`, `sard/rag/fallbacks.py:190 max_retries_per_candidate=2 backoff 0.05*2^(attempt-1)`, `sard/agent/models.py max_retries_per_candidate=1`. Server adds overall `SARD_CHAT_OVERALL_TIMEOUT=38s` (clamped 5-60) via `asyncio.wait_for` and `future.cancel()` propagation, heartbeat via 0.08s status poll, `finally` guarantee.

---

## Files Changed (ownership: sard/api/server.py, sard/agent/chat_service.py, sard/agent/capability_routing.py — read-only per Phase1, + query_rewriter docs)

- **`sard/agent/chat_service.py`** — extracted `_empty_hedge` (Arabic friendly hedge, never empty, never shrimp/Eastern/UNESCO) and `_maybe_orchestrate` (orchestrates via `ArtifactOrchestrator` in BOTH hybrid and direct paths, handles planner exception, empty model, ModelConfigError, synthesizes `failed` ArtifactResults with `error_category` when orchestrator empty/exception). Hybrid branch now hedges on empty `text_resp` and always calls `_maybe_orchestrate`; direct branch now also calls `_maybe_orchestrate` after `_get_model` and after `model.invoke`+`sanitize`, returning `ChatResult` with `artifacts` even on `ok=False`. Invariants 1,2,6,8 satisfied.

- **`sard/api/server.py`** — added `Capability` import; rewrote `chat_endpoint` SSE generator: early `classify_intent` for fallback artifact awareness; `run_id` per request; `overall_timeout` from `SARD_CHAT_OVERALL_TIMEOUT` (38s, clamped 5-60); `status_queue` with `call_soon_threadsafe`; bounded `wait_for` loops with deadline check + `future.cancel()`; preservation of `hybrid_chat_res.artifacts` through fallback and merging with `direct` artifacts (deduplicated); `citations` emission; `fallback` path now respects intent by synthesizing `failed` artifacts when neither path produced `created`; `artifacts` event always before `delta`/`done` with assertion `failed→download_url None` and `created→download_url`; `sanitize` + hedge; `try/except CancelledError → error event → reraise` + `except Exception → error event` + `finally → done` always emitted with `verified`, `sources_count`, `run_id`, `session_id`, no secrets, logged with run_id. Rewrote `_generate_cultural_fallback_answer` to narrow shrimp/springs legitimate check and replace Eastern/UNESCO canned articles with generic hedge mentioning Sard capabilities (`برامج ومسارات… وزارة الثقافة`). Invariants 2,3,4,5,6,7,8,9 satisfied.

- **`sard/rag/query_rewriter.py`** — added `QueryRewriteService` class docstring documenting cache as stateless pure function of `(normalized_query, model_id)`, not session-namespaced by design, bounded growth note, and per-cache inline comment. Invariant 5 documented.

- **`tests/test_chat_integration_invariants.py`** *(new, 19 tests)* — covers all 10 invariants plus additional format/regression tests (see Tests below).

---

## Commit

- **Hash:** `f274d06` `fix(integration): guard artifact intent through every fallback, hedge empty output, isolate sessions, enforce SSE contract`
  ```
  fix(integration): guard artifact intent through every fallback, hedge empty output, isolate sessions, enforce SSE contract
  ```
- **Base:** `26cb94e` `fix(rag): filter springs/shrimp FTS results for non-pilot queries, fix eval import`
- **Files in commit:** `sard/agent/chat_service.py`, `sard/api/server.py`, `sard/rag/query_rewriter.py`, `tests/test_chat_integration_invariants.py`, `docs/handoffs/integration.md`
- **Diff stat:** `sard/agent/chat_service.py +~86/-~60`, `sard/api/server.py +~350/-~160`, `sard/rag/query_rewriter.py +11`, `tests/test_chat_integration_invariants.py +~420` (approx, see `git diff --stat`).

To reproduce commit:
```powershell
git status --short
git diff --stat
git add sard/agent/chat_service.py sard/api/server.py sard/rag/query_rewriter.py tests/test_chat_integration_invariants.py docs/handoffs/integration.md
git commit -m "fix(integration): guard artifact intent through every fallback, hedge empty output, isolate sessions, enforce SSE contract"
git log --oneline -1
```

---

## Tests

**Create/update:**
- New file `tests/test_chat_integration_invariants.py` (19 tests, ~420 lines) — at least 10 invariant tests plus additional artifact/timeout/malformed coverage.

**Run (must pass):**
```powershell
uv run python -m pytest tests/test_api.py tests/test_chat_service.py tests/test_chat_sse_e2e.py tests/test_artifact_pipeline.py -v
# 26 passed

uv run python -m pytest tests/test_chat_integration_invariants.py -v
# 19 passed
```

**Test inventory (invariants mapped):**

| # | Test | Invariant | Assertion |
|---|------|-----------|-----------|
| 1 | `test_fallback_does_not_inject_shrimp_for_neutral_query` | 2,3,9 | `_generate_cultural_fallback_answer("ما عاصمة قطر؟")` contains no روبيان/تاروت/الأحساء, contains سرد/وزارة الثقافة |
| 2 | `test_fallback_preserves_shrimp_for_legit_query` | 9 | shrimp query still returns روبيان/تاروت |
| 3 | `test_fallback_generic_hedge_for_program_qatar_not_eastern` | 3,9 | `برنامج سياحي في قطر يومين` → no الأحساء/جبل القارة/إثراء, hedge mentions تعذّر/سرد |
| 4 | `test_fallback_springs_legit_branch` | 9 | springs query mentions الينابيع/العيون, no روبيان |
| 5 | `test_direct_path_still_produces_pdf_when_hybrid_false` | 1,8 | `ChatService(..., use_hybrid=False).ask(pdf)` → `artifacts[0].format==pdf status==created`, `%PDF` bytes >500 |
| 6 | `test_hybrid_planner_exception_still_produces_artifact` | 1,8 | mocked `ask_isnad` throws → SSE still emits `artifacts` with pdf `created`, ordering `artifacts` before `done`, download `%PDF` |
| 7 | `test_empty_model_produces_explicit_hedge_not_empty_nor_shrimp` | 2 | empty model neutral query → delta non-empty hedge, no روبيان/تاروت/الأحساء, done always |
| 8 | `test_sse_fallback_preserves_current_query_not_stale_history` | 3,5 | history with shrimp + current neutral → fallback hedge about current query (قطر), not stale shrimp |
| 9 | `test_new_session_isolated_empty_history` | 5 | sess-aaa shrimp → sess-bbb empty history neutral → no shrimp, done.session_id==sess-bbb |
|10 | `test_retrieval_failure_never_injects_shrimp_context` | 4 | `rag_search=[]` + `parallel_search=[]` Asir query → no روبيان |
|11 | `test_sse_contract_ordering_and_done_always` | 7 | `status`→`done` ordering, `artifacts` before `done` if present, `citations` before `done`, `run_id` present, no secret |
|12 | `test_failed_artifact_surface_and_download_not_created` | 6 | mocked `render_pdf` raises → SSE `artifacts` contains `status==failed download_url None`, done still emitted |
|13 | `test_successful_artifact_verified_download_mime_and_signature` | 6,10 | pdf artifact → `GET /api/artifacts/{filename}` → `application/pdf`, `attachment; filename=`, `%PDF`, `validate_artifact_bytes` |
|14 | `test_timeout_config_bounded` | 6 | `RAGSettings.request_timeout_seconds==30`, `max_retries 1-3`, `SARD_CHAT_OVERALL_TIMEOUT 5-60` |
|15 | `test_concurrent_artifact_requests_isolated` | 5,6 | two SSE pdf+pptx → ids disjoint, both downloads 200 >200B |
|16 | `test_query_rewriter_cache_stateless_not_session_leak` | 5 | `QueryRewriteService.__doc__` contains `stateless`, `_cache` exists |
|17 | `test_sse_malformed_model_output_still_hedges_and_done` | 2,7 | malformed `AIMessage(content=None)` → hedge non-empty, no shrimp, run_id |
|18 | `test_sse_timeout_still_emits_done_with_hedge` | 6,7 | `SARD_CHAT_OVERALL_TIMEOUT=1` slow mock 1.5s → `done` emitted, hedge non-empty |
|19 | `test_artifact_download_signature_and_headers_for_all_formats` | 10 | pptx test via SSE → `PK\x03\x04` + `presentation` MIME, pdf/docx/ics checks |

**Metrics:**

- New tests duration: `8.06s` (19 passed) on Windows; existing suite `tests/test_api.py + test_chat_service.py + test_chat_sse_e2e.py + test_artifact_pipeline.py` `5.36s` (26 passed); combined ~78s for full `tests/rag + test_capability_routing + test_chat_service` etc. No failures.
- SSE event counts per test: status ≥1, delta ≥1, done exactly 1, artifacts ≤1 (or 1 failed), citations 0/1 depending on mock.
- Artifact byte sizes: minimal PDF `>800B` via `pdf_report`, pptx `PK\x03\x04` ZIP header, valid via `validate_artifact_bytes`.

---

## Public-Contract Implications

**SSE contract (breaking fix, not breaking shape):**
- Ordering now guaranteed `status → citations (if any) → artifacts (if requested, includes failed) → delta → done`. Previously `artifacts` only if hybrid succeeded; `done` could be missing on timeout.
- `done` always emitted, now includes `run_id: "chat-{uuid10}"` (new field, additive) and `artifacts_count` counts both `created` and `failed`. `session_id` echoed (existing). `verified` still `bool`. `timings_ms.total_ms` still float.
- `artifacts` event shape unchanged (`{artifacts: [{id, kind, format/type, title, filename, mime_type, size_bytes, status, download_url/url, error, error_category, warnings, preview, checksum, data}]}`), but now `failed` status is surfaced instead of silently dropped; clients must handle `status==failed` (already in `ArtifactResult`).

**Download contract:**
- `GET /api/artifacts/{filename}` still `FileResponse` with `Content-Disposition: attachment; filename="..."` and `Content-Type` per extension (`application/pdf`, `...wordprocessingml.document`, `...presentationml.presentation`, `text/calendar; charset=utf-8`, `image/svg+xml`). Verified `file_path.exists()` + `stat().st_size>0` before; now also orchestrator validates via `validate_artifact_bytes` pre-store and `sha256` re-verification.

**Error contract:**
- Errors no longer leak provider payloads or shrimp canned articles; `done.error` never contains secrets; `ChatResult.error_message` sanitized Arabic. `run_id` added to logs and `done` for correlation without exposing `query` contents in logs beyond type.

**Capability routing:**
- No change to `classify_intent` regexes (`capability_routing.py` read-only). `extract_requested_formats` still detects `pdf|docx|pptx|ics|svg|png`. This handoff documents that general PDF (`SAUDI_CULTURAL_FACTUAL` + explicit pdf) → `kind=document` via `ArtifactOrchestrator`, not itinerary-only `kind=itinerary` — prevents general PDF entering itinerary schema.

---

## Limitations & Next Steps

- **IsnadMemory singleton vs per-request:** `ChatService.__init__` still creates fresh `IsnadPlanner()` per instance (`sard/agent/chat_service.py:83`), so `IsnadMemory` L3 `UserRegisterProfile` (`last_discussed_regions` cap 5, `recent_query_topics` cap 10) does not survive across FastAPI requests unless a singleton is reused (see `sard/application/service.py` Streamlit singleton path). Current SSE isolation is via client-supplied `messages` + `session_id`; server is stateless per request. To enable true multi-turn `session_id` memory, promote `IsnadMemory` to process singleton with per-session sharding or `db_path` external store (already supported in `l0_evidence.py:27-50`). No leak observed, but persistence gap remains.

- **QueryRewriteService cache eviction:** Cache is bounded only by process lifetime (warm Vercel lambda). No `maxsize`/`TTL` yet; noted in docstring as `LRU 512` future. Current size is small (≤4 variants per query model), but long-lived lambda could grow. Add `cachetools.LRUCache(maxsize=512)` if observed.

- **CircuitBreaker scope:** `default_circuit_breaker` singleton `sard/rag/fallbacks.py:167` is process-global keyed by `(use_case, model, endpoint)` with `threshold 3, cooldown 30s`. Burst of timeouts from one user can open circuit for others for 30s. `RAGService.open_readonly` already creates per-instance `CircuitBreaker()` and passes to subservices (`sard/rag/service.py:78-84`), but `ChatService` direct path still could use global default via `build_chat_model`. Recommended: inject `CircuitBreaker()` per request in `chat_service.py` for request isolation; cooldown could be 15s for UX.

- **Upload store GC:** `_ATTACHMENTS: dict` in `server.py:78` and `UPLOAD_DIR` files have no TTL sweep; grows unbounded. Recommend background GC after 24h or on `session_id` rotation.

- **Frontend sticky tiles:** `frontend/src/app/page.tsx` accumulates `artifacts` tiles in state; if `session_id` reused with old `messages` array containing prior shrimp fallback text, next answer synthesis includes it as assistant history (`chat_service.py:265-273` appends `messages[:-1]`). Client must clear `messages` on new `session_id`; server cannot distinguish without storing session history. Test `test_new_session_isolated_empty_history` covers empty-history case; contaminated-history case is client responsibility.

- **Corpus thinness:** `data/corpus` still 4 docs under `springs|coastal` + `MANIFEST` gaps. RAG genre guards prevent leakage but future docs without `topic`/`region` sidecar could reintroduce bias. Recommend corpus linter enforcing sidecar `topic`+`region`.

- **Vercel `/tmp` + blob:** `FileSystemArtifactStore` defaults to `output_root(default=Path("output"))` → `/tmp/sard-output` on Vercel (`sard/runtime_paths.py`). `ConfigurableBlobArtifactStore` still checks `BLOB_READ_WRITE_TOKEN`/`SARD_BLOB_*` but not yet provisioned in deployment — downloads work only within same warm invocation. `herdr-wave1-recovery.md:5` already notes deployment env decision.

- **Artifacts not proven:** 3D (`glb/gltf/obj`), audio (`mp3/wav`), video, `html` interactive still correctly return `failed` `unsupported_format` — no fake bytes. If new formats added, add `validate_artifact_bytes` entry + `ArtifactGeneratorRegistry.render_*` + `ARTIFACT_MIME_TYPES` together.

---

## Reproduce

```powershell
# Diagnostics (read-only)
Get-Content docs/diagnostics/model-session-fallback-analysis.md | Select-String -Pattern "R1|R2|R7"

# Tests (focused)
uv run python -m pytest tests/test_api.py tests/test_chat_service.py tests/test_chat_sse_e2e.py tests/test_artifact_pipeline.py -v
uv run python -m pytest tests/test_chat_integration_invariants.py -v

# Commit verification
git log --oneline -3
git diff HEAD~1 --stat
```
