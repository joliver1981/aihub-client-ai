# Logging Research / Response Forensics — Analysis

**Date:** 2026-08-25 · **Scope:** analysis only, no code changes.
**Question:** when an agent tells a user *"I can't do X because…"*, how do we let admins/devs research **why** — days later — without RDP + grep? The dominant client scenario is huge multi-hundred-page document ingestion followed by Q&A that comes back empty or wrong.

This doc synthesizes a five-track deep read of the codebase: (1) logging infrastructure, (2) per-stack session persistence, (3) document-pipeline observability, (4) admin-facing research surfaces, (5) the actual code paths that produce "I can't…" responses.

---

## 1. Executive diagnosis

The platform has **abundant logging and almost no researchability**. 125 files in `logs\`, hundreds of MB, `LOG_LEVEL=DEBUG` in production — yet a specific client complaint from three days ago is effectively unresearchable. Five root causes, in order of importance:

### RC1 — The cause is destroyed at the exact moment it becomes a user-facing message
The dominant failure idiom platform-wide is `except → return a well-formed empty value`. The value is structurally valid, so nothing downstream flags it, and the LLM narrates absence as fact:

- **Landscape scan** fails → `landscape = {}` (`command_center_service/graph/nodes.py:2287-2290`, `:8328-8331`) — while the system prompt at `nodes.py:2953` instructs the model *"Never say 'no agents found' or 'I couldn't retrieve'"*. An infrastructure outage becomes a confident "you don't have any agents set up yet."
- **Document type detection**: any proxy error (429/529/context-length on a huge doc) → `document_type = 'unknown'` with only a `print()` (`LLMDocumentEngine.py:1437-1439`). Ingest reports success; days later a different session says "no lease documents found."
- **Retrieval**: Chroma exception → `return []` (`LLMDocumentVectorEngine.py:977-979`, `LLMDocumentSearchEngine.py:353/629`, `doc_search_v3/acl.py:60-106`). A vector-store outage and a genuine zero-hit query produce byte-identical answers.
- **CC tool executor** collapses every exception into one of three fixed friendly strings (`nodes.py:7062-7092`); the trace records the *sanitized* string as the tool result (`result_preview`), not the cause. There is no `tool_error` event type — a failed tool is indistinguishable from a successful one in the CC trace.
- **Two systems actively delete the cause after knowing it**: the response rewriter discards the pre-rewrite text (`response_filter.py:86-89`, wired at `LLMDataEngineV2.py:1468`), and `AppUtils.py:93-94` strips the proxy `details` field (where "prompt is too long: N tokens > 200000" lives) before re-raising — callers see only `"Proxy returned status code 400"`. The full body *is* logged at `CommonUtils.py:876-882`, but on an uncorrelated line in the doc-service log.
- The error map at `config.py:474-481` tells users *"Our team has been notified"* — nothing notifies anyone (`error_handling_system.py:59-75` just logs).
- The one genuinely good crash substrate — `telemetry.capture_exception` (`telemetry.py:345-391`: full traceback, breadcrumbs, context, shipped to the cloud) — is wired **only in `app.py`** (~18 sites; best one is the `llm_deadline` site at `app.py:5537`). It has **zero occurrences** in `command_center_service`, `agent_service`, `builder_service`, `workflow_execution.py`, `DocUtils.py`, or the `LLMDocument*` engines — the exact inverse of where the "I can't…" strings are produced. And when telemetry is unconfigured or crash-reporting consent is declined, it bare-returns with **no local fallback write**, so those sites degrade to the adjacent `logger.error(str(e))`. (`track_error`/`track_errors` are dead code — zero call sites.)

### RC2 — No identifier ever bridges the user's report and the evidence
**No user-facing message anywhere in the platform carries an error/reference id.** Trace ids exist (`trace_store.TraceMeta.trace_id`, workflow `execution_id`, proxy `user_request_id`) but never surface to users, never appear in log lines (no logging.Filter injects them — `request_tracking.py` sets Flask `g` but only ever emits one anchor line), and never cross service boundaries (zero hits for any `X-Trace-Id`/`X-Correlation` header repo-wide). The proxy request-id is additionally clobbered under concurrency — verified end-to-end: `get_document_processor()` is a process-wide singleton (`app_doc_api.py:156-167`), every ingest calls `_set_tracking_params('document_processor')` on it (`LLMDocumentEngine.py:913`), and that assigns mutable instance state (`CommonUtils.py:734-739`). The gate admits up to `SERVER_THREADS−2` concurrent ingests, last writer wins, so every subsequent LLM call from *any* in-flight document is attributed to whichever started most recently. This matters doubly because that id becomes `PlatformUsageLog.RequestId` — the **only** correlation key in the platform that reaches a datastore — and in the doc lane it is silently wrong exactly when load is high. Result: matching a complaint to evidence is done by timestamp, across 16-thread-interleaved log files, days later, post-rotation.

### RC3 — What is persisted per turn varies from gold to nothing, by stack

| Fact | Command Center | The Agent (5111) | Classic agents | Workflow exec |
|---|---|---|---|---|
| Tool args / results | trace, **round 1 only** (`nodes.py:7055-7101`; rounds ≥2 log-only) | SDK transcript, **full + verbatim incl. `is_error`** | lost (global `agent_log.txt`, uncorrelated) | `StepExecutions.output_data` |
| Tool failure detail | service log only (rotates away at ~50 MB) | SDK transcript | service log only | `error_message` + `ExecutionLogs` ✔ |
| LLM calls | trace `llm_call` (rich but truncated; model id recorded only as `"mini"`/`"full"`) | SDK transcript (all, real model id, full token/cache usage, Anthropic requestId) | never | prompt only (`ExecutionLogs.details.prompt` — untruncated ✔) |
| System prompt | inside `llm_call.messages` ✔ | **not persisted** (source + settings.json only) | not persisted | not persisted |
| Token usage / cost | never | full usage; `total_cost_usd` streamed but not stored | never | never |
| Retention | **unbounded, no pruning** | **SDK deletion clock (~30d default, unconfigured by us)** | 90d policy, **no scheduled caller** | unbounded |

Key traps inside that table:
- **The Agent's SDK transcript is the best forensic record in the platform** — and it sits on a rolling deletion clock we don't configure (`data/agent/claude/.last-cleanup` shows cleanup runs; `cleanupPeriodDays` appears nowhere in the repo). 639 transcripts vs 391 ledger rows: headless/email/scheduled sessions never get a `chat_sessions` row, so ~250 transcripts are unreachable via any API. `replay()` deliberately drops tool results and reduces tool_use to bare names (`chat_history.py:189-253`).
- CC's **scheduled runs are traceless** (`routes/scheduled.py:82-92` builds graph input without a `trace` key) and produce no session file — only a 2,000-char summary.
- Classic: `/chat/general_system` (the route the **workflow AI Action node** calls) and `/api/agents/<id>/chat` persist *nothing*. The latter has at least **four producers** — CC delegation, data-collection completions (`data_collection_agent/actions/agent_action.py:67`), Builder Agent capability dispatch (`builder_agent/actions/platform_actions.py:412-419`), and direct API clients — and none leaves a conversation record; the only trace is the global, id-less `agent_log.txt`, so attributing such a turn to its originator is impossible after the fact. The `Message` schema has `tool_calls`/`tokens` fields that are never populated (`app.py:5229`, `:5426`). The classic 90-day retention policy is confirmed inert: `prune_old_conversations` has exactly one caller — a user-triggered POST `/api/history/prune` — so `data/history/` grows unbounded while *looking* governed by a policy.
- Workflow `continueOnError` writes `StepExecutions.status='Completed'` for a failed node (`workflow_execution.py:6254-6290`); truth is only in `ExecutionLogs`.
- A failed CC turn produces an **orphan user message** — the assistant save at `routes/chat.py:808` is never reached from the exception handler.

### RC4 — Admins have no supported way to look at any of it
- **No cross-user conversation browser exists.** Main-app history API is hard-scoped to `current_user.id` (`local_history_routes.py:147`); the role-3 bypass on the detail route requires already knowing the conversation id. The Agent's history API returns **404 to admins** (`owns_session`, no role bypass). The only thing that works — CC's "admin sees all sessions" (`services/__init__.py:326-329`) plus the trace inspector (`static/inspect.html`) — is undocumented, has **no auth at all on `/api/inspect/*`** (`routes/inspect.py`), and its Ops Room front-end is disabled (`CC_UI=classic`).
- **Gate denials leave no record.** `role_decorators.py`, `tier_allows_feature`, `CC_ALLOW_ALL_USERS`-style doors, The Agent's 403 — none logs or stores anything. If the user was refused by a *gate* rather than an *error*, there is literally nothing to look up. (Exceptions: The Agent turn cap logs + counts; auth middleware logs unauthenticated hits.)
- **No login/activity audit** (Tier page activity counters are hardcoded zeros, `admin_tier_usage.py:279-284`); the per-user turn counter is write-only (`usage_store.turns_today()` has zero callers); `work_item_events` — the designed lifecycle audit — has no reader; `cc_ToolAudit` table exists with zero writers.
- The `/system_logs` viewer reads the `app_log` DB table — which has **no user_id/request_id columns** (`AppUtils.py:284`), is gated on a per-record `TenantSettings` SELECT, and is empty whenever `database_logging_enabled` was off at incident time.
- `PlatformUsageLog` (cloud DB, one row per LLM call, includes `RequestBody` and `ErrorMessage`!) is only ever consumed as a monthly `COUNT(DISTINCT RequestId)` — the richest LLM forensic source has no reader.

### RC5 — The log substrate itself leaks evidence
- **No NSSM stdout capture**: `AppStdout`/`AppStderr` appear zero times in all three installers. Every `print()` in an installed deployment is discarded — including `DocUtils.py`'s 228 prints (vs 4 logger calls), i.e. essentially the entire repository-search diagnostic trail.
- **Rotation only fires at process start** (`rotate_logs_on_startup`, `CommonUtils.py:1356`): long-lived services blow past 10 MB (a 122 MB backup exists on disk). Only 5 of 15 services use a real `RotatingFileHandler`. `AIHubBuilderData` has no file log at all. CC logs outside the central `logs\` dir. `log_setup.py` — the one clean shared helper — is dead code with zero importers.
- **The telemetry crash pipeline is shadowed**: duplicate `@app.errorhandler(Exception)` registrations (`app.py:1544` with `capture_exception` vs `app.py:6627` plain log) — Flask keeps the last one, so unhandled exceptions never reach Sentry/cloud crash reporting. If crash reports look sparse, this is why.
- Three logging idioms, two correlation implementations (one dead), no retention policy anywhere.
- `APP_ERRORS_LOG.txt` in the repo root is written by **nothing in this repo** (two "Sentry telemetry relay" lines from 2026-08-08, unattributable) — despite the name, it has never captured an app error.

---

## 2. The huge-document scenario specifically

This is the client-facing pain, and it is the worst-instrumented lane. Walking the exact complaint — *"we ingested the 400-page contract and the agent says it can't find the payment terms"*:

**At ingest time:**
- `Documents` has **no status/error column**; `DocumentPages` has **no per-page status** — a page that failed OCR and a genuinely blank page are the same `full_text=''` row. "Partially ingested" is unrepresentable (`LLMDocumentEngine.py:831-876`).
- The one per-file status table (`DocumentJobFileDetails`, with `Status`/`ErrorMessage`/`ProcessingDurationSeconds`) is only written when `execution_id > 0` (`LLMDocumentEngine.py:949`) — and **no interactive caller ever posts execution_id**: agent import, knowledge upload, workflow Process-Document all omit it. The lanes clients actually use for huge docs produce **zero durable ingest records**. (Bonus trap: a caller that did post it would hit a `str > int` TypeError — `app_doc_api.py:476` never casts.) Compounding this, `DocumentJobFileDetails` and `DocumentJobExecutions` — the only two tables in the stack carrying an ingest `Status`/`ErrorMessage` — have **no DDL anywhere in the repo** (migrations 001–019 skip both): they exist only where an earlier install created them out-of-band, so their shape on a given tenant DB is unverifiable from source and a fresh install never creates them.
- **One page's LLM timeout discards the whole document's extraction and restarts all 400 pages via AI-only** (`fast_pdf_extractor.py:807-829` has no per-page try/except; caught at `LLMDocumentEngine.py:4106` as a single generic warning with no page number).
- `Documents.page_count` = pages *stored*, never pages *in the source file* — a 400-page PDF that yielded 250 pages is indistinguishable from a 250-page PDF (nothing compares against the PDF's own page count).
- The scheduled-lane roll-up `DocumentsSucceeded` counts engine-internal failures as successes (post-2026-08-14 "honest failure" change, `LLMDocumentEngine.py:3633-3642` vs `app_doc_job_q.py:285-292`).
- Per-page field-extraction failures land as a `DocumentFields` row literally named `extraction_error` — which **nothing reads** and which pollutes the learned schema.
- The good news: `BLANK_PAGE_STORED` warning (`fast_pdf_extractor.py:414-427`) names failed page numbers (log-only), and `[ingest-timing]`/`[sql-store]` phase timers are genuinely good — undermined by the `*` marker firing on every ingest (`summary()` called before `end()`, `:1240-1241`) and `[sql-store]` being DEBUG-invisible in prod.

**At query time:**
- **A vector search leaves no durable record in production.** Query, filters, ACL scope, scores, hit count — all `print()`-only (`app_vector_api.py:303-406`), and stdout is discarded (RC5).
- The repository-search engine builds an excellent retrieval trail (`search_attempts[]`, `fallback_attempts[]`, per-tier hit counts) — and it is **commented out of the response** (`DocUtils.py:4442-4448`) and dumped to `print()`.
- "Zero chunks retrieved" vs "chunks retrieved but the LLM ignored them" is distinguishable only in the agent-knowledge lane, via one `logging.info` line. The rich `skr_trace` is **off in production** (`KNOWLEDGE_ENABLE_TRACE` default false; `dist\.env` doesn't set it).
- The ACL fail-closed path makes a DB outage produce the same *"You do not have access to any document categories"* message as a genuine no-grant (`acl.py:60-74` deny-all on exception; `managed_category_ids` swallows with **no log**; and an identity-less caller silently falls **open**, `document_search_wrapper.py:231-238`).
- The v3 counting lane produces a beautiful ledger (in-scope/read/failed/not-reached) — **only inside the response text handed to the LLM**; never logged or stored (`enumerate_engine.py:409-434`). Same for the 400K-char brute-force truncation notice (`agent_knowledge_integration.py:148-189`): "the answer was on page 300 but we stopped sending at page 180" is invisible to an admin.

So today, the honest answer to "why did it say it can't find X" is: *the evidence was either never written, written to discarded stdout, written without any key to find it, or already rotated away.*

---

## 3. What already works — generalize these, don't invent

The codebase contains five proven in-house patterns that are exactly the right shape:

1. **`DocumentRecords.__manifest`** (`migrations/017`, `LLMDocumentEngine.py:2581-2595`) — per-run status row distinguishing *not run* / *ran-complete* / *ran-partial* with counts; surfaced in a coverage UI and in every answer. The model for ingest status.
2. **CC `TraceStore`** (`trace_store.py`) — append-only JSONL, one file per user message, every event stamped `trace_id/user_id/session_id`. The model for turn tracing; needs completion (all rounds, `tool_error` events, real model ids), auth, and retention.
3. **`inflight_gate`** (`inflight_gate.py`) — honest LLM-relayable busy text + logged rejections + live `/health` snapshot. The model for "truthful degradation."
4. **`IntegrationExecutionLog`** (`integration_manager.py:1173-1196`) — full request/response/status/error/duration per external call, with redaction. The model for cross-service call auditing.
5. **Automation run directories** (`automations/.../runs/<run_id>/` with `events.jsonl`, frozen code, checkpoints, egress log) — the most complete artifact on the box. The model for run-level forensics.

Plus the Claude SDK transcript for The Agent, which already *is* the full record — it needs ownership (retention config, admin access, complete replay), not rebuilding.

---

## 4. Recommendations (prioritized)

### Tier 0 — Stop destroying evidence (config-level / tiny, days)
1. **Set NSSM `AppStdout`/`AppStderr` in the v5 installer** (+ `AppRotateFiles`). Instantly rescues the doc-pipeline's print-based diagnostics on client boxes. (Installer-fix rule: land in the highest .iss.)
2. **Configure SDK transcript retention** for The Agent (`cleanupPeriodDays` via CLAUDE_CONFIG_DIR settings) so the platform — not an SDK default — decides how long the best forensic record lives.
3. **Fix the shadowed `errorhandler(Exception)`** so crashes reach telemetry again; fix the `[ingest-timing]` `*` marker (call `end()` before `summary()`).
4. **Turn on the cheap traces in prod**: `KNOWLEDGE_ENABLE_TRACE` in `dist\.env`; drop global `LOG_LEVEL=DEBUG` to INFO with per-service overrides (volume is why rotation drowns).
5. **Log gate denials** — one `logger.warning` with user/route/gate in `role_decorators.py` and the tier gates. One-line changes; converts "nothing to look up" into "grep-able."

### Tier 1 — The reference-id chokepoint (the single highest-leverage build)
**Every user-facing failure/refusal message gets a short reference id, and a durable `IncidentLog` row keyed by it.** Client says *"it told me it couldn't, ref `K7QX-31`"* → admin pastes the ref into a lookup and sees: user, session, turn, tool, full exception + traceback, downstream HTTP body, timestamps.

This works because the "I can't…" messages already converge on a handful of chokepoints:
- CC tool executor `nodes.py:7062-7092` (also: add the missing `tool_error` trace event and trace rounds ≥2)
- The Agent's `_text(..., is_error=True)` helper (`platform_tools.py:67`) — 200+ call sites, one helper
- `document_search_wrapper` terminal messages (no-access / no-results / busy)
- The gates (role/tier/allow-all-users doors)
- The generic 500 handlers and `azureMiniQuickPrompt` error-paraphrase path (`app_agent_api.py:583-602`)

Rules at the chokepoint: mint id → persist **pre-sanitization** cause (exception + traceback + context ids + downstream body) → append `(ref: …)` to the friendly text. The `response_filter` rewriter and `AppUtils` proxy-detail stripper must store the original before transforming. This is the mini-LLM-era version of "guard the chokepoint every caller converges on" — the lesson already learned twice in pack-09.

### Tier 2 — Correlation + honest empty-values (medium)
6. **One correlation id per turn, propagated as a header** (`X-AIHub-Trace`) across main app → doc/vector/executor/agent services, injected into every log record via a `logging.Filter` (the dead `request_tracking_thread.py` contextvars implementation is the right base). Include it in the proxy tracking params — and fix the shared-singleton clobbering by passing per-call ids instead of mutating the client.
7. **Typed result envelopes instead of empty values.** Retrieval/scan functions return `{status: ok|empty|error|denied, detail…}` rather than `[]`/`{}`/`'unknown'`. The tool layer renders `error` differently from `empty` to the model ("search infrastructure failed — do not conclude the data is absent"), and the trace records which one happened. Kill the `nodes.py:2953` "never say I couldn't retrieve" instruction for the error case. This is the retrieval-side completion of the silent-success program (the existing 2,678-line regression oracle covers mutations only).

### Tier 3 — The Session Inspector (the admin-facing deliverable)
8. **A supported, authed, cross-stack admin screen: find user → sessions across CC + The Agent + classic → open turn → see tools/args/results/errors → jump to IncidentLog by ref.** ~70% exists: CC's session store + trace inspector (needs auth — `/api/inspect/*` currently has none — an admin entry point, and retention); The Agent needs an admin read API over the SDK transcripts (full replay incl. tool results, with the already-identified secret-redaction applied *at read time*); classic needs an admin override on `local_history_routes`. Fold in the readerless data that already exists: `work_item_events`, `agent_usage`, `portal_watches`, `PlatformUsageLog` detail rows.
9. **A denial/incident ledger view** on the same screen (from Tier 0.5 + Tier 1), plus a basic login/last-seen audit so "was the user even on yesterday?" is answerable.

### Tier 4 — Document pipeline status (the client-pain center)
10. **An `IngestRuns` ledger written unconditionally by `process_document`** (drop the `execution_id > 0` gate or generalize past it): ingest_id, doc_id, filename, caller lane, user, phase timings, source-page-count vs stored-page-count, per-page failure list, terminal status (`complete|partial|failed`), error. This is `__manifest` generalized to the whole ingest. Add `Documents.status` + expected-vs-stored page counts so *partial* is representable.
11. **Per-page failure isolation** in the hybrid extractor (try/except per page; record page-level failures instead of restarting 400 pages), and route `extraction_error` rows somewhere visible instead of into the schema learner.
12. **Persist the retrieval record**: un-comment `search_execution` (`DocUtils.py:4442`) into the response *and* write one row per search (query, lane, ACL scope size, hit count, top scores, zero-vs-error flag, trace id). Persist the counting-lane ledger and the 400K truncation events. Then "no results because outage / because ACL / because truly absent / because truncated at page 180" becomes a lookup, not an archaeology dig.
13. **A per-document "what happened" page** (ingest history + blank-page list + extraction status + which searches have hit it), fed by 10-12. The `/document_schemas` coverage view shows the pattern.

### Tier 5 — Substrate hygiene (background)
14. Adopt one logging helper (resurrect `log_setup.py`: `RotatingFileHandler` + correlation filter + redaction filter, which today exists only in the main app) across all 15 services; give `AIHubBuilderData` a file log; move CC's log into `logs\`.
15. Retention jobs: prune CC traces/sessions, schedule the existing-but-uncalled `prune_old_conversations`, cap `nlq_agentic_trace.jsonl` (and add user_id/timestamp to it), bound `ExecutionLogs`.
16. Local per-LLM-call metering (model, tokens, latency, request id, caller module) — currently only the cloud has this and nothing reads it.

---

## 5. Security findings surfaced in passing (flag, decide separately)

Not the question asked, but load-bearing for any inspector built on these stores:
- `GET/POST /api/inspect/*` (CC trace API) has **no auth**; user/session ids are plain query params (`routes/inspect.py`).
- CC session files persist **unmasked** assistant text — credential masking runs only on the SSE payload (`routes/chat.py:808` vs `:813-852`).
- The Agent SDK transcripts hold portal **passwords/TOTP secrets in plaintext** — redaction is UI-seam-only by design (`brain.py:154-162`).
- `/api/workflow/executions/<id>` + `/steps` + `/variables` + `/logs` have **no role decorator** — any logged-in role-1 user can read any execution's variables (`app.py:10686-10881`).
- `QuickJobLog` insert is built by string replacement, not parameters (`data_config.py:101-108`) — SQL-injection-shaped on a log path.
- ACL fail-**open** for identity-less callers in a module documented fail-closed (`document_search_wrapper.py:231-238`).

---

## 6. The target workflow (north star)

> Client: *"Yesterday the agent told Maria it couldn't find the payment terms in the Hendricks contract."*
>
> Admin opens **Session Inspector** → searches Maria + yesterday → opens the turn → sees `search_documents` returned `status: empty` with ACL scope 3/40 types, 0 hits, and the ingest ledger shows the Hendricks PDF at `partial: 251/400 pages, 149 pages failed vision extraction (proxy 529), ref K7QX-31` → answers the client in five minutes, with the fix ("re-run ingest; the relay was overloaded Tuesday") instead of "I'll check the logs."

Every recommendation above removes one obstacle on that path. Tier 0 + Tier 1 alone convert the current situation ("evidence destroyed or unfindable") into "evidence exists and is keyed to the complaint" — the Session Inspector and ingest ledger then make it self-service.
