# Document Processing Engine — Anthropic Prompt Caching Analysis

**Date:** 2026-07-18 · **Status:** analysis only, no code changes
**Scope:** AI Extract workflow node + agent/user knowledge PDF uploads
**Question:** can Anthropic prompt caching reduce cost, and where?

---

## 1. How the engine spends tokens today (verified against code)

All Claude traffic flows through one wrapper, `AnthropicProxyClient` (`CommonUtils.py:678`), which by
default POSTs to the hosted Azure relay (`AI_HUB_DOCUMENT_API_URL` → `ai-hub-api.azurewebsites.net`).
The relay holds the real Anthropic key and builds the actual API request. Direct `anthropic.Anthropic`
SDK use happens only under BYOK or `AI_HUB_BYPASS_DOCUMENT_PROXY=True` (`api_keys_config.py:394`).

**There is no prompt caching anywhere today** — no `cache_control` in either pipeline, client or
(visibly) relay side.

### Pipeline 1 — AI Extract node (workflow engine, main app)

| Step | Call pattern | Doc content sent |
|---|---|---|
| PDF ≤100 pages | 1 call: `populate_schema_with_claude` (`AppUtils.py:3757`) | Whole PDF, native document block (uploaded multipart; relay builds the block) |
| PDF >100 pages | 1 call per ≤100-page chunk, 5-page overlap (`populate_schema_with_claude_chunked`, `AppUtils.py:3421`) | Each chunk PDF once |
| Text / non-PDF | 1 call via `azureQuickPrompt` (OpenAI path — out of scope) | — |

Request shape per call: system = `build_extraction_instructions(filename, …)` (per-document — embeds
the filename), user text = the JSON schema only, PDF as a separate block. Model `cfg.ANTHROPIC_MODEL`
(opus-4-8). The streaming↔non-streaming fallback (`AppUtils.py:3894-3925`) re-uploads and **re-bills
the entire PDF** when it fires.

### Pipeline 2 — Knowledge upload (Document API :5011 → Vector API :5031)

Per uploaded PDF:

1. **Doc-type detection** — 1 call on pages [0,1] (`LLMDocumentEngine.py:1023`).
2. **Text extraction** — text PDFs: PyMuPDF, **zero API calls**. Scanned PDFs: Claude Vision
   page-by-page or batches of 3 (`MultiPagePDFHandler`), each page sent once, on `cfg.ANTHROPIC_MODEL`.
3. **Knowledge summary** — 1 call on a ~5,000-char sample (`agent_knowledge_integration.py:334`).
4. **Smart chunking** — 1 call with the whole extracted text (≤~128k chars) or several windowed calls
   with overlapping segments (`TextChunker_LLM.py:363/431`).
5. **Table detection** — 1 Haiku call per oversize chunk (`TextChunker_LLM.py:690`).
6. **Embeddings** — local (ONNX/SentenceTransformer inside Chroma). No API cost.

---

## 2. The core economics

Prompt caching is a **prefix match**: a cache write costs **1.25×** normal input (5-min TTL) or
**2×** (1-hour TTL); a cache read costs **~0.1×**. So caching only saves money when **the same byte
prefix is sent ≥2 times within the TTL**. Minimum cacheable prefix on opus-4-8 is **4,096 tokens** —
smaller prefixes silently don't cache.

Two consequences for this engine:

- **Only the document content is big enough to cache.** The extraction system prompts and schemas
  are far below 4,096 tokens (and `build_extraction_instructions` embeds the filename, so it isn't
  even shared across documents).
- **Almost every call today is single-touch.** Each PDF, chunk, page, and text window is sent to the
  model exactly once. Pay-once is already optimal — blanket-adding `cache_control` would *add* ~25%
  to input cost with zero reads.

So the honest answer is: **naively turning on caching saves nothing and costs more. The wins come
from (a) making the relay capable of caching, then (b) restructuring the few flows where a second
touch of the same document exists or is worth creating.**

---

## 3. Recommended approaches (ranked)

### A. Relay support for `cache_control` — the enabler (prerequisite for everything else)

