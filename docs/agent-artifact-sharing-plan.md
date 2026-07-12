# Cross-Agent Artifact & Data Sharing — Plan

**Status:** "this-version" cut line BUILT 2026-07-12 (P0 + P1 + P2-core + P3 + P4-lite `read_artifact`) — commits 8efed34, 837426c, 95e7230, aa2c1ac, cdf361d. 83 unit tests green. Ships behind existing thresholds/flags; needs a main-app + CC-service restart to go live. Phase 5 (surfacing hardening: robust chip passthrough, download-token signing, split-deploy) deferred. See §Build status below.
**Goal:** let the Command Center (CC) agent orchestrate data/artifacts produced by data agents and general agents — so the user talks only to CC and CC gathers files/datasets on their behalf.

Related: [nlq-engine architecture review], [cc-silent-success-remediation-plan.md](cc-silent-success-remediation-plan.md), [data-provenance.md](data-provenance.md).

---

## 1. Why this is a CC *completeness* gap (not v-next)

The single-interface premise is that the user talks to CC and CC delegates to the right agent. Today CC can **direct** another agent to produce a file or a dataset and **report that it did** — but it cannot **fetch, read, or hand that file to the user**. That is the same "silent success" failure mode documented in the remediation plan, applied to the whole orchestration promise:

- General-agent delegation is **text-only** — no file/handle comes back (`app.py` agent-chat route returns `{response, chat_history}`, `use_smart_render=False`; `command_center/orchestration/delegator.py:116` result carries only `text/status/raw/rich_content/query/answer_type`, no `files`).
- CC has **no `read_artifact`** and `run_python` is walled off from artifacts (its workdir is seeded only from the chat-upload store), so CC can't read a file's contents even when it exists.
- Files live in ~10 stores across 3 services with **no shared handle**, so the produced file sits somewhere CC can't reach.

CC is complete for what it does with its **own** tools (`run_python`, `export_data`, portal fetch all produce and serve files fine). The gap is precisely the **hand-off moment** when it delegates. Recommendation: land the core of this in the current version.

## 2. Current state (verified in code)

- **Delegation flattens everything to text.** Three receive paths: `converse` stringifies (a delegated chart becomes an inert `[CHART_IMAGE:…]` text marker that nothing parses), `decompose` **drops** rich content entirely, only single-agent `gather_data` maps a chart/table to inline blocks. None carry a file.
- **Big data collapses at the CC boundary.** `/data_explorer/internal/query` returns `"response": str(answer)` — pandas' ~10-row repr for a 100k-row frame. The only structured data CC gets is a table block **capped at 1000 rows by a config-name bug**: `config.py:366` defines `SMART_RENDER_HYBRID_MAX_TABLE_DISPLAY_ROWS = 100000`, but `SmartContentRenderer_hybrid.py:276` reads the bare `getattr(cfg, 'MAX_TABLE_DISPLAY_ROWS', 1000)` off the config module (name doesn't exist there) → silent fallback to **1000**. **CONFIRMED.**
- **No LIMIT is ever injected;** the full result is materialized in RAM (`pd.read_sql_query`, `cursor.fetchall()`). A "list everything" question can OOM the box.
- **Full-fidelity CSV+download exists only in the browser Data-Explorer path** (`app.py:1305`), unreachable via CC.
- **~10 siloed stores, no registry, no shared handle.** CC keys on session, main chat on conversation, agent_files on (agent,user), knowledge on (agent,user,tenant); tenant is absent from `chat_files`/`agent_files`/`tmp`/`exports`.
- **Two unauthenticated endpoints (security).** `app.py:8141` `/document/serve/<path>` and `:8175` `/document/serve?path=` `send_file` an **arbitrary local/UNC path from the URL** with no auth (arbitrary file read). `app.py:2216` `/download/<file_id>` is decorator-free. **CONFIRMED.**

## 2.1 New: the optional agentic NLQ engine (NLQ V3) — and why it *helps*

As of commit `408da3a` (NLQ V3 P6) there are now **two** NLQ engines behind a factory (`nlq_engine_factory.create_nlq_engine`): **legacy** (default) and **agentic** (`nlq_agentic/`, ships dormant, per-agent allowlist `NLQ_AGENTIC_AGENT_IDS`, circuit-breaker + fallback-to-legacy + shadow mode). This *simplifies* the artifact plan:

