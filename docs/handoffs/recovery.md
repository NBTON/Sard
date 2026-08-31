# Handoff — Wave 1 Recovery Audit

Date: 2026-08-31 07:51 +0300 • Worker: `herdr/recovery-wave1` • Commit: `5c5dc22a01c606e5cff072fe6356ecf9cc1e5a49` • Base: `main` `2b30673` • Auditor: `opencode/muse-spark-1.2-contributor-free` (xhigh, read-only)

## Root causes (from `docs/diagnostics/agentic-rag-artifact-root-causes.md` `fc2c828`)

- **R1** empty-model shrimp contamination: `_generate_cultural_fallback_answer` + `cultural_router._synthesize_grounded_answer` returning raw top RAG chunk (Eastern Province shrimp/spring) for unrelated queries (Asir/Qasīm, photosynthesis).
- **R2** artifacts only in `use_hybrid_retrieval=True` branch (`chat_service:206-253`); direct fallback (`server:618` `use_hybrid=False`) drops `requested_formats` — PDF/DOCX silently lost.
- **R3** `_CORPUS_KEYWORDS` hard list biases routing — non-Eastern always forces web search.
- **R4** `_DETERMINISTIC_EQUIVALENTS` injects shrimp/spring synonyms.
- **R5** broad `except Exception` + empty `text_resp` swallow errors, lose citations.
- **R6** `FileSystemArtifactStore` defaults to `output/` but Vercel uses ephemeral `/tmp`; `ConfigurableBlobArtifactStore` never used remote (stub).
- **R7/R8** process-global `QueryRewriteService._cache`, history-borne contamination, SSE `artifacts` only if truthy, never surfaces `failed`.
- Diagnostics ranks R1/R2/R3 as High-confirmed, others M/H.

## Files changed

- This handoff + diagnostic report only on `herdr/recovery-wave1` (no merges):
  - `docs/diagnostics/herdr-wave1-recovery.md` (new, 143 lines + body) — full worktree/branch/patch/test inventory.
  - `docs/handoffs/recovery.md` (this file).
- Audited branches (not merged here, patches backed up to `C:\Users\nawaf\AppData\Local\Temp\opencode\sard-recovery-patches`):
  - `fc2c828` (ao/sard_agent-3/root): `docs/diagnostics/agentic-rag-artifact-root-causes.md`
  - `ebb3b1a` (ao/sard_agent-4/root): 12 files incl. `sard/rag/retrieve.py`, `rerank.py`, `schemas.py`, `config/rag.py`, `query_rewriter.py`, `agent/cultural_router.py`, `zvec_store.py`
  - `0fa6f1e` (ao/sard_agent-5/root): 6 files incl. `sard/outputs/validation.py`, `orchestrator.py`, `artifacts.py`, `runtime_paths.py`

## Commit hash & branch

- Recovery branch: `herdr/recovery-wave1` from `main` `2b30673`.
- Recovery commit: `5c5dc22a01c...` `docs(diagnostics): herdr wave1 recovery audit from main 2b30673` (commit succeeded; warnings about `.git/worktrees/sard_agent-2|21|orchestrator` permission denied are harmless — branch was created correctly).
- Useful commits remain on origin AO branches: `fc2c828`, `ebb3b1a`, `0fa6f1e` — all diverge cleanly from `2b30673`, disjoint file sets, `git merge-tree 2b30673 ebb3b1a 0fa6f1e` shows no conflicts.

## Tests

- **No code edited.** `uv run pytest` on `main` (Python 3.11.15):
  - `tests/rag/test_retrieve.py`: 6/6 passed.
  - Full focused suite (rag + capability + chat_service + runtime_paths + api, `--ignore=tests/outputs/test_agentic_outputs.py`): **142 passed, 1 skipped**.
  - `tests/outputs/test_step6_artifacts.py`: 15/15 passed.
  - `test_step6 + test_pdf` combined: **32/32 passed**.
  - `--collect-only` (ignoring broken agentic_outputs): 478 tests collected.
- Pre-existing failure on `main`: `tests/outputs/test_agentic_outputs.py` `ImportError: circular import ArtifactOrchestrator` (outputs ↔ agent cycle) — present on `2b30673`, fixed in `0fa6f1e` via `TYPE_CHECKING` guard (not merged here yet).
- Branch artifact foundation tests (`tests/outputs/test_artifact_foundation.py` in `0fa6f1e`) not executed on `main` to keep audit read-only; branch read + `merge-tree` verified clean.

## Metrics

- Uncommitted/untracked files: **0** across all worktrees + main (`git status --porcelain` empty).
- Patch backups: 3 mbox patches + 2 diffs + stats + manifest in `sard-recovery-patches` (outside repo, ~320 KB total).
- Wave-1 diff sizes: `ebb3b1a` +682/−81, `0fa6f1e` +832/−88, `fc2c828` +334.
- Recovery report: 109 lines (under 500-line guideline) + detailed handoff here.
- Model variant: `xhigh` (512 reasoning) confirmed via invocation + `opencode debug info` `1.18.25`.

## Limitations

- Read-only: no merges, no AO worktree mutation, no `reset --hard`/`clean`, no worktree deletion, no secrets/`.env`/vector-DB/cache archiving.
- No live LLM/RAG network calls, no `BLOB_*`/`SARD_*` secret inspection, no Vercel cold-start repro, no AO venv activation for branch-local pytest.
- Carry-over gaps not fixed in Wave-1: hybrid→direct SSE artifact gating (`server.py`/`chat_service.py`), session-scoped rewrite cache, frontend `page.tsx` contract tests — see `herdr-wave1-recovery.md` §4/§7 for integration order.

## Next steps (recommended integration order, no merge performed)

1. `cherry-pick fc2c828` (docs), 2. `cherry-pick ebb3b1a` (RAG), 3. `cherry-pick 0fa6f1e` (artifacts) — or merge AO branches in same order. Then tackle SSE gating + cache session isolation + Vercel blob provisioning.
