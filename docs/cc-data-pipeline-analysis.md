# Command Center — Data Display + Enrichment Pipeline Analysis

> **Phase A: foundation — done (2026-05-10).** Four enrichment workstreams
> have shipped behind opt-in env flags:
>
> * **WS1 — Audit trace limits bumped** (`graph/tracing.py`): system 2k→16k,
>   user 1.5k→32k, response 3k→24k, tool_calls 1k→16k. A soft per-trace cap
>   constant (100 MB) is declared with a TODO for the actual writer-side
>   guard.
> * **WS2 — Real geocoder.** `plugins/web_intelligence/geocoder.py` ships
>   a Nominatim adapter with a polite 1 req/sec rate limiter, 10k-entry /
>   7-day LRU cache, and a Mapbox stub for future paid backends. The
>   `geocode_address` tool no longer returns `(0.0, 0.0)`. Backend selected
>   via `CC_GEOCODER` (default `nominatim`).
> * **WS3 — Auto-enrichment re-wired**, opt-in. `CC_ENRICHMENT=auto|tools|off`
>   (default `tools`). When `auto`, the AQG runs `_enrich_geographic` on
>   `GAP_GEOGRAPHIC` and `_enrich_knowledge` on `GAP_KNOWLEDGE`, each wrapped
>   in try/except with a per-turn budget (max 3 calls, max 8s). Geographic
>   enrichment now calls the real geocoder per region for marker lat/lng.
> * **WS4 — Data provenance.** `command_center_service/provenance.py`
>   introduces `ProvenanceEntry` + `Provenance` (sibling-map approach,
>   keyed by dot-notation field path). Schema documented in
>   `docs/data-provenance.md`. Ops Room drawer renders source badges with
>   tooltips for each field that has a matching `_provenance` entry.
>
> New env vars: `CC_GEOCODER`, `CC_ENRICHMENT`. See `.env.template`.

**Scope:** the Command Center (CC) agent service in `command_center_service/` and the
classic + Ops Room front-ends under `command_center_service/static/`. Goal: assess the
current data-display and enrichment pipeline end to end, identify gaps that would
break in a high-stakes ops-center context, and propose a "military-grade and
reliable" path forward — including the AI's enrichment tool surface.

This document is a research aid, not a marketing brief. Every claim about current
state is anchored to a file path and (where useful) a line number. Where I'm
inferring rather than reading, I say so explicitly.

---

## 1. Current state

### 1.1 Map points

**Library and rendering.** Leaflet 1.9.4 is loaded from a CDN
(`command_center_service/static/index.html:15-16`). There are two independent
map surfaces:

- **In-message rendering** — `CCRenderers._renderMap` in
  `command_center_service/static/js/cc-renderers.js:317-463`. Each map block
  becomes a fresh `L.map` rooted in a unique `cc-map-N` div. Tiles are
  OpenStreetMap (`{s}.tile.openstreetmap.org/{z}/{x}/{y}.png`,
  `cc-renderers.js:349-351`). Markers are plain `L.marker([lat,lng])` with an
  optional popup; no clustering, no custom icons in classic mode.
- **Dominant Ops Room map** — `OpsRoom._initMap` in
  `command_center_service/static/js/ops-room.js:117-160`. Same OSM tile source,
  same Leaflet instance, but markers are `L.divIcon`s with class hooks
  (`ops-marker ops-marker-<kind>`, `ops-room.js:162-175`) so categories can be
  shown/hidden by toggling CSS `display`.

**Block format.** A map block is JSON of shape:

```json
{ "type": "map", "title": "...", "center": [lat,lng], "zoom": N,
  "markers": [{"lat":..,"lng":..,"label":..,"popup":..}],
  "regions": [{"name":"State","value":N,"label":"..."}] }
```

(`graph/nodes.py:1810-1912`, `_renderMap` accepts the same fields.)

**Choropleth.** `CCRenderers._loadGeoJSON`
(`cc-renderers.js:279-298`) fetches `/static/data/us-states.geojson` once and
caches it on the JS object. Region values are matched by lowercased state name
(`cc-renderers.js:370-374`). Color uses a hand-rolled cyan gradient
(`_getChoroplethColor`, `cc-renderers.js:307-315`); no library scale, no
configurable palette.

**Projections / coords.** Leaflet defaults (Web Mercator). Lat/lng are coerced
to floats inside `generate_map` (`graph/nodes.py:1853`); invalid points are
silently dropped (`graph/nodes.py:1851-1852`). No CRS handling for non-WGS84
inputs.

