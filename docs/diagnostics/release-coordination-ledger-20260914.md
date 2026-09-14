# Sard release coordination ledger — 2026-09-14

## Baseline

- Repository: `https://github.com/NBTON/Sard.git`
- Local branch / HEAD: `main` / `bb042e8c0df3690caded98893b0fdb70a6a36cee`
- `origin/main`: `bb042e8c0df3690caded98893b0fdb70a6a36cee` (0 ahead, 0 behind)
- Pre-existing user-owned untracked paths: `.zed/`, `pyrightconfig.json`
- Python workflow: `uv sync --extra nvidia --extra anthropic --extra openai --extra dev`; `uv run ruff check sard/ tests/`; `uv run pytest -q`
- GitHub run: `34763924341`; frontend PASS; Python FAIL at pytest with 6 failed, 642 passed, 2 skipped.
- Vercel deployment: `dpl_B9GySqHpAGEgFtaAedJrdrTzX3qc`; CLI 59.17.0 unavailable without device authentication. No login was performed.
- Tools: Herdr `0.9.0-preview.2026-09-08-62431dbd033b`; Antigravity CLI `1.2.2`; OpenCode `1.18.31`; Codex CLI `0.154.0-alpha.6.2`; uv `0.12.10`; Node `24.19.0`.
- Coordinator local Ruff: PASS. Local pytest: 649 passed, 1 skipped, 2 warnings in 478.61s; this contrasts with clean CI and proves environment sensitivity.
- Coordinator `uv sync`: locally blocked by an existing Windows `.venv` directory ACL/lock; isolated worktrees are used for clean verification.
- Frontend baseline: `npm ci` PASS (470 packages); lint PASS; typecheck PASS; Next 15.5.23 production build PASS; browser/contract matrix 61/61 PASS.
- `npm ci` reported 3 audit findings (2 high, 1 critical); no unreviewed automatic audit rewrite was applied.

## Resolved agents

| Name | Herdr kind/provider | Exact model | Reasoning | Branch | Workspace / pane | Assignment | Status |
|---|---|---|---|---|---|---|---|
| `ci-python-worker` | `agy` / Antigravity | `gemini-3.8-flash-high` = Gemini 3.8 Flash (High) | high | `herdr/ci-python-repair-20260914` | `wF` / `wF:p1` | Python CI | PASS, integrated, closed |
| `vercel-worker` | `opencode` / OpenCode | `opencode/muse-spark-1.3-contributor-free` | xhigh (TUI footer verified) | `herdr/vercel-deployment-repair-20260914` | `wG` / `wG:p1` | Vercel | PASS, integrated, closed |
| `cultural-worker` | `codex` / Codex | `gpt-5.6-luna` | xhigh | `herdr/cultural-integration-repair-20260914` | `wH` / `wH:p1` | Cultural integration | remediating CHANGES_REQUIRED |
| `sol-judge` | `codex` / Codex | `gpt-5.6-sol` | high | `herdr/judge-20260914` | `wJ` / `wJ:p1` | Independent review | idle between reviews |
| `cultural-reviewer` | `opencode` / OpenCode | `9router/frontier-coding` (model deviation, see log) | 256 (no xhigh setting applies) | `herdr/cultural-review-20260914` | `wM` / `wM:p1` | Cultural independent review | PASS, read-only, no commits |

Herdr lifecycle integrations for Codex, OpenCode, and Antigravity were updated to the installed current hook revisions before launch. Every worker and reviewer was explicitly prohibited from spawning nested agents.

## Frozen ownership

- CI: `.github/workflows/**`, `sard/application/demo.py`, `sard/application/service.py`, packaged demo-cache assets/manifests, CI tests, unique handoff.
- Vercel: `vercel.json`, `api/**`, `requirements.txt`, `next.config.*`, `package*.json` when evidence requires, deployment tests, unique handoff.
- Cultural: `sard/agent/**`, `sard/rag/**`, `sard/api/server.py`, cultural/API/SSE tests, narrowly necessary RTL UI, unique handoff.
- Coordinator: this ledger, integration/reconciliation, release verification and delivery.
- Judge: read-only review; no implementation authorship.

