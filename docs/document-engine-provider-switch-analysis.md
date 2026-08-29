# Document Engine: OpenAI / Anthropic provider switch — feasibility analysis

**Date:** 2026-08-29
**Scope:** analysis only, no code changes.
**Question:** can we safely add a provider toggle (OpenAI *or* Anthropic) to the core
document processing engine, to cut cost on large-PDF processing?

---

## Bottom line

**Yes, and it's a smaller job than it looks.** The expensive part of large-PDF ingest is
already provider-neutral in *shape* — plain text in, JSON out. The genuinely
Anthropic-specific code (raw PDF/image bytes in a `document`/`image` block) is only 6 call
sites, and those are **not** where large-PDF money goes.

- **Phase 1 (text lane) covers ~99% of the token spend on a large text-based PDF.** One new
  adapter file, three import swaps, one config key. Zero call-site edits if done right.
- The binary/vision lane can be deferred indefinitely without blocking the savings.
- **No new credentials required** — your Azure OpenAI stack is already live and already
  serves the *other half* of document processing.
- Biggest real risk is **accuracy drift**, not plumbing: extraction prompts are Claude-tuned.
  Treat it as a measured cutover, not a flag flip.

---

## 1. Where the money actually goes

Ingest pipeline for a PDF with current settings (`DOC_USE_FAST_PDF_EXTRACTION=True`,
`DOC_DOCUMENT_LEVEL_EXTRACTION=true`, `DOC_EXTRACT_RECORDS=true`):

| Phase | LLM calls per doc | Payload | Anthropic-specific? |
|---|---|---|---|
| Document-type detection | 1 | pages 0–1 as base64 PDF | **yes** |
| Text extraction — text-based PDF | **0** | PyMuPDF, local | n/a |
| Text extraction — scanned pages | 1/page (or 1/batch) | base64 PDF | **yes** |
| Document-level field extraction | ≈ chars ÷ 480,000 | **text** | no |
| Record extraction | ≈ chars ÷ 48,000 | **text** | no |
| Schema evolve / record-set define / consolidate | 0–3 | **text** | no |
| Per-page extraction (fallback path) | 1/page | **text** | no |
| Category assignment (v3) | 1 | **text** | no |
| Page summaries (if enabled) | 1/page | **text** | no |

Worked example — a 400-page text-based PDF (~1M chars):

- text extraction: **0 LLM calls** (PyMuPDF handles it)
- document-level fields: ~3 calls @ 120K input tokens
- **record extraction: ~20+ calls** @ 12K input tokens (`DOC_RECORDS_INPUT_TOKENS=12000`)
- type detection: 1 small binary call

**Record extraction is the single biggest call generator on large documents**, and it is
pure text in / JSON rows out. Every dominant cost line above is provider-portable with no
message-format change at all.

---

## 2. The seam is unusually clean

The whole engine funnels through **one function signature**, in two transports:

- `anthropic_messages_create(client, model, max_tokens, messages, system, temperature)`
  — [anthropic_streaming_helper.py](anthropic_streaming_helper.py), imported by exactly
  three files: [LLMDocumentEngine.py:29](LLMDocumentEngine.py:29),
  [LLMDocumentSummarizer.py:13](LLMDocumentSummarizer.py:13),
  [app_doc_api.py:19](app_doc_api.py:19)
- `AnthropicProxyClient.messages_create(...)` — [CommonUtils.py:816](CommonUtils.py:816),
  the relay twin used when not on direct API

**13 LLM-calling functions** in [LLMDocumentEngine.py](LLMDocumentEngine.py), each with an
`if use_direct_api: … else: proxy` fork:

| Function | Line | Payload |
|---|---|---|
| `_extract_fields_with_llm` | [1592](LLMDocumentEngine.py:1592) | text |
| `_evolve_schema` | [1979](LLMDocumentEngine.py:1979) | text |
| `_define_record_set` | [2133](LLMDocumentEngine.py:2133) | text |
| `_extract_records` | [2321](LLMDocumentEngine.py:2321) | text |
| `_extract_with_ai` | [2692](LLMDocumentEngine.py:2692) | text |
| `_consolidate_schema_fields` | [2932](LLMDocumentEngine.py:2932) | text |
| `_excel_detect_structure` | [4411](LLMDocumentEngine.py:4411) | text |
| `_extract_single_page_with_claude` | [280](LLMDocumentEngine.py:280) | PDF block |
| `_extract_batch_with_claude` | [404](LLMDocumentEngine.py:404) | PDF block |
| `_detect_document_type` | [1266](LLMDocumentEngine.py:1266) | PDF block |
| `_call_claude_vision` | [1500](LLMDocumentEngine.py:1500) | PDF block |
| `_process_image` | [4125](LLMDocumentEngine.py:4125) | image block |
| `_process_generic_file` | [5081](LLMDocumentEngine.py:5081) | proxy `messages_with_document` |