**Auto-zoom.** A spread heuristic in `generate_map`
(`graph/nodes.py:1875-1894`) picks zoom 3..11 from the point spread;
choropleth defaults to US-centered `[39.8, -98.5]` zoom 4
(`graph/nodes.py:1888-1891`). On the Ops Room dominant map the bounds are NOT
derived from points yet — center is hard-coded to `[39.8, -97.5]` with the
seed fleet (`ops-room.js:127-132`, plus `TODO(ops-wire)` at `ops-room.js:142-144`).

**Capacity.** No clustering, no canvas/WebGL renderer (deck.gl etc.). Each
marker is a DOM element, so practical limit is a few hundred before scrolling
gets choppy on lower-end hardware. Region count is bounded by the GeoJSON
(50 US states only).

**Popup / tooltip content.** Plain string (`m.popup || m.label`) bound via
`bindPopup`/`bindTooltip`. No structured fields, no source attribution, no
freshness indicator.

**Ops Room selection drawer.** `_showSelection` in `ops-room.js:237-263`
displays the seed point's id, kind, lat/lng, detail, "last update" (literally
`new Date()` — the current wall clock, NOT the data's update time). Linked
agents is hard-coded to `<em>TODO — wire to /api/inspect/links</em>`. The
"Run enrichment" button `_handleDrawerAction("enrich")` only logs a ticker
entry — there is no `POST /api/inspect/enrich` yet (`ops-room.js:290-292`,
`TODO(ops-wire)`).

### 1.2 Charts and graphs

**Library.** Chart.js 4.4.0 from CDN (`index.html:13`).

**Render path.** `CCRenderers._renderChart` (`cc-renderers.js:159-223`). Block
format is `{type:"chart", chartType, data, xKey, yKeys, colors, title}`. Chart
types supported by string match: `line`, `bar`, `pie`, `doughnut`, plus an
`area` shorthand that resolves to a filled line (`cc-renderers.js:184`,
`cc-renderers.js:195`). Default palette is five hard-coded hex colors
(`cc-renderers.js:183`).

**Data binding.** Synchronous one-shot — block.data is mapped into Chart.js
labels + datasets at render time. **No live updates.** No streaming. New data
arrives only as a fresh chat message containing a fresh chart block, which
creates a new Chart.js instance (instances kept in `_chartInstances` keyed by
counter id, `cc-renderers.js:215`).

**Interactivity.** Default Chart.js tooltips and legend. No drill-down, no
linked selection between charts and other blocks, no cross-filtering with the
map.

**Tables.** `_buildInteractiveTable` (`cc-renderers.js:536-730`) is a
hand-rolled engine — pagination (25/page), client-side sort, free-text filter,
CSV export. Used for both `_renderTable` (header/row form) and
`_renderDataFrameTable` (pandas-flavored schema+data form). Strong, but
search/sort all happen on the client; for very large datasets the server
either returns the full set or truncates upstream.

**KPI cards.** `_renderKPI` (`cc-renderers.js:253-269`) is a thin block
rendering a list of `{label, value, trend, trendDirection}` cards with a CSS
class. The Ops Room KPI strip (`OpsRoom._renderKpis`, `ops-room.js:57-73`) is
separate and currently fed by hard-coded mock numbers in
`OpsRoom._refreshKpis` (`ops-room.js:92-114`, `TODO(ops-wire)` at line 99-100).

### 1.3 Data enrichment

**Today's enrichment surface, end-to-end:**

1. **Agent delegation as the primary path.** `gather_data` in
   `graph/nodes.py:2899-...` delegates a query to a data agent
   (`delegate_to_agent`, `graph/nodes.py:2950-2956`) and returns the agent's
   text + any rich-content blocks the agent generated. This is the only path
   that ever produces grounded business data.

2. **Tool-driven explicit enrichment** (only fires when the LLM picks a tool):
   - `generate_map(locations_json, title)` — `graph/nodes.py:1810-1912`. Takes
     pre-existing data and produces a map block.
   - `search_web(query, num_results)` — `graph/nodes.py:1970-2036`. Calls
     Tavily directly via `httpx.post("https://api.tavily.com/search", ...)`,
     gated on the `TAVILY_API_KEY` env var. Returns a markdown-formatted
     summary + sources list. No structured per-field output, no source
     attribution carried into a downstream block.
   - `search_documents(question)` — `graph/nodes.py:2038-2108`. Hits an
     internal `/api/internal/document-search` endpoint.
   - `generate_image(prompt, size)` — DALL-E (`graph/nodes.py:1914-1968`).
   - `export_data` / `send_email` / preference tools — orthogonal to display
     enrichment.
   The full tool list bound to the conversational LLM:
   `graph/nodes.py:2219` —
   `[query_data_agent, query_general_agent, delegate_to_builder_agent,
     save_user_preference, recall_all_memories, forget_preference,
     switch_active_agent, export_data, run_generated_tool, generate_map,
     search_web, send_email]` (+ `generate_image` and `search_documents`
     conditionally).

