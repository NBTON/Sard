# Workstream C — OpenRouter model ID discovery + bakeoff (2026-09-15)

Branch: `work/release-bakeoff-docs`. Prod code untouched. **No secrets in this report or any created file** — the API key was read only from process environment / ignored local env files, never echoed, never printed, never written to disk.

## 1. Method

- Read `sard/config/openrouter.py`: `fetch_catalog` (GET `{BASE}/models`, bounded `timeout=8.0s`, no retry), `filter_free_candidates`, `rank_candidates` (sort by `-context_length`, then id).
- Live fetch on 2026-09-15: **445 models total, 22 free** (`pricing.prompt=="0"` and `pricing.completion=="0"`, same predicate as `_is_free_pricing`).
- Flags below use `openrouter.py` semantics exactly:
  - `supports_tools` = `architecture.supports_tool_calling` OR `"tools" in supported_parameters`
  - `supports_structured` = `"structured_outputs"` or `"response_format" in supported_parameters`
  - `supports_vision` = `"image" in architecture.modality` OR `"vision" in id.lower()`
  - `pricing` free/paid by the same `prompt=="0" and completion=="0"` test.
- Human names were **never** assumed to equal IDs — every ID below was resolved by regex against live `id + name`.

## 2. Canonical IDs (11 found, 4 not on OpenRouter)

| # | Human label | Exact canonical free ID | ctx | tools | struct | vision | Status |
|---|---|---|---|---|---|---|---|
| 1 | Nemotron 3.5 Lightning | `nvidia/nemotron-3.5-lightning:free` | 1,000,000 | Y | N | N | FOUND (`:free` variant; paid twin `nvidia/nemotron-3.5-lightning` ctx 262k) |
| 2 | Gemma 4 26B A4B | `google/gemma-4-26b-a4b-it:free` | 262,144 | Y | Y | Y | FOUND (modality `text+image+video->text`) |
| 3 | Gemma 4 31B | `google/gemma-4-31b-it:free` | 262,144 | Y | Y | Y | FOUND |
| 4 | LFM2.5-2.6B | `liquid/lfm-2.5-2.6b:free` | 65,536 | Y | Y | N | FOUND — **reasoning-mandatory** (see §4) |
| 5 | LFM2.5-Embedding-350M | — | — | — | — | — | **NOT FOUND** in `/models` (embed count = 0) |
| 6 | Nemotron 3 Super | `nvidia/nemotron-3-super-120b-a12b:free` | 262,144 | Y | Y | N | FOUND (note size suffix `120b-a12b` is part of the ID) |
| 7 | Nemotron 3 Ultra | `nvidia/nemotron-3-ultra-550b-a55b:free` | 1,000,000 | Y | N | N | FOUND (note `550b-a55b`; paid twin ctx 262k) |
| 8 | Nemotron 3 Nano Omni | `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free` | 256,000 | Y | N | Y | FOUND (only Omni entry is the `-reasoning:free` ID; modality `text+image+audio+video->text`. Paid `nvidia/nemotron-3-nano-30b-a3b` is non-Omni, different model) |
| 9 | Nemotron 3 Embed 1B | — | — | — | — | — | **NOT FOUND** (embed count = 0) |
| 10 | Llama Nemotron Embed VL 1B V2 | — | — | — | — | — | **NOT FOUND** (embed count = 0) |
| 11 | Llama Nemotron Rerank VL 1B V2 | — | — | — | — | — | **NOT FOUND** (rerank count = 0) |
| 12 | Inkling | `thinkingmachines/inkling:free` | 1,048,576 | Y | N | Y | FOUND but **403 via plain chat completions** ("only available on agentic harnesses") |
| 13 | Inkling Small | `thinkingmachines/inkling-small:free` | 1,048,576 | Y | N | Y | FOUND, same 403 caveat |
| 14 | Dots3-Note Preview | `dots-studio/dots-3-note-preview:free` | 512,000 | Y | Y | Y | FOUND (tools+structured+vision) |
| 15 | Ling 3.0 Flash VL | `inclusionai/ling-3.0-flash-vl:free` | 262,144 | Y | N | Y | FOUND (paid twin ctx 131k; `:free` ctx 262k) |

Embed/rerank conclusion: items 5, 9, 10, 11 are **not OpenRouter chat IDs**. Keep them on the existing NVIDIA NIM / local RAG path (dense embeddings + rerank). Never use the human names as `model=` values.