- **One engine-agnostic injection seam.** CC's delegation path `/data_explorer/internal/query` constructs its engine via the factory (`data_explorer.py:602`) and **both engines return through the same contract** (`answer`, `answer_type`, `rich_content`) into one response-builder (`data_explorer.py:628–690`; the agentic engine mirrors the legacy shape by design — `nlq_agentic/contract.py`). So Phase 1's "big result → CSV artifact + handle" hooks at that **shared builder** and covers both engines with one change.
- **Phase 0 config-bug fix is shared.** The agentic engine reuses `SmartContentRenderer_hybrid` (`nlq_agentic/engine.py:34`, `:358`), so the 1000-row `MAX_TABLE_DISPLAY_ROWS` bug bites **both** engines — one fix corrects both (still needed: legacy remains default).
- **OOM / no-LIMIT risk is now legacy-only.** The agentic engine injects a **10,000-row cap** (`NLQ_AGENTIC_SQL_ROW_CAP`, `engine.py:290`) through the `sql_gate`, so it never materializes a 100k-row frame. Phase 0's safety cap is therefore a **legacy-parity** fix.
- **New tension — the 10k cap vs. a full-export artifact.** On the agentic path a result is already truncated to 10k rows, so a downloadable artifact of the *full* result needs either (a) a dedicated **`export_dataset` tool** on the agentic engine (run the query at a high export cap, write CSV to the shared folder, return a handle) or (b) a raised cap for export intent. Reconcile the artifact-persist threshold with `NLQ_AGENTIC_SQL_ROW_CAP`. The tool form is the cleaner long-term fit — the agentic engine already retains a dataset across turns (`get_dataset_preview`) and its tool-loop is the natural home for `export_dataset` / `read_artifact`.

**Net:** no rework. Primary Phase-1 injection stays at the shared response-builder (covers both engines); add the agentic `export_dataset`/`read_artifact` **tools** as the nicer long-term form, and treat the OOM safety cap as legacy-only.

## 3. Design principles (constraints from review)

1. **Artifacts by reference, not by value.** Delegation carries a *handle* (`artifact_id`, schema, row count, download URL) + a small preview; bytes stay in a shared store.
2. **No new SQL table.** `ArtifactManager` already writes a `{artifact_id}.meta.json` sidecar next to each file — **the sidecars are the registry.** Owner, tenant, producing-agent, row count, schema, source query live there. Lookup = file read; list = directory scan.
3. **No new microservice.** The shared store is **one shared folder** (configurable path, e.g. `AIHUB_ARTIFACTS_DIR`) on the same box. `ArtifactManager` becomes a shared **import** (library), not a server. Producers write as a library; **CC is the only thing that serves downloads** (its `/api/artifacts/{id}/download` already exists) — fits the single-interface model.
4. **CSV-first — no new dependency; CC never parses artifacts.** Verified 2026-07-12 (filesystem): pyarrow lives in **`aihub2.1` only** — absent in CC's **`aihubbuilder`** env (and cloud-gateway/browser-use). So CC must never read a columnar format, and it doesn't need to: the store `ArtifactManager.create(...)` writes **raw bytes** (`artifact_manager.py:62`) and the download route streams them via `FileResponse` (`command_center_service/routes/artifacts.py:114`) — no pandas/pyarrow, no parsing. Producers (main app, `aihub2.1`) already write CSV via `df.to_csv` (`app.py:1305`, `GeneralAgent`, the artifact CSV renderer); the store holds CSV bytes; CC serves them; preview rows come from the JSON handle. **Parquet is deferred** to an optional optimization for the `run_python` big-data reload path ONLY, read solely by the `CODE_INTERPRETER_PYTHON` interpreter (which we control), never by CC. So "pyarrow only in aihub2.1" is a non-issue for the core.
5. **High, tiered thresholds with explicit preview labeling** (below).
6. **Lightweight, sized for low concurrency.** No object storage. **Assumption: services share a filesystem (true today, single box).** If services are ever split across machines, the write-as-library step needs a thin HTTP shim — noted, not built.
7. **One ownership model.** Reuse `SessionManager._matches_owner` (tenant-aware, handles the tenant None/0 collision) for the shared store; identity via the existing `shared_auth.py` JWT, not spoofable query params.

## 4. Target architecture

```
User ⟷ Command Center ─┐        ┌─ Data agent  (aihub2.1) ── writes parquet + csv ─┐
                        ├── one shared artifact folder ──────────────────────────────┤
       serves + reads ──┘        └─ General agent (aihub2.1) ── writes files ─────────┘
                                    (sidecar .meta.json = registry; no SQL, no service)
```

- **Handle-passing delegation.** The data- and general-agent endpoints and `delegate_to_agent` gain `artifacts: [{artifact_id, name, type, rows, schema, sample, download_url}]`. CC **preserves and forwards** it across converse / gather_data / decompose (and stores it in the side-conversation log, which today keeps text only).
- **Big data as an artifact.** Above threshold the producer persists the full frame to the shared folder as parquet (+ csv), returns `{handle, schema, row_count, sample_rows}`. CC shows the preview and a download chip; the full rows never touch CC's LLM context.
- **`read_artifact` tool + `run_python` seeding.** CC can pull an artifact's text/rows into context; `run_python` can seed its workdir from artifacts (not just uploads) to compute over big data by reference.

