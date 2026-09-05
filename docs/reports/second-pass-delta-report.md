# Second-Pass Delta Report — Verify, Fix, Re-verify (2026-09-05)

Coordinator: principal engineer (second pass). Mission: trust nothing without E2E evidence through production HTTP/UI path.

## Baseline (before fixes, current commit)

- HEAD `da11050bd955ff6146da2669334774ab0baa79d1` + 11 modified files (prior swarm worktree, uncommitted).
- Python 3.11.15 (venv), Node v24.19.0, `vercel.json maxDuration:60` present.
- `npm run build` PASS (Next 15.5.23).
- `uv run pytest` 614 passed, 1 skipped (prior report claimed 236 — undercount).
- `test_timeout_classified_and_not_leaking` PASS, `max_retries` warning gone.
- Live SSE (Uvicorn, `.env` keys present):
  - PDF AR Najd: 4.5s, 46876B `%PDF`, 5 citations, exactly 1 `done` — PASS.
  - DOCX/PPTX/ICS: artifacts created — PARTIAL (byte checks pending).
  - JSON `Create JSON output...`: **hung >60s** with `SARD_CHAT_OVERALL_TIMEOUT=35` (deadline not bounding) — FAIL.
  - `/api/status`: `degraded/model_configured:false` despite NVIDIA key — FAIL (G1).

Gap register: `docs/reports/second-pass-gap-register.md` (11×P0, 5×P1, 6×P2).

## Fixes (dependency order) + tests (fail-before → pass-after)

Contracts → runtime → store → orchestration/registry → attachments/cultural → frontend → tests.

| Gap | Fix (files) | Test (fails before, passes after) |
|-----|-------------|-----------------------------------|
| G1 status always degraded (`settings.api_key` AttributeError) | `sard/api/server.py:_is_model_configured()+_check_storage_readiness()`, `system_status` now checks real env keys + `storage:{durable,mode}` | `tests/test_gap_g1_status_truth.py` (2 tests) |
| G2 duplicate `orchestrate_from_intent` shim | `sard/outputs/orchestrator.py` deleted dead shim (804), kept intent version + `generate_artifact(deadline_monotonic)` | `tests/test_gap_g2_single_contract.py` |
| G3 no official Blob SDK | Added `VercelBlobArtifactStore` (`vercel.blob.BlobClient.put`, fallback REST→FS), default store now SDK-first; `/api/status.storage` truth; `runtime_paths` already handles `/tmp` ephemeral | `tests/test_gap_g3_blob_durable.py` (2) |
| G4 attachments process-local | Durable `UPLOAD_DIR/.attachment-index.json` + `_resolve_attachment_meta()` (memory→index→glob); upload persists index; chat + download use resolver | `tests/test_gap_g4_attachment_second_process.py` (canary PDF, wipe memory, resolve + download identical bytes) |
| G5 recipe empty sections | `tool_generate_recipe_or_craft_card`: unknown item + no inputs → `success:False/no_match` clarification | `tests/test_gap_g5_recipe_honesty.py` |
| G6 research hardcoded verified | `tool_conduct_verified_research`: added `verification:template`, `warnings:[template_timeline_not_verified]`, honest message | `tests/test_gap_g6_research_honesty.py` |
| G7 calendar first-4 canned | `tool_sync_heritage_calendar`: empty filters → `success:False/missing_filters`; `orchestrator.render_ics`: empty→`missing_filters`, no-match→`no_match` (no silent `[:4]`) | `tests/test_gap_g7_calendar_honesty.py` (2) |
| G8 craft silent sadu | `tool_advise_artisan_craft(craft_name="")`: empty→`missing_input`, unknown→`success:False` (kept) | `tests/test_gap_g8_craft_honesty.py` (3) |
| G9 registry docs lie | `sard/capability_registry.py`: audio/vision fallback → `capability_unavailable`/`provider_error`; ICS → no silent `[:4]`; `docs/capability-matrix.md` timeout/fallback table synced | `tests/test_gap_g9_registry_truth.py` |
| G10 JSON hangs >60s | `ChatService.ask(deadline_monotonic)` + fast-path: pure json/csv/txt skips RAG/web planner, renders deterministically in ms | `tests/test_gap_g10_json_deadline.py` (0.4s, parseable JSON) |
| G11 orphan after deadline | `ArtifactOrchestrator.generate_artifact(deadline_monotonic)`: expired→`failed/timeout`, no `store_bytes`; server passes `deadline=t_start+overall_timeout` to hybrid + direct paths | `tests/test_gap_g11_no_orphan_after_deadline.py` (zero new files) |
| P1-3 multimodal provider-error placeholder | `multimodal_tools.py`: vision/audio call-failure → `failed/provider_error` (was `core_fallback` generic) | covered by existing `tests/agent/test_multimodal.py` (still pass) |
| P1-4 slack 38→35 | `server.py` default `SARD_CHAT_OVERALL_TIMEOUT` 38→35 (client 50s ⇒ 15s slack); `.env.example` documents | timing probes below |
| P1-2 streaming flags stick | `src/app/page.tsx`: `clearTimeout` always + per-message `isStreaming:false` in final safety + `handleStop` | `npm run build` PASS |
| Test honesty (not weakening) | `tests/frontend/test_frontend_artifact_download_contract.py`: ICS topics → real `سهيل`; added `test_ics_no_match_is_failed_not_canned`. `tests/outputs/test_artifact_foundation.py`: ICS topic → `سهيل`. | 6 + 9 pass |

