# Tier usage counts: TTL cache + `PlatformUsageLog` covering index

*2026-08-21 — follow-up to `doc-api-concurrency-and-fast-busy.md` §2/§7 (recommendation 1).*

## 1. The problem

`admin_tier_usage.get_agent_user_env_info()` computes the tenant's usage counts:

| query | DB | cost |
|---|---|---|
| `SELECT COUNT(DISTINCT RequestId) FROM PlatformUsageLog WHERE TokensUsed > 0 AND RequestTimestamp in current month` (+ RLS tenant predicate) | "cloud" DB — on this platform the **same** Azure SQL server/DB as the tenant DB (`aihub.database.windows.net/AIHUB`, Standard **S1 = 20 DTU**) | **30–230 s cold**, 0.05 s warm |
| `COUNT(*)` over `AgentEnvironments`, `Agents`, `AgentTools`, `[User]` (+ role breakdown) | tenant DB | ~0.5 s |

It ran **uncached on every call** — every `@tier_allows_feature`-decorated route hit (`/document-manager`,
`/document-search`, `/document_processor`, `/workflow_tool`, `/monitoring`, environment routes), every
tier-dashboard load, and — the real driver of the cadence in the log — the email dispatcher's 5-minute
enterprise re-check (`email_agent_dispatcher._is_enterprise_enabled`, `_enterprise_cache_ttl = 300`),
which calls `get_cached_tier_data()` from a **fresh** `app_context()` each time, so the per-request `g`
cache never helped it.

### Before (live log, `logs/admin_tier_usage_log.txt`, 2026-08-21, 95 runs)

```
TIMING: Cloud DB took   n=95  min=0.05  p50=57.31  p90=78.49  max=212.15  (seconds)
TIMING: Local DB took   n=95  min=0.07  p50=59.63  p90=79.78  max=230.50  (cumulative from start)
```
One run every ~5 min + its own duration (21:00:57 → 21:06:46 → 21:13:06 …): the dispatcher's
`cache_time` is stamped *after* the query returns, so the cadence drifts by the query time — that drift is
the dispatcher's signature (the scheduled workflow runs sit exactly on :00/:05). The dispatcher's poll loop
also **blocks** behind the query (`Found N pending email(s)` lands right after each `TIMING: Local DB` line).

### Why it is slow (measured from the app login, same session, `EXEC tenant.sp_setTenantContext`)

| probe | result | elapsed |
|---|---|---|
| tenant `COUNT(*)` under RLS only | 85,349 rows | 6.4–7.8 s |
| same with an explicit `TenantId = 1` | 85,349 rows | **0.16 s** |
| the tier-usage query, cold | 3,848 distinct requests (7,443 month rows) | **37.1 s / 48.4 s / 60.8 s** (three cold runs) |
| same, immediately again (warm) | 3,848 | 0.05 s |
| relay-shaped quota query (`TenantId = ? AND month`, `aihub-api rate_limiter.py`) | 5,365 | 26.8 s |

`PlatformUsageLog` has `IX_PlatformUsageLog_RequestTimestamp` keyed on **`RequestTimestamp` only** (no
INCLUDE). The month-range seek therefore needs **one key lookup per row** into the clustered index (rows
carry `NVARCHAR(MAX)` `RequestBody`/`ErrorMessage`) to read `TokensUsed`, `RequestId` and `TenantId`:
physical IO on an IO-governed tier = 30–230 s cold, ~0 warm. And the RLS predicate is **not a seek
predicate** (6.4 s vs 0.16 s above), so a `TenantId`-leading index would not help the tenant app's query.

## 2. What changed

### 2.1 `admin_tier_usage.py` — usage-count cache (`_usage_cache`)

* `get_agent_user_env_info(force_refresh=False)` is now a **TTL cache** around the unchanged query body
  (`_query_agent_user_env_info()`, which keeps the `TIMING:` lines). Same return shape, still never raises.
  * TTL `USAGE_CACHE_TTL` ← `config.TIER_USAGE_CACHE_TTL` ← env **`TIER_USAGE_CACHE_TTL`** (default **300 s**).
  * one refresher at a time (`_usage_cache['lock']`); concurrent callers get the **last-known values
    immediately** (they never queue behind a multi-minute query); callers with nothing cached wait.
  * `force_refresh=True` always re-queries (serialised behind the same lock).
  * a failed refresh keeps/serves the last-known values and records `last_error`.
  * callers get deep copies; a generation counter makes a refresh that started before an invalidation
    drop its result.
