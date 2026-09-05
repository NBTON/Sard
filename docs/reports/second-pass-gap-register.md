# Second-Pass Gap Register — Evidence-First Audit (2026-09-05)

Baseline: commit `da11050bd955ff6146da2669334774ab0baa79d1` (HEAD) + uncommitted prior-swarm worktree (11 files modified, see `git diff --stat HEAD`).
Python: 3.11.15 (venv) / 3.12.10 (system), Node v24.19.0, npm 11.17.0, uv 0.12.7.
`vercel.json`: `maxDuration:60` for `api/**/*.py` + `api/index.py` — present.
Env names (no values): MODEL_PROVIDER, MODEL_NAME, MODEL_TEMPERATURE, NVIDIA_API_KEY, NVIDIA_CHAT_MODEL_PRIMARY, NVIDIA_QUERY_MODEL_PRIMARY, NVIDIA_EMBEDDING_MODEL_PRIMARY/FALLBACK, NVIDIA_RERANK_MODEL_PRIMARY, NVIDIA_VISION_MODEL_PRIMARY, PARALLEL_API_KEY, DASHSCOPE_API_KEY (unset), BLOB_READ_WRITE_TOKEN (unset), SARD_* etc. `.env` present locally with NVIDIA_API_KEY + PARALLEL_API_KEY set; BLOB/DASHSCOPE unset.
Build: `npm run build` PASS (Next 15.5.23, 65.5kB /, 168kB first load).
Tests: `uv run pytest -q` → **614 passed, 1 skipped** in 248s (prior report claimed 236 — undercount, suite grew). `test_timeout_classified_and_not_leaking` PASSES. `max_retries` removed from `ChatNVIDIA` kwargs — warning gone.

Live SSE probes via Uvicorn subprocess (offline-masked env, but `.env` reloads real keys — see G10):
- `GET /api/status` → `degraded` with `model_configured:false` even though NVIDIA key present (BUG G1).
- `POST /api/chat` Arabic PDF `أنشئ لي ملف PDF عن تاريخ نجد` → 8 status + citations(5) + artifacts(1, 46876B, `%PDF`, sha verified, download 200 identical bytes) + ~100 delta + exactly 1 `done` (4547ms, verified:true). PASS.
- DOCX EN `Write a report about Diriyah as DOCX` → artifacts(1 DOCX) + delta hedge (verified:false, sources 0) + 1 done. Artifact created despite hedge — needs byte validation (pending).
- PPTX AlUla → citations(2) + artifacts(1, 33379B) + done 1890ms. PASS (slide count pending).
- ICS sync → citations(1) + artifacts(1, 3894B) + done 3828ms. PASS (date match pending).
- JSON `Create JSON output about Najdi architecture` → triggered live `parallel_search` (SDK, 2797ms) then **client SSE read timed out at 60s** (server did not close within SSE deadline 35s). FAIL — deadline not bounding (G10).

## P0 (fake success / lost files / ignored attachments / process-local / false readiness)

