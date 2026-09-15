# Sard release coordination ledger — 2026-09-15 (artifact-overhaul completion run)

Coordinator backend: opencode/muse-spark-1.3-contributor-free (xhigh). Codex out of gas — not used.
Integration branch: `work/release-integration` off `release/sard-artifact-overhaul` @ `dcb4863`
  (worktree `C:\Users\nawaf\.herdr\worktrees\Sard_Agent\work-release-integration`, workspace `wK`).
Primary checkout `main` @ `bb042e8` left untouched (dirty user state preserved).

## 1. Preserved-worktree inventory (all panes closed 2026-09-15, worktrees kept)

| Worktree | HEAD | Branch | State |
|---|---|---|---|
| release-sard-artifact-overhaul | `dcb4863` | release/sard-artifact-overhaul | dirty: `.claude-flow/metrics/*`, `.claude-flow/policy/state.json`, `.gitignore` (+`.vercel`,+`.env*`) — worker residue, NOT integrated |
| work-release-model-retrieval-fix | `e435dd5` | work/release-model-retrieval-fix | 1 unintegrated commit on top of `dcb4863` (13 files, +2802/-328) |
| work-release-artifact-backend-fix | `315bffb` | work/release-artifact-backend-fix | 1 unintegrated commit on top of `dcb4863` (14 files, +2795/-323) |
| work-release-bakeoff-docs | `dcb4863` + dirty | work/release-bakeoff-docs | dirty: 3 doc redactions + 2 test additions (tracked); evolved bakeoff files (untracked, newer than primary copies) |
| work-release-frontend-fix | `4e21bce` | work/release-frontend-fix | 1 unintegrated commit on top of `dcb4863` (7 files, +1620/-208) |

Rev0 lanes (arch-audit, model-audit, storage-audit rev2, frontend-audit2 rev3, renderer-audit rev3)
left no worktree checkouts under `.herdr/worktrees/Sard_Agent` — no output to salvage; treated as no-output per brief.

## 2. Known finding (a): intent_router.py — COVERED, no action

- `sard/agent/intent_router.py` never existed in any branch history (`git log --all` empty).
- Intent classification lives in `sard/agent/capability_routing.py` (Capability enum + structured
  intent: domain capability, artifact formats, modalities, interactive modes).
- Cultural grounding pipeline lives in `sard/agent/cultural_router.py` (rag_search-first,
  freshness/relevance routing rules, web search budgets).
- Graph-edge routing lives in `sard/agent/routing.py` (compose→verify retry).
- Recovery prompt assignment was stale. No file to create.

## 3. Known finding (b): cultural_tools.py Zvec import — JUSTIFIED, no fix

- `sard/agent/tools/cultural_tools.py:180,189` are function-local lazy imports inside `rag_search()`,
  wrapped in try/except with fallthrough to the bundled retriever.
- Step-5 boundary bans module-level NVIDIA/Zvec imports in `nodes/`, `graph.py`, `routing.py`,
  `state.py` only — `tools/` is outside it, and `git grep` on `dcb4863`, `e435dd5`, `315bffb`
  confirms the boundary holds (only docstrings + one `"nvidia"` string comparison).
- Module-level `ZvecRepository` imports already exist in `sard/rag/{service,retrieve,ingest}.py`
  and `sard/cli/rag.py`; the lazy pattern here is strictly more careful (no hard dep).
- Decision: keep. No change.

## 4. Bakeoff provenance (primary untracked files)

- Primary `docs/reports/model_bakeoff_20260915.md` (10,342 B), `..._routing_....json` (9,897 B),
  `evals/bakeoff_runner_20260915.py` (6,346 B), `evals/bakeoff_suite_20260915.json` (5,690 B),
  all dated 04:39 = early workstream-C snapshot delivered to primary.
- `work-release-bakeoff-docs` holds NEWER evolved versions (12:02–12:12): md +3,853 B (+100/-51 lines),
  runner +12,941 B (+438/-91), suite +1,289 B (+174/-1). Review before adopting; full 11×N bakeoff
  still NOT run (report status `HYPOTHESIS_PENDING_LIVE_BAKEOFF`, smoke T1-only).