* `invalidate_tier_cache()` also clears it (`invalidate_usage_cache()` exists on its own too).
* `get_cached_tier_data(force_refresh=False, include_usage=True)`:
  * `include_usage=False` — for consumers that only need `tier_features`/`subscription` — **never
    queries**; it attaches the last-known cached counts if any, else `{}`; a later `include_usage=True`
    call in the same request upgrades `g.tier_data` in place.
  * `@tier_allows_feature` and `@require_tier` use `include_usage=False`; `@tier_allows_resource` keeps
    counts (cached); `@check_usage_limits` loads counts only when it checks `resources`. Allow/deny
    outcomes are identical to before.
* Routes: `/admin/tier/api/stats` and `/api/subscription-info` serve cached counts (`?force_refresh=true`
  re-queries); `/api/cache-status` and `/api/subscription-info` expose `usage_cache` status;
  `/api/cache-invalidate` clears both caches.
* Log lines: `USAGE-CACHE: hit (age …)`, `USAGE-CACHE: refreshed`, `USAGE-CACHE: refresh failed …`.

### 2.2 Feature-only callers outside the module (the ~5-minute driver)

`email_agent_dispatcher._is_enterprise_enabled()` and the two enterprise checks in
`app_executor_service.py` now call `get_cached_tier_data(include_usage=False)` — they only read
`tier_features['enterprise_features_enabled']`. With that, nothing on this box runs the
`PlatformUsageLog` count except an admin opening the tier dashboard / subscription-info (once per TTL).

### 2.3 `migrations/019_platform_usage_log_covering_index.sql`

Rebuilds `IX_PlatformUsageLog_RequestTimestamp` **in place** (`DROP_EXISTING = ON, ONLINE = ON`) as
`(RequestTimestamp) INCLUDE (TokensUsed, RequestId, TenantId)` — no key lookups for the month-range
count; the RLS predicate is evaluated on the included `TenantId`; the relay's per-LLM-call quota query
(`TenantId = ? AND month`) is covered too. Same key/name, so write cost per INSERT is unchanged (the
relay inserts one row per LLM call). Idempotent; ASCII; rollback statement in the header.
**Not applied yet** — needs a DDL-capable login (the app login `TenantAppUser` has no `ALTER`), run it
like 016–018, off-peak (the build reads the table once).

## 3. After (same code path, fresh process, 2026-08-21 21:46)

```
A  get_cached_tier_data(include_usage=False)  [cold process, feature gate]     0.261s   (no DB access; current_usage {})
B  get_agent_user_env_info()                  [1st call: UNCACHED query]      61.759s   (TIMING: Cloud DB took 60.84s)
C  get_agent_user_env_info()                  [calls 2-4: cached]              0.000-0.001s   (USAGE-CACHE: hit)
D  get_cached_tier_data()                     [new request, usage from cache]  0.001s
```
* Feature gates / dispatcher: **61.8 s → 0.26 s, and 0 DB statements** (was one 30–230 s count per hit).
* Count consumers (dashboard, `@tier_allows_resource`): one query per `TIER_USAGE_CACHE_TTL` (300 s)
  instead of one per call; a single slow refresh no longer blocks other callers.
* Live log after the services restart on this code: no more `TIMING: Cloud DB took …` every ~5 min from
  the executor; `USAGE-CACHE:` lines only on dashboard/resource-limit use. (The live processes that were
  started at 20:49 still run the old code until restarted.)
* The remaining 30–60 s cold cost of the one refresh per TTL is what 019 removes (expected: month-range
  seek on a ~44-byte-per-row index, sub-second even on S1).

## 4. Tests

* `tests_v2/unit/test_admin_tier_usage_cache.py` (32): TTL hit/expiry/force/invalidate, copies,
  failure fallbacks, invalidate-during-refresh, serve-stale-while-refreshing, wait-when-empty,
  connection cleanup, status shape, config env override/default, `include_usage` semantics incl. the
  in-request upgrade, and Flask-client tests proving the decorators' allow/deny is unchanged while
  `@tier_allows_feature`/`@require_tier` make **zero** usage queries.
* `tests_v2/migrations/test_migration_019_platform_usage_log_index.py` (6): exact index shape, ONLINE,
  guards, no destructive statements, ASCII. (`.gitignore` hides `test*.py` — add test files with `git add -f`.)

## 5. Knobs / operations

* `TIER_USAGE_CACHE_TTL` (env / `.env`): seconds; default 300; 60–300 recommended. `TIER_CACHE_TTL` untouched.
* Force fresh counts: `GET /admin/tier/api/stats?force_refresh=true`, `GET /admin/tier/api/subscription-info?force_refresh=true`,
  or `POST /admin/tier/api/cache-invalidate`.
* Trade-off accepted: `@tier_allows_resource` / `check_usage_limits` enforce counts up to `TTL` seconds
  stale (a create can slip through within the window after the limit is reached); the `base.html` tier
  banners on tier-gated pages show last-known counts (or none on a cold process) instead of fresh ones.