| ID | Severity | Finding | Evidence | Owning files | Proposed fix | Regression test |
|----|----------|---------|----------|--------------|--------------|-----------------|
| G1 | P0 | `/api/status` always `degraded`/`unavailable`: checks `settings.api_key` which does not exist on `ModelSettings` (only provider/model_name/temperature) → AttributeError → `model_configured=False` always. | `hasattr(settings,'api_key')==False`; `/api/status` returns `model_configured:false` with NVIDIA key set. | `sard/api/server.py:268-273`, `sard/config/models.py:50-56` | Check real env keys per provider (`NVIDIA_API_KEY`/`NVIDIA_CHAT_BASE_URL`, etc.) via helper; add `storage` + per-capability truth. | `tests/test_gap_g1_status_truth.py::test_status_model_configured_truthful` |
| G2 | P0 | Duplicate `orchestrate_from_intent`: line 804 shim `(request:ArtifactRequest)->ArtifactResult` shadowed by line 914 `(intent,raw_text...)->list`. Dead code, violates ONE contract. | `sard/outputs/orchestrator.py:804,914`; `grep orchestrate_from_intent` shows 2 defs. | `sard/outputs/orchestrator.py` | Delete shim, keep intent version + `generate_artifact`; update callers to ONE contract. | `tests/test_gap_g2_single_contract.py` |
| G3 | P0 | No official Vercel Blob SDK usage; still custom `ConfigurableBlobArtifactStore` manual PUT. ADR Decision 3 requires `vercel.blob put/head/get/delete` with `BLOB_READ_WRITE_TOKEN`. Second instance cannot download (ephemeral `/tmp` on Vercel). Deps `vercel`+`vercel-blob` added but unused. | `grep vercel.blob` → 0 hits in `sard/`; `orchestrator.py:359-504` custom REST only. | `sard/outputs/orchestrator.py`, `sard/runtime_paths.py`, `sard/api/server.py:429-474` | Add `VercelBlobArtifactStore` trying official SDK, fallback to custom REST, then FS; surface `storage` in `/api/status` (`durable_blob`/`ephemeral_unconfigured`); keep FS atomic + sidecars for local. | `tests/test_gap_g3_blob_durable.py` |
| G4 | P0 | Attachments process-local: `_ATTACHMENTS` in-memory dict; chat resolution only checks dict, no disk fallback. Second process loses attachment bytes. Upload files exist on disk but unresolvable. | `server.py:78,637-652` only `if att_id in _ATTACHMENTS`; `get_attachment` has glob fallback but chat path does not. | `sard/api/server.py` | Persist attachment index to `UPLOAD_DIR/.attachment-index.json` (atomic) + resolve via index or glob by `att_*` prefix; add TTL GC that preserves index. | `tests/test_gap_g4_attachment_second_process.py` |
| G5 | P0 | Recipe silent empty: `tool_generate_recipe_or_craft_card` with unknown item + no ingredients/steps creates card with empty lists (no validation). Violates "empty recipe sections → explicit clarification". | `cultural_agentic_tools.py:141-178` builds `RecipeOrCraftCard` with `ing_objs=[]`, `step_objs=[]`. | `sard/agent/tools/cultural_agentic_tools.py:122` | Return `success:False` + `error_category:no_match` + clarification when unknown item and no inputs; keep jareesh/sadu curated only for exact matches. | `tests/test_gap_g5_recipe_honesty.py` |
| G6 | P0 | Research hardcoded verified: `tool_conduct_verified_research` returns same 3 milestones (1727/1824/1932) + same 3 bibliography for ANY topic with `success:True` + "موثق/معتمد". Fabricated citations. | `cultural_agentic_tools.py:617-657` static lists, `f"...{topic}"` interpolation only. | Same | Mark as `limited/template`: add `verification:"template"`, `warnings:["template_timeline_not_verified"]`, change message to explicit template notice; require provider/RAG for verified claim. | `tests/test_gap_g6_research_honesty.py` |
| G7 | P0 | Calendar silent canned default: empty query/category/region/month → `HERITAGE_EVENTS_DATABASE[:4]` (first four events). Also `orchestrator.py:690` same. | `cultural_agentic_tools.py:218`, `orchestrator.py:690`. Prior fix covers filtered no-match but not empty. | Both + `sard/outputs/calendar_sync.py` | Empty filters → return 0 events + `error_category:missing_filters` + clarification, not 4 canned. | `tests/test_gap_g7_calendar_honesty.py` |
| G8 | P0 | Craft silent default: `craft_name=""` → sadu; default param `"sadu"`. Unknown craft fixed (returns False) but empty still canned. | `cultural_agentic_tools.py:478,492-493`. | Same | Default `""`; empty → `success:False`, `error_category:missing_input`, list valid crafts. | `tests/test_gap_g8_craft_honesty.py` |
| G9 | P0 | Registry docs lie: audio fallback says "Hasawi oasis template with 2 mock segments", vision says "templated description", but code now returns `capability_unavailable`. Matrix/registry out of sync. | `capability_registry.py:732,766` vs `multimodal_tools.py:304-312,378-388`. | `sard/capability_registry.py`, `docs/capability-matrix.md` | Update fallback/limitations to `capability_unavailable` truth; regen matrix from registry. | `tests/test_gap_g9_registry_truth.py` |
| G10 | P0 | JSON chat path exceeds SSE deadline: `Create JSON output...` did not terminate within 60s with `SARD_CHAT_OVERALL_TIMEOUT=35`. Deadline not bounding planner (RAG 30s×retries + live parallel + NVIDIA). | Probe log: `parallel_search 2797ms` then hang; `http.client TimeoutError` on `resp.read()`. | `sard/api/server.py:676-724`, `sard/config/rag.py`, `sard/rag/fallbacks.py` | Enforce planner-level deadline propagation + fail-fast on JSON/CSV/TXT (5s per matrix): skip web research for data formats, bound `run_pipeline` with monotonic check, ensure `done` always by deadline. | `tests/test_gap_g10_json_deadline.py` |
| G11 | P0 | Orphan risk remains: `_SHARED_EXECUTOR` + `loop.run_in_executor` threads cannot be cancelled; `future.cancel()` no-ops if running. Late worker may `store_bytes` after terminal `done`. No discard-after-deadline. | `chat_service.py:116-122,440-445`, `server.py:678-712` use `cancel()` without generation guard. | `sard/agent/chat_service.py`, `sard/api/server.py`, `sard/outputs/orchestrator.py` | Add monotonic deadline to artifact generation: `generate_artifact` checks deadline before `store_bytes`; server passes `deadline_monotonic`; late stores rejected + logged; timing probe proves zero orphans at +60s. | `tests/test_gap_g11_no_orphan_after_deadline.py` |