**7 text, 6 binary.** Everything downstream — SQL storage, vector store, schema learning,
chunking, page assembly — is already provider-agnostic.

---

## 3. Precedent already exists in this repo

You are not inventing a pattern. Three things already ship:

1. **A provider switch on the same argument shape.**
   `AppUtils.azureQuickPrompt(prompt, system, provider="openai"|"anthropic")`
   ([AppUtils.py:445](AppUtils.py:445)) — same for `quickPrompt` and `azureMiniQuickPrompt`.
2. **A working Claude→OpenAI vision fallback.**
   [command_center_service/routes/upload.py:264](command_center_service/routes/upload.py:264)
   tries Claude Vision on base64 images, then falls back to OpenAI Vision via
   `image_url` + `data:` URI. That is the template for the binary sites.
3. **Half of document processing is already 100% OpenAI.**
   [DocUtils.py](DocUtils.py) and [ai_metadata_generator.py](ai_metadata_generator.py) — the
   *search / metadata* half — run entirely on `azureQuickPrompt`. Only the **ingest** half
   is Anthropic-locked.

---

## 4. Credentials: nothing new needed

`get_openai_config()` ([api_keys_config.py:246](api_keys_config.py:246)) resolves to your
live Azure OpenAI deployments. `.env` has three configured endpoints (primary, alternate,
mini) and GeneralAgent / DocUtils / the NLQ engine hit them daily. An OpenAI document lane
needs **zero new keys**.

⚠ **One caveat — metering.** This install runs the doc engine in **proxy mode**
(`byok_enabled: false`, `AI_HUB_BYPASS_DOCUMENT_PROXY = False`), so Anthropic calls go to
`ai-hub-api.azurewebsites.net/api/proxy/anthropic/messages/v2` and are billed and metered
*there*. An OpenAI lane on Azure creds bypasses that relay. You must choose:

- **(a)** add a matching `/api/proxy/openai/…` endpoint to the cloud API (keeps central
  metering, needs a change in the `aihub-api` repo), or
- **(b)** accept that OpenAI document calls are metered locally only.

Decide this **before** building, not after — it changes where the adapter sits.

---

## 5. Recommended design

### The trick: return an Anthropic-shaped response

Only **5 of the 13** call sites go through the `_response_text()` normalizer
([LLMDocumentEngine.py:1730](LLMDocumentEngine.py:1730)). The other 8 reach into
`response.content[0].text` / `response['content'][0]['text']` directly — 16 raw accesses.

If the OpenAI adapter wraps its reply in the **existing** `StreamedResponse` /
`ContentBlock` classes from [anthropic_streaming_helper.py](anthropic_streaming_helper.py),
then **zero call sites change**. This is what makes the change safe rather than sprawling.
Do not normalize the call sites first — that's a 16-site refactor you don't need.

### Phase 1 — text lane *(this is the whole win)*

1. New `document_llm.py` exposing `document_messages_create(...)` with the **identical
   signature** to `anthropic_messages_create`.
2. It reads a provider flag; on `openai` it flattens `system` + Anthropic `messages` into
   OpenAI chat messages, calls `client.chat.completions.create` through the existing
   `_create_openai_client(get_openai_config())`, and wraps the reply in `ContentBlock`.
3. **Fail-closed guard:** if any message contains a non-text content block, fall through to
   Anthropic. Phase 1 then *physically cannot* break the vision paths.
4. Swap the import in the three files. No other edits.
5. Config: `DOC_LLM_PROVIDER = anthropic|openai` in `config.py`, plus a `doc_llm_provider`
   key in `model_overrides.KEY_TO_ENV_VARS` ([model_overrides.py:54](model_overrides.py:54))
   so it is switchable from the admin Model Overrides UI.

