# Sard Step 8 final evaluation

Run date: 2026-08-14 (Asia/Riyadh)

Golden set: `evals/golden.json` (10 labelled Arabic questions)

Command: `uv run sard-demo evaluate --json-out output/evaluation/final-evaluation.json`

## Result summary

| Measure | Result |
|---|---:|
| Retrieval pass rate | **8/10 (80%) — gate passed at 8/10** |
| Evaluation route | Offline lexical rehearsal over the real corpus; verified-source URL labels |
| Live model-backed evaluation | **Not run** — no NVIDIA key or self-hosted NIM endpoint was configured |
| Cached hero pipeline | Passed (`cached_demo`) |
| Citation coverage | **100%** of citation-bearing cached itinerary fields |
| Unsupported factual claims | **0** in the cached hero fixture after citation validation |
| PDF | Passed — 3 pages, 41,330 bytes, bundled Arabic font, visually inspected RTL |
| Calendar | Passed — 4 events, 4 unique UIDs, `Asia/Riyadh`, successful parse round-trip |
| Raw text | Passed — 2,206 UTF-8 bytes |

The 8/10 score is not a live dense/reranked score. It is the deterministic clean-environment retrieval gate used when model access is absent. It exercises the real corpus loaders, metadata sidecars, Arabic normalization/query variants, chunk IDs, content deduplication, lexical ranking, and explicit verified-source labels. The two failures below remain failures; an empty gold-source list is never counted as a successful abstention.

## Per-question retrieval results

| Case | Result | Relevant evidence / honest failure | Retrieval latency |
|---|---|---|---:|
| SARD-EP-SPRINGS-001 | Pass | `CIT-3AE406450E19`, `CIT-A6E7E5FB5C8B` | 1.94 ms |
| SARD-EP-SPRINGS-002 | Pass | `CIT-3AE406450E19`, `CIT-A6E7E5FB5C8B` | 1.17 ms |
| SARD-EP-SPRINGS-003 | Pass | `CIT-3AE406450E19`, `CIT-A6E7E5FB5C8B` | 1.18 ms |
| SARD-EP-SPRINGS-004 | Pass | Sources are retrieved for context; the answer must still refuse an exact temperature or clinical claim | 1.49 ms |
| SARD-EP-SPRINGS-005 | Pass | `CIT-3AE406450E19`, `CIT-A6E7E5FB5C8B` | 1.09 ms |
| SARD-EP-SHRIMP-001 | Pass | `CIT-32EF35918C4D`, `CIT-D69B54CB0B4C` | 1.00 ms |
| SARD-EP-SHRIMP-002 | Pass | `CIT-32EF35918C4D`; source does not establish an exact origin date, so the answer must say so | 1.67 ms |
| SARD-EP-SHRIMP-003 | Pass | `CIT-32EF35918C4D`, `CIT-D69B54CB0B4C` | 1.17 ms |
| SARD-EP-SHRIMP-004 | **Fail** | No verified source confirms a current visitor location or live demonstration | 1.18 ms |
| SARD-EP-SHRIMP-005 | **Fail** | No verified source confirms a bookable shrimp-drying tourism activity | 1.28 ms |

Latencies vary slightly by machine; the values above are from the final local run. The cached UI contains pre-recorded simulated node durations for rehearsal: understand 210 ms, plan 180 ms, retrieve 340 ms, compose 420 ms, verify 260 ms, render 510 ms. These are **not live measurements**. Live graph-node latency was not measurable without model access.

## Artifact and grounding checks

- The cached answer and itinerary use the same four stable citations produced from the evaluated corpus: `CIT-3AE406450E19`, `CIT-A6E7E5FB5C8B`, `CIT-D69B54CB0B4C`, and `CIT-32EF35918C4D`.
- Placeholder `example.org` sources were removed.
- The fallback states visibly that it is saved content and was not generated at display time.
- PDF checksum: `feedfed693c2c99c894bd366e8fd1b9d1662f963a9fc29b4ebf5839a14e937c3`.
- Calendar checksum: `d3824c897c63661d185ecb82f990df55dfbb63fec0120afe92b1fbe95b05b835`.
- Raw-text checksum: `5f90216ae658cb8eeaa92d27366ce781172d43e9964cd6c504508acb29a636f7`.

## Remaining limitations

- Live mode cannot be declared ready until `NVIDIA_API_KEY` or self-hosted NIM endpoints are configured and `uv run sard-demo check` reports model access as reachable.
- The two shrimp visitor-experience cases remain unsupported.
- The springs sources describe heritage and older development efforts, not current hours, prices, safety status, or medical efficacy.
- The cached trip uses fixed demonstration dates and proposed time blocks; they are not current operating hours.

## Clean-environment rehearsal

A new Python 3.11 virtual environment was created under ignored `tmp/`, dependencies were installed from `.[nvidia,dev,anthropic,openai]` using copy mode after the cloud-synced Windows filesystem rejected hardlinks, and the documented commands were rerun. `sard-demo check`, `evaluate`, and `rehearse` all exited 0; rehearsal reported `ready_fallback_only`, golden score `8/10`, and `clean_rehearsal: passed`. The complete suite then finished with **410 passed, 2 skipped**, and one expected warning from a fake reranker model ID used by a unit test. A headless Streamlit startup smoke check returned HTTP 200 from `/_stcore/health`.
