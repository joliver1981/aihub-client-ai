# Document API concurrency, the "serialization" stall, and fast-busy (503 + Retry-After)

_Investigated and built 2026-08-21. Files: `inflight_gate.py` (new), `app_doc_api.py`,
`LLMDocumentEngine.py`, `app.py`, `agent_service/document_tools.py`, `app_vector_api.py`,
`LLMDocumentVectorEngine.py`; tests in `tests_v2/unit/test_inflight_gate.py`,
`test_doc_api_fast_busy.py`, `test_engine_phase_timer.py`, `test_document_tools_busy.py`._

## 1. The question

`/document/process` on the document API (`app_doc_api.py` behind `wsgi_doc_api.py`,
waitress, `SERVER_THREADS=16` on this box) appeared to **serialize** requests: a probe
submitted at 17:34:40 seemed to start its engine work only ~17:38:45, while an earlier
request was still running. With 16 threads that cannot be thread starvation, so the
suspects were an in-process lock / singleton around the engine, a shared DB session,
chromadb `PersistentClient` serialization, or posthog telemetry stalls.

## 2. What the logs actually show (2026-08-21, `logs/doc_api_log.txt` + `llm_document_engine_log.txt`)

Both probes (`lagprobe_statement_ZEPHYRQUARTZ…`, `…KRAKENOPAL…`) entered the engine the
same second they arrived, and ran **concurrently** through their LLM phases:

| request | arrived | engine start | detect | extract/category | vector add | **SQL store** | done |
|---|---|---|---|---|---|---|---|
| probe 1 (new type) | 17:34:40.7 | 17:34:40.8 | 125 s (relay LLM) | +120 s category LLM, 7 s schema | 0.3 s | **17:38:54 → 17:46:12 = 7 m 18 s** | 17:46:12 |
| probe 2 (known type) | 17:39:40.7 | 17:39:40.8 | 4 s | 3 s | 0.3 s | **17:39:48 → 17:46:11 = 6 m 23 s** | 17:46:11 |
| lagprobe2 (after the 17:49 restart, **alone**) | 17:50:07 | 17:50:24 | 11 s | 3.5 s | 0.4 s | **17:50:39 → 17:57:09 = 6 m 30 s** | 17:57:09 |

* Probe 2 did its detection + extraction (17:39:40–17:39:48) **while probe 1 was already
  inside its SQL store** — so nothing in-process serialized them.
* Between `Added 1 pages to vector DB` and `Stored document … in SQL database` the engine
  runs exactly one thing: `_store_in_sql_db()` — `INSERT Documents`, `INSERT DocumentPages`,
  `_insert_fields` (a handful of `INSERT DocumentFields` + two small SELECTs per page), commit.
  ~13 statements. At 16:03/16:29/16:54/17:20 the same store took 12–14 s for a 1-page
  invoice; in the probe window it took 6–7 **minutes**, and both probes were released within
  one second of each other (17:46:11.7 / 17:46:12.7). The third probe ran completely alone
  in a fresh process and still spent 6.5 minutes there.
* The client side: both imports came from The Agent's `import_documents` tool
  (`agent_service_log.txt`: `user=lag-probe … failed=1` at exactly +300 s), i.e. the tool
  gave up at its 300 s read timeout while the server went on to store the document — the
  "client hangs to read timeout, then a ghost document lands" failure the fast-busy work is
  meant to remove.

### Code facts (refuting the in-process suspects)

* `LLMDocumentEngine.py` has **no** `threading.Lock`/`Semaphore`; every DB call opens its own
  `pyodbc` connection (`get_db_connection()`); `self.sql_conn` is only used for the startup
  DDL check. The engine singleton in `app_doc_api.get_document_processor()` shares only
  `schemas` (reassigned per document) and a proxy client whose request-id is per-module.
* chromadb is **not** in the doc API process at all: `LLMDocumentVectorAdapter(use_remote=True)`
  → HTTP to the vector API (:5031). A page add is ~0.35 s end to end. The posthog
  `ReadTimeout`s in `doc_vector_api_log.txt` are chromadb's telemetry thread backing off 15 s
  **after** the add has already returned — noise, not a stall (now disabled anyway, §4.6).
* LLM calls go through the cloud relay (`AnthropicProxyClient` →
  `https://ai-hub-api.azurewebsites.net/api/proxy/anthropic/messages/v2`,
  timeout 300 s, no lock). The relay is a plain Flask app; per call it runs several
  queries against the **same** Azure SQL database (key validation, tenant limits, monthly
  quota `COUNT(DISTINCT RequestId) FROM PlatformUsageLog`, and a usage-log INSERT), so its
  latency tracks the database's health. Detection round-trips today were 12–125 s.

