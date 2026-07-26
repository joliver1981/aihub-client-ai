# Document Search Engine — Architecture Analysis & Re-Core Plan

**Date:** 2026-07-24, P0 measurements added 2026-07-25 · **Status:** analysis + plan only, NO code changes
**Pattern precedent:** `docs/nlq-agentic-engine-plan.md` (side-by-side engine + config switch + fallback)
**Companion:** `docs/document-engine-prompt-caching-analysis.md` (cost model, relay constraints)

---

## P0 RESULTS — measured 2026-07-25 against 20 real leases (`C:\temp\leases`)

Measurement scripts are in the session scratchpad (`p0_1_corpus.py`, `p0_1b/c/d_*.py`,
`p0_2b_sql.py`, `p0_3_fanout.py`). All read-only: no product code changed, no live store
mutated. **Three hypotheses confirmed, one refuted, three new defects found.**

> **Second-pass verification 2026-07-25 (james challenged two findings).** One finding was
> **materially corrected**: the ingestion page-loss is real but the first pass mischaracterized
> it — it measured `validate_extraction_quality`, which has **no production caller**, and framed
> the loss as requiring base+amendment merged into one PDF. The corpus's amendments are
> **separate files** (james was right), and on the LIVE path they lose pages anyway — worse than
> first reported. See the corrected section below. All other load-bearing claims re-verified
> against code (cliff values, `apply_caps=False`, FANOUT constants, broken tools, verbatim page
> storage).

### The corpus

20 PDFs · 264 pages · ~100K tokens. 13 base leases (9–17 pages, ~3K tokens each) plus one
realistic 79-page / 64K-token commercial lease, plus **7 amendments**.

### ✅ D1 CONFIRMED — production's cliff is at 999 pages

| where | value | applies to |
|---|---|---|
| `config.py:696` default | 500 pages | only if nothing overrides it |
| **`dist\.env:50`** | **999 pages** | **production installs — this is the live value** |
| this dev tree's `.env:7` | 5 pages | dev only |

(`.env.template` is not used by production installs — disregard it.)

The routing decision is `agent_knowledge_integration.py:2764`:

- `total_pages <= threshold` → **brute force, `apply_caps=False`** — every page of every
  document, uncapped. Cannot miss.
- `total_pages > threshold` → smart retrieval (NEEDLE / FANOUT / AGGREGATE). Can miss.

So in production the cliff sits at **999 pages** ≈ **77** of these leases, or **~13** at the
realistic 79-page size. A client with hundreds of leases is 4–20× past it. Below the line they
get a method that cannot miss; above it, one that can — and nothing signals the crossing. That
is the reported symptom, exactly.

Measured against the live knowledge base (69 agents, 1,197 documents, 11,268 pages): the largest
agent has 475 pages, so **every agent here is below 999 and on brute force in production**. On
this dev tree, pinned to 5, 24 of 69 are on retrieval. **Dev exercises the retrieval path;
production mostly doesn't** — the path that matters most at scale is the one least exercised.

### 🔴 NEW — the brute-force dump is uncapped, and can exceed the code's own "safe" limit

`_format_knowledge_response(..., apply_caps=False)` on the brute-force branch sends everything.
The capped path used elsewhere sets `MAX_TOTAL_CHARS = 400_000` (~100K tokens), commented
*"safe for Claude's context window"*. Below the threshold, that cap does not apply. Measured:

| agent | docs | pages | chars sent | ~tokens | vs the code's own 400K "safe" cap |
|---|---|---|---|---|---|
| 466 | 1 | 396 | 1,304,486 | **326K** | **3.3×** |
| 292 | 51 | 51 | 1,150,393 | **288K** | **2.9×** |
| 736 | 8 | 475 | 1,148,761 | **287K** | **2.9×** |

All three are under 999 pages, so production sends this **on every question to that agent**.
Fine on a 1M-context model, an overflow on a 200K one — worth confirming which model the
knowledge path actually resolves to. Either way it is a real per-question cost and latency fact,
and the page-count threshold is a poor proxy for context size: agent 292 is 51 pages but 288K
tokens, while 475 pages elsewhere costs about the same. **The gate should be measured in
characters/tokens, not pages.**

### 📊 BLANK-PAGE AUDIT — historical exposure is tiny; the weak point is remediation

Full audit of `DocumentPages` (2026-07-25, script `audit_blank_pages.py` / `audit_sources.py`):

| | count | share |
|---|---|---|
| pages stored | 11,809 | |
| **pages with no usable text (≤20 chars)** | **17** | **0.14%** |
| documents affected | 10 | 7 wholly blank, 3 partially |

Of the 17, most are deliberate test artifacts (`W07_empty.docx` ×2, `W04_images_only` ×2,
`test_file_*.txt` ×3) that are *supposed* to be empty. Genuine content loss is:

| document | pages | blank | note |
|---|---|---|---|
| `TEST_B_lease_with_amendment_merged.pdf` | 16 | **4 (25%)** | james's own test upload — pages 13–16, exactly as predicted |
| `mixed_test_document` (invoice) | 12 | **5 (42%)** | an earlier mixed test, same class |
| `ResumeTershelleDolcy.pdf` | 3 | 1 | source gone; genuinely-blank vs lost unverifiable |

**So the mechanism is confirmed in production data, but this corpus has barely been touched by
it** — it contains almost no flattened-text PDFs. The exposure is *prospective*, and it is
concentrated by document class: 7 of 7 amendments in `C:\temp\leases` are that shape. A
real-estate client uploading a lease portfolio hits it on every amendment.

**The real structural problem the audit surfaced — no re-processable source:**

| | count | share |
|---|---|---|
| documents with `original_path` set | 1,252 / 1,266 | 99% |
| …whose file still exists on disk | **0 of the 10 affected** | — |
| documents with `archived_path` set | **57 / 1,266** | **4.5%** |

`original_path` points at transient upload locations that are cleaned up; `archived_path` (the
Azure file share, which does persist) is populated on 4.5% of documents. **For ~95% of the corpus
there is no source to re-process.** Any ingestion defect found after the fact cannot be
remediated in place — it requires the user to re-upload. That is worth fixing independently of
this bug: an ingestion pipeline with no retained source has no repair path.

### ❌ D3 REFUTED — nothing is being rejected at the embedding limit