The Azure relay is the choke point: it builds the real Anthropic request for every client install.
Nothing else on this list works without it.

1. **JSON path** (`api/proxy/anthropic/messages/v2`): the client already sends the `messages` array
   verbatim (`CommonUtils.py:813-818`). Confirm the relay forwards content blocks (including
   `cache_control`) untouched, and forwards `system` when sent as a block array rather than a string.
   If it does, the JSON path needs **zero relay changes** — callers can start marking breakpoints.
2. **Document path** (`api/proxy/process/documents/v2`): multipart form — there is currently *no way
   to express caching*. Add an opt-in form field (e.g. `cache_document=true`, optional `cache_ttl=5m|1h`)
   that makes the relay attach `cache_control: {type: "ephemeral"}` to the document block it builds.
3. **Pass usage back**: ensure `usage.cache_creation_input_tokens` / `usage.cache_read_input_tokens`
   flow through in responses so callers (and billing dashboards) can verify hits. The clients already
   read `response['usage']`, so this is likely free.
4. Optional relay-side smarts: content-hash uploaded files; only set `cache_control` when the flag is
   present *or* the same hash was seen recently. Since the relay serves all installs under one
   Anthropic org, cache reuse potential is maximal there (cache hits require byte-identical prefixes,
   so cross-tenant sharing is privacy-neutral).

**Pilot shortcut:** the direct-SDK path (BYOK / `AI_HUB_BYPASS_DOCUMENT_PROXY=True`) needs no relay
work — caching can be piloted there immediately with the stock SDK.

### B. AI Extract: "cache the document once, run N cheap passes" — the biggest real win

Today one giant call does everything, which forces wide schemas into one response (the code raises
on `max_tokens` truncation, `AppUtils.py:3942`) and makes verification passes cost-prohibitive.
With the document block cached, follow-up calls over the same PDF pay ~0.1× for the document tokens:

