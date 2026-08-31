# Handoff — RAG & Search Worker (herdr/rag-repair)

Date: 2026-08-31 • Worker: `herdr/rag-repair` • Base: `herdr/sard-agent-repair` (Wave1: fc2c828 + 84691c7 + 449c8de + e0df96f) • Model: `opencode/muse-spark-1.2-contributor-free` (xhigh, verify variant) • Worktree: `C:\Users\nawaf\.herdr\worktrees\Sard_Agent\herdr-rag-repair`

## Root causes (Wave1 carry-over + current audit)

- **R1** empty-model shrimp/springs contamination via `_generate_cultural_fallback_answer` / `_synthesize_grounded_answer` returning raw Eastern chunk for unrelated (Asir/Qassim/photosynthesis) – Wave1 fixed via `calibrate_candidate_confidence` + `is_relevant` filtering + region rejection, but remaining gaps were: raw BM25 vs calibrated scale mixing in `cultural_tools.rag_search` (BM25 0.7 passed threshold but calibrated 0.43 should fail), incomplete 13-region mapping, missing week freshness terms.
- **R2** query rewriting pilot injection: `_PILOT_TOPIC_ENTITIES` + `_DETERMINISTIC_EQUIVALENTS` could inject shrimp/springs into variants; sanitizer `_sanitize_search_variants` existed but coverage not proven across 20+ queries (Asir, Qassim, Najd, Hijaz, English/mixed).
- **R3** score-type conflation: `dense` cosine (‑1..1), `fts` BM25 (0..∞), `lexical` match_ratio (0..0.95), `rerank` relevance (0..1) compared as one `score >=0.65` without `ScoreType` gate – `zvec_store` set `score_type` as raw strings, `cultural_router` compared generic `score` without type check.
- **R4** freshness under-coverage: `_FRESHNESS_PATTERN` missed `هذا الأسبوع/الأسبوع/أسبوع/week` – query `ما الفعاليات في الرياض هذا الأسبوع؟` would not trigger live search.
- **R5** citation provenance gap: `cultural_router.answer_query` citations for `web`/`media` lacked `topic`, `region`, `channel`, `snippet` uniformity – public contract requires all citations to expose `id,title,url,snippet,topic,region,channel,score,score_type`.
- **R6** `diagnose_collection_compatibility` existed in `zvec_store` but only used in `RAGService.open_readonly` missing-path branch; `cultural_tools.rag_search` fast FTS path never surfaced diagnostic for observability.
- **R7** `region_clusters` covered only 8 clusters, missing `الباحة`, separable `نجران`, `تبوك`/`حائل` split, etc. – out-of-corpus queries for those regions could leak Eastern pilot docs via default `eastern` fallback when `query_regions` empty-ish.
- **R8** no 13-region evaluation: existing `evals/test_cultural_search_rag.py` covered 5 out-of-corpus + 2 in-corpus + 2 ood – no coverage for all 13 admin regions, English/mixed, aggregate metrics (Recall@K, MRR/nDCG, ood rejection, irrelevant-context, web-routing, citation precision, contamination).

Wave1 already fixed: `calibrate_candidate_confidence` with dual-channel logic, `is_relevant` gating (`min_evidence_confidence 0.60`), `_scan_local_cultural_corpus` region+topic rejection, removal of `_CORPUS_KEYWORDS` hard list, `_sanitize_search_variants` pilot strip. This iteration hardens scale separation and coverage.

## Files changed (owned only, verified no shared edits)

