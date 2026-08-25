# Chat Uploads: Admit Everything or Deny — No Silent Truncation

**Status: PLAN (not built). 2026-08-25.**
Scope: files a user uploads **directly to an agent in chat** — General Agent chat
(Agent Files), Command Center chat attachments, The Agent chat attachments.
Explicitly OUT of scope: backend document processing (Doc API ingest for the
searchable repository, schema extraction, vector indexing) — those pipelines
keep their own rules.

## The policy

> A file a user hands to an agent in chat is either **fully usable** by that
> agent, or it is **rejected at upload with a clear message**. Nothing in
> between. No tool, cache, or prompt-injection step may silently cut content
> after the file was accepted. When a limit is hit, the user is told the
> numbers ("this file is ~410K tokens; the chat-upload limit is 300K") and
> given the alternative (import to the document repository / use the tabular
> query tools / start a fresh conversation).

Two kinds of "fully usable" are allowed, and both are honest:

1. **Full-content lane** — the whole extracted content can reach the model
   (The Agent's `read_file` is the existing reference implementation: whole
   file or refusal, never a slice).
2. **Structured-query lane** — the model never needs the whole file in context
   because tools compute over the full file on disk (the tabular lane shipped
   2026-08-25: `analyze/read/aggregate_excel_data` for General Agent,
   `run_python` for CC, `query_tabular_file` for The Agent). This lane is
   honest **because the computation sees everything**, and the model is told
   the preview is a preview.

A third state — "we stored 80 of your 100 pages" or "the tool returns the
first 500 chars of each page" — is the bug class this plan eliminates.

## Why limits can be high: the context budget

Current model classes give us ~200K tokens (Sonnet/Opus today) and 1M+ tokens
(current large-context tiers). Sizing rule used throughout: **tokens ≈ chars / 4**.

| Real document | Typical extracted size | Tokens |
|---|---|---|
| 25-page contract | ~60–100K chars | ~15–25K |
| 50-page report | ~120–200K chars | ~30–50K |
| 180-page PDF | ~450–900K chars | ~110–225K |
| 1,000-row × 200-col CSV | ~2.7M chars raw | ~670K (→ structured lane) |

So James's example — a user uploads a 25-page, then a 50-page, then a
180-page document to one conversation — totals roughly **160–300K tokens** of
content. That FITS a 1M-context model with room for conversation, and mostly
fits 200K-class models one-document-at-a-time. The correct posture is
**generous admission with honest refusal at the true ceiling**, not
prophylactic 500-char slices designed in the 8K-token era.

### Proposed config (all new, one place, overridable per install)

| Setting | Proposed default | Meaning |
|---|---|---|
| `CHAT_UPLOAD_MAX_TOKENS_PER_FILE` | `300_000` | Admission ceiling per file measured AFTER extraction (chars/4). Above it: **deny** with numbers, offering repository import (which stays unlimited) and, for tabular files, the structured lane (which admits regardless of this cap because content never enters context wholesale). |
| `CHAT_CONVERSATION_ATTACHMENT_BUDGET_TOKENS` | `600_000` | Sum of admitted attachment tokens per conversation. The 4th big file that would blow the budget is denied with "start a new conversation or import to the repository", not silently degraded. |
| `CHAT_UPLOAD_TOKENS_SOFT_WARN` | `150_000` | Above this, accept but tell the user "large file — answers may be slower / consider the repository for many-file work". |
| (existing) `DOC_MAX_UPLOAD_SIZE_MB` | keep | Raw-bytes gate stays as the first-line DoS guard. |

Installs on 200K-class models set the two ceilings to ~120K/200K; the point is
the mechanism, not the exact numbers.

## Current state and dispositions, per surface

### 1. General Agent chat (worst offender)

Upload → Doc API → pages in SQL. The agent reads pages back through tools.