- **Verification / read-back pass** — a second call ("re-check these low-confidence fields against
  the document") becomes ~10% of the cost of the first, instead of ~100%. This matches the
  read-back-verification pattern already adopted elsewhere in the product.
- **Field-group splitting** — extract 40 fields as 4 calls of 10 (cached doc + different schema
  suffix each time) instead of one call that risks truncation. Cost: `1.25 + 3×0.1 = 1.55×` doc
  tokens vs `4×` if done naively without cache — and barely more than today's single fragile call.
- **Multiple AI Extract nodes on the same document in one workflow** — same cache entry serves all
  of them if the prefix matches (see breakpoint placement below).

Illustrative math (100-page PDF ≈ ~200k tokens native, opus-4-8 input $5/MTok ≈ $1.00/pass):

| Scenario | Uncached | Cached (5-min TTL) |
|---|---|---|
| 1 extraction pass only | $1.00 | $1.25 ← **worse — don't cache single-pass** |
| Extraction + verification | $2.00 | $1.35 (−32%) |
| 4 field-group passes | $4.00 | $1.55 (−61%) |
| Extraction + verify + formatting | $3.00 | $1.45 (−52%) |

**Breakpoint placement:** the document block must be the *first* (stable) content, with
`cache_control` on it; the varying schema/instruction text comes after. The per-document system
prompt is fine — it's tiny, and it sits in the same per-document cache entry. Passes must reuse the
same model and identical system/doc bytes or the prefix breaks.

**Make it opt-in per call/node**, not global — single-pass extractions should stay uncached.

### C. Streaming-fallback retry — fix the re-send, don't cache it

The fallback path re-uploads and re-bills the full PDF. Pre-paying the 1.25× write on *every* call
to insure against a *rare* retry is expected-value negative (break-even only if >~35% of calls
retry). The cheaper remedy is code-level: pick streaming vs non-streaming deterministically up front
(the 3 MB auto-switch already exists) so the fallback rarely fires. If pass B lands, retries get
cache reads for free anyway.

### D. Where caching does NOT help (checked, so nobody re-litigates it)

| Flow | Why not |
|---|---|
| >100pp chunked extraction | Each chunk is a distinct PDF sent once; pay-once beats 1.25× write. The 5-page overlaps are different byte streams — no prefix match. |
| Scanned-PDF Vision OCR pages | Every page/batch is unique content. Shared system prompt is below the 4,096-token minimum. |
| Smart-chunker windowed mode | Overlaps are mid-text, not prefixes. Restructuring to "full doc as cached prefix + per-window suffix" costs `1.25 + 0.1(W−1)` × doc vs ~`1.0 + overlaps` today — the write premium loses unless overlap >~35% of the doc. Leave as is. |
| Doc-type detection / summary / table detection | Single small calls on distinct content. |
| Embeddings | Local, no API cost. |
| 429/529 retries | Failed requests aren't billed; nothing to save. |

### E. Adjacent cost levers (not prompt caching, but bigger in places — flagging for completeness)

1. **Batches API (−50% on everything).** The entire knowledge-indexing stage is already an async
   background queue — summary, smart-chunk, table-detection, and Vision-OCR page calls are all
   batch-eligible with no user-facing latency impact. Scheduled-workflow AI Extract runs qualify
   too. Batching composes with caching (cache_control works inside batches).
2. **Model tiering for Vision OCR.** Scanned-page transcription currently runs on
   `cfg.ANTHROPIC_MODEL` (opus-4-8, $5/$25). Plain OCR transcription is a Haiku/Sonnet-class task
   ($1/$5, $3/$15); the engine already trusts Haiku for table detection. Likely the single largest
   saving in Pipeline 2 for scan-heavy tenants.
3. **Application-level result cache.** Prompt cache lives ≤1 hour. A SQL-backed cache keyed on
   `(file sha256, schema hash, model)` skips repeat extractions entirely and forever — big for
   re-run workflows and test fixtures that get processed repeatedly.
4. **Revisit `DOC_SCHEMA_EXTRACTION_MAX_PAGES=100`.** The API allows up to 600 PDF pages per request
   on 1M-context models; the 100-page cap looks like a 200k-context legacy. Fewer chunks = fewer
   calls, no overlap duplication, no merge-conflict risk. (Validate extraction quality at larger
   sizes before raising.)

---

## 4. Practical constraints checklist

- **Min cacheable prefix:** 4,096 tokens on opus-4-8 (2,048 on Sonnet 5) — only document content qualifies.
- **TTL:** 5-min (1.25× write) covers multi-pass within one workflow execution comfortably; 1-hour
  (2× write) needs ≥3 reads to pay off — only worth it for known re-run-heavy flows.
- **Cache is model- and org-scoped.** Relay users share the relay org's cache (good); BYOK users
  cache on their own org. Switching `ANTHROPIC_MODEL`/`ANTHROPIC_MINI` mid-flow breaks the prefix.
- **Byte-identical means byte-identical.** Same PDF bytes, same system string, same block order.
  The relay must build the document block deterministically across calls for hits to occur.
- **Verify with usage fields.** `cache_read_input_tokens == 0` across repeated calls means a silent
  invalidator (e.g. the relay stamping a timestamp or request-id into the prompt).
- **Max 4 breakpoints per request** — one on the document block is all these flows need.

---

## 5. Suggested sequence (for review)

1. **Relay work (enabler):** confirm JSON-path pass-through; add `cache_document` opt-in to the
   document route; surface cache usage fields.
2. **Pilot B on the direct-SDK path** (BYOK/bypass) with a verification-pass scenario; measure
   `cache_read_input_tokens` to prove hits before touching the relay.
3. **Ship B behind per-node/per-call opt-in** once the relay supports it; keep single-pass uncached.
4. **Separately evaluate E1 (Batches for knowledge indexing) and E2 (Haiku/Sonnet Vision OCR)** —
   both are likely larger absolute savings than caching for Pipeline 2, with no architectural change.

**Bottom line:** as built, the engine sends nearly every byte to Claude exactly once, so prompt
caching has no free wins — but it *unlocks* the multi-pass patterns (verification, field-group
splitting, multi-node reuse) the product keeps wanting, at ~10% of the marginal document cost
instead of 100%. The relay is the one place to implement it; the Batches API and OCR model tiering
are the bigger levers for the knowledge pipeline.