## 3. Bakeoff suite (cheap by design)

Definition: `evals/bakeoff_suite_20260915.json`. 11 tasks, short prompts, `temperature=0.2`, per-call timeout 30s, single attempt:

| ID | Task | Out cap | Axes |
|---|---|---|---|
| T1 | Arabic factual (Al-Ahsa oasis) | 200 | fluency, citation, latency, failure |
| T2 | Cultural explanation (shrimp-drying, no fabrication) | 220 | fluency, citation, latency, failure |
| T3 | Long-form report (Turaif, intro+3 sections+conclusion) | 400 | fluency, markdown, latency, failure |
| T4 | Itinerary (2-day Eastern Province, no invented prices) | 300 | fluency, markdown, latency, failure |
| T5 | Bilingual (AR→EN + AR summary) | 220 | fluency, latency, failure |
| T6 | Markdown table (3 cols, Diriyah/AlUla/Historic Jeddah) | 220 | tables, markdown, fluency, latency, failure |
| T7 | Citation-heavy (UNESCO list; "needs verification" rule, no fake CIT/links) | 250 | citation, fluency, latency, failure |
| T8 | Structured JSON (fixed schema `{topic, region, days, stops[2]}`) | 200 | JSON reliability, latency, failure |
| T9 | Query rewrite (2 short AR variants) | 150 | fluency, latency, failure |
| T10 | Fact verification (Al-Ahsa UNESCO 2018 claim) | 200 | citation, fluency, latency, failure |
| T11 | Long-context synthesis (3 pasted passages → 5 AR bullets) | 300 | fluency, markdown, citation, latency, failure |

Scoring axes: Arabic fluency/fidelity (0–2), markdown structure (0–2), tables (0–2 for T6), citation obedience (0–2 for T1/T2/T7/T10/T11), JSON reliability = first-pass parse + schema-valid (0–2 for T8), latency seconds (median; gate p50 ≤ 30s), failure rate = empty/timeout/malformed share. Excluded from Arabic composition: TTS/music (`lyria-*`) and code-only (`north-mini-code`, `laguna-*`) plus content-safety classifier.

## 4. Live Smoke Discovery (T1 × 10 models, 2026-09-15)

| Model | Result | Latency | Note |
|---|---|---|---|
| `dots-studio/dots-3-note-preview:free` | OK, fluent Arabic | 4.72s | Needs `reasoning.enabled=false` |
| `inclusionai/ling-3.0-flash-vl:free` | OK, fluent Arabic | 13.02s | Needs `reasoning.enabled=false` |
| `nvidia/nemotron-3-super-120b-a12b:free` | OK, fluent Arabic | 5.69s | Needs `reasoning.enabled=false` |
| `liquid/lfm-2.5-2.6b:free` | OK (reasoning channel extracted) | 6.13s | Reasoning-mandatory endpoint |
| `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free` | OK, fluent Arabic | 27.05s | Reasoning model; high latency on free tier |
| `nvidia/nemotron-3.5-lightning:free` | OK, leaks thinking preface / timeout | 14.30s / 30s | Leaks English thinking or times out when reasoning disabled |
| `nvidia/nemotron-3-ultra-550b-a55b:free` | FAIL (ReadTimeout) | 30.22s | Free tier server queue / timeout |
| `google/gemma-4-26b-a4b-it:free` | FAIL (HTTP 429) | 0.45s | Free-tier upstream throttle |
| `google/gemma-4-31b-it:free` | FAIL (HTTP 429) | 0.48s | Free-tier upstream throttle |
| `thinkingmachines/inkling-small:free` | FAIL (HTTP 403) | 0.37s | Only available on agentic harnesses |

## 5. Bounded 11-Task Bakeoff Benchmark (Live, 2026-09-15)

Full execution across all 11 suite tasks using `evals/bakeoff_runner_20260915.py --benchmark`:

### Summary Metrics