Shared-file changes must be proposed in handoffs and reconciled by the coordinator; no simultaneous overlapping edits are authorized.

## Integration / review log

- Vercel worker commits `33f052af902aa315dabc2b7f64343d2b7a4520b4` + handoff `18aabc17c04f3299a6c7227a8009b80f5ac6b8d5`. Sol verdict: **CHANGES_REQUIRED**. Main issue: real log proves Python function discovery failure, but duplicate-glob causality and the synthetic matcher are not sufficient proof. Returned to the same worker for corrected claims and real Vercel build/preview evidence.
- Python CI worker amended commit `d16f88bb748ee429124eb0ed977dff1699e16df4` after coordinator caught trailing whitespace in the first commit. Evidence: exact sync PASS, Ruff PASS, application suite 117 PASS, base-to-head diff check PASS. Submitted to Sol for review.
- Sol Python CI verdict on `d16f88bb748ee429124eb0ed977dff1699e16df4`: **CHANGES_REQUIRED**. The line-ending canonicalization is sound, but the unrelated unvalidated `service.py` in-memory byte path must be removed or fully typed/verified/tested; add a negative tampering regression and correct the handoff. Returned to the same worker.
- Python CI remediation commit `8f97f4295b788a107a88a6254a37fe254074af1e`: removed the unrelated `service.py` change, added same-length tampering rejection, and retained only platform-independent cache canonicalization. Sol re-review: **PASS**. Cherry-picked into release as `eaf7ae8`; coordinator focused verification: Ruff PASS and 7 tests PASS. Worker workspace `wF` closed.
- Vercel remediation head `906a48c0dfec4f7ff26068f2fef4e44cdf70bbc0` (including `33f052af` and `615972b`): corrected the root-cause claim to the observed function-discovery failure, made `api/index.py` an explicit FastAPI app export, removed the overlapping broad function glob, and replaced the synthetic proof with repository-policy tests. Exact Vercel 59.11.7 build attempt was externally blocked by invalid/missing CLI authentication before build. Local npm CI/lint/typecheck/build and 11 Node tests passed. Sol re-review: **PASS**, with successful post-push Vercel discovery/build and API probes retained as release gates.
- Vercel commits were integrated one at a time as `0807b62`, `a9f7eb1`, `82ad199`, and `edfcf60`. Coordinator checks: 6 direct Vercel contract tests PASS, FastAPI entrypoint import PASS, then wired npm test 11/11 PASS; release diff check PASS. Workspace `wG` closed.
- Cultural first commit `7c8231371dd8b024401c2ee0ee4bba8dfa538e4f`: Sol verdict **CHANGES_REQUIRED**. Blockers: no explicit source-backed organization-mandate/product-proposal model, and removed evidence could remain in already-generated prose. Major findings: brittle duplicate entity matching, Asir/Jazan/Najran leakage, valid direct-path history discarded, and missing real-retriever/public-SSE acceptance coverage. Independent evidence: focused 55 PASS, Ruff PASS, Node 61 PASS, but the actual combined prompt returned one topic with no organization proposals; several paraphrase/isolation probes failed. Findings returned to the same Luna worker for remediation; commit is not integrated.

## Coordinator takeover (OpenCode Muse 1.3 xhigh, 2026-09-15)

Previous Codex coordinator hit its usage limit with the cultural worker
(`wH`, Codex Luna) mid-remediation: uncommitted changes on
`herdr/cultural-integration-repair-20260914` plus untracked
`sard/agent/proposals.py`. Codex was not used further, per policy.

- Snapshotted the worker's in-progress state as `b71cb6b` (wip, no behavior
  claims), then inspected live retrieval: single-topic shrimp/springs queries
  returned ZERO hits (expanded-term denominator collapse in the local-corpus
  scorer) and the combined request produced 21 undiscriminating proposals.