- `sard/rag/schemas.py:129` – added `ScoreType.LEXICAL` and `ScoreType.WEB` to enum; clarifies calibrated vs lexical vs web scales.
- `sard/rag/retrieve.py:30,143` – import `ScoreType`, set `candidate.score_type = ScoreType.CALIBRATED_CONFIDENCE.value` via enum (was string literal).
- `sard/rag/zvec_store.py:598,625` – set `dense`/`fts` `score_type` via `ScoreType` enum with comment “never compare without calibration”.
- `sard/rag/rerank.py:123` – set `ScoreType.RERANK.value` and document “never compare to dense threshold”.
- `sard/agent/cultural_router.py:19,34,240-315` – import `ScoreType` + `_infer_cultural_metadata`; freshness pattern expanded to cover `هذا الأسبوع/الأسبوع/أسبوع/week/this week`; multimodal/rag/web citations now all emit full provenance `{type,id,title,url,snippet,topic,region,channel,score,score_type}` via `ScoreType`; rag citations normalize `score_type` through enum.
- `sard/agent/tools/cultural_tools.py:24,144-263,295-410` – import `ScoreType`, add `_CALIBRATED_THRESHOLD=0.65` and `_calibrate_fts_raw_score` mirroring `retrieve.py` buckets; `rag_search` FTS branch calibrates BM25→confidence and emits `CALIBRATED_CONFIDENCE`; filter gate now checks `score_type` allow-list (`CALIBRATED_CONFIDENCE, LEXICAL, FTS`) before `>=0.65`; diagnose hook via `diagnose_collection_compatibility` when repo present; `region_clusters` expanded from 8 to 13 covering all admin regions (riyadh, makkah, madinah, eastern, asir, jazan, najran, bahah, tabuk, hail, qassim, jouf, northern) with city-level keywords; lexical scanned results emit `LEXICAL` type.
- `evals/test_rag_13regions.py` (new, 330 lines) – 13 regions ×2 queries (26) + 7 controls = 33 parametrized cases; 25-query rewriter injection test; `ScoreType` distinction test; `diagnose` usability test; aggregate metrics test printing Recall@K, MRR/nDCG notes, ood rejection, irrelevant-context, web-routing, contamination, citation precision.

Unchanged (read-only per ownership): `sard/api/server.py`, `sard/agent/chat_service.py`, `sard/agent/capability_routing.py`, `src/lib/api.ts`.

## Commit hash & branch

- Branch: `herdr/rag-repair` (worktree `herdr-rag-repair`)
- Base commit before this iteration: `2704c12 docs(handoffs): record wave1 recovery handoff` (includes Wave1 `449c8de` + `84691c7` etc.)
- This commit: `fix(rag): calibrate FTS, ScoreType enum, 13-region reject, week freshness, citation provenance, evals` (HEAD `a8b35b7` at 2026-08-31 08:11, 8 files `628+/33-`; `git status` owned-only; see `git log --oneline -3` for exact hash).
- Parent diff vs `2b30673` retains Wave1 history (`fc2c828` docs diagnostics, `84691c7` RAG calibrate, `449c8de` artifact hardening, `e0df96f` recovery audit) – no rewrite of shared history.

## Tests

Focused command (per brief):
```
uv run --with pytest pytest tests/rag evals/test_cultural_search_rag.py -v
uv run --with pytest pytest evals/test_rag_13regions.py -v
uv run --with pytest pytest tests/rag evals/test_cultural_search_rag.py evals/test_rag_13regions.py -q
```