### The shared chokepoint: the S1 Azure SQL tier

The tenant database (`aihub.database.windows.net/AIHUB`, **Standard S1 = 20 DTU**, RCSI on,
no triggers on the document tables) is hit by every service on the box **and** by the relay.
Measured from this machine on a quiet moment: connect 0.06–0.10 s, `SELECT 1` ≈ 0 ms —
and yet, minutes earlier, on the same connection:

| statement | quiet | while the DB was busy |
|---|---|---|
| `_insert_fields` `SELECT COUNT(*) FROM DocumentFields WHERE page_id=? AND field_name='document_type'` (indexed) | 0.026 s | **12.08 s** |
| `_insert_fields` document-type lookup (`Documents ⋈ DocumentPages`) | 0.010 s | **2.77 s** |
| `admin_tier_usage` "cloud" query `COUNT(DISTINCT RequestId) FROM PlatformUsageLog WHERE TokensUsed>0 AND RequestTimestamp in month` | — | **62 s** (and 30–230 s on every run today, ~every 6 min: `logs/admin_tier_usage_log.txt` `TIMING: Local DB took …`) |

This is the DTU-model IO governor: S1 has only tens of IOPS; one IO-heavy statement (the
tier-usage count needs ~7,400 key lookups into a table with `NVARCHAR(MAX)` payload
columns) drains the budget for a minute or more and **every other statement queues behind it
— including the doc engine's trivial inserts**. `get_agent_user_env_info()` in
`admin_tier_usage.py` runs that count **uncached on every `@tier_allows_feature` route hit
and every dashboard `/admin/tier/api/stats` poll** (the "cloud DB" is the same server/DB as
the local one). Add the relay's per-call DB work, the vector/knowledge/email pollers and any
purge (`purge_document` → `DELETE FROM Documents` cascading through pages/fields), and the
store phase can stall for minutes; when the governor catches up, everything that was waiting
is released together — exactly the 17:46:11/17:46:12 signature.

The app login (`TenantAppUser`) lacks `VIEW DATABASE PERFORMANCE STATE`, so Query Store /
`sys.dm_db_resource_stats` could not be read from here; §6 has the forensic queries for an
admin login if the exact wait category (IO governor vs. lock) needs confirming.

**Conclusion:** there is no in-process serialization point. Extraction *is* concurrent. What
serializes ingests in practice is the shared S1 database's IO budget during `_store_in_sql_db`
(plus relay latency that tracks the same database) — and the new `[sql-store]` line then
named the exact statements: the two per-page SELECTs in `_insert_fields` (§5), fixed with a
`CAST` that makes them index seeks. The right response is not a lock but
(a) observability that names the phase/statement, (b) a fast-busy admission gate so callers
never hang into their read timeouts, and (c) fixing the one true concurrency bug the
parallel run exposed in the vector API (§4.5).

## 3. Intended concurrency design (documented)

* **`/document/process` is concurrent.** Waitress (`SERVER_THREADS`, default 10 → 16 here)
  runs ingests in parallel; each runs its LLM phases via the relay and its store on its own
  SQL connection. Nothing requires serialization.
* **Admission is bounded, not queued.** `app_doc_api._PROCESS_GATE` admits at most
  `DOC_PROCESS_MAX_INFLIGHT` concurrent `/document/process` requests — default
  `SERVER_THREADS − 2`, so a thread is always free to serve the 503 and `/document/health`.
  The (N+1)th caller gets **HTTP 503 immediately** (measured 60–80 ms) with
  `Retry-After` (adaptive: half the median recent hold time, clamped 10–300 s, default
  `DOC_BUSY_RETRY_AFTER_SECONDS=30`) and a JSON body `{status:"busy", message, in_flight,
  max_in_flight, retry_after}` worded so an LLM tool can relay it verbatim.
  **Admitted requests are processed exactly as before — extraction semantics unchanged.**
* **Search is gated the same way in the main app** (`app.py` `_SEARCH_GATE`,
  `DOC_SEARCH_MAX_INFLIGHT`, default `SERVER_THREADS − 4`, floor 2) on the JSON endpoints
  The Agent / Command Center / UI consume: `/api/internal/document-search`,
  `/api/internal/document-search-unified`, `/api/internal/document-records`,
  `/api/search-documents-hybrid`, `/api/search-documents-by-attributes`. The HTML
  `/document-search` page is not gated (it renders for a browser).