## P1 (broken UX / missing advertised capability)

| ID | Finding | Evidence | Fix |
|----|---------|----------|-----|
| P1-1 | `ArtifactModal.tsx` gutted: 1143 lines removed, rich viewers (16:9 deck, recipe 2-col, lore box) replaced with minimal placeholders. Hooks fixed (good) but UX degraded; needs verification of all 9 viewers + repeated open/close + Strict-Mode. | `git diff HEAD -- src/components/ArtifactModal.tsx` | Restore/verify viewers behind current minimal shell; add browser mock test for modal open/close ×5 + Strict-Mode double-mount. |
| P1-2 | `page.tsx` streaming cleanup removed: `setMessages(isStreaming:false)` mapping deleted at end of `handleSend`. `isStreaming` set false only via `setIsStreaming(false)` but message `isStreaming` flags may stick true. | Diff `src/app/page.tsx` | Restore per-message `isStreaming:false` finalization in `finally`. |
| P1-3 | Multimodal provider-error fallback still placeholder: `multimodal_tools.py:359-363` (`core_fallback` description) and `:401-405` run when key IS set but call fails. Should be truthful `failed`, not generic description. | Read lines 359,401 | Return `status:"failed"`, `error_category:"provider_error"`, no fake description. |
| P1-4 | Client/server slack mis-documented: code comment says 15s slack over 35s, actual server default 38s + client 50s = 12s. ADR says backend 30 max45. Need single source + measured trace. | `page.tsx:178` (50000), `server.py:605` (38) | Set server default 35s (cap 5-60), client 50s → 15s slack; document + timing test. |
| P1-5 | `/api/itinerary` calls `orchestrate_from_intent(intent, raw_text=...)` which only works due to duplicate def; fragile. Also bypasses SSE single-terminal contract (non-SSE, okay) but must use ONE orchestrator. | `server.py:541` | After G2, keep call but assert returns list; add test that `/api/chat` itinerary intent and `/api/itinerary` share orchestrator. |