| Model ID | Passed | Fail | Success Rate | Median Latency (s) | Arabic Fluency (0-2) | Citation Obedience (0-2) | Markdown (0-2) | Table (T6) | JSON (T8) |
|---|---|---|---|---|---|---|---|---|---|
| `dots-studio/dots-3-note-preview:free` | **11/11** | 0 | **100%** | **3.65s** | **2.0 / 2** | **2.0 / 2** | 1.8 / 2 | **2 / 2** | **2 / 2** |
| `nvidia/nemotron-3-super-120b-a12b:free` | **10/11** | 1 | **91%** | **3.46s** | **1.82 / 2** | **2.0 / 2** | 1.6 / 2 | 2 / 2 | **2 / 2** |
| `inclusionai/ling-3.0-flash-vl:free` | **9/11** | 2 | **82%** | **2.45s** | **1.55 / 2** | **2.0 / 2** | 1.5 / 2 | **2 / 2** | N/A* |
| `liquid/lfm-2.5-2.6b:free` | **11/11** | 0 | **100%** | **9.74s** | 1.0 / 2 | **2.0 / 2** | 1.7 / 2 | 0 / 2 | 1 / 2 |
| `google/gemma-4-31b-it:free` | **0/11** | 11 | **0%** | — | — | — | — | — | — |

*\*Note: `inclusionai/ling-3.0-flash-vl:free` does not support `response_format` (catalog `supports_structured=False`); passing `response_format` yields HTTP 400. Direct JSON without `response_format` is required.*

### Detailed Task-by-Task Observations

1. **T1 (Arabic factual - Al-Ahsa oasis, 200 tokens):**
   - `dots-studio/dots-3-note-preview:free`: Fluent Arabic definition, 4.39s.
   - `inclusionai/ling-3.0-flash-vl:free`: Accurate historical overview, 3.12s.
   - `nvidia/nemotron-3-super-120b-a12b:free`: Accurate geography and cultural importance, 6.00s.
2. **T2 (Cultural explanation - Tarut shrimp drying, 220 tokens):**
   - `dots-studio/dots-3-note-preview:free`: Step-by-step drying process, no hallucinated names, 5.37s.
   - `nvidia/nemotron-3-super-120b-a12b:free`: Concise cultural craft steps, 3.16s.
3. **T3 (Long-form report - Turaif, 400 tokens):**
   - All three top models produced well-structured reports with intro, 3 sections, and conclusion.
   - `ling-3.0-flash-vl`: 3.80s; `dots-3-note-preview`: 8.25s; `nemotron-3-super`: 8.47s.
4. **T4 (Itinerary - Eastern Province 2-day, 300 tokens):**
   - Clean markdown day-by-day plan with bullet points; no invented prices.
   - `nemotron-3-super`: 3.49s; `ling-3.0-flash-vl`: 5.43s; `dots-3-note-preview`: 6.04s.
5. **T5 (Bilingual translation + Arabic summary, 220 tokens):**
   - Natural English translation followed by single Arabic summary sentence.
   - `nemotron-3-super`: 1.13s; `dots-3-note-preview`: 1.82s; `ling-3.0-flash-vl`: 1.70s.
6. **T6 (Markdown table, 3 columns, 220 tokens):**
   - `dots-studio/dots-3-note-preview:free` & `inclusionai/ling-3.0-flash-vl:free`: Perfect 3-column GFM table with headers (`الموقع | المنطقة | ملاحظة`), 2.18s–3.65s.
7. **T7 (Citation-heavy & unverified claim handling, 250 tokens):**
   - `dots-studio/dots-3-note-preview:free`: Correctly output `يحتاج توثيقاً` without inventing fake `[CIT-]` tags or URLs (3.06s). Perfect citation obedience.
   - `nemotron-3-super`: Accurately listed confirmed UNESCO sites without hallucinated citations (3.46s).
8. **T8 (Structured JSON, 200 tokens):**
   - `dots-studio/dots-3-note-preview:free`: Emitted schema-valid JSON object with keys `topic`, `region`, `days`, `stops` in **1.76s** (Score: 2/2).
   - `nvidia/nemotron-3-super-120b-a12b:free`: Emitted valid JSON matching schema in **1.74s** (Score: 2/2).
9. **T9 (Query rewrite, 150 tokens):**
   - `dots-3-note-preview`: 1.64s; `ling-3.0-flash-vl`: 1.59s; `nemotron-3-super`: 2.73s.
10. **T10 (Fact verification - Al-Ahsa UNESCO 2018 claim, 200 tokens):**
    - `dots-3-note-preview` and `ling-3.0-flash-vl` verified correctly with `"صحيحة"` and exact UNESCO 2018 inscription rationale (2.17s–4.14s).