Frontend `ArtifactModal.tsx` hooks already at top (prior fix kept); modal shell minimal but functional — full 9-viewer restoration deferred (see limitations).

## Re-verification (after fixes, same commit worktree)

- `npm run build` PASS (65.5kB /, 168kB first load).
- Targeted: 42 passed (`test_gap_*` 16 + cultural 9 + pipeline 5 + fallback 12).
- Full offline (excl. `tests/evals` live): **630 passed, 1 skipped** in 326s (was 614+1; +11 gap tests +1 ICS honesty +4 strengthened = 630). One initial failure (`test_every_public_format...[ics]`) was canned-fallback reliance — fixed by using real topic, not by weakening.
- Live SSE (Uvicorn, `SARD_CHAT_OVERALL_TIMEOUT=35`, real `.env` keys):
  - `/api/status`: `ready/جاهز`, `model_configured:true`, `storage:{durable:false,mode:local_filesystem}` — PASS (was degraded).
  - PDF AR `أنشئ لي ملف PDF عن تاريخ نجد`: **8.8s**, 8×status + citations(5) + artifacts(1, 46876B, `%PDF`, sha `f4a765d1`) + ~100 delta + exactly 1 `done` (`verified:true`, 8797ms). Download 200 identical bytes. No late duplicate. PASS.
  - JSON `Create JSON output...`: **0.4s** (was >60s hang), artifacts(1, 184B, `{`, sha `665bcd04`), `done` 391ms — PASS G10.
  - DOCX Diriyah: 4.2s, 3796B `PK`, download OK — PASS.
  - PPTX/ICS pre-fix evidence holds (33379B/3894B); re-probe pending in CI with live keys.
  - Secrets: server logs sampled show `run_id` only, no keys — PASS.
  - Second-instance download: FS sidecars OK same-disk (`test_new_store_instance_resolves_id...` passes); cross-instance blob requires `BLOB_READ_WRITE_TOKEN` (unconfigured locally → `local_filesystem`, truthfully reported).

## Before/after timing trace (Arabic PDF `أنشئ لي ملف PDF عن تاريخ نجد`, redacted)

Before (35s deadline, prior worktree):
`status:init(0ms) → classify → locate → retrieving → assembling_isnad → scoring → deciding → generating → citations(5) → artifacts(1,46876B) → delta×~100 → done(4547ms, verified:true, artifacts:1)` — 1 terminal, no orphan observed, but status degraded + JSON hung.

After (35s deadline, fixed worktree):
`status:init(0ms) → ... (same 8 stages) → citations(5) → artifacts(1,46876B, sha f4a765d1) → delta×~100 → done(8797ms, verified:true)` — 1 terminal, download `%PDF` identical, no duplicate. 40s race stays fixed: backend 35s + client 50s = 15s slack; `max(2.0,remaining)` absent in `sard/`; `_SHARED_EXECUTOR` + `deadline_monotonic` discard guards orphans (G11 test: zero new files after expired deadline).

JSON before: `parallel_search 2797ms → hang → client TimeoutError at 60s (no done)`.
JSON after: `status → artifacts(184B JSON) → delta×2 → done(391ms)` — fast-path, no planner, parseable.

## Capability matrix

Regenerated truth from `sard/capability_registry.py`: `docs/capability-matrix.md` timeout/fallback rows updated (audio/vision `capability_unavailable`, OCR truthful, ICS no canned `[:4]`, json fast-path, research `template`). Each row linked to public-path test; `supported` still requires offline render+validate+store proof. Full regen script deferred (manual sync + `test_gap_g9` guard).

## Deployment / env (behavior changed)