## P2 (quality/perf/tech-debt)

- D1: `scratch/fix_server_deadline_attachments.py` contains `max(2.0,...)` example + duplicate logic; `scratch/` untracked, must not commit. Delete or gitignore.
- D2: `.swarm/memory.db-shm/wal` untracked; add to `.gitignore`.
- D3: `docs/capability-matrix.md` + `sard/capability_registry.py` drift (see G9); needs regen script + test.
- D4: Competing SSE parsers? `src/lib/sseParser.ts` vs inline `streamChat` in `page.tsx` — verify single parser.
- D5: `vercel.json` OK (60s) but `SARD_OUTPUT_ROOT` on Vercel must be `/tmp/sard-output`; verify `runtime_paths.output_root` handles `VERCEL=1`.
- D6: Tests prove journey? Most do (`validate_artifact_bytes` + `%PDF`/ZIP checks) — keep; do NOT weaken. Need to audit for previously weakened tests (e.g., `test_empty_output_chat_service_is_empty_not_shrimp` now expects empty not hedge — verify intentional).

## Acceptance-matrix status (pre-fix)

| Row | Result |
|-----|--------|
| PDF AR Najd | PASS (46876B, %PDF, 1 done, download identical) |
| DOCX Diriyah EN | PARTIAL (artifact created, text hedge, need byte check) |
| PPTX AlUla 6-slide | PARTIAL (33379B, need slide-count=6 + ZIP check) |
| ICS dates | PARTIAL (3894B, need date match + parse) |
| JSON/CSV/TXT | FAIL (JSON hangs >60s) |
| Grounded Q&A citations | PASS (5 cites, CIT-xxx) |
| Itinerary via chat | PENDING |
| Non-jareesh recipe | FAIL (empty sections per G5) |
| Unknown craft/proverb/event | PASS craft/proverb/event-filtered; FAIL empty calendar/craft (G7/G8) |
| Upload canaries PDF/image/audio | PENDING (need nonce probes) |
| Research citations | FAIL (hardcoded per G6) |
| Provider-deadline failure | PARTIAL (timeout test passes unit, but SSE JSON hangs per G10) |
| Idempotent retry | PENDING |
| Reload/session restore | PASS code present (`localStorage`), need browser test |
| Modal open/close ×N | PENDING (P1-1) |
| Second-instance download | FAIL per G3/G4 (FS sidecars OK same-disk, blob missing cross-instance) |
| `/api/status` truth | FAIL per G1 |
| Timing 40s race | PARTIAL (max(2.0) gone, 50 vs 38 slack, but JSON deadline fails) |
| Secrets in logs | PASS (run_id only, no keys in server logs sampled) |

## Prior-report diff

- Claimed 236/236 pass → actually 614 tests now, all pass. Claim undercounts, not overclaims on count, but overclaims on contracts (G1-G11).
- Claimed "migrated to daemon-backed shared executor" → true (`_SHARED_EXECUTOR`), but orphan risk remains (G11).
- Claimed "cleaned provider kwargs" → true (`max_retries` gone).
- Claimed "monotonic deadline without max(2.0)" → true in `sard/` (only `scratch/` retains example).
- Claimed "resolved attachments" → partially true (same-process dict) but cross-process fails (G4).
- Claimed "deleted fabricated strings" → true for no-key path, but provider-error path still placeholder (P1-3) + recipe/research/calendar/craft canned defaults remain (G5-G8).
- Claimed "configured maxDuration:60 + ConfigurableBlobArtifactStore" → true, but NOT official SDK as ADR requires (G3).
- Claimed "reordered hooks + localStorage + 50s" → true, but modal gutted + streaming cleanup removed (P1-1/P1-2).
- Claimed "truthful /api/status + no silent defaults" → false for status (G1) + remaining defaults (G5-G8).