3. **Auto-enrichment path — RE-ENABLED, opt-in (WS3, done 2026-05-10).** The
   answer_quality_gate node now reads `CC_ENRICHMENT`:
   * `tools` (default) — original observe-only behavior; helpers stay dead.
   * `off` — explicit kill switch.
   * `auto` — gate classification drives `_enrich_geographic` (now backed by
     the real geocoder) and `_enrich_knowledge`. Each call is wrapped in
     try/except and bounded by a per-turn budget (max 3 helper calls, max 8s
     total). Output is stamped with provenance (WS4) and appended as a
     separate AIMessage so the classic UI's parsing is unchanged.

4. **Web Intelligence plugin — geocoder is real (WS2, done 2026-05-10).**
   `plugins/web_intelligence/handler.py` `geocode_address` now dispatches
   to `plugins/web_intelligence/geocoder.py`, which implements a
   Nominatim-backed `GeocoderBackend` with rate limiting + LRU cache and
   a `MapboxBackend` stub. Backend selection via `CC_GEOCODER`. The
   `search_web` handler in this plugin is still a placeholder — the LLM
   continues to use the Tavily-backed `search_web` tool defined in
   `graph/nodes.py:1970-2036`.

**Where enriched data lands.** Always inline as a JSON block array in the
final `AIMessage.content`. There is no separate enriched-fields store, no
sidecar metadata, no per-field provenance. The "fact" that a value came from
`search_web` vs. an internal data agent is invisible to the renderer.

**UI surfacing of enrichment.** None today. The renderer treats every block
identically; there's no badge, color, or tooltip distinguishing a primary
result from a web-augmented one. The Ops Room "Run enrichment" drawer button
is wired only to a ticker line.

### 1.4 Web / knowledge enrichment

**Web search.** `search_web` (Tavily) is wired and works, but:
- Returns markdown text (`graph/nodes.py:2010-2032`), not a structured list
  of `{title, url, snippet, ts, confidence}` that downstream code could
  attach to fields.
- No ranking-confidence derivation (e.g., agreement between top-K results).
- No caching — every call hits Tavily.

**Model knowledge.** `_enrich_knowledge`
(`graph/nodes.py:5014-5054`) does parametric-knowledge enrichment with a
fabrication-detection check (`_check_fabrication`,
`graph/nodes.py:4972-4997`), but is not currently invoked from any active
code path (see 1.3.3).

**Gap.** No tool today returns `{value, source_url, source_type, fetched_at,
confidence}`. Every enrichment is "text in, text out".

### 1.5 Validation / quality