Results:
- `tests/rag` (120 tests): **120 passed, 1 skipped** (58–62s, zvec in-process). Covers `test_retrieve` hybrid/dense/FTS/fusion/dedup/low-confidence-dispatch/diagnose, `test_zvec_store` versioned path/dimension/schema/filter/langchain adapter, `test_query_rewriter` fallback/cache/sanitize, `test_rerank`, `test_embeddings`, `test_ingest`, `test_chunking`, `test_normalize`, `test_answer`, `test_service`.
- `evals/test_cultural_search_rag.py` (19 tests): **19 passed** – 3 mandatory scenarios (in-corpus no-web, fresh 2026 web, low-coverage web), metadata/source-policy/budget/graceful-fallback, out-of-corpus 5 queries + 2 ood zero-contamination, legitimate shrimp/springs in-corpus, comprehensive metrics `Recall@K 1.0 / ood-rejection 1.0 / web-routing 1.0 / contamination 0.0`.
- `evals/test_rag_13regions.py` (38 tests): **38 passed** – 33 parametrized routing+no-contamination cases across 13 regions (riyadh/makkah/madinah/eastern×2 + asir/jazan/najran/bahah/tabuk/hail/qassim/jouf/northern) plus English/Arabic/mixed/fresh/non-cultural controls; rewriter 26-query zero-injection; ScoreType distinction (dense/fts calibration); `diagnose_collection_compatibility` usable + `RAGService.open_readonly` raises actionable `RAGServiceUnavailableError` with diagnose message; aggregate metrics.
- Combined `tests/rag + evals/test_cultural_search_rag.py + evals/test_rag_13regions.py`: **177 passed, 1 skipped**.
- Manual verification scripts (not committed):
  - `test_qr.py` (25 queries: Qassim, Asir, Hijaz, Najd, Tabuk, Hail, Jazan, Najran, Bahah, Jouf, Northern, Eastern, English/mixed) – `deterministic_query_variants` + `_sanitize` contamination 0/25 PASS.
  - `test_router.py` – Q1 `ما الفعاليات في الرياض هذا الأسبوع؟` freshness true/web true/PASS; Q2 `ما خصائص العمارة في رجال ألمع؟` rag 0/web true/contamination false/PASS; Q3 `Explain photosynthesis` rag 0/citations 0/contamination false/PASS; budget caps 2 search/1 extract PASS; citation provenance fields present PASS.

No fabrication: corpus remains `data/corpus/coastal+springs` 4 docs (MANIFEST.md honest gaps) – no synthetic docs added. Evaluation uses local scan + lexical fallback; web fallback mocked via `_mock_web_results_for` to avoid live network.

## Metrics (aggregate over 33-query 13-region suite, `test_aggregate_metrics_across_all_queries -s`)

```
=== 13-region aggregate ===
Recall@K (in-corpus Eastern): 1.000 (2/2)
Out-of-domain rejection: 1.000 (31/31)
Irrelevant-context rate: 0.000
Web-routing accuracy: 1.000 (33/33)
Topic-contamination rate: 0.000 (0/31) target 0.0
Citation precision: 1.000
```

- `Recall@K` conditioned on in-corpus Eastern pilot (shrimp + springs) – both hit RAG without web.
- `OOD rejection` + `irrelevant-context` over 31 out-of-corpus/English controls – 0 Eastern leakage after region expansion + calibrated FTS.
- `Web-routing` 33/33: fresh `هذا الأسبوع` (new pattern) + all out-of-corpus trigger `parallel_search` per calibrated confidence `<0.65` or `is_time_sensitive`; in-corpus do not.
- `Contamination` 0.0 for all unrelated (Asir, Qassim, Najd, Hijaz, Tabuk, Hail, Jazan, Najran, Bahah, Jouf, Northern, English/mixed, photosynthesis) – verified via `CONTAMINATION_TERMS` across `rag_sources`, `citations`, `answer_text`.
- `Citation precision` 1.0 – every citation (rag/web/media) emits required 9 fields.
- Rewriter injection rate 0/26 queries (see `test_query_rewriter_never_injects_pilot_entities`).
- Golden `evals/golden.json` (10 cases, 2 adversarial trap `SARD-EP-SPRINGS-004`/`SHRIMP-002`) still evaluated via `OfflineCorpusRetrievalService` – term-hit recall unchanged; no new synthetic coverage claimed.
- Zvec diagnostic: `diagnose_collection_compatibility` returns `missing_base_path|model_mismatch|ready` with Arabic actionable message – surfaced in `rag_search` debug log.

## Limitations