11. **T11 (Long-context synthesis, 300 tokens):**
    - High-fidelity 5-bullet summary synthesized from multi-passage input.
    - `ling-3.0-flash-vl`: 2.01s; `dots-3-note-preview`: 3.97s; `nemotron-3-super`: 11.13s.

## 6. Validated Routing Recommendations

Based on empirical bakeoff data, model capability flags, and routing table (`sard/config/routing_table.py`):

| TaskClass / Capability | Primary Model | Fallback Model | Rationale |
|---|---|---|---|
| `structured_json` / `generative_ui` | `dots-studio/dots-3-note-preview:free` | `nvidia/nemotron-3-super-120b-a12b:free` | 100% JSON valid in 1.76s; Super handles fallback; Gemma 31B behind retry. |
| `compose_short` / `saudi_cultural_factual` | `nvidia/nemotron-3-super-120b-a12b:free` | `dots-studio/dots-3-note-preview:free` | High cultural fluency, fast (3.46s), excellent Arabic prose. |
| `compose_longform` / `research_synthesis` | `dots-studio/dots-3-note-preview:free` | `nvidia/nemotron-3-ultra-550b-a55b:free` | Dots passed 11/11 with 512k context; Ultra primary when paid/non-queue tier available. |
| `plan` / `itinerary_planning` | `nvidia/nemotron-3-super-120b-a12b:free` | `dots-studio/dots-3-note-preview:free` | Structured + tools, 3.49s on T4 itinerary. |
| `vision` / `document_ocr` | `inclusionai/ling-3.0-flash-vl:free` | `dots-studio/dots-3-note-preview:free` | Dedicated vision-language model, 2.45s median latency. |
| `fast_classify` / `query_rewrite` | `liquid/lfm-2.5-2.6b:free` | `nvidia/nemotron-3-super-120b-a12b:free` | 65k context, slot-filling, reads reasoning channel. |
| `verify` / `fact_verification` | `dots-studio/dots-3-note-preview:free` | `nvidia/nemotron-3-super-120b-a12b:free` | Strict citation obedience (2.0/2.0), verified Al-Ahsa claim accurately. |
| `embed_text` / `embed_multimodal` | *NVIDIA NIM / Local* | — | Not present in OpenRouter catalog; remain on NVIDIA dense path. |
| `rerank` / `tts` | *Deterministic / Local* | — | Deterministic fallback per architecture specification. |

## 7. Adapter Rules & Production Requirements

1. **Reasoning Disable Flag (`reasoning.enabled=false`):**
   - `dots-studio/dots-3-note-preview:free`, `nvidia/nemotron-3-super-120b-a12b:free`, and `inclusionai/ling-3.0-flash-vl:free` must receive `{"reasoning": {"enabled": False}}`. Otherwise, internal thinking is either prepended to the response or returned in place of content.
2. **Reasoning Channel Fallback for LFM:**
   - `liquid/lfm-2.5-2.6b:free` requires reasoning; the client adapter must read `message.reasoning` or `message.reasoning_content` if `content` is empty.
3. **Structured Outputs Format:**
   - `response_format` must be passed as an object: `{"type": "json_object"}`. Passing a raw string causes HTTP 400 from OpenRouter.
   - Do not pass `response_format` to `inclusionai/ling-3.0-flash-vl:free` (it lacks structured output support).
4. **Upstream Rate Limiting on Gemma Free:**
   - `google/gemma-4-31b-it:free` and `26b` frequently return HTTP 429 upstream on the public free tier. Keep behind circuit breaker with transient retry.
5. **Inkling Exclusion:**
   - `thinkingmachines/inkling:free` and `inkling-small:free` return HTTP 403 unless invoked through an agentic harness. Keep excluded from direct chat routes.

## 8. Artifacts Created & Validated

- `evals/bakeoff_suite_20260915.json` — 11 tasks, budgets, scoring criteria, and normalized `response_format`.
- `evals/bakeoff_runner_20260915.py` — Secure benchmark runner; loads key strictly from env, flushes logs, normalizes params, stores raw results to scratch.
- `docs/reports/model_bakeoff_routing_20260915.json` — Machine-readable validated routing table with live timestamps and empirical scores.
- `docs/reports/model_bakeoff_20260915.md` — This comprehensive discovery, smoke, and 11-task bakeoff benchmark report.