- **Structured response wrapper.** `STRUCTURED_RESPONSE_FORMAT` (referenced
  in `cc_graph.py:73-75` boundary, full prompt elsewhere) tells the LLM to
  emit a JSON block array, but is enforced only by client-side parsing
  fallbacks (`cc-renderers.js:62-156` walks through "is this double-encoded?
  is it pandas? is it HTML?" patterns).
- **Inline coercion.** `generate_map` coerces lat/lng with `float(..)` and
  drops malformed entries silently (`nodes.py:1851-1852`). No schema check
  on `regions` beyond `name`/`value`.
- **Answer Quality Gate.** A mini-LLM classifies the final response into
  PASS / GAP_* / ERROR_INFRA / UNCLEAR (`nodes.py:4765-4790`). Today this is
  observe-only — it logs but does not act (`nodes.py:4881-4892`).
- **Fabrication check.** `_check_fabrication` (`nodes.py:4972-4997`) is a
  gate around model-knowledge enrichment, fail-open. Not used in the active
  pipeline.

### 1.6 Reliability / failure handling

- **Map render failure.** `try/catch` around the whole map block render
  (`cc-renderers.js:344-460`). On error the map div is replaced with
  `<p style="color:#ef4444">Map error: ${e.message}</p>` — visible, but no
  retry, no fallback, no telemetry beyond `console.error`.
- **GeoJSON load failure.** Falls back to no choropleth (`cc-renderers.js:292-296`).
  The map still renders without colors. No user-facing warning.
- **Chart render failure.** Similar try/catch
  (`cc-renderers.js:216-219`); error appended as a paragraph after the canvas.
- **Tile failure.** No handling — Leaflet just shows broken tiles. No
  fallback tile source.
- **Tavily failure.** `search_web` returns the error string back to the LLM
  (`nodes.py:2034-2036`), which then has to decide what to say. There's no
  retry, no second-source fallback, no circuit breaker.
- **Data agent failure.** Classified by `_classify_delegation_result`
  (called at `nodes.py:3077`) and may auto-fall-back to an alternative agent
  (`nodes.py:3079-3097`) — this part is well thought out, but lives only on
  the agent side, not the field-level enrichment side.

### 1.7 Caching

- **GeoJSON.** Cached in JS module variable
  `CCRenderers._geoJsonCache` (`cc-renderers.js:272-298`). Process-lifetime
  only.
- **Chart instances / map instances.** Cached in `_chartInstances` /
  `_mapInstances` keyed by counter id (`cc-renderers.js:7-10`,
  `cc-renderers.js:215`, `cc-renderers.js:454`). Used to avoid leaks; not a
  data cache.
- **Web search / agent results.** No caching layer. Identical queries hit
  Tavily / the data agent again.
- **Trace / route memory.** `services/trace_store.py` and the route memory
  module (`command_center/memory/route_memory.py`, referenced from
  `nodes.py:1545-1546`) keep usage history but not the values of enriched
  fields.

### 1.8 Audit trail

- **Trace store.** `TraceStore` in
  `command_center_service/services/trace_store.py:39-162` writes append-only
  JSONL, one file per `trace_id`, under
  `data/traces/{user_id}/{session_id}/{trace_id}.jsonl`. Events include
  `node_start`, `node_end`, `route`, `llm_call`, `tool_start`, `tool_end`,
  `delegate_start`, `delegate_end`. **(WS1, done 2026-05-10)** Truncation
  limits in `tracing.py` raised: system 2k→16k, user 1.5k→32k, response
  3k→24k, tool_calls 1k→16k — provenance walkbacks now keep enough payload
  to be useful. A 100 MB soft per-trace cap constant is declared with a
  TODO for the actual writer-side guard.
- **What's NOT in the trace.** A given visible field — e.g. "Massachusetts:
  $5.1M" on a choropleth — has no traceable line back to the SQL query,
  the tool, or the LLM call that produced it. The trace records that
  `generate_map` was called and what it returned; it does not stamp each
  region with a `source_id` so the user can replay the chain.

---

## 2. Gaps + risks

Worst-case failures **today**, by capability:

**Map points.**
- Tiles are loaded from public OSM in production
  (`cc-renderers.js:349-351`, `ops-room.js:137-140`). Single source, no key,
  no SLA. If OSM rate-limits, the room goes white. There is no offline
  fallback, no second tile source, no air-gap mode.
- The Ops Room dominant map is seeded with **fake fleet data**
  (`ops-room.js:145-159`) and the `ENRICHMENTS · 24H` / `ALERTS · 1H`
  KPIs are **literally hard-coded numbers** the page sets after a successful
  `/api/health` call (`ops-room.js:97-105`). A demo viewer is shown numbers
  that look operational but are not. In an ops-center context this is the
  worst possible failure mode.
- Marker count > a few hundred will start lagging — there's no clustering or
  canvas-mode rendering.

**Charts.**
- A bad value in `block.data` (NaN, null, string in a numeric column)
  produces an unreadable Chart.js axis or a runtime exception caught into a
  red error paragraph (`cc-renderers.js:216-219`). There is no schema check
  on the data before `new Chart(...)`.
- `chartType: "area"` is silently rewritten to `line + fill: true`
  (`cc-renderers.js:184, 195`); user / agent-set chart types beyond the
  five known strings are silently bucketed as `bar`. Off-mode types fail
  silently.

**Data enrichment.**
- The auto-enrichment path is **opt-in (done WS3)** via `CC_ENRICHMENT=auto`.
  In `tools` (default) mode the conversational LLM still drives enrichment;
  the helpers remain available for tooling work.
- `generate_map` extracts geo data via a mini-LLM call
  (`nodes.py:3858-3872`) which can hallucinate coordinates. The
  auto-enrichment geographic helper now cross-checks region names against
  the real geocoder (WS2), so its markers carry real lat/lng with
  `_provenance` source `geocoder`. The base `generate_map` tool is
  unchanged and still LLM-extracted.
- The `web_intelligence` plugin's `geocode_address` tool is now real
  (WS2). It returns either a `status=ok` payload with real coords or
  `status=geocoding_failed`. It never returns `(0.0, 0.0)`.

**Web / knowledge.**
- `search_web` returns markdown — there's no machine-readable source list
  attached to any final block (`nodes.py:2010-2032`). The user sees the
  sources only because the markdown text happens to contain them.
- Tavily key is read from `os.environ.get("TAVILY_API_KEY", "")`
  (`nodes.py:1988`). It is logged as configured/not-configured but the call
  body has the key embedded; if `httpx`/`requests` debug logging is ever
  turned on, the key leaks.

**Validation.**
- No schema validation on inbound block JSON. The renderer treats unknown
  block types by JSON-fencing them (`cc-renderers.js:46-58`) and unknown
  shapes for known types may render partially or empty.

**Reliability.**
- No retries anywhere on the enrichment path.
- No circuit breakers.
- No latency budgets on tool calls — `search_web` has a 15s timeout
  (`nodes.py:2000`); `search_documents` has none visible in the snippet I
  read; `generate_image` is unbounded.
- A single slow tool can stall the chat turn indefinitely (the LLM is
  awaited).

**Audit.**
- Trace events truncate at fixed character limits (`tracing.py`). **(WS1
  done)** Limits were raised so the typical full LLM prompt + response +
  tool I/O fits without truncation: system 16k, user/tool 32k, response
  24k, tool_calls 16k. Anything past that is still elided with `...` —
  audit is still lossy by design, just much less so.
- The mapping from a rendered field → its provenance is now modelled
  (WS4) via the `_provenance` sibling map. See `docs/data-provenance.md`
  for the v1 schema and the source vocabulary.

**What an ops/military user expects but is missing.**
- Per-field source attribution + timestamp + confidence.
- Multi-source corroboration ("Massachusetts: $5.1M (data-agent: $5.1M;
  finance-system: $5.0M; Δ 2%, OK)").
- Freshness contracts (different TTLs for live data vs. cached enrichment).
- Deterministic rendering — given the same data, same colors, same
  labels, every time, on every machine. (Today, the cyan gradient is
  computed from min/max of the *current dataset only*, so the color of
  "California: 5M" depends on what other states are in the dataset —
  intentional but not documented.)
- Failure transparency — when an enrichment fails, the UI should clearly
  show "couldn't enrich" rather than silently dropping.
- Offline-tolerant tiles / reference data.
- Secure transport for enrichment APIs (Tavily call goes over TLS but the
  key is in the request body — fine for Tavily, but no broader audit).
- Display/data-logic separation — currently the renderer makes
  data-shaping decisions (e.g. detecting pandas DataFrame JSON in a text
  block, `cc-renderers.js:96-141`). This is convenience but makes audit
  harder because the data shape can mutate at render time.

---

## 3. "Military grade and reliable" — definition for THIS app

For the CC, this means each of the following is true and verifiable:

1. **Source attribution per field.** Every visible value (a marker label, a
   chart datapoint, a KPI number) is keyed to a `provenance` object:
   `{source_id, source_type, fetched_at, confidence, evidence_url?}`. The
   block format is extended to carry it (see §4). The UI renders a small
   badge/tooltip on every visible field that exposes it.
2. **Multi-source corroboration where possible.** Critical numeric fields
   that ALSO appear in two or more sources are marked corroborated; if
   sources disagree beyond a tolerance, the field is flagged.
3. **Freshness contracts.** Each `source_type` has a default TTL
   (e.g. `internal_db: 1h`, `web_search: 24h`, `model_knowledge: never_fresh`)
   and the UI shows the age in plain English.
4. **Deterministic rendering.** Same blocks → same pixels. Color scales
   come from a fixed registry, not derived per-dataset; user-controlled
   "scale-to-this-dataset" mode is opt-in and labeled as such.
5. **Failure transparency.** Enrichment failures produce visible "couldn't
   enrich" placeholders with the reason, NOT silently dropped fields.
6. **Audit trail.** A user can click a field, get a "Show source" panel
   that shows: the chain of tools / LLM calls that produced it, the
   timestamps, the raw evidence, and (if applicable) the SQL / API URL.
7. **Secure transport.** All enrichment calls over TLS; API keys never in
   request bodies that could be logged; secrets redacted from traces.
8. **Offline-tolerant.** Map tiles for the most-used regions cached
   locally; reference data (us-states.geojson, gazetteer) ships with the
   app; agent runs fall back to last-known state with a clear "stale" mark
   when offline.
9. **Performance contracts.** Each enrichment tool has a hard latency
   budget; over-budget calls abort gracefully and the field is marked
   "enrichment skipped (timeout)".

---

## 4. Recommendations

### Quick wins (single sprint)

**Q1. Block-level provenance schema, opt-in.** Add an optional `provenance`
key to each block, with sub-keys per field for table/chart/map blocks. The
renderer reads it and renders a tiny badge. Existing blocks without
provenance render unchanged.
- **Cost:** ~2-3 days. Touches `cc-renderers.js`, the block-emitting code in
  `nodes.py` (`generate_map`, `query_data_agent` rich-content path,
  `search_web`), and a small block-schema module.
- **Verification:** snapshot tests on rendered HTML; one E2E test where a
  map block with provenance shows a badge.

**Q2. Sandbox the OSM tile dependency.** Add an environment-level tile URL
template (`CC_MAP_TILE_URL`) defaulting to OSM; allow a self-hosted tile
proxy + an offline mbtiles fallback. Display a "tiles offline" banner if
both fail.
- **Cost:** ~1-2 days. Touches both `cc-renderers.js:349-351` and
  `ops-room.js:137-140`.
- **Verification:** unit test on URL resolution; manual offline test.

**Q3. Replace the Ops Room hard-coded KPIs and seed fleet with explicit
"DEMO MODE" markers OR a real endpoint.** The current behavior — fake
numbers that look authoritative — is the single most operationally
dangerous thing in the app today. Either implement
`GET /api/inspect/active-points`, `GET /api/inspect/feed`,
`GET /api/inspect/kpis` (the `TODO(ops-wire)` callouts in
`ops-room.js`), or wrap the demo data in an explicit
`if (DEMO_MODE)` flag and overlay a "DEMO" watermark.
- **Cost:** 1 sprint for either path. Demo-watermark first; real wiring
  next.
- **Verification:** any ops-center person can tell at a glance whether a
  number is real.

**Q4. Make `search_web` return structured results.** Have the tool also
return a JSON sidecar (sources list with `{title, url, snippet, fetched_at,
rank}`) that the LLM can pass into a downstream block's `provenance` field.
- **Cost:** ~1 day. Touches `nodes.py:1970-2036`.

**Q5. Redact API keys from traces.** Audit every `trace_log` /
`trace_llm_call` call site to ensure secret-bearing payloads (Tavily query
body, etc.) are filtered before write. Add a small `_redact()` helper in
`tracing.py`.
- **Cost:** ~1 day.

**Q6. Latency budgets on every tool.** Add a per-tool default timeout
(e.g. `TOOL_LATENCY_BUDGETS = {"search_web": 8, "search_documents": 30,
"generate_map": 5, ...}`) and wrap every `await tool_fn.ainvoke(...)` in
`asyncio.wait_for`. On timeout, return a structured "enrichment skipped:
budget exceeded" message that downstream code can render as a placeholder.
- **Cost:** ~1-2 days. Mostly mechanical changes around the
  `for tc in response.tool_calls:` loop in `converse`
  (`nodes.py:2241+`).

### Medium lift (multi-sprint refactors)

**M1. Introduce an Enrichment Service.** A new module
`command_center/enrichment/` with:
- A `Source` interface — `internal_db`, `web_search`, `model_knowledge`,
  `geocoder`, etc.
- A `Pipeline` runner that takes a field key, walks a configurable chain of
  sources, captures every step (success/failure/value) and emits a
  `ProvenanceRecord`.
- Pluggable corroboration rules.
- Cache layer (per-source TTL, per-user vs global key spaces).
- Hard latency budget per source + global pipeline budget.
- This replaces the ad-hoc enrichment scattered across `nodes.py`.
- **Cost:** 2-3 weeks for v1.
- **Data-model change:** new `ProvenanceRecord` table or JSON sidecar; new
  `provenance` field on every block-producing tool's return value.
- **Verification:** unit test each source adapter; integration test the
  full pipeline.

**M2. Split display logic from data logic in the renderer.** Today
`_renderText` contains four-deep heuristics that detect pandas/array/HTML
inside a text block (`cc-renderers.js:62-156`). Pull this detection into a
server-side step that emits the right block type the first time. The
renderer should only render — no interpretation. This makes audit
deterministic.
- **Cost:** ~1 week. Touches both server-side block emitters and
  `cc-renderers.js`.
- **Verification:** add a "no double-encoded JSON in `text` blocks" test
  that walks the trace store.

**M3. Map-rendering layer with deterministic styling + clustering.**
Replace the hand-rolled cyan gradient with a palette registry (e.g.
ColorBrewer schemes by data type), and add Leaflet.markercluster for point
counts > N. Add the same to the Ops Room dominant map.
- **Cost:** ~1 week.
- **Verification:** snapshot tests on a fixed dataset.

**M4. Explicit "verify with web search" tool.** Today the LLM either calls
`search_web` or trusts the data agent. Add a `corroborate(field, sources)`
tool (see §5) so the LLM can ask "is this number consistent with public
sources?" before showing it.
- **Cost:** 1-2 weeks (depends on what sources are wired in).

**M5. Trace store: store full responses for tagged events.** Today the
`tracing.py` truncates at 3k chars, which is fine for performance but
fatal for audit. Add a "full-archive" mode for events of type
`tool_end` and `llm_call` where the payload exceeds the limit — the
archive is a separate file alongside the JSONL trace.
- **Cost:** ~3 days.

### Strategic (architectural)

**S1. Provenance baked into the data model.** Every block carries a
required `provenance` key; the renderer ENFORCES it (warns when missing).
Move from "JSON blocks" to a versioned "block envelope" with provenance,
schema_version, and a hash that lets the audit replay verify the rendered
state didn't drift from the recorded state.
- **Cost:** 4-6 weeks; touches every block emitter and the renderer.

**S2. Multi-stage fan-out enrichment pipeline.** A pipeline that runs N
sources in parallel against a field key and uses corroboration to assign
confidence. Failures don't stop the pipeline; they're recorded.
- **Cost:** 3-4 weeks atop M1.
- **Verification:** chaos-test the pipeline (kill each source one at a
  time, confirm the user-visible output stays sensible).

**S3. Offline mode.** A `CC_OFFLINE` env that:
- Switches tile URL to a local mbtiles file
- Disables `search_web` (returns a "offline — web disabled" enrichment
  failure)
- Returns last-known-good cached agent results, marked stale
- Hides any block whose provenance hasn't been refreshed within the user-set
  freshness window
- **Cost:** 4-8 weeks. Major operational hardening.

---

## 5. AI enrichment design — web + own knowledge

This is the surface I'd add or reshape to fix the gaps in §1.3 / §1.4.

### 5.1 Tool surface

```python
# command_center/enrichment/tools.py — new module

@dataclass
class SourceRef:
    source_id: str               # e.g. "tavily://search?q=..."
    source_type: str             # "internal_db" | "web_search" | "model_knowledge" | "geocoder"
    fetched_at: str              # ISO timestamp
    confidence: float            # 0.0..1.0
    evidence_url: str | None
    raw_snippet: str | None      # bounded, e.g. <= 500 chars

@dataclass
class EnrichmentResult:
    value: Any
    source: SourceRef
    notes: str | None = None     # "extracted from result #2", "median of 3 results", etc.

@lc_tool
async def web_search_enrich(query: str, context: str = "",
                            max_sources: int = 5) -> str:
    """Search the public web for evidence relevant to `query`.

    Returns a JSON-encoded list of EnrichmentResult objects so the caller
    can attach them as field-level provenance, NOT as text.

    Confidence is derived from: (a) number of agreeing sources within the
    top max_sources, (b) source quality scoring (gov/edu/recognized
    publishers > unknown), (c) recency.
    """
    ...

@lc_tool
async def knowledge_enrich(query: str, context: str = "") -> str:
    """Answer `query` from the model's parametric knowledge.

    Returns an EnrichmentResult with source_type='model_knowledge'.
    Confidence is the model's own self-reported certainty (asked
    explicitly in the prompt). The caller MUST surface this as a marked
    "untrusted" source — the renderer reads source_type and styles
    accordingly.

    Refuses (returns null with a noted failure reason) when:
      - The query is about realtime/live data
      - The query is about specific business facts ("our Q3 revenue")
      - The model self-reports < 0.5 confidence
    """
    ...

@lc_tool
async def corroborate(field: str, value: str,
                      proposed_sources: list[SourceRef]) -> str:
    """Given a field name, a candidate value, and >=1 proposed sources,
    check each source for agreement.

    Returns:
      {
        "agreement_count": int,
        "disagreement_count": int,
        "notes": [str],          # per-source explanation
        "confidence_delta": float, # how much to bump/cut original confidence
      }
    """
    ...

@lc_tool
async def geocode_enrich(address: str) -> str:
    """Convert an address to {lat, lng} via a real geocoding service.

    Replaces the stub in plugins/web_intelligence/handler.py. Returns an
    EnrichmentResult with source_type='geocoder' and an evidence_url to
    the OSM/Nominatim or Mapbox response.
    """
    ...
```

### 5.2 Enrichment pipeline

```python
# command_center/enrichment/pipeline.py — new module

class EnrichmentPipeline:
    """Runs a configurable chain of sources against a field key.

    Stage order is configurable per field, default:
      internal_db -> web_search -> model_knowledge

    Each stage's outcome is recorded; the first non-null result with
    confidence >= min_confidence wins for the value, but ALL stage
    results are persisted in the audit trail so the user can later see
    how the pipeline arrived at the answer.
    """
    def __init__(self, sources: list[SourceAdapter],
                 budget: PipelineBudget,
                 corroborator: Corroborator | None = None,
                 audit_sink: TraceStore | None = None):
        ...

    async def enrich(self, field: FieldKey, ctx: EnrichmentContext) -> FieldOutcome:
        record = ProvenanceRecord(field=field, attempts=[])
        chosen: EnrichmentResult | None = None
        for src in self.sources:
            if self.budget.exhausted():
                record.attempts.append(self._skipped(src, "budget_exhausted"))
                break
            try:
                async with self.budget.scope_for(src):
                    res = await src.enrich(field, ctx)
                record.attempts.append(self._ok(src, res))
                if chosen is None and res.source.confidence >= src.min_confidence:
                    chosen = res
            except asyncio.TimeoutError:
                record.attempts.append(self._timeout(src))
            except Exception as e:
                record.attempts.append(self._error(src, e))
        if chosen and self.corroborator and len(record.attempts) > 1:
            # Cross-check chosen against the other attempts
            chosen = self.corroborator.adjust(chosen, record.attempts)
        if self.audit_sink:
            self.audit_sink.write(record)
        return FieldOutcome(value=chosen.value if chosen else None,
                            provenance=record,
                            failed=chosen is None)
```

### 5.3 UI surfacing — primary vs. enriched vs. model-knowledge

The renderer reads `provenance.source_type` and applies a class:

| source_type      | badge color  | tooltip text                        |
|------------------|--------------|-------------------------------------|
| internal_db      | green        | "Source: internal data agent (1h fresh)" |
| web_search       | cyan         | "Source: web — Tavily, 5m fresh"   |
| model_knowledge  | amber        | "Source: model knowledge (untrusted, no live evidence)" |
| geocoder         | green        | "Source: geocoder (1d fresh)"      |
| corroborated     | green-double | "2 sources agree"                  |
| failed           | red          | "Enrichment failed: {reason}"      |

A click on the badge opens the per-field provenance panel.

### 5.4 Cost / latency budgets

| tier            | per-call budget | global pipeline budget |
|-----------------|-----------------|-----------------------|
| internal_db     | 5s              | 15s                   |
| web_search      | 8s              | (counts toward global) |
| model_knowledge | 3s              | (counts toward global) |
| geocoder        | 4s              | (counts toward global) |

When the global pipeline budget is exhausted, remaining sources are
recorded as `skipped: budget_exhausted` and the field shows whatever
the pipeline got so far (or "enrichment incomplete" if nothing landed).

---

## 6. Phased rollout

**Phase A — Foundation (4-6 weeks).**
- Q1 (block-level provenance schema)
- Q5 (key redaction in traces)
- Q6 (per-tool latency budgets)
- M5 (full-archive trace mode)
- New `command_center/enrichment/` skeleton (the SourceRef / ProvenanceRecord
  data classes; no behavioral change yet).
- Renderer learns to display provenance badges (badges only on blocks
  that carry the new field; everything else unchanged).

**Phase B — Web enrichment (4-6 weeks).**
- M1 (Enrichment Service v1, with `internal_db` and `web_search` adapters).
- New tools: `web_search_enrich`, `knowledge_enrich`, `corroborate`,
  `geocode_enrich`. Replace the existing `search_web` LLM tool with
  `web_search_enrich` and have the LLM use it for any field-level
  augmentation.
- Re-enable the answer_quality_gate path on top of the pipeline (the gate
  proposes a field, the pipeline tries to enrich it, the user sees the
  result with provenance).
- M4 (corroborate tool surfaced to the LLM).

**Phase C — Hardening (4-8 weeks).**
- Q2 (tile sandbox + offline fallback).
- Q3 (Ops Room real wiring; end the demo data).
- M2 (display/data-logic split in the renderer).
- M3 (deterministic palette + clustering).
- S3 (offline mode).
- Chaos / latency tests on the enrichment pipeline.
- Performance budget: a single chat turn never exceeds 30s end to end;
  enrichments past 8s run async and stream into the rendered block as
  they arrive.

---

## Source files cited (for verification)

- `command_center_service/main.py:1-269`
- `command_center_service/cc_config.py:261-292`
- `command_center_service/static/index.html:13-29`
- `command_center_service/static/js/cc-renderers.js:1-773` (whole file)
- `command_center_service/static/js/ops-room.js:1-520` (whole file)
- `command_center_service/static/CC_OPS_ROOM_DESIGN.md:1-253`
- `command_center_service/static/data/us-states.geojson` (referenced from
  cc-renderers.js:285)
- `command_center_service/graph/cc_graph.py:1-148`
- `command_center_service/graph/nodes.py:1372-2219` (tool surface)
- `command_center_service/graph/nodes.py:1810-1912` (`generate_map`)
- `command_center_service/graph/nodes.py:1970-2036` (`search_web`/Tavily)
- `command_center_service/graph/nodes.py:2899-...` (`gather_data`)
- `command_center_service/graph/nodes.py:4279-4288` (`render_response`)
- `command_center_service/graph/nodes.py:4793-4892` (Answer Quality Gate)
- `command_center_service/graph/nodes.py:4895-5054` (disabled enrichment helpers)
- `command_center_service/graph/tracing.py:1-352`
- `command_center_service/services/trace_store.py:1-162`
- `command_center_service/plugins/web_intelligence/manifest.json:1-13`
- `command_center_service/plugins/web_intelligence/handler.py:1-87`
- `command_center_service/routes/tools.py:1-47`
- `tests/TEST_MAP.md:50-53` (no test rows for renderer / enrichment paths)