Sits **above** the `use_direct_api` / proxy fork — otherwise you double the branch count in
all 13 functions.

### Phase 2 — binary lane *(optional, defer)*

- **Images** (`_process_image`, `_detect_document_type` on images): copy the
  `upload.py` pattern verbatim.
- **PDFs**: OpenAI chat completions has no `document` block. Either render pages to PNG with
  the PyMuPDF you already ship, or move those calls to the Responses API `input_file`.
  Meaningful work, low payoff — **defer it**. Scanned PDFs stay on Claude.
- `_process_generic_file` uses the proxy-only `messages_with_document`; leave it alone.

### Phase 3 — the stragglers *(~2 hours)*

Same import swap for [LLMDocumentSummarizer.py](LLMDocumentSummarizer.py),
`doc_search_v3/enumerate_engine._llm` ([doc_search_v3/enumerate_engine.py:64](doc_search_v3/enumerate_engine.py:64)),
and the `/analyze` route in [app_doc_api.py:956](app_doc_api.py:956).

---

## 6. What will bite you, in order

1. **Prompts are Claude-tuned.** [system_prompts.py](system_prompts.py) extraction prompts
   were written and tuned against Claude. This is the *real* risk, not the plumbing. Re-run
   the pack-13 document competency suite
   (`tests_v2/competency/test_competency_agent_knowledge_pdf.py` and siblings) before and
   after — you already have the harness.
2. **Context window.** `DOC_DOC_LEVEL_MAX_TOKENS=120000` is sized for a 200K Claude window.
   Verify the target OpenAI model's window first, or doc-level extraction silently falls
   back to the slow, expensive per-page path — the worst of both worlds.
3. **Parameter drift.** `ANTHROPIC_MAX_TOKENS=64000` is passed everywhere; OpenAI reasoning
   models want `max_completion_tokens` and reject `temperature`.
   `call_dropping_unknown_kwargs()` ([AppUtils.py:397](AppUtils.py:397)) already absorbs
   exactly this — reuse it, don't reinvent it. Also note
   `reasoning_effort_for_tools()` ([api_keys_config.py:351](api_keys_config.py:351)) and the
   terra `tools + reasoning_effort` 400 you already hit once.
4. **JSON parsing.** Every text call parses JSON out of prose, backed by
   `_remove_json_comments`, `_salvage_rows`, and fence-stripping. OpenAI's
   `response_format={"type":"json_object"}` (already plumbed in `azureMiniQuickPrompt`)
   makes this *better* — but opt in deliberately, or you get different failure modes than
   the salvage code expects.
5. **Metering** — §4 above.
6. **Two transports.** The adapter must sit above the `use_direct_api`/proxy fork.

---

## 7. Effort estimate

| Phase | Work | Estimate |
|---|---|---|
| 1 — text lane | 1 new file (~200 lines), 3 import swaps, 1 config key, 1 override key, unit tests | ~1 day + competency re-run |
| 2 — image/scanned lane | port `upload.py` pattern; PDF→PNG or Responses API | ~1 day |
| 3 — summarizer / v3 enum / doc API route | import swaps | ~2 hours |

---

## 8. Cheaper win available *today*, zero code

`data/model_overrides.json` → `anthropic_primary` is already wired to `ANTHROPIC_MODEL`
and beats `.env`. Setting it to `claude-haiku-4-5` cuts Anthropic document cost immediately,
no code change, no restart-deploy.

You measured this on 2026-08-28: large-Excel battery 12/12 adjusted, ingest 62s, structure
detection flawless. Current file has `anthropic_mini: claude-haiku-4-5` but
`anthropic_primary: claude-sonnet-5` — **the document engine reads `ANTHROPIC_MODEL`, i.e.
`anthropic_primary`, so it is still on Sonnet 5.**

**Suggested sequence:**
1. Flip `anthropic_primary` → `claude-haiku-4-5`, re-run the document competency pack.
   Instant savings, and it establishes the accuracy floor.
2. Build Phase 1 behind `DOC_LLM_PROVIDER`, defaulted to `anthropic`.
3. Run the same competency pack on `openai`. Ship the switch only if it clears the Haiku
   baseline — otherwise you've spent a day and lost nothing.