| # | Truncation today | Where | Disposition |
|---|---|---|---|
| 1 | Page text sliced to **500 chars** (1,000 on dev) in every page tool (`get_document_pages`, `get_document_page_by_number`, next/prev) | `DOC_PAGE_TEXT_LIMIT_IN_RESULTS`, config.py:745; DocUtils.py ×6 sites | **REMOVE for these tools.** Replace with a per-call token budget (`AGENT_PAGE_READ_BUDGET_TOKENS`, default ~60K): serve WHOLE pages in order until the budget is reached, then STOP AT A PAGE BOUNDARY with an explicit continuation line — "Returned pages 1–14 of 41 in full. Call again with start_page=15." Never a partial page. (The 500-char constant stays only for the search-results *snippet* path it was actually designed for, where a labeled snippet is the contract.) |
| 2 | Smart-retrieval parent page cut at 12K chars | `KNOWLEDGE_PARENT_PAGE_CHAR_CAP`, agent_knowledge_integration.py:1981 | Retrieval snippets are legitimately partial — but must SAY so. Append `[page N shown in part — X of Y chars; get_document_page_by_number(N) returns it in full]`. Requires #1 so the escape hatch is real. |
| 3 | Whole-knowledge reader caps 50K/page, 400K total with an omission notice | `_format_knowledge_response`, agent_knowledge_integration.py:150 | Convert to page-boundary budget + continuation (same pattern as #1). The existing omission notice is already honest; make it enumerate exactly which pages were omitted. |
| 4 | **Admission**: none today — any accepted file may be silently unusable later | app.py `/add/agent_knowledge` | **ADD the admission gate.** After Doc API processing returns `total_chars`/`page_count`: if tokens > per-file ceiling and the file is not tabular → delete the just-created knowledge doc and return the deny message with real numbers. Tabular files bypass (structured lane). This is the "allow 100 pages or deny — never 80" enforcement point. |

### 2. Command Center chat

Upload → disk; extracted text injected into the user message; `run_python`
sees the full raw file.

| # | Truncation today | Where | Disposition |
|---|---|---|---|
| 1 | Extraction capped at **50K chars/file** before injection | upload.py:278 | Raise to the admission ceiling (inject up to `CHAT_UPLOAD_MAX_TOKENS_PER_FILE`); a file that extracts beyond it is **denied at upload** with numbers — except tabular files, which are admitted with the (now labeled) preview + `run_python` full-file lane. |
| 2 | **Truncated cache is permanent** — `{file_id}_analysis.txt` stores the cut text; every later turn re-serves it | upload.py:284, :251 | **Cache FULL extraction; budget at injection time, not extraction time.** The cache must never be lossy — it is the source of truth for later turns. |
| 3 | CSV/XLSX preview = first 500 rows | attachment_text_extractor.py | KEEP (it is a preview by design) — now carries exact totals + "PREVIEW … run code against the original file" (shipped 2026-08-25, commit c4f0407). |
| 4 | `run_python` can be disabled for non-Developers → tabular lane vanishes silently | nodes.py `_code_interpreter_allowed` | When disabled and a tabular file is attached, the injected block must say "computation tools are disabled for your role — answers about this file are limited to the preview shown". Honesty beats capability here. |
| 5 | History budget 600K chars trims old turns silently | chat.py:507 | Acceptable (conversation memory, not file content) — but attachments count toward the conversation budget at ADMISSION (deny the upload that cannot fit), so trimming never eats an attachment the user just paid for. |
| 6 | Post-restart ownership loss hides files (`_file_store` rebuilt with `user_id=None`) | upload.py:33-75 | Not a truncation but violates "fully usable": persist the registry (sidecar JSON per file) so admitted files stay usable across restarts. |

### 3. The Agent chat

Already closest to the policy: whole-file reads with honest refusals.

| # | Truncation today | Where | Disposition |
|---|---|---|---|
| 1 | `read_file` — whole file or refuse at 25 MB | document_tools.py | KEEP — this is the reference pattern. Consider raising the default (`AGENT_READ_FILE_MAX_MB`) with the same message. |
| 2 | **Excel 5,000 rows/sheet** in the shared engine's markdown table (disclosed) | `EXCEL_MAX_ROWS_PER_SHEET`, config.py:391, used by `read_file`'s doc path | The disclosure is honest but the DATA is cut. Now that `query_tabular_file` exists (c4f0407), make `read_file` on a tabular file append: "table shows first 5,000 rows of N — query_tabular_file computes over ALL rows". The compute lane, not the text dump, is the full-fidelity path. |
| 3 | No admission/conversation budget — a 180-page PDF's full text lands in the transcript and **re-ships every turn** | brain/SDK transcript | ADD the conversation attachment budget: at upload, estimate tokens (extract lazily for PDFs: pages × avg-chars sample, or accept-then-measure on first read); when a read_file result would blow the conversation budget, refuse with "this file is ~X tokens; this conversation has ~Y left — start a fresh conversation for it or import_documents + search_documents". Numbers, alternatives, no slice. |
| 4 | Email-lane attachment reader clamps at 100K/500K chars (disclosed) | email_tools.py | Out of scope here (not a chat upload) — flag for a later pass under the same policy. |

## The 25 → 50 → 180-page walk-through (target behavior)

Same conversation, any of the three surfaces, defaults above, 1M-class model:

1. **25-page upload** (~20K tokens): admitted silently. Fully readable/injected.
2. **50-page upload** (~40K tokens): admitted silently. Running total ~60K.
3. **180-page upload** (~180K tokens): over the soft-warn line → admitted with
   "large file" notice. Running total ~240K of the 600K budget. Every page of
   all three documents is reachable in full.
4. A hypothetical **4th upload of ~400K tokens**: per-file ceiling (300K)
   exceeded → **denied at upload**: "This file extracts to ~400K tokens;
   the chat-upload limit is 300K. Import it to the document repository
   (searchable, no size limit) or, if it's a spreadsheet/CSV, I can query it
   with the data tools without loading it into chat." Nothing was stored as
   chat knowledge; nothing pretends to work later.

On a 200K-class install (lower ceilings), step 3 is where the deny fires —
same mechanism, same honest message, smaller numbers.

## Build order (when approved)

| Phase | Content | Size |
|---|---|---|
| **P1** | General Agent: admission gate in `/add/agent_knowledge` + page-tool budget/continuation (kills the 500-char slice). This is where the customer pain lives. | ~1.5 days incl. tests |
| **P2** | CC: full-fidelity cache + injection-time budgeting + deny message + role-disabled honesty line (+ registry persistence fix). | ~1 day |
| **P3** | The Agent: conversation attachment budget + Excel-row-cap cross-reference to `query_tabular_file`. | ~0.5 day |
| **P4** | Config plumbing (the 3 new settings + admin UI rows), pack-level regression: a 25/50/180-page fixture trio run against all three surfaces asserting "full content or explicit deny — zero silent-truncation markers". | ~1 day |

Each phase is independently shippable; P1 alone would have prevented the
FedEx-invoice report even without the tabular lane.

## Non-goals / kept truncations (all labeled, none silent)

- Search-result snippets and passages (they are samples by contract and say so).
- Log/preview/error-message clamps (`[:200]` on an HTTP error, UI transcript
  renders) — display concerns, not model-facing content.
- The CSV/XLSX 500-row *preview* in CC — kept, because the full file is one
  `run_python` away and the preview now states its own limits and the true totals.
- Backend Doc API ingest (page storage, vectors, schema extraction) — separate
  pipeline, separate rules, explicitly out of scope per James 2026-08-25.
