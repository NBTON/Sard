# Sard RAG pilot corpus manifest

This corpus is intentionally small and honestly documented, per the Step 3
instructions: **no fabricated documents, URLs, quotations, or citations are
permitted.** Where a verified document from a preferred source (Saudi
Heritage Commission, Saudi Museums Commission, Visit Saudi, Culinary Arts
Commission, or an academic/institutional publication) could not be located
and fetched during this build, that gap is recorded below instead of being
papered over with placeholder content.

## Pilot topic 1 — الينابيع الحارة في المنطقة الشرقية (hot springs)

**Status: partially seeded with 2 real, fetched, attributed documents.**

| File | Source | Type | Verified |
|---|---|---|---|
| `springs/aleqt-2013-hot-springs.md` | صحيفة الاقتصادية (Al-Eqtisadiah, a mainstream Saudi financial newspaper), 2013-04-04, "عيون الأحساء «الحارة».. استشفاء واستثمار سياحي واقتصادي" | News article covering a workshop run by the (then) هيئة السياحة والآثار | Yes — fetched verbatim from `aleqt.com`; text in the corpus matches the live page at ingestion time. |
| `springs/saudipedia-al-ahsa-water-springs.md` | سعوديبيديا (Saudipedia), an encyclopedia operated under وزارة الإعلام (Ministry of Media); article cites أمانة الأحساء (Al-Ahsa Municipality) as its own source | Institutional/encyclopedic reference | Yes — fetched verbatim from `saudipedia.com`. |

**Neither document is an original publication of the Saudi Heritage
Commission, Visit Saudi, or the Saudi Museums Commission.** They are the
best verifiable, fetchable, on-topic Arabic sources located during this
build. A search for the springs' page on `visitsaudi.com/ar/al-ahsa`
returned only a JavaScript-rendered shell with no extractable article text
in this environment — it is a plausible future source but is NOT included
here since its actual body text could not be verified.

**Known gaps for this topic** (do not treat as satisfied until a verified
document is added):

- An original Saudi Heritage Commission (هيئة التراث) publication about
  Al-Ahsa's hot/mineral springs specifically as heritage/intangible
  practice (as opposed to tourism-investment framing).
- A Visit Saudi article with actual extractable body text (not just a
  client-rendered shell).
- A geological/hydrological study giving verified temperature figures —
  golden case `SARD-EP-SPRINGS-004` is an adversarial trap case that
  specifically expects the system to refuse invented temperature/medical
  claims in the absence of such a source; this gap is intentional and
  should stay unfilled unless a real study is sourced.

## Pilot topic 2 — تجفيف الروبيان بوصفه ممارسة تراثية ساحلية (traditional shrimp drying)

**Status: NO documents ingested. This topic has zero corpus coverage.**

Searches were run against Saudi Heritage Commission, Visit Saudi, Culinary
Arts Commission, and general Arabic web sources for a verified account of
shrimp-drying (تجفيف الروبيان) specifically as a heritage practice on the
Eastern Province coast. Results found:

- Recent news coverage of "مهرجان روبيان الشرقية" (an annual Eastern
  Province shrimp *festival* run by the Ministry of Environment, Water and
  Agriculture) — this documents a modern festival, not the traditional
  drying practice itself, and does not describe the technique, so it was
  **not** included to avoid answering a heritage-practice question with an
  unrelated modern-festival source.
- A well-documented, closely-related practice — sun-drying small fish
  ("الجاشع"/"البرية") on the Gulf coast, and a UAE (Abu Dhabi Culture)
  heritage-register entry for "راعي الجسيف" (the fish-drying tradesman) —
  but these describe **fish** drying, are largely **UAE**-attributed, and
  are not a verified Saudi Eastern Province source specifically about
  **shrimp**. Including them under the shrimp-drying topic would
  misattribute a related-but-distinct practice/geography, which the task
  explicitly disallows ("Do not fabricate... documents").

**Action required before this topic can be evaluated honestly:** source
and add one or more of:

1. A Saudi Heritage Commission (هيئة التراث) publication on coastal
   food-preservation crafts in the Eastern Province.
2. A Culinary Arts Commission (هيئة الفنون الطهيية) publication on
   traditional Gulf/Saudi seafood preservation.
3. An academic/anthropological study of Eastern Province fishing
   communities that specifically documents shrimp (not only fish) drying.
4. A recorded oral-history/interview transcript with named provenance from
   a Saudi heritage or municipal body.

Until one of these is added, any golden-set question under
`SARD-EP-SHRIMP-*` **must be reported as failing retrieval** (no evidence
in corpus) — never as passing based on a generated-but-unsourced answer.

## Adding documents to this corpus

1. Add the source file (`.pdf`, `.html`, `.md`, or `.txt`) under
   `data/corpus/<topic-folder>/`.
2. Add a matching `<filename>.meta.json` sidecar with at least
   `source_name`, `source_url`, `title`, `topic` (and, when known,
   `publication_date`, `language`). See the two existing `.meta.json`
   files in `springs/` for the exact shape.
3. Run `uv run python -m sard.cli.rag ingest data/corpus`.
4. Re-run `uv run python -m sard.cli.rag evaluate evals/golden.json` and
   check the reported gate honestly reflects the new coverage.