## 5. Thresholds & preview UX

The chat table already pages/filters/downloads, so keep inline limits **high**; the artifact is always the full-fidelity backing copy.

| Result size | CC shows | Artifact |
|---|---|---|
| ≤ ~10k rows | full inline table (client pages/filters) | optional |
| ~10k–100k rows | preview slice in pager **+ download button + preview banner** | yes (CSV) |
| > ~100k rows | reference/link + schema + small sample, no inline rows | yes (CSV) |
| the query itself | high hard safety cap (~1M rows / memory budget) to prevent OOM | — |

All limits **env-tunable**. Enforce the safety cap in the read-only SQL execution wrapper (tie into the recent `sql_gate` work) — reject/curtail a runaway `SELECT *` with a clear message rather than materializing it. The **agentic** engine already does exactly this (`NLQ_AGENTIC_SQL_ROW_CAP`=10k via `sql_gate`); align the inline/artifact thresholds with that value, and note that a *full-export* artifact must deliberately bypass the display cap (dedicated export path), not silently inherit 10k.

**Preview labeling (required).** Whenever a slice is shown, it must be unmistakable:
- A **banner on the table block** — e.g. *"Preview — first 10,000 of 248,391 rows; download for all."* The block metadata already carries `total_rows` / `displayed_rows` / `truncated` (`SmartContentRenderer_hybrid.py:293–295`) — surface it.
- Keep the **download button** on the block (pulls the full CSV artifact).
- Pass a **"this is a preview, do not imply completeness"** note into CC's context so its prose doesn't overclaim.

## 6. Phased plan (each phase ships value alone)

- **Phase 0 — correctness & security (days).** Fix the `MAX_TABLE_DISPLAY_ROWS` config-name bug (shared — corrects legacy *and* agentic renderer); add the high row-count safety cap on the **legacy** path (the agentic engine already caps at `NLQ_AGENTIC_SQL_ROW_CAP`=10k); **authenticate `/document/serve` (both routes) and `/download`** (must-fix independent of the vision).
- **Phase 1 — big data as an artifact on the CC data path.** In the **shared** `/data_explorer/internal/query` response-builder (covers legacy *and* agentic via the factory/contract), above threshold write a **CSV** artifact and return `{handle, schema, sample}` instead of `str(df)`. CC renders preview + download chip. Closes the 100k-row scenario end-to-end. No parquet. Note: on the agentic path the result is pre-capped at `NLQ_AGENTIC_SQL_ROW_CAP` (10k), so a *full-export* artifact beyond that needs the agentic `export_dataset` tool (§2.1) or a raised export cap.
- **Phase 2 — shared folder + `ArtifactManager` as a shared library.** One folder, one ownership model (`_matches_owner`, tenant-aware), signed identity, richer sidecar metadata (producing-agent, source query, schema, row count).
- **Phase 3 — handles in the delegation channel (both directions, all paths).** Add `artifacts` to both callee endpoints and the delegator result; forward across converse / gather_data / decompose; fix decompose dropping rich content and the log storing text-only. Point general-agent `create_*` tools at the shared folder → "general agent made a CSV, CC hands it to the user" works.
- **Phase 4 — artifact as input.** `read_artifact` tool + seed `run_python` workdir from artifacts (via `CODE_INTERPRETER_PYTHON`, not CC). CC can use another agent's file, chain steps, compute over big data by reference. Reads CSV with `pandas.read_csv`; **optionally** introduce parquet here (typed/faster reload for very large frames) — written and read only in the interpreter env, never CC.
- **Phase 5 — surfacing hardening.** Make the chip passthrough robust (a stray `text` block currently trips the `all(...)` gate at `nodes.py:3876` and the LLM paraphrases the chips away); fix history re-render flattening; move download auth off spoofable query params to a signed token; make delegated-agent artifacts render as chips; handle split-deployment file access.

**Testing:** `tests_v2` fixtures for the 100k round-trip (data agent → CC → user) and a general-agent-CSV → CC → user e2e; assert preview banner + working download; coverage gate.

## 7. Recommended "this version" cut line

To remove the embarrassing gap without over-reaching: **Phase 0 + Phase 1 + Phase 2 + Phase 3 + `read_artifact` (Phase 4 lite) + preview labeling.** Phase 5 polish (chip-passthrough robustness, history re-render, download token signing, split-deploy) can trail into the next version, **except** the two unauth endpoints in Phase 0, which ship now.

## 8. Open items / risks

- Same-box shared-filesystem assumption (fine today; revisit if services split).
- Parquet is optional and deferred (Phase 4, interpreter env only); CC never needs pyarrow. **Verified 2026-07-12:** pyarrow present in `aihub2.1`, absent in `aihubbuilder`/cloud-gateway/browser-use. Core path is CSV-only.
- Download-route identity is client-asserted today; sign it (Phase 5) — reuse `shared_auth.py`.
- Injecting a row cap into LLM-authored SQL should go through the read-only `sql_gate`, not string-munging.

