# Pack 23 — 255-document retrieval corpus

A synthetic corpus built to break the document search engine in the two ways that matter at
client scale: **questions that require reading every document** ("which store leases make HVAC
our responsibility?") and **needle-in-a-haystack** questions where one fact hides on page 4 of
a boring 6-page report.

**255 documents · 1,153 pages · 1.74M characters · 33 MB.**

The generator is committed here; the corpus itself lives outside the repo at
`C:\temp\doc_corpus_250` and is regenerated on demand.

---

## Why this exists when packs 13 already has a lease corpus

| | pack 13 `scale_corpus` | pack 23 |
|---|---|---|
| documents | 120 | 255 |
| genres | leases only | leases + 9 other document types |
| formats | `.txt` only | pdf, docx, xlsx, csv, txt, scanned-image, jpg |
| graded dimensions | 1 (HVAC) | 10, independently drawn |
| near-miss distractors | none | 45 |
| multi-hop / supersession | none | 35 |
| planted needles | none | 18, three difficulty grades, 4 with decoys |
| pages | 360 | 1,153 |

The old corpus is a monoculture: every document is the same kind of thing, so nothing in it can
produce a **false positive**. Precision has never actually been tested. Pack 23's second tier
exists entirely to fix that.

## The constraint that drove the design

Production routes on volume ([`dist/.env:53`](../../dist/.env)):

```
KNOWLEDGE_BRUTE_FORCE_PAGE_THRESHOLD=999
KNOWLEDGE_BRUTE_FORCE_CHAR_BUDGET=400000
```

Under **both** limits the engine dumps every page of every document into context and literally
cannot miss. A corpus below that line measures nothing about retrieval. This corpus is past
both (1,153 pages / 1.74M chars), so the smart-retrieval path — NEEDLE / FANOUT / AGGREGATE —
is the one under test. `load_corpus.py --status` warns if a partial load has left you back
under the threshold.

## Tiers

| tier | n | what it tests |
|---|---|---|
| 1 | 130 store leases, 5–7pp | fan-out across 10 independent dimensions |
| 2 | 45 near-miss distractors | **precision** — HVAC service agreements, roof warranties, elevator contracts, fire inspections, equipment leases. All rank high on the headline query. None is a store lease. |
| 3 | 35 multi-hop | 18 amendments (8 flip the HVAC answer), 8 estoppels (3 deliberately wrong), 5 assignments, a 4-document MSA→SOW→change-order chain |
| 4 | 30 needle carriers | CAM reconciliations, COI schedules, utility audits, condition assessments, board minutes |
| 6 | 15 governance | 5 no-answer documents, 1 contradictory pair, 1 near-duplicate pair, 6 restricted-category |

Format is an *attribute*, not a tier — 26 documents (10.2%) are rasterised images with no text
layer, so the OCR path runs for real.

## Files

| file | purpose |
|---|---|
| `corpus_spec.py` | fact tables and clause text — the 10 dimensions and their phrasings |
| `render.py` | format renderers (pdf / docx / xlsx / csv / txt / scanned / jpg) |
| `gen_corpus.py` | builds the corpus + `ground_truth.json` + `MANIFEST.md` |
| `verify_corpus.py` | **10 checks on the corpus itself, before anything is ingested** |
| `gen_questions.py` | derives `questions.json` from ground truth |
| `grade.py` | deterministic grader |
| `test_grader.py` | **tests the grader** against a perfect and a broken answer sheet |
| `load_corpus.py` | loads into an agent's knowledge base via the real ingest path |

## Running it

```bash
python gen_corpus.py --prune && python verify_corpus.py && python gen_questions.py
```

`--prune` removes files a run did not produce. Without it, regenerating into a dirty directory
leaves stale documents that get ingested alongside the real corpus and corrupt every count.

```bash
python test_grader.py          # prove the grader discriminates before trusting a number
python load_corpus.py --limit 12   # ALWAYS smoke first
python load_corpus.py              # the full 255 — see timing below
python load_corpus.py --status
python load_corpus.py --teardown
```

Loading is resumable: progress is saved after every document, so an interrupted run (or one
that hits the doc API's 503 admission gate) restarts where it stopped.

Then run `questions.json` against the agent however you like, and:

```bash
python grade.py --answers answers.json
```

`answers.json` is `[{"id": "Q001", "answer": "<the reply text>"}]`.

## Cost

Generation is **free** — deterministic seeded templates, no LLM calls, ~90 seconds.

Ingest is the real cost, and it is **time, not tokens**. Measured on a 5-document smoke run:
**~84 s/document → roughly 6 hours for the full 255.** Run it overnight, or load a tier at a
time with `--tier`.

Two per-document AI calls are **off by default** because they cost money and buy a retrieval
test nothing:

* `extract_fields` / `detect_document_type` — enable with `--with-ai-extraction`
* the knowledge-summary LLM call inside `index_knowledge_document` — enable with
  `--with-summaries`. This tree runs with `DOC_SEARCH_ENABLE_SUMMARIES=False`, so those
  summaries are generated and then never read.

Per-question: extrapolating pack 13's measured sweep (~$0.24 over 360 pages), a full
all-documents question over 1,153 pages costs roughly **$0.60–0.90**. Needle questions are far
cheaper. Fan-out is what to budget for.

## The 75 graded questions

| class | n | graded by |
|---|---|---|
| fanout | 28 | set equality + a separate recall figure over the synonym-only subset |
| needle | 18 | exact answer |
| aggregate | 8 | exact count; superlatives are tie-aware |
| multihop | 8 | exact answer, where the amendment must win over the base lease |
| negative | 5 | refusal expected — a confident answer is a hallucination |
| acl | 4 | run twice: authorised (expect the value) and unauthorised (expect refusal) |
| precision | 2 | must NOT cite the near-miss distractors |
| conflict | 2 | must surface both values and say they disagree |

**The synonym-only metric is the one to watch.** 37 of the 130 leases never use the query
keyword at all — they say "climate control plant" where the question says "HVAC". For the
headline landlord-HVAC question, 21 of the 45 correct answers are in that class, so pure
keyword matching caps out around 53% recall. That gap is the cleanest available separator
between real semantic retrieval and lexical matching.

## Things this pack refuses to do quietly

Both of these are direct lessons from earlier packs, and both are enforced in code:

* **`verify_corpus.py` runs before ingest.** A needle in a format that drops it, a "silent"
  lease that actually says HVAC, a scanned page that kept its text layer, an amendment that
  restates instead of flipping — all of those are invisible once you are looking at recall
  numbers. Four of them were real and were caught here during the build.
* **`test_grader.py` tests the grader.** Pack 13 published two engine comparisons that were
  twenty points wrong because a store-ID regex only matched `S1\d\d`. Store IDs here are
  validated against the actual universe read from ground truth, and IDs with the right shape
  but no document behind them are surfaced as hallucinations rather than silently dropped.

## Known finding from the smoke run

**DOCX documents are stored as a single page regardless of length.**
[`LLMDocumentEngine.py:4322`](../../LLMDocumentEngine.py) — *"Handle as a single page
document"*. A 7-page DOCX with 6 explicit `w:br type="page"` breaks became 1 page row with all
11,629 characters in it. Text is not lost; pagination is.

Consequences worth knowing before you read any result: page-level retrieval cannot locate a
fact *within* a DOCX; a DOCX page-hit returns the whole document into context
(`DOC_INCLUDE_FULL_PAGE_IN_CHUNK_RESULTS=true`); and the 999-page routing threshold undercounts
DOCX by roughly 4.5×. 31 documents in this corpus are DOCX, contributing 139 ground-truth pages
that store as 31 — so a full load stores **1,045 pages, not 1,153** (108 lost). Still past the
999 threshold, so the corpus design survives the defect. Not fixed here — this pack builds the
corpus, it does not change the engine. Write-up for the fixer:
[`docs/docx-pagination-handoff.md`](../../docs/docx-pagination-handoff.md).

## Reproducibility

Same seed, same corpus. `ground_truth.json` is byte-identical across runs, and 194 of the 255
documents are byte-identical. The other 61 (31 docx, 25 scanned pdf, 5 xlsx) differ only in
format-level metadata — Office zip member timestamps and PIL's PDF creation date — with
identical content. Native PDFs are pinned via `rl_config.invariant`.