- Fixed forward on the same branch, head `f9e724a` (`feat(cultural):
  source-grounded per-topic proposals, pre-synthesis gating, history safety,
  SSE acceptance`):
  - local-corpus match ratio uses user base terms; expansion is recall-only;
  - typed mandate proposals with distinguishing-capability gates, per-topic
    isolation, mandate-`product_dimension` products;
  - strict `بن` token matching; strong-alias coffee anchors; split southern
    provinces; coastal Eastern aliases; metadata-only region keys;
    national-practice scoping with fail-closed fallback;
  - web-hit relevance gate before planner synthesis; topic-aware post-filter
    (trims sources, clears synthesis only when a topic loses all support);
  - shared same-session history helper on both paths; client `role="system"`
    never promoted; multi-topic deterministic synthesis; SSE `proposals`
    event + `proposals_count`.
- New coverage: `tests/agent/test_cultural_acceptance.py` (13 tests) and
  `tests/test_chat_sse_cultural.py` (4 tests): normalization, paraphrases,
  reordered topics, province isolation, cross-topic isolation, unsupported-org
  exclusion, abstention, history, system-role injection, real bundled/local
  retrieval, public `/api/chat` SSE.
- Independent review: fresh OpenCode reviewer in clean worktree/branch
  `herdr/cultural-review-20260914` (`wM`/`wM:p1`), read-only, ran its own
  tests/probes. Verdict: **PASS**. Model deviation (honestly disclosed by the
  reviewer and recorded here): the environment routed to
  `9router/frontier-coding`, not Muse 1.3 xhigh. Independence (fresh context,
  own runs, reproduced evidence) is satisfied; model identity is not the
  requested one. Reviewer evidence: focused pytest 70 passed / 0 failed;
  `ruff check sard/ tests/` clean; principal combined prompt through real
  path covers all 4 topics with proposals and zero Fashion/Museums/Film
  leakage; Asir yes / Jazan no / Najran no; paraphrases 4/4; follow-up
  resolves same-session and fails closed cross-session; banned
  `_TOPIC_MANDATE_CAPABILITIES` and `len(topics)!=1` collapse absent.
- Integrated onto `herdr/release-integration-20260914` as `5b8f2e7`,
  `93c6a35`, `25035ca` (clean cherry-picks, no conflicts, no ours/theirs
  resolution needed). The rejected `7c82313` was included only as the base of
  the reviewed stack; the PASS verdict applies to the cumulative tree at
  `f9e724a`/`25035ca`.

## Final release validation (release branch `herdr/release-integration-20260914`)

- `ruff check sard/ tests/`: PASS. `git diff --check`: clean.
- Focused (cultural acceptance + integration, SSE cultural + e2e, chat
  invariants, model/session, RAG, API, CI demo-cache): **200 passed,
  1 skipped**.
- Full Python suite: **662 passed, 2 skipped, 0 failed** in ~142s
  (excludes the 17 new tests already covered in the focused run; no overlap
  double-counted in the total claim).
- `from api.index import app` is a FastAPI application: PASS.
- `npm.cmd test` (SSE parser + Vercel contract): **11/11 PASS**.
  `npm.cmd run lint`: clean. `npm.cmd run typecheck`: clean.
- `npm.cmd run build`: compile + lint + types succeed; static-page
  generation fails with `spawn UNKNOWN` (errno -4094). Reproduced identically
  on unmodified `main`, so this is environmental (Node worker spawn blocked in
  this Windows environment), not a patch regression. Recorded as an
  environment limitation, not a code failure.
- Authenticated Vercel CLI build/deployment: **external blocker, not
  performed**. No Vercel CLI is installed in this environment and no
  login/authentication was available. Repository-policy Vercel contract tests
  (11/11) are the standing local evidence; post-push Vercel discovery/build
  and API probes remain release gates per the earlier Sol PASS note.

## Final release state

- Release branch `herdr/release-integration-20260914` at `25035ca` + this
  ledger commit; worktree clean; ahead of `origin/main` by 9 commits
  (5 prior + 3 cultural + ledger).
- Workstreams: Python CI repair accepted; Vercel repair accepted (modulo the
  external auth blocker); cultural integration remediated, independently
  reviewed PASS, integrated, validated.
- Verdict: **READY WITH EXTERNAL BLOCKER** (authenticated Vercel
  build/deploy not performed; `next build` static generation environmentally
  blocked for local reproduction only).