- Corpus honesty gap persists: pilot corpus only 4 docs under `data/corpus/coastal+springs` (springs: `aleqt-2013-hot-springs.md`, `saudipedia-al-ahsa-water-springs.md`; coastal: `alqhat-2019-shrimp-memory.md`, `kapl-eastern-cuisine.md`) per `data/corpus/MANIFEST.md`. Cases requiring Heritage Commission / Visit Saudi original publication, VLM-scanned PDF, or fresh 2026 festival schedule still fail retrieval by design – correctly route to web rather than hallucinate. No synthetic docs added to close gap.
- Dense semantic search in `cultural_tools.rag_search` still FTS-only for latency; full hybrid dense+FTS via `RetrievalService` lives in `sard/rag/service.py` and is exercised only when `ChatService(use_hybrid_retrieval=True)` – direct `cultural_tools.rag_search` callers (e.g., `cultural_router`) use lexical/Fts + calibration, not dense cosine. Mismatch is mitigated by shared threshold but not eliminated.
- `_CALIBRATED_THRESHOLD` 0.65 is shared nominally but lexical `match_ratio` calibration (`*0.70+0.25`) vs BM25 calibration (`0.55+...`) are independently tuned heuristics, not learned – may need per-channel recalibration on larger corpus.
- `region_clusters` now 13 but keyword lists are heuristic (city names) – ambiguous queries without explicit region token (e.g., "الحرف التقليدية بشكل عام") bypass region rejection and rely solely on lexical match_ratio + pilot-topic filter; low lexical overlap still correctly filters via `0.65` but near-threshold generic queries could be sensitive.
- `parallel_search`/`parallel_extract` live network not exercised in CI (mocked); `_PREFERRED_DOMAINS` / `_DISALLOWED_DOMAINS` policy not live-validated; `DEFAULT_PARALLEL_API_KEY` committed in source is a dev key.
- `QueryRewriteService` cache remains process-global (`_cache` dict keyed by normalized query+model) – not session-scoped; history-borne contamination mitigated via `_sanitize` but concurrent user isolation not provided.
- No live NVIDIA NIM smoke test in this worktree – embedding/rerank/generation fallback to deterministic FTS-only paths in CI; `diagnose_collection_compatibility` not yet surfaced in ingestion CLI beyond `service.py`.
- `ScoreType.WEB`/`LEXICAL` are new enum values – downstream consumers that naively compare `score_type == "fts"` may ignore lexical type; this iteration updates owned consumers only.

## Public-contract implications

- **No breaking change** to `sard/api/server.py`, `sard/agent/chat_service.py`, `sard/agent/capability_routing.py`, `src/lib/api.ts` (untouched, read-only per ownership). Public `ChatService.ask(use_hybrid_retrieval=)` contract unchanged.
- RAG public surface (`sard/rag/service.RAGService`, `sard/rag/schemas.RetrievalResult`, `sard/agent/cultural_router.CulturalQueryResult.citations`) gains additive fields: `citations[].channel`, `topic`, `region` already present for rag now also guaranteed for web/media; `score_type` now enum-validated (`calibrated_confidence|lexical|web|dense|fts|rerank|rrf`). Consumers reading citations should treat new fields as optional but present – no removal.
- `_FRESHNESS_PATTERN` expansion is additive – more queries now correctly classified `is_time_sensitive=True` (e.g., week-scoped). Clients relying on previous narrow freshness will see slightly higher web-routing rate (correct per policy).
- 13-region `region_clusters` tightening reduces false-positive Eastern leakage – clients that previously received an Eastern pilot citation for Asir/Qassim/etc will now correctly receive empty `rag_sources` + `web_sources` (fallback) or abstention. This is a correctness fix, not a contract break, but snapshot tests asserting old contaminated result will fail (intentionally).
- `ScoreType` enum extension is additive – JSON serialization still string-valued; older string literals `"fts"` etc remain valid subset.
- No new env vars; `RAG_*` thresholds unchanged (`RAG_DENSE_SIMILARITY_THRESHOLD 0.65`, `RAG_MIN_EVIDENCE_CONFIDENCE 0.60`, `RAG_RERANK_RELEVANCE_THRESHOLD 0.45`). `diagnose_collection_compatibility` message is Arabic + actionable; `RAGServiceUnavailableError` message remains user-safe.