- `vercel.json` unchanged (`maxDuration:60`) — correct.
- `.env.example`: documented `SARD_CHAT_OVERALL_TIMEOUT=35` (server default 35, client 50).
- `/api/status` now returns `model_configured` (real key check) + `storage:{durable,mode,ephemeral_host}` (`durable_blob` only with `BLOB_READ_WRITE_TOKEN`/`SARD_BLOB_*`; Vercel without token → `ephemeral_unconfigured`, truthfully degraded).
- `.gitignore`: added `.swarm/*.shm/wal`, `scratch/`, `output/uploads/.attachment-index.json`, `output/artifacts/`, `.next/`. `scratch/` + `.swarm` DBs remain untracked, never commit. No secrets, transient artifacts, test uploads, or local indexes committed (verified `git status --short`: only intended `M` + new `tests/test_gap_*` + reports).

## Deployed-preview smoke (credentials-gated)

Not claimed: `BLOB_READ_WRITE_TOKEN` + `DASHSCOPE_API_KEY` unset locally; Vercel preview requires owner secrets. Checklist for owner (do not claim verification until run):
```
# Vercel env (dashboard → Settings → Environment Variables):
BLOB_READ_WRITE_TOKEN=<vercel-blob-rw-token>
DASHSCOPE_API_KEY=<dashscope-key>   # else vision/ASR/OCR honestly capability_unavailable
NVIDIA_API_KEY=<nvidia-key>         # else chat honestly ok=False + failed artifacts
PARALLEL_API_KEY=<parallel-key>     # else web_research evidence_limited
SARD_CHAT_OVERALL_TIMEOUT=35
# Deploy preview, then:
curl -s https://<preview>/api/status | jq .status,.status_label,.model_configured,.storage
curl -N -X POST https://<preview>/api/chat -H 'Content-Type: application/json' -d '{"query":"أنشئ لي ملف PDF عن تاريخ نجد"}' | grep -E '^event: (artifacts|done)'
# Expect: status ready (or degraded with explicit reason), 1 artifacts + 1 done, PDF %PDF download 200, checksum match across two instances.
```

## Remaining limitations (issues with owner + acceptance)

1. **Cross-instance durability without token** (owner: platform): local FS + `/tmp` ephemeral on Vercel; second instance download only with `BLOB_READ_WRITE_TOKEN`. Acceptance: set token, store PDF via instance A, `GET /api/artifacts/<file>` via instance B returns identical sha. Status truthfully reports `ephemeral_unconfigured` until then.
2. **Modal rich viewers minimal** (owner: frontend): hooks fixed, open/close works, but 16:9 deck/recipe 2-col/lore box simplified. Acceptance: browser test opens each of 9 formats ×5 + Strict-Mode double-mount without hook error + download works.
3. **Research is template, not verified** (owner: RAG/research): `tool_conduct_verified_research` labeled `template`; real provider-backed research needs `PARALLEL_API_KEY` + RAG grounding. Acceptance: with key, research returns filtered citations with URLs + `verification:provider`; without, explicit template warning (current).
4. **Live-provider suite separate** (owner: QA): offline suite never consumes real keys (`models.py` skips `.env` under `PYTEST_CURRENT_TEST`; `server.py` still loads `.env` — tests inject mocks). Acceptance: `RAG_LIVE_SMOKE=true` + keys runs `tests/evals` + browser suites green; offline `uv run pytest --ignore=tests/evals` stays green.
5. **Orphan proof at +60s under load** (owner: runtime): unit discard proven (G11), SSE single-terminal proven, but full worker/thread snapshot before/at-timeout/+60s under induced 40s-model-hang needs dedicated timing probe in CI. Acceptance: output-dir snapshot + thread count at +60s shows zero new files after `done`.

## Verdict

Not production-ready until P0 rows all green in deployed preview with tokens: PDF/DOCX/PPTX/ICS/JSON/CSV/TXT valid+durable, canary bytes consumed, honest `capability_unavailable`, single terminal timeout with zero orphans at +60s, idempotent retry, reload restore, Strict-Mode modal, second-instance checksum match. Code now enforces these contracts; local evidence above passes except deployed-preview (credentials-gated) + browser E2E (needs Next preview + Playwright).

Worktree: HEAD `da11050` + uncommitted fixes (`git diff --stat HEAD` ≈ 15 files + 11 new gap tests + 2 reports). No commit per policy (no explicit request); hashes: HEAD `da11050bd955ff6146da2669334774ab0baa79d1`, worktree diff available via `git diff HEAD --stat`.