## 9. Key files

- Delegation: `command_center/orchestration/delegator.py`, `command_center_service/graph/nodes.py` (converse ~1660, gather_data ~4343, decompose ~5661/5858, passthrough gate ~3866–3876), `command_center_service/graph/delegation_log.py`.
- Data path: `routes/data_explorer.py` (shared builder ~628–690; internal/query 551–606; factory call `:602`), `AppUtils.py` (`:654`, `:2462`), `GeneralAgent.py` (`:652`, `:1472`), `LLMQueryEngine.py:725`, `SmartContentRenderer_hybrid.py:269–301`, `config.py:361–417` + `:441` (NLQ flags), `app.py:1282–1316` (browser CSV path), `DataFrameFileManager.py`.
- NLQ engines (two, factory-selected): `nlq_engine_factory.py` (`create_nlq_engine`, breaker, shadow), `nlq_agentic/` (`engine.py` reuses `SmartContentRenderer_hybrid` `:34`/`:358`; `NLQ_AGENTIC_SQL_ROW_CAP` `:290`; `tools.py`, `contract.py`), `sql_gate.py`. Docs: `docs/nlq-agentic-engine-plan.md`.
- Artifacts/store: `command_center/artifacts/artifact_manager.py`, `artifact_models.py`, `command_center_service/routes/artifacts.py`, `command_center_service/routes/upload.py`, `command_center_service/services/__init__.py:277–330` (`_matches_owner`), `command_center/tools/code_interpreter.py`, `command_center/tools/portal_fetch.py`, `chat_file_manager.py`.
- Producers (main app): `GeneralAgent.py:1145–1468` (create_* tools), `chat_file_manager.py`.
- Security: `app.py:2216` (`/download`), `app.py:8141`/`:8175` (`/document/serve`).
- Env: `Build_AIHub_Executables_OneDir_Dev_v3.bat` (main app + agents = `aihub2.1`; CC + builder = `aihubbuilder`).

## Build status (2026-07-12)

Implemented the "this-version" cut line. New/changed modules:
- **P0** (8efed34): `SmartContentRenderer_hybrid.py:276` reads the real config name; `config.SMART_RENDER_HYBRID_MAX_TABLE_DISPLAY_ROWS` default 10k (env-overridable) + `SQL_QUERY_ROW_SAFETY_CAP`; `sql_row_cap.py` (new) wired into `AppUtils` reads + `query_a_database`; `@login_required` + validation on `/download` and both `/document/serve`; CC delegated-table LLM inlining capped (`CC_DELEGATED_TABLE_LLM_ROW_CAP`).
- **P2-core** (837426c): `ArtifactManager.resolve_shared_artifacts_dir()` (`AIHUB_ARTIFACTS_DIR`) + `get_shared_artifact_manager()`; `ArtifactMetadata` provenance (producing_agent/source/row_count/columns, sidecar-back-compat); both CC construction sites repointed.
- **P1** (95e7230): `command_center/artifacts/data_export.py` (new) + `ARTIFACT_EXPORT_ROW_THRESHOLD` (10k); `/data_explorer/internal/query` persists big results as CSV + returns `artifacts`; delegator forwards them; `_build_response_blocks` chip + preview note; converse download note; `cc-renderers.js` preview banner.
- **P3** (aa2c1ac): `produced_sink.py` (new) + capture in `GeneralAgent._save_artifact_and_block`; `/api/agents/<id>/chat` `_register_delegated_artifacts`; delegator sends `session_id` for general agents; `ArtifactType.DOCX`; converse + `aggregate` surface general-agent artifacts.
- **P4-lite** (cdf361d): `read_artifact` CC tool (ownership-gated, CSV row-capped, binary-refused); `prepare_workdir` seeds session artifacts into the `run_python` workdir.

Tests: `tests_v2/unit/test_artifact_phase{0,1,2_store,3_delegation,4_read}.py` (68 new) + `test_nlq_engine_factory.py` hygiene. Full endpoint/engine→persist→HTTP and the CC render path are e2e-tier (need live DB + running services), NOT covered by unit tests — verify after restart.

**Restart required:** main app (data path, `/download`+`/document/serve` auth, general-agent capture) **and** CC service (delegation surfacing, `read_artifact`, workdir seeding). Set `AIHUB_ARTIFACTS_DIR` only if the shared folder shouldn't be `command_center_service/data/artifacts`.

**Deferred (Phase 5):** robust chip passthrough (a stray text block still trips the `all()` gate at `nodes.py`), history re-render flattening, download-token signing (identity still client-asserted), delegated-agent artifacts as real chips on the converse path (currently a markdown link), split-deployment shared filesystem.