Hypothesis was that chunks exceeding `text-embedding-3-small`'s 8,192-token input limit are
silently dropped, because the document path has no `VECTOR_EMBEDDING_MAX_TOKENS` enforcement.
Measured across both stores:

| store | chunks | p50 | p99 | max | over 8,192 tok |
|---|---|---|---|---|---|
| `documents` | 3,642 | 508 ch | 2,028 ch | 7,551 ch (~1,887 tok) | **0** |
| `agent_knowledge` | 18,500 | 510 ch | 1,959 ch | 3,408 ch (~852 tok) | **0** |

The smart chunker never produces chunks near the limit. The missing cap is a latent risk, not an
active defect. **Retracted as a priority.**

### 🔴 CONFIRMED — CORRECTED 2026-07-25 second pass — silent page loss at ingestion, on the LIVE path, in ANY packaging

> **Correction.** The first pass tested `validate_extraction_quality` and concluded standalone
> scans were "caught correctly (7/7 route to OCR)" and only *merged* base+amendment PDFs lose
> pages. Both statements are wrong about production: **`validate_extraction_quality` has no
> production caller** (grep: only its definition and unit tests), and the corpus's amendments
> exist as **separate files**. The live path was re-traced and re-measured 2026-07-25 (env
> `aihub2.1`, the main app's env). The defect is real, simpler, and broader.

**The live path.** `LLMDocumentEngine._process_pdf` (`:2269`) → `FastPDFExtractor
.extract_with_details` → **`extract_hybrid_with_details`** (`DOC_USE_FAST_PDF_EXTRACTION=True`
hardcoded, `config.py:611`). Routing is **per page** on a single signal —
`classify_page_needs_ai` = *"does the page embed an image?"* (`fast_pdf_extractor.py:325`).
No-image pages get `page.get_text()` verbatim, and **no emptiness check runs afterwards**:
`_process_pdf`'s docstring promises *"if fast extraction produces poor results: fall back to
AI"* — that fallback is not implemented anywhere. Pages are stored into
`DocumentPages.full_text` exactly as extracted (`process_document` `:909-957`), and the empty
text propagates to **both** search stacks *and* the brute-force dump. Also:
`force_ai_extraction=True` (passed by two callers) does **not** force AI page extraction — per
its own docstring it forces AI *document-type/structure* detection only (`:875`, `:919`).

**Measured on the live routing, files as they actually exist (separate):**

| standalone file | pages | → AI vision (saved) | → fast, stored EMPTY |
|---|---|---|---|
| S001-a1 Market Square | 4 | 2 | **2** |
| S002-a1 Harborview | 4 | 0 | **4 — entire file** |
| S003-a1 Riverdale | 4 | 0 | **4 — entire file** |
| S003-a2 Riverdale | 5 | 0 | **5 — entire file** |
| S003-a3 Riverdale | 6 | 0 | **6 — entire file** |
| S003-a4 Riverdale | 6 | 2 | **4** |
| S009-a1 Peach Plaza | 5 | 0 | **5 — entire file** |
| **7 of 7 amendments** | **34** | **4** | **30 pages · 5 files lost entirely** |

All 13 base leases and the 79-page lease: **zero loss** (digital text). Merged fixtures lose the
same pages — **packaging is irrelevant; the loss follows page type.** Every lost page carries
843–1,405 vector drawing ops — visible ink, zero text layer, zero embedded images. That is
flattened/outlined text: the signature of e-sign platforms, some print-to-PDF drivers, and fax
converters. True raster scans (pages with embedded images) route to AI vision and are fine.

**What the lost pages say** (visually verified from renders): S003's First Amendment — trade-name
change, permitted-use rewrite — is **100% invisible** to the system. S003's Fourth Amendment
keeps its headline term by luck — the 7-year extension to **2033-04-21** sits on page 1, which
embeds an image and routes to vision — but its page 3 (exclusive-use protections, renewal at 94%
FMV, parking allocation, going-dark covenant) is silently lost. The structural point stands,
stronger: the amendment layer — where terms supersede — is precisely the class the live path
drops, in any packaging.

**Already-ingested documents never self-heal.** Any client store loaded through this path may
already hold silently-empty pages; an audit (stored-empty `full_text` vs page ink) plus a
re-extraction pass is required. The audit doubles as the real-world frequency measurement for
this defect class.

### ❌ RETRACTED — "the embedding function will break on restart"

I claimed the vector API was a restart-triggered landmine. **That was wrong**, and it was my
error: I tested in the wrong environment.

Each service runs in its own conda env. The vector API (:5031) runs under **`aihubvector2`**,
which has **openai 1.79.0**, where Chroma 0.6.3's `OpenAIEmbeddingFunction` uses the modern
client. I tested under `aihub2.1` (the main-app env, openai **2.22.0**), where that same class
hits the removed `openai.Embedding` API. The service has restarted many times and works; nothing
here is at risk.

What remains true, and is minor: `_get_embedding_function`'s `azure` branch
(`LLMDocumentVectorEngine.py:129-164`) references `EmbeddingFunction` / `Documents` /
`Embeddings`, which are imported only inside the later `hash` branch → `NameError` swallowed by a
bare `except: pass` → returns `None`. It is dead code, not a live defect, because the configured
value is `openai`. Worth a cleanup, nothing more.

**Lesson for this document:** per-service conda envs mean a finding in one env says nothing about
another. Any environment-dependent claim must name the env it was measured in.

### ⚠️ D2 PARTLY REFUTED — the skip gate is not uniformly lossy, it is *unpredictably* lossy

13 text-bearing leases (967 chunks, 512/64) × 8 realistic portfolio questions, replaying the
exact gate: per-document search, skip below 0.4, else read top 2 chunks.

| question | read | skipped | best sim | doc read |
|---|---|---|---|---|
| HVAC maintenance responsibility | 13 | **0** | 0.721 | 6.5% |
| Lease expiration date per property | 13 | **0** | 0.607 | 7.1% |
| Percentage rent clause | 13 | **0** | 0.603 | 6.1% |
| Base rent per store | 13 | **0** | 0.612 | 6.8% |
| Assignment / subletting without consent | 13 | **0** | 0.698 | 6.9% |
| **Roof repairs and replacement** | 6 | **7 (54%)** | 0.450 | 5.9% |
| Security deposit amount | 13 | **0** | 0.577 | 6.5% |
| Co-tenancy / exclusive use | 13 | **0** | 0.615 | 6.7% |
| **overall** | | **7/104 (6.7%)** | 0.350–0.721 | **6.6%** |

**Correction to my earlier claim.** I implied FANOUT routinely drops a large, unknown share of
documents. It does not — on 7 of 8 questions it skipped **nothing**. The 6.7% aggregate is much
better than I asserted, and the production path should do better still (see caveats).

**But the one failure is worse than a uniform loss would be.** "Who is responsible for roof
repairs?" silently dropped **7 of 13 leases**, and every dropped document scored **0.350–0.397** —
all within 0.05 of the gate. The answer would confidently cover 46% of the portfolio and say
nothing about the rest. Two consequences:

1. **Completeness depends on question phrasing, not on document content.** The gate is a
   knife-edge; a reworded question flips seven leases in or out. A user cannot tell which kind of
   question they just asked.
2. **A rare, silent, total failure is harder to live with than a steady partial one.** Uniform
   30% loss would be noticed and calibrated against. 0% loss seven times and 54% the eighth time
   is exactly what produces "it seems to work well, but I don't trust it."

**The systemic finding is coverage, and it is uniform: 6.6%.** Of documents that *do* pass the
gate, the extractor sees about 1,000 of ~12,000 characters — roughly one page of a 13-page lease.
That holds on every question, including the seven where nothing was skipped. This, not the skip
rate, is the main argument for SWEEP.

**Caveats — read the 6.7% as an upper bound.** The harness chunks with the standard
512/64 recursive splitter, not production's LLM smart chunking with `[doc_identifier]
[section_breadcrumb]` prefixes, which should *raise* similarities and lower skips. N is small
(13 docs × 8 questions). The image-only amendments are excluded, so this is the best case for
the current engine. **The right follow-up is to instrument production directly** rather than
treat this number as definitive.

### Live evidence for the FANOUT gate being a noisy signal

A `/search` on the production store for *"HVAC maintenance responsibility"* returns, as its
**top** hit at similarity **0.508**, a retail manager's résumé (about occupancy rates and
scheduling contractors) — comfortably above the 0.4 FANOUT threshold. Second and third are a
vendor data-handling agreement (0.430) and a hazmat compliance manual (0.418), also above the
gate. The gate admits these while potentially rejecting a real lease clause.

### Cost, recalibrated on real documents

My earlier 30K-tokens-per-lease estimate was ~6× too high for these leases. Measured:

| corpus | tokens | Haiku 4.5 sync | Haiku batched |
|---|---|---|---|
| these 20 leases | 100K | $0.10 | $0.05 |
| 500 leases at this size | 2.5M | **$2.51** | **$1.25** |
| 500 leases at the realistic 79-page size | 32M | $32 | $16 |

Reading every lease in full costs between **$1 and $32** depending on document size. The
conclusion in §3 holds and gets stronger: the top-2-chunks-per-document assumption is not
buying anything worth its recall cost.

---

## 0. Non-negotiables (copied from the NLQ re-core, because they worked)

1. **The current engines are not touched.** `DocUtils.py`, `LLMDocumentVectorEngine.py`,
   `agent_knowledge_integration.py`, `TextChunker_LLM.py` stay byte-for-byte. They are the
   trusted fallback and they stay indefinitely.
2. **New code is purely additive** — a new package plus mechanical one-line swaps at the
   construction sites. Copy from the old engines; never import from them, never edit them.
3. **A setting switches engines**, globally and per-agent, defaulting to `legacy`.
4. **Runtime auto-fallback + circuit breaker.** A failure in the new engine must never mean a
   dead feature.
5. **New rule, specific to search:** *no silent drops.* Every exhaustive answer must state what
   it read and what it didn't. This is the actual fix for the confidence problem.

---

## 1. What exists today (verified against code + live stores, not assumed)

There are **two independent document search subsystems**, with different stores, different
metadata schemas, and different retrieval philosophies.

### 1a. Document repository search — the `document_super_search` path

| Layer | Location |
|---|---|
| Agent tool (PRIMARY per `core_tools.yaml:157-160`) | `GeneralAgent.py:808` `document_super_search` |
| Implementation | `DocUtils.py:3605` `document_search_super_enhanced_debug` |
| Semantic sibling (declared **FALLBACK only**, `core_tools.yaml:155`) | `GeneralAgent.py:822` `search_documents_meaning` → `DocUtils.document_search` |

The primary path is **a multi-stage LLM planner that chooses among four retrieval strategies**:

1. `get_document_types()` → mini-LLM picks relevant document types (`DocUtils.py:3648-3660`)
2. ACL intersect with the per-agent allow-list (`:3671-3680`)
3. Field metadata + mini-LLM field selection (`get_document_universe` :2151,
   `ai_select_relevant_fields` :3257)
4. **Strategy planner LLM** (`:3742-3800`) emits one of
   `semantic` | `field` | `hybrid` | `wide_net_filter`
5. Dispatch:
   - `semantic` → `VectorEngineClient.search_for_ai` → `POST /search_for_ai`
     (`app_vector_api.py:335`) → `ChromaDBStore.search` (`LLMDocumentVectorEngine.py:335`)
     → `_group_by_best_chunks` (`:400`)
   - `field` → `DocUtils.document_search` (`:607`) — pure SQL, `full_text LIKE`
   - `wide_net_filter` → `document_search_wide_net_strategy` (`:4884`)
6. Fallback chains 1–4 (`:4060-4213`)
7. **Haiku rerank** — `rank_search_results` (`:401`), fetch 30 / keep ≤30 / threshold 0.5
8. Optional completeness check (`check_document_completeness` :3016)
9. JSON response, page-text snippets capped at `DOC_PAGE_TEXT_LIMIT_IN_RESULTS = 500` chars

So this path *does* have a vector stage and *does* have a reranker. Its weaknesses are
different from what a first read suggests — see D3/D4 below.

Entry points: the agent tool (`GeneralAgent.py:808`, ACL-wrapped at `:3159`) and Command Center
(`command_center_service/graph/nodes.py:3460` → `POST /api/internal/document-search` →
`app.py:5395`). **No frontend JS calls it** — the human `/document-search` UI (`app.py:7523`) is a
separate SQL attribute search that never touches this path.

### 1b. Agent knowledge search — the vector path

`agent_knowledge_integration.py:1207` `search_knowledge_vectors`, fronted by a **mini-LLM router**
(`:1299-1342`) that classifies each query as one of three modes — no keyword heuristics, which
matches the standing "mini-LLM over regex" directive:

| Mode | Behavior |
|---|---|
| `NEEDLE` | top-k dense retrieval + parent-page expansion (`KNOWLEDGE_PARENT_CHILD_RETRIEVAL=True`, page cap 12,000 chars, total cap 80,000 chars) |
| `AGGREGATE` | wide vector search (top-30), or iterate document summaries when enabled |
| `FANOUT` | **per-document map-reduce** — the existing answer to "touch every document" (`:1695-1898`) |

**But before any of that runs, there is a size gate.** `smart_knowledge_retrieval`
(`:2044-2337`) first counts the agent's knowledge pages (`_count_agent_knowledge_pages` :2338).
Below `KNOWLEDGE_BRUTE_FORCE_PAGE_THRESHOLD` (**500 pages**, `config.py:696`) it calls
`_load_agent_knowledge_contents` (`:2372`) and **dumps every page into context — no retrieval at
all.** Above 500 pages, retrieval engages. This is the single most important fact in this
document; see D1.

FANOUT itself is genuinely well-built: parallel workers, Haiku per-document extraction, a reduce
step, a kill switch (`KNOWLEDGE_FANOUT_ENABLED`). Its constants (`config.py:712-716`, so they
*are* env-overridable):

| Constant | Default | Read at |
|---|---|---|
| `KNOWLEDGE_FANOUT_MAX_DOCS` | 1500 | `:1715` |
| `KNOWLEDGE_FANOUT_PER_DOC_TOP_K` | **2** | `:1721` |
| `KNOWLEDGE_FANOUT_SKIP_SIMILARITY_THRESHOLD` | **0.4** | `:1722` |
| `KNOWLEDGE_FANOUT_PARALLEL` | 20 | `:1723` |

### 1b-bis. Prior art on the document side: `wide_net_filter`

The document stack has its own full-corpus scan, reachable only when the strategy planner picks
it (`DocUtils.py:4042`): `document_search_wide_net_strategy` (`:4884`) → `ai_extract_search_terms`
(`:4948`, LLM emits 3–8 terms) → **`find_all_candidate_pages` (`:4989`) — SQL over every page
with no TOP/LIMIT** → `ai_filter_pages_in_batches` (`:5081`, map) → `format_wide_net_results`
(`:5191`, reduce).

The map-reduce skeleton this plan needs therefore already exists in two places. What is missing
in both is *recall guaranteed by construction*: `wide_net_filter` gates on keyword `LIKE`
matches, FANOUT gates on embedding similarity. Neither reads documents that its gate rejected,
and neither reports what it rejected.

### 1c. The stores (live measurements, this working tree)

| | `chroma_db/` → `documents` | `data/chroma_knowledge/` → `agent_knowledge` |
|---|---|---|
| Documents | 93 | 276 (across 15 agents, no cross-agent duplication) |
| Chunks | 3,862 | 18,500 |
| Avg chunk | 544 chars (~136 tok) | 911 chars (~230 tok) |
| Embedding | `text-embedding-3-small`, 1536-dim | same |
| Scoping metadata | **none** (no `agent_id`/`user_id`/`tenant_id` keys) | `agent_id` + `user_id` (`SHARED` = 15,720 of 18,500) |

Chunking: `VECTOR_CHUNK_SIZE = 512` chars / 64 overlap, with LLM "smart chunking"
(`VECTOR_USE_SMART_CHUNKING = True`, `TextChunker_LLM.py`) layered on top — 3,835 of 3,862
chunks carry `splitter_type='llm'`, plus `section_breadcrumb` / `section_summary` /
`chunk_type` / `contains_table` metadata. The smart-chunker is a real asset and should be kept.

Storage note: the `documents` collection stores a `full_text` metadata value per chunk averaging
2,489 chars against a 544-char chunk — 9.6 MB of metadata for 2.1 MB of chunk text (4.6×
amplification). Worth revisiting, but it is a cost issue, not a correctness one.

### 1d. Environment constraints that shape every option below

| Constraint | Consequence |
|---|---|
| Azure `TenantAppUser` has **zero DDL** (memory: `automation-approvals-bridge` — live `ALTER` 1088 / `CREATE` 262) | **No new SQL tables.** A derived-facts layer must reuse `Documents.document_metadata` (NVARCHAR(MAX), already exists per `migrations/001`) or a file sidecar (the proven `_approvals/` pattern) |
| `anthropic` package is **not installed** in `aihub2.1` | All Claude traffic is raw REST through `AnthropicProxyClient` (`CommonUtils.py:678`) |
| The relay payload has **no `tools` field** and **no `cache_control` passthrough** | Prompt caching and the Batch API are **not reachable today** — see §5 sequencing |
| `chromadb 0.6.3` | No native hybrid/BM25 search. `duckdb 1.4.1` **is** installed and has an FTS extension — that is the zero-dependency lexical index |
| No `sentence-transformers`, no local cross-encoder | Reranking must be an API call (a Haiku pass), not a local model |

---

## 2. Diagnosis — why confidence drops as document count rises

Six concrete mechanisms. The instinct is correct; these are the reasons.

### D1. The brute-force cliff — the system genuinely changes behavior as documents pile up

`KNOWLEDGE_BRUTE_FORCE_PAGE_THRESHOLD = 500`. Under 500 pages, agent knowledge **reads every
page of every document** — recall is 100% by construction, and answers are as good as the model.
Cross 500 pages and the system silently switches to retrieval, where recall is whatever the
embeddings happen to give you.

500 pages is roughly **12 leases**. A client with hundreds of leases is an order of magnitude
past the cliff, and nothing anywhere tells them they crossed it.

This is the mechanism behind the exact symptom described: *"it seems to work well, but I get
nervous when a lot of documents are uploaded."* That is not a vague unease — it is an accurate
read of a real discontinuity. Small corpora are handled by a method that cannot miss; large ones
are handled by a method that can, and the output looks identical either way.

The strategic consequence: **the fix is not to make retrieval better, it is to raise the ceiling
on brute force.** The threshold was set when reading everything was expensive. §3 shows it now
costs $7–15 for 500 leases.

### D2. FANOUT silently drops documents

For "which leases put HVAC on the tenant and which on the landlord?", FANOUT does this per lease:

1. Dense-search *within that lease*, take the best chunk.
2. **If that chunk's similarity is below 0.4, skip the lease entirely.**
3. Otherwise pass the **top 2 chunks** (~1,800 characters) to Haiku for extraction.

A 40-page lease is ~30,000 tokens. Two 900-char chunks is roughly **2% of the document**. And a
lease whose HVAC clause reads *"Tenant shall, at its sole cost and expense, maintain the heating,
ventilating and air conditioning systems serving the Premises"* may or may not clear 0.4 cosine
against the phrase "HVAC responsibility" — with a 128–230 token chunk, that similarity score is
noisy.

The failure is not that FANOUT is wrong. It is that when it drops a lease, **the answer looks
complete anyway**. There is no "read 340 of 500 leases" line. That is exactly the shape of a
system that "seems to work well" while producing an uneasy feeling at scale.

### D3. Silent drops at *ingestion*, not just at retrieval — CONFIRMED, different cause

Original hypothesis (chunks rejected for exceeding the 8,192-token embedding limit) was
**refuted** by measurement — zero chunks come close. See the P0 results above.

The real ingestion loss is in the live per-page router (**corrected 2026-07-25** — see the P0
section): `classify_page_needs_ai` sends any page without an embedded image to fast text
extraction, flattened/vector-outlined pages extract as empty, and empty pages are stored — no
fallback, no error, no log line (the docstring's promised fast→AI fallback is unimplemented;
`validate_extraction_quality` has no production caller). Verified on the live routing: 7/7
standalone amendments lose pages (30 of 34; five files lose every page), in any packaging.

This outranks every retrieval concern in this document. Content that never entered the index
cannot be recovered by better retrieval, better chunking, or a bigger context window. And the
amendment layer — where lease terms are actually superseded — is precisely the content most
likely to be affected, because amendments are the scanned/flattened part of a lease file.

### D4. 512-character chunks are a 2023 constraint applied to 2026 models

512 chars / 64 overlap was the right call when context windows were 8K and embeddings were weak.
Today it fragments precisely the multi-sentence provisions that matter, and it makes each
embedding a low-signal vector. Parent-child page expansion (§1b) mitigates this at *read* time
but does nothing at *match* time — a chunk that doesn't match is never expanded.

### D5. Lexical and semantic never run together, and the reranker is misgated

The strategy planner picks `semantic` **or** `field` — the two channels are alternatives, not a
fusion. Exact-term needles (a property address, a party name, a defined term, a dollar figure, a
section number) are the weakest case for embeddings and the strongest for BM25; a wrong planner
call sends them down the wrong channel with no second chance.

A Haiku reranker *does* exist (`rank_search_results`, `DocUtils.py:401`; `DOC_USE_LLM_RERANK`,
fetch 30 / keep ≤30 / threshold 0.5) — but it is gated on an unrelated flag:
`DocUtils.py:4222` requires `DOC_INCLUDE_SNIPPET_IN_RESULT` to be True before reranking runs at
all. Turning off snippets to save tokens silently turns off reranking.

### D6. No citations, so nothing is verifiable

Answers return prose plus filenames. There is no page or character anchor to click. The only way
to build trust is manual spot-checking, which does not scale and never fully resolves the doubt.

### Adjacent defects found while mapping (fix independently of this plan)

| Defect | Evidence |
|---|---|
| **`document_intelligent_search` raises `NameError` at runtime** — calls `document_search_super_enhanced(...)`, which does not exist in the repo (only `..._debug` and `..._with_intelligent_sizing`). Exposed as a live agent tool. | `DocUtils.py:5565`, `:5711`; tool at `core_tools.yaml:163` |
| **`search_documents_meaning` does not do semantic search** — it runs SQL `full_text LIKE '%…%'`, despite its name and its "conceptual/meaning-based" routing hint | `GeneralAgent.py:822` → `DocUtils.py:607`, `:1226-1233`; hint at `core_tools.yaml:155` |
| `DocUtilsEnhanced.py` is dead code importing the same missing symbol | its only importer is commented out at `GeneralAgent.py:1890` |
| `validate_extraction_quality` is exported but **never called in production** — the "quality gate" exists only in unit tests; `_process_pdf`'s docstring promises a fast→AI fallback that is unimplemented | grep: callers are `fast_pdf_extractor.py` (definition) + `tests/unit` only; docstring at `LLMDocumentEngine.py:2277` |
| `force_ai_extraction` is a misnomer — forces AI *structure/type* detection, never AI *page extraction*; two callers pass it apparently expecting the latter | `LLMDocumentEngine.py:805,820,875,919`; callers `app.py:6555`, `LLMDocumentEngine.py:1856` |
| Retrieval is `is_active`-blind — deleted documents' chunks are still retrieved | memory `agent-knowledge-vector-orphan-leak`; filter is `agent_id` + `user_id` only |
| Single global `documents` Chroma collection, no tenant field in the vector filter — isolation relies entirely on the SQL `tenant.sp_setTenantContext` layer | `app_vector_api.py:31-34`, `search_for_ai` filters on `document_type` only |

> **✅ Status update 2026-07-25 — four of the adjacent defects FIXED (same-day batch):**
> `document_intelligent_search` **removed** (tool fn, all yaml refs, prompt references; the
> name→function resolver logs-and-skips stale DB configs — verified graceful).
> `search_documents_meaning` **rewired to true semantic search** — new
> `DocUtils.document_search_meaning`: `search_for_ai` + dedupe + citation formatting +
> deleted-doc drop, ACL-scoped, with automatic fallback to the legacy SQL LIKE path
> (live-smoked: semantic finds HVAC results where LIKE returns zero).
> **Reranker ungated** from `DOC_INCLUDE_SNIPPET_IN_RESULT` (that gating also silently skipped
> deduplication; `rank_search_results` self-gates via `DOC_USE_LLM_RERANK`).
> **`is_active` leak closed at retrieval** via `KNOWLEDGE_FILTER_INACTIVE_VECTORS` (default
> True, fail-open on SQL errors, `SHARED` preserved; FANOUT already active-scoped by its
> caller). Live-verified: orphan served with gate off → blocked with gate on; active-doc
> results identical either way. **Scale discovery: 190 of 276 (agent,doc) vector sets in the
> dev knowledge store are orphans of deleted documents (69%)** — now unservable, but the
> one-time Chroma purge remains open backlog. `DocUtilsEnhanced.py` and
> `document_intelligent_search_with_ai_filtering` remain as inert, unexposed dead code.

---

## 3. What changed in two years — the economics are the headline

Verified current model facts:

| Model | Input $/MTok | Output $/MTok | Context |
|---|---|---|---|
| Haiku 4.5 (`claude-haiku-4-5`) | $1.00 | $5.00 | 200K |
| Sonnet 5 (`claude-sonnet-5`) | $3.00 ($2.00 intro thru 2026-08-31) | $15.00 ($10.00 intro) | 1M |
| Opus 4.8 (`claude-opus-4-8`) | $5.00 | $25.00 | 1M |

Plus: prompt caching (cache reads ~0.1×, writes 1.25× at 5-min / 2× at 1-hour TTL), the Batch API
(**−50%**, ≤24h), native **Citations** on document blocks (page/char anchored), and
**`search_result` content blocks** — GA, no beta header, supported on all active models — which
give model-native source attribution for RAG content.

**Apply that to the lease scenario.** 500 leases × ~30K tokens ≈ **15M tokens** for the whole
corpus. (Grounded: this system already stores single documents of 58K tokens.)

| Approach | Cost per exhaustive question |
|---|---|
| Read every lease in full, Haiku, synchronous | **~$15** |
| Same, via Batch API | **~$7.50** |
| Repeat question inside a 1-hour cache window | **~$1.50** |
| Extract facts once, then answer from structure | ~$15 once, then **~$0.01/question** |

**This is the whole argument.** Reading all 500 leases end to end is a $7–15 operation. The
architectural assumption baked into FANOUT — that you must pick 2 chunks out of each document —
is no longer an engineering constraint. It is now a *choice*, and it is the wrong one for
exhaustive questions.

---

## 4. Proposal — `doc_search_v2/`: one router, three strategies

Keep the existing mini-LLM router idea (it is good and it is already the right pattern). Replace
what happens *after* the routing decision.

### Strategy A — NEEDLE: hybrid retrieval, rerank, cite

- **Hybrid, always both channels**: dense (Chroma, existing) **+ BM25 (DuckDB FTS, already
  installed)** run in parallel and fused with reciprocal-rank fusion — replacing the planner's
  `semantic`-or-`field` either/or. Fixes D5, with no new dependency and no DDL.
- **Larger chunks**: raise the target to ~2,000 characters with structure-aware boundaries from
  the existing smart-chunker, **and add the missing embedding token cap on the document path**.
  Keep the 512-char index alongside if A/B shows it still wins for some query classes.
  Fixes D3 and D4.
- **Rerank**: reuse the existing `rank_search_results` Haiku reranker — but ungate it from
  `DOC_INCLUDE_SNIPPET_IN_RESULT` and give it its own flag. Cheap (~$0.001/query).
- **Cite**: return evidence as `search_result` content blocks with `citations: {enabled: true}`,
  so the model's answer carries `cited_text` + source + title natively. Fixes D6.
- Gate the where-filter by active `document_id`s (fixes the `is_active` leak), preserving
  `user_id='SHARED'`.

### Strategy B — SWEEP: an actual exhaustive scan

This is the new capability, and it replaces FANOUT for cross-corpus questions.

```
scope   → resolve the document set deterministically (filters, doc type, folder, date) — NOT by similarity
map     → one call per document, WHOLE DOCUMENT in context (Haiku, 200K ctx)
          structured output per doc: {answer, evidence_quote, page, confidence, not_found}
reduce  → assemble; group; count; produce the table the user actually asked for
ledger  → "500 in scope · 500 read · 0 skipped · 6 low-confidence (listed)"
```

Key differences from FANOUT and `wide_net_filter`: **no gate of any kind** — nothing is skipped
for being a poor embedding match (FANOUT) or for missing a keyword (`wide_net_filter`); **no
top-k truncation** (the whole document is read); **structured per-document output** (so the
reduce step is deterministic, not another LLM guess); and **a coverage ledger** (so the user can
see what was and wasn't read).

Framed against D1, SWEEP is simply **the brute-force path, made affordable at any corpus size** —
`KNOWLEDGE_BRUTE_FORCE_PAGE_THRESHOLD` generalized from "500 pages that fit in one context" to
"any number of pages, mapped one document at a time." The behavior users already get on small
corpora becomes the behavior they get on large ones.

Cost controls, in order of preference: Batch API for corpora above a threshold; prompt caching per
document for repeat questions; a per-run token budget with an explicit "this run would cost ~$X
across N documents — proceed?" confirmation above a configurable ceiling; documents over 150K
tokens fall back to a chunked map with overlap.

The coverage ledger is the trust fix. An answer that says *"read all 500"* is believable in a way
that today's answer is not — and an answer that says *"read 486, skipped 14 (listed below)"* is
**more** useful than a silently-complete-looking one.

### Strategy C — FACTS: extract once, answer forever

For homogeneous corpora (leases, contracts, invoices, POs) the same clauses get asked about
repeatedly. Run a one-time structured extraction per document into a per-document fact record,
then repeated aggregate questions stop being retrieval problems at all.

- Schema per document type, either author-defined or LLM-proposed from a sample.
- Storage **without DDL**: `Documents.document_metadata` (NVARCHAR(MAX), already present) and/or a
  JSON sidecar mirrored into DuckDB for querying.
- Refresh on re-upload; version the schema; keep provenance (page + quote) on every field so any
  fact is traceable back to the source.
- Then *"which leases put HVAC on us?"* is a **6-second, $0.01** query through the re-cored NLQ
  engine that already earned trust — instead of a 15M-token scan.

Strategy C is the strategic play; B is what makes C's answers auditable and covers the
long tail C's schema didn't anticipate. Ship B first (it is self-contained), C second.

### Routing

Extend the existing mini-LLM router: `NEEDLE` → A, `SWEEP` → B, `FACTS` → C when a fact record
exists for every document in scope and the question maps onto the schema, else fall through to B.
Router failures resolve to A (cheapest, safest).

---

## 5. Sequencing dependency — read this before scoping

**Prompt caching and the Batch API are not reachable from the client today.** The relay
(`api/proxy/anthropic/messages/v2`) does not accept `cache_control`, and there is no batch route
(see `docs/document-engine-prompt-caching-analysis.md` §3A). That affects the *cost* of Strategy B,
not its *feasibility*:

- **Buildable now, at full price:** the SWEEP map step is a plain per-document completion — no
  tools, no caching required. It works through the existing relay unchanged. ~$15 per 500-lease
  question.
- **After relay work (−50% to −90%):** Batch support halves it; per-document caching makes repeat
  questions ~10× cheaper.
- **Escape hatch:** the OpenAI path is a *direct SDK* (`get_openai_config`) and is already proven
  in production by the NLQ V3 loop. If relay work stalls, the map step can run there.

Recommendation: build B against the relay as-is, measure real cost on a real corpus, and use that
number to justify the relay work — rather than blocking the build on it.

---

## 6. The switch (mirror `nlq_engine_factory.py` exactly)

`doc_search_factory.py`, one construction point, precedence **deny-list → allow-list → default →
legacy**, every error path resolving to legacy.

| Key | Default | Purpose |
|---|---|---|
| `DOC_SEARCH_ENGINE_DEFAULT` | `legacy` | global engine |
| `DOC_SEARCH_V2_AGENT_IDS` | `` | per-agent allowlist |
| `DOC_SEARCH_LEGACY_AGENT_IDS` | `` | per-agent denylist (wins) |
| `DOC_SEARCH_V2_FALLBACK` | `true` | serve via legacy on failure |
| `DOC_SEARCH_V2_TIMEOUT_S` | `120` | wall-clock budget (sweeps are slower) |
| `DOC_SEARCH_V2_BREAKER_THRESHOLD` / `_COOLDOWN_S` | `3` / `600` | circuit breaker |
| `DOC_SWEEP_MAX_DOCS` | `1000` | hard scope cap — **logged in the ledger when it bites** |
| `DOC_SWEEP_COST_CONFIRM_USD` | `5.00` | above this, ask before running |
| `DOC_SWEEP_MODEL` | `claude-haiku-4-5` | map-step model |
| `DOC_SEARCH_V2_FORCE_ERROR` | `false` | chaos drill |
| `DOC_SEARCH_V2_ECHO_ENGINE_HEADER` | `false` | dev/CI `X-Doc-Search-Engine` |
| `DOC_SEARCH_SHADOW_COMPARE` / `_SAMPLE_PCT` | `false` / `10` | shadow mode |

Plus: promote FANOUT's four inline constants into `config.py` so the *legacy* path becomes tunable
too — a one-line, zero-risk improvement independent of everything else here.

Four safety layers, same as NLQ: in-request fallback, process circuit breaker, chaos flag, shadow
mode. Rollback ladder: automatic breaker → per-agent denylist → global default.

---

## 7. Testing — recall is the metric, and nothing measures it today

Model on `test_human/12_Data_Explorer_NLQ/` (single-source-of-truth `battery.py`, derived Word doc
and answer key, tiered questions, live oracle). **But the oracle has to work differently.**

NLQ could re-execute ground-truth SQL against the live DB. Documents have no such oracle — so
build one:

1. **A labeled fixture corpus.** ~40 synthetic-but-realistic leases (varied phrasing for the same
   provisions, some silent on a provision, some contradictory, a few scanned/OCR-degraded).
2. **Per-document ground truth** for ~10 sweep questions — every document's true answer is known
   and written down. This makes **recall measurable**, which is the entire point.
3. **Deliberate traps**, in the spirit of pack 12's negative-margin honesty probe:
   - a lease where the HVAC obligation is split (tenant for routine, landlord for replacement)
   - a lease that is genuinely silent — the correct answer is "not stated", not a guess
   - a lease where the provision is in an amendment, not the base document
   - a lease using only synonyms ("climate control equipment", never "HVAC")
   - a scanned lease where the clause is in an image-only page
4. **Scoring gates fixed in advance:**
   - **Sweep recall ≥ 0.95** and **precision ≥ 0.95** vs. human labels
   - **Coverage honesty = 100%** — every skipped document is reported; a silently-dropped
     document is an automatic FAIL regardless of the answer's quality
   - **Zero fabrication** — an answer for a silent lease is an automatic FAIL
   - Needle: nDCG@8 ≥ legacy, and every cited quote must actually exist in the cited document
     (deterministically checkable — the strongest anti-hallucination gate available here)
   - Latency: sweep p50 under 3 minutes for 100 documents
5. **Both-engines A/B runner** (mirror `tests_v2/competency/run_nlq_engine_comparison.py`) driving
   legacy and v2 through the same battery in-process, with a cross-family LLM judge that can only
   rescue a regex miss, never overturn a pass.

Run the same battery against a **500-document** corpus, not just 40, because the entire premise of
this work is that the failure mode is scale-dependent.

---

## 8. Phases

> **Reordered 2026-07-25 after P0.** The measurements moved three bug/config fixes ahead of the
> re-core. Two of them are worth more per hour of effort than anything in the original plan, and
> one is a one-line change. Build the new engine only after these land.

| Phase | Work | Acceptance |
|---|---|---|
| **P0** | ✅ **DONE** — measured against 20 real leases; see the P0 RESULTS section at the top | D1 confirmed, D2 partly refuted, D3 refuted-and-replaced, 3 new defects found |
| **PA — bugfix** ✅ **BUILT 2026-07-25** | `classify_page_needs_ai` now rescues no-image pages that fast-extract to <`DOC_HYBRID_BLANK_PAGE_MIN_CHARS` (50) chars while carrying ≥`DOC_HYBRID_BLANK_PAGE_MIN_DRAWINGS` (10) drawing ops → routed to AI vision. Kill switch `DOC_HYBRID_BLANK_PAGE_RESCUE` (default True). Every hybrid return point logs `BLANK_PAGE_STORED` warnings for any page still stored empty. Audit tool shipped as repo-root **`audit_blank_pages.py`** (read-only; classifies genuinely-blank vs CONTENT_LOST via source ink; checks `original_path` + `archived_path`; CSV; exit 2 on confirmed loss) | ✅ 0/20 corpus files lose pages (was 7/20, 30 pages); merged fixtures 0 lost; base leases still 100% fast (no cost added). ✅ Live vision smoke (env `aihubant`, proxy mode): S003-a1 fully recovered — 5,496 chars incl. "Skyline Clearance Center" (was 0). ✅ 42/42 unit tests (14 new lock the router incl. kill switch + thresholds). ✅ Audit run on dev store: 0 content-lost among classifiable; résumé p3 resolved genuinely-blank via archived copy. ⚠ Needs `app_doc_job_q` (env `aihubant`) restart to go live — it imports `fast_pdf_extractor` at startup |
| **PB** ✅ **BUILT 2026-07-25 (56cd4ed)** | Gate = page threshold AND `KNOWLEDGE_BRUTE_FORCE_CHAR_BUDGET` (default 400K chars; ≤0 disables); one-query pages+chars preflight; both knowledge tools share `_brute_force_within_budget`; routing trace logs pages/chars/budget | ✅ 7 unit tests; live: agents 292 (50pg/1.15M ch) and 736 (475pg/1.15M ch) now route to retrieval at prod threshold |
| **PC** ✅ **DONE 2026-07-25** | Dev `.env`=5 confirmed DELIBERATE by james (exercises the retrieval path); documented in dev `.env` + prod `dist\.env` comments referencing the char budget | Values documented as chosen, not inherited |
| **P1** ◐ partial | **Pack 13** (`test_human/13_Document_Competency/`, 5065ff9) = the labeled battery + live runner; legacy baseline published: **FANOUT portfolio sweep 86% completeness / 83% correctness** | Scale corpus (synthesize 100–500 leases) + scale re-run still pending |
| **P2** ✅ **BUILT 2026-07-25 (1105b64)** | `doc_search_v2/factory.py`: denylist→allowlist→`DOC_SEARCH_ENGINE_DEFAULT`(=legacy)→legacy, every error→legacy, 3-failure/600s circuit breaker; one guarded insertion at the `search_agent_knowledge` chokepoint with fallback into the untouched legacy call; full config block incl. `DOC_SWEEP_COST_CONFIRM_USD=5.00` | ✅ Legacy default provably inert; 25 unit tests (precedence, breaker, defer/chaos/fallback) |
| **P3** ✅ **BUILT — knowledge path 2026-07-25 (1105b64 + c2199b6)** | SWEEP: whole-document map (proxy Haiku, strict-JSON per doc, parse-fallback never dropped) → deterministic reduce → **coverage ledger**; cost estimate + confirm-above-threshold; NEEDLE defers to legacy via mini-LLM router. **Routing doctrine:** agents decompose portfolio questions into per-doc NEEDLE-shaped tool calls — route AND extract on `latest_user_input`, cache one sweep per (agent, user, question, **active-doc-set fingerprint**) so call storms reuse one run and deletes invalidate instantly | ✅ Live (agent 835 pilot): **C2 100% completeness / 100% correctness vs legacy 86%/83%** — incl. the Sunset landlord-full class both legacy paths missed; ledger relayed in the agent answer; C5 deleted-doc honesty holds. Scale run pending corpus |
| **P4** | **Strategy A (NEEDLE)** — DuckDB BM25 + RRF + Haiku rerank + `search_result` citations; larger-chunk index built alongside the existing one | nDCG@8 ≥ legacy; 100% of cited quotes verifiable in source |
| **P5** | **Strategy C (FACTS)** — extraction schema, DDL-free storage, NLQ hand-off | Aggregate questions answered in <10s at <$0.05, matching P3's sweep answers |
| **P6** | Fallback/breaker hardening, shadow mode, pilot runbook | Chaos drill passes live; shadow logs accumulating |

P0 first, deliberately: it costs almost nothing and it tells us whether D1 is the real problem or
a plausible-sounding theory. If FANOUT turns out to skip 2% of documents rather than 30%, the
priority order in this plan should change.

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| Sweep cost surprises a client | Cost estimate + confirmation above `DOC_SWEEP_COST_CONFIRM_USD`; ledger reports actual spend; Batch for large runs |
| Sweep latency on 500+ docs | Parallel map (existing FANOUT worker pattern), Batch for async, progress reporting; not offered as an interactive path above N docs |
| Re-embedding cost/effort for larger chunks | Build the new index *alongside*; never re-embed in place; `text-embedding-3-small` is $0.02/MTok — the 22K chunks in both stores re-embed for under $1 |
| Relay lacks caching/batch | §5 — build at full price, measure, justify; OpenAI direct-SDK escape hatch |
| Two stores diverge further | v2 reads both through one adapter; do **not** attempt a store migration in this project |
| Facts layer blocked by zero DDL | `Documents.document_metadata` + file sidecar + DuckDB; the `_approvals/` sidecar precedent proves this works in the Azure env |
| LLM judge bias in the battery | Cross-family judge (Haiku judging an Anthropic-served answer is same-family — use the GPT path for judging, or rely on the deterministic quote-existence check, which needs no judge at all) |

---

## 10. Open questions for james

1. **Which corpus do we tune against?** The lease client is the motivating case — can we get
   (or synthesize from) a realistic 100–500 lease corpus? The plan's central claim is
   scale-dependent and cannot be validated on the 93-document dev store.
2. **Interactive or async for sweeps?** A 500-document sweep is minutes, not seconds. Is that a
   "we'll email you when it's done" flow, a progress-bar flow, or both?
3. **Strategy C priority.** For a lease client asking the same 20 questions across the portfolio,
   C is dramatically better than B. Is that the real shape of the demand, or are the questions
   genuinely open-ended each time?
4. **Cost ceiling.** What is the per-question number that feels acceptable to a client — $1?
   $10? That sets `DOC_SWEEP_COST_CONFIRM_USD` and decides how hard we push on relay caching.
5. **Scope: both subsystems, or one?** This plan covers the repository path *and* the agent
   knowledge path. Halving the scope to agent-knowledge-only (where FANOUT already lives) would
   land faster.