* **Clients fail fast and say "busy".** The Agent's `import_documents` reports a 503 file as
  `BUSY (not imported)` + the server's own message and retry hint (no retry storm, one POST
  per file); `search_documents` / `query_document_records` relay the busy text; the main
  app's `process_document_as_knowledge` returns `{status:"error", busy:true, retry_after}`
  instead of the old "invalid file type" fallthrough.
* **Observability.** `/document/health` now returns `busy` + `process_gate` (in_flight,
  limit, peak, admitted/rejected totals, recent durations, retry_after_s). Every ingest logs
  one `[ingest-timing] … total=… | context= detect= extract= doclevel= pages= category=
  schema= vector= sql= records= jobupdate=` line (an unfinished phase carries `*`), and the
  store logs `[sql-store] doc=… connect= context= doc_insert= pages_fields= commit=
  statements=N slowest: …` — at WARNING when the store exceeds
  `DOC_SQL_STORE_SLOW_SECONDS` (default 10). The engine result and the `/document/process`
  response carry `timings` (additive). `[inflight] document/process admitted/released/
  REJECTED` lines show the gate live.

## 4. What changed

1. **`inflight_gate.py`** (new): `InflightGate` — non-blocking bounded slot counter, adaptive
   `Retry-After`, `snapshot()`, `busy_payload()`; `limit_from_env()`; Flask helpers
   `flask_busy_response()` and the `gated()` decorator (apply innermost, below auth).
2. **`app_doc_api.py`**: `_PROCESS_GATE`; `/document/process` admits-or-503s and releases in
   `finally` (body unchanged in `_process_document_route_body`); `/document/health` exposes
   the gate; `timings` passthrough; lazy singletons (`get_document_processor` /
   `get_document_searcher`) built once under a lock (three concurrent first requests used
   to build three engines).
3. **`LLMDocumentEngine.py`**: `_PhaseTimer` + `_TimedCursor` (transparent pyodbc cursor
   proxy), `[ingest-timing]` and `[sql-store]` lines, `result["timings"]`. No control-flow
   change. Plus the one store fix the instrumentation pointed at: the two SELECTs in
   `_insert_fields` now compare `page_id = CAST(? AS VARCHAR(100))` (index-seekable; same
   rows, same results) — 14.4 s → 0.1–0.25 s per 1-page store, measured live (§5).
4. **`app.py`**: `_SEARCH_GATE` + `@_inflight_gated` on the five search/records JSON routes;
   `process_document_as_knowledge` handles 503.
5. **`app_vector_api.py`** — the one real concurrency bug: the chroma engine was created
   lazily **per request** and torn down by an `@app.teardown_appcontext` hook (which fires
   after every request, not at shutdown as its docstring believed). Under the first truly
   parallel run, three concurrent ingests all failed their vector step
   (`Vector engine not initialized` / `'NoneType' object has no attribute 'add_documents'`).
   Now: process singleton created under a lock, assigned only after `initialize()`, closed
   `atexit`. Side benefit: no ~1 s chroma re-init per request.
6. **`LLMDocumentVectorEngine.py`**: `Settings(anonymized_telemetry=False)` by default.
7. **`agent_service/document_tools.py`**: `_busy_text()`; 503 handling in import / search /
   records; truthful comment.

## 5. Verification

Unit: `test_inflight_gate.py` 9/9 (aihub2.1 pytest), `test_doc_api_fast_busy.py` 6/6 and
`test_engine_phase_timer.py` 7/7 (aihubant), `test_document_tools_busy.py` 7/7 (aihub-agent);
the env-specific ones self-skip in the main-app sweep (20 skipped, 0 errors).

Live (after `00_Start-Restart_AIHub_Services_V3.bat`, VERIFY OK ×2):

* `/document/health` → `process_gate.limit=14` (16 threads − 2).
* **Burst of 18 concurrent `/document/process` (no-store) against limit 14:** 14 × HTTP 200
  (6.6–12.0 s, one relay LLM call each, all running in parallel — `[inflight] … admitted
  14/14`), **4 × HTTP 503 in 0.06–0.08 s** with `Retry-After: 10`, `X-Inflight: 14/14` and
  the busy JSON; health afterwards: `peak_in_flight 14, rejected_total 4, admitted 17,
  recent median 9.5 s`.
* **3 concurrent stored imports:** see the table below (filled from the run after the vector
  fix).