- Routing table `sard/config/routing_table.py` (release) must be cross-checked against the smoke table
  (Lightning 140 s, LFM reasoning-channel, Inkling 403, Gemma 429) — open audit item.

## 5. Salvage decisions (pending lane verification)

- KEEP candidate `e435dd5` (deadlines/transient-retry/breaker/fanout/cancel) — verify tests/agent green.
- KEEP candidate `315bffb` (revision contract, canonical PDF routing, RTL remediation, CORS/SSE hardening).
- KEEP candidate `4e21bce` (revision UX, iframe sandbox, ICS, version history).
- KEEP bakeoff-docs redactions (3 doc files: dev Parallel key → `[REDACTED]`) as forward-fix commit.
  NOTE: the dev key value is ALSO on `main` (pre-existing since August merge); release redacts forward,
  no history rewrite without user approval.
- KEEP bakeoff-docs `tests/test_secret_redaction.py` additions (2 tests) if they pass.
- DROP release worktree `.claude-flow/metrics/*` + `policy/state.json` dirt (runtime state, never commit).
- MAYBE `.gitignore` `+.vercel` line only (`.env*` already covered on release tip).
- DISCARDED: nothing else yet; rev0 lanes had no checkouts.

## 6. Verification lanes (this run)

- `verify-model-lane`: work-release-model-retrieval-fix — ruff + pytest remediation/model-router/matrix/agent.
- `verify-artifact-lane`: work-release-artifact-backend-fix — ruff + pytest revision/rtl/outputs.
- `verify-frontend-lane`: work-release-frontend-fix — node tests + tsc.
- All lanes read-only: no edits, no commits, no implementation. Report model ID, command, SHA,
  counts, duration, failures verbatim.

## 7. Lane evidence (2026-09-15)

- `verify-model-lane` (opencode/muse-spark-1.3-contributor-free, xhigh) @ `e435dd5`:
  ruff 12 errors (all pre-existing S110/F401/F841 class); remediation+router+matrix 70/70;
  tests/agent 48 passed 1 skipped + 1 failure (`test_tool_generate_presentation` — proven
  pre-existing on `dcb4863` baseline run, caused by filler-refusal vs stale success expectation).
- `verify-artifact-lane` (same backend) @ `315bffb`: revision+rtl 23/23; full tests/outputs 115/115.
- Frontend lane: opencode TUI deterministically crashes (Bun 1.3.14) in
  `work-release-frontend-fix`; coordinator executed the same commands directly @ `4e21bce`:
  node normalize+remediation 16/16, `npm test` (sse_parser) 5/5, `npx tsc --noEmit` clean.
- Focused gate on `1bc034c`: 232 passed, 1 xpassed, 0 failed (118.95 s).
- Full suite on `1bc034c`: 860 passed, 2 skipped, 1 xpassed, **8 failed** — all 8 reproduced
  identically on pristine `dcb4863` probe checkout (zero regressions from integrated commits).

## 8. Pre-existing failure repairs (all on `work/release-integration`)

- `test_tool_generate_presentation` + e2e endpoint: tool now returns honest
  `{"success": False, "error": "empty_content"}` on DeckBuildError (was raw 500/raise);
  tests updated (success-with-content + refusal cases). Strengthens honesty rule.
- `test_state_machine` x2: rewrote to deterministic-first contract (unknown-CIT strip ->
  recompose -> completed; persistent failure -> exhausted partial). Proves retry loop live.
- `test_fixes_verification` locale x2: mocks gained deadline/deadline_monotonic/cancel_event
  (real `plan_and_execute` contract). Wart (not blocking): timeout heuristic matches the
  substring "deadline" in TypeError text — only reachable with stale mocks, left as limitation.