| 3 concurrent stored imports (1-page TXT, known type) | total per request | `sql` phase | `[sql-store]` slowest statements |
|---|---|---|---|
| before the CAST fix (vector race already fixed) | 21.3 s each, all parallel (peak in_flight 3) | **14.3–14.5 s** | `SELECT COUNT(*) FROM DocumentFields WHERE page_id…` 9.65–11.87 s; `SELECT d.document_type FROM Documents d JOIN DocumentPages…` 2.37–2.39 s; 7 × `INSERT DocumentFields` ≈ 0.05 s; commit 0.01 s |
| after the CAST fix | 6.7–6.9 s each (relay LLM 3.7–5.6 s + vector 0.5–2.2 s) | **0.08–0.25 s** | INSERTs only, ≤ 0.06 s each |

So the "exact serialization point" has a precise answer: **the two read-queries inside
`_insert_fields`** — non-SARGable against the `VARCHAR(100)` id columns — which under the S1
tier's IO governor cost ~14 s per page on a quiet day and minutes when anything else
(tier-usage scans, purges, other tenants, the relay's own queries) competes for IO. Every
ingest paid it per page (a 264-page document: ~60 min in the store alone), and concurrent
ingests multiplied the IO demand on each other. All three probe documents were purged after
each run (vector + SQL), so the tenant store is as it was.

## 6. Forensics for an admin login (Query Store / DMVs)

`TenantAppUser` cannot read these; run as a login with `VIEW DATABASE PERFORMANCE STATE`
(intervals are UTC; the probe window was 2026-08-21 21:30–22:00 UTC):

```sql
-- wait categories per interval for the doc-store statements
SELECT rsi.start_time, LEFT(qt.query_sql_text,70) sql70, ws.wait_category_desc,
       ws.total_query_wait_time_ms, ws.max_query_wait_time_ms
FROM sys.query_store_wait_stats ws
JOIN sys.query_store_plan p ON p.plan_id = ws.plan_id
JOIN sys.query_store_query q ON q.query_id = p.query_id
JOIN sys.query_store_query_text qt ON qt.query_text_id = q.query_text_id
JOIN sys.query_store_runtime_stats_interval rsi ON rsi.runtime_stats_interval_id = ws.runtime_stats_interval_id
WHERE rsi.start_time >= '2026-08-21 20:00' AND rsi.start_time < '2026-08-22 00:00'
  AND (qt.query_sql_text LIKE '%INSERT INTO Documents (%' OR qt.query_sql_text LIKE '%INSERT INTO DocumentPages%'
       OR qt.query_sql_text LIKE '%INSERT INTO DocumentFields%' OR qt.query_sql_text LIKE '%sp_setTenantContext%')
ORDER BY ws.max_query_wait_time_ms DESC;

-- resource governance, 15 s buckets (last hour only)
SELECT TOP 240 end_time, avg_cpu_percent, avg_data_io_percent, avg_log_write_percent
FROM sys.dm_db_resource_stats ORDER BY end_time DESC;

-- live blocking right now
SELECT r.session_id, r.blocking_session_id, r.wait_type, r.wait_time, LEFT(t.text,120)
FROM sys.dm_exec_requests r OUTER APPLY sys.dm_exec_sql_text(r.sql_handle) t
WHERE r.blocking_session_id <> 0 OR r.wait_time > 5000;
```

## 7. Recommendations (not done here — each is its own change)

1. **`admin_tier_usage.get_agent_user_env_info()`**: cache it (TTL 60–300 s, like the tier
   cache) and/or give `PlatformUsageLog` a covering index
   (`RequestTimestamp` INCLUDE `TokensUsed, RequestId, TenantId`). Today every tier-gated
   page hit and dashboard poll costs 30–230 s of the tenant DB's IO budget. This is the
   single biggest lever on the doc store stalls measured here.
2. **DB tier**: S1 (20 DTU) is under-provisioned for a multi-service tenant plus the relay;
   S3/GP-serverless or at least S2 would end most of the IO queueing.
3. **Relay per-call DB work**: the monthly-quota count and usage INSERT run on the same DB
   on every LLM call; cache the quota per tenant for a minute.
4. **Optional store timeout**: `pyodbc` `cursor.timeout` / connection `timeout` on the store
   path would turn a multi-minute stall into a fast, honest failure (with the existing vector
   rollback). Not done — it changes what an admitted request does (the user asked for no
   semantic change); decide explicitly.
5. Gate the other engine-backed doc API routes (`/document/extract`, `/extract_text`,
   `/analyze`, `/process_directory`) with the same gate once the behaviour is accepted.