- `test_10e_html_as_stored_format_xfail`: removed xfail (HTML is stored since `dcb4863`), renamed.
- step7/step8 x3: adopted reviewed `eaf7ae8` demo.py line-ending canonicalization (+ its tampering
  regression test); dropped the run-specific handoff doc. step7 failure was collateral of the
  broken cache load.
- Ruff S110/F841/F401: all fixed (logger.debug pattern); `ruff check sard/ tests/` clean.

## 9. Open items

- Evolved bakeoff files in `work-release-bakeoff-docs` (+438 runner): review then adopt or drop.
- Full gate re-run on final SHA; frontend `npm run build`; failure-matrix / QA / perf evidence.
- Secret scan; Vercel preview + Blob proof; independent judge; push; smoke; final report.

## 10. Renderer findings from live QA probe (fixed on `work/release-integration`)

- PDF body duplication: `build_pdf_from_document` fed the same text as flat `paragraphs`
  AND per-section `content` into `render_document_pdf`, which renders both — every
  chat-path PDF repeated its body (proven by rendered PNG inspection). Fixed: sections-only
  body; lone flat section folds to untitled body (no `◆ <title>` echo); untitled sections
  emit no header/HR. Commit `3e8b260`.
- Tables only in PDF: markdown tables stayed raw paragraphs for HTML/DOCX/PPTX. Fixed:
  `document.parse_markdown_table` (GFM + lenient no-separator) creates one shared TableBlock
  in `from_request`; all four renderers already consumed `data.rows`. Proven: DOCX 1 table
  3x3, PPTX 1 table slide, HTML `<table>`, PDF table, all from one block. Commit `3e8b260`.
- QA evidence (1.5 s total): PDF 32933 B/1 page/Arabic extracted, DOCX 37301 B/PK/19 parts,
  PPTX 30773 B/3 slides, HTML 4428 B/RTL, revision v1->v2 with v1 intact.
- Regression tests added: `test_markdown_table_parsed_to_table_block`,
  `test_from_request_table_renders_once_in_every_renderer`,
  `test_single_section_pdf_has_no_title_echo_or_body_dup`.

## 11. Production-wiring revision gap (found live, fixed)

- Browser E2E (real Chromium, dev UI + local API): chat TXT generation, workspace
  preview, and UI download all work; revision submit failed `unknown_artifact`.
- Root cause: the DEFAULT store (`VercelBlobArtifactStore`) and `ConfigurableBlobArtifactStore`
  never implemented `put/get_document` + `list/get_version_bytes`, while the orchestrator
  guards with getattr-None. Tests inject FileSystem stores, so they passed; production
  wiring always failed revision. Test/prod divergence.
- Fix: both blob stores delegate the version/document surface to their local mirror
  (commit `522a7a8`) + regression test over both classes.
- Live proof after restart: chat created art-d301d109982f; `/api/artifacts/revise` 200 created;
  versions count=2; v1 bytes intact; v2 new. E2E evidence: desktop-after-gen.png (workspace),
  ui-download.txt (64 B real bytes), chat_raw.txt (SSE progress + done 91 ms).

## 12. Final integration and release gates

- `origin/main` @ `3c9d738` was merged into `work/release-integration` as `0ac8be9`; cultural proposal, relevance, and offline-serving changes were retained alongside artifact deadlines, revision storage, and renderer fixes.
- Final integration fixes committed as `516d69d`: restored session-scoped follow-up retrieval, planner evidence filtering, typed organization proposals over SSE, medical qualification, fail-closed product rendering, uncertainty-only fallback text, and unconstrained web dedup compatibility.
- Python: `uv run pytest -q` passed with exit code 0; `uv run ruff check sard/ tests/` passed with exit code 0.
- Frontend: `npm run lint`, `npm run typecheck`, `npm run build`, and `npm test` passed; Node suite 11/11.
- Release tree is clean. Primary checkout-only `.claude-flow` runtime state, `.swarm/hnsw.index`, `.zed/`, and `pyrightconfig.json` were not included.
- Publish target: tested `work/release-integration` HEAD `516d69d` to `origin/main`.
