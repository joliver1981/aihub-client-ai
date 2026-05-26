# Module 32: Ops Command Room — Live Flow

**Purpose:** End-to-end smoke test of the experimental Ops Command Room (`/ops`) and its supporting API endpoints. Verifies that KPI tiles increment as new chats arrive, the feed populates with recent traces, and the SSE stream emits live events when the chat pipeline runs.

**Time estimate:** 15–25 minutes

**Prerequisites:**
- Command Center Service running. Default port is `5091`; environment variable `CC_BASE_URL` overrides (e.g. `http://10.0.0.7:5091`).
- Main app reachable for the chat pipeline backend (delegations, agents).
- A developer-role user logged in if the proxy enforces auth (the routes themselves are public — see `routes/ops.py` docstring).
- Browser with DevTools open for SSE inspection (Chrome / Edge / Firefox).
- `curl` available on the local machine for header / event-stream sanity checks.

> **Note on isolation:** the ops endpoints read from the same on-disk
> `data/traces/` tree that the production chat route writes. Run this
> module on a non-production tenant if you don't want to pollute the
> trace archive.

---

## OPS-1: Land on the Ops Room
**Action:** Navigate to `/ops` in the browser.
**Expected:**
- Page loads (HTTP 200) and serves `static/ops_room.html`. If `CC_UI` is unset, `/ops` is always available regardless.
- The dominant map renders centered on a default view.
- KPI tiles are visible: **Sessions**, **Traces (24h)**, **In Flight**, **Map Pts (session)**. The first three are populated from `/api/ops/kpis`; map pts is `in 0 blocks` until a chat with a map block is run.
- The ticker shows initial entries seeded from `/api/ops/feed`. If the trace dir is empty, the ticker reads "no recent activity".
**Pass criteria:** 🟢 Page renders, no JS console errors, all KPI tiles show a numeric value (even 0), no 5xx in network panel.

---

## OPS-2: Verify `/api/ops/kpis` baseline
**Action:** In a terminal, hit `curl -s "$CC_BASE_URL/api/ops/kpis" | jq .`.
**Expected:** JSON shape matches:
```
{
  "sessions": {"value": <int>, "trend": "...", "tone": "info"},
  "traces_24h": {"value": <int>, "trend": "last 24h", "tone": "info"},
  "in_flight": {"value": 0, "trend": "live", "tone": "warning"},
  "map_pts_session": {"value": 0, "trend": "in 0 blocks", "tone": "ok"},
  "graph_ready": true,
  "ts": "<iso 8601>"
}
```
**Pass criteria:** 🟢 200, shape matches, `graph_ready` is `true`, `in_flight.value` is 0 (no active chat).

---

## OPS-3: Verify `/api/ops/feed` baseline
**Action:** `curl -s "$CC_BASE_URL/api/ops/feed?limit=10" | jq '.entries | length'`.
**Expected:** Returns a list of recent trace summary entries (or `0` on a freshly-installed system).
**Pass criteria:** 🟢 200, response is `{"entries": [...]}`, each entry has `trace_id`, `ts`, `kind`, `text`.

---

## OPS-4: Subscribe to `/api/ops/stream`
**Action:** In a fresh terminal:
```
curl -N -H "Accept: text/event-stream" "$CC_BASE_URL/api/ops/stream"
```
**Expected:** First chunk is `event: ready` with a timestamp payload. The stream stays open. Every 15s a comment line (`: keepalive`) appears.
**Pass criteria:** 🟢 Stream opens, `event: ready` arrives within 2s, no buffering (response is unbuffered chunked).

> Leave this terminal open for the next steps.

---

## OPS-5: Send a chat → ops feed updates
**Action:** From the browser at `/ops`, or via the classic UI at `/classic`, send a simple message: "What time is it?".
**Expected (in the curl terminal from OPS-4):**
- One `event: ops` payload with `kind: "info"` and text starting with `chat · request open (1 in flight)`.
- One `event: ops` payload with `kind: "info"` and text matching `trace · <8 hex chars> · What time is it?`.
- After the chat completes, an `event: ops` payload with `kind: "ok"` and text matching `trace · <8 hex chars> · done (<N> blocks)`.
- Finally `chat · request closed (0 in flight)`.
**Pass criteria:** 🟢 All four events arrive within 30s. The trace_id prefix matches between the "trace ·" and "done" lines.

---

## OPS-6: KPI tiles increment
**Action:** Refresh the Ops Room page or wait for the auto-refresh tick (~5s). Compare KPI tiles to OPS-2 baseline.
**Expected:** `sessions.value` ≥ baseline (+0 if reusing an existing session, +1 for a fresh session). `traces_24h.value` increased by ≥1. `in_flight.value` is 0 again.
**Pass criteria:** 🟢 traces_24h delta is at least 1.

---

## OPS-7: Trigger a chat that produces a map block
**Action:** Send the prompt "show me sales by region on a map" (or any prompt that should call the `geocode_address` tool / produce a map block). Wait for the response to render with a map.
**Expected:** Map block renders on the Ops Room dominant map (markers appear at their geocoded coordinates — NEVER at `(0.0, 0.0)`). `map_pts_session` KPI tile updates within ~5s to a non-zero value.
**Pass criteria:** 🟢 Markers visible at real coordinates; `map_pts_session.value > 0`; `map_pts_session.trend` reads `in 1 block` (or more).

---

## OPS-8: `/api/ops/session-points` exposes the markers with provenance
**Action:** Get the session_id from the URL hash (or DevTools `session` event). Then:
```
curl -s "$CC_BASE_URL/api/ops/session-points?session_id=$SESSION_ID" | jq .
```
**Expected:** `points` list with ≥1 entry. Each point has `id` (e.g. `2.0.0`), `lat`/`lng` (real floats, not 0.0), `kind` (slug of map title), `name`, `_provenance` containing `lat` + `lng` keys with `source: "geocoder"`.
**Pass criteria:** 🟢 Coordinates non-zero, source is `geocoder` (not `model_knowledge`), `confidence` ≤ 1.0.

---

## OPS-9: Concurrent chats increment IN FLIGHT
**Action:** Open three browser tabs to `/ops` (or `/classic`) and post a slow prompt to each within ~2 seconds. Watch the KPI tile.
**Expected:** `in_flight.value` reaches 3 (or whatever # of concurrent requests) while they're processing, then returns to 0 as each finishes.
**Pass criteria:** 🟢 Tile shows a value > 1 at some point during the burst; returns to 0 after all complete.

---

## OPS-10: Error path emits an `alert` event to ops stream
**Action:** Trigger a chat that will fail (e.g. send a request with a malformed `user_context` that the graph rejects, or send during a known-bad downstream condition).
**Expected (in the OPS-4 curl terminal):** One `event: ops` with `kind: "alert"` and text matching `trace · <id> · error · <message>`.
**Pass criteria:** 🟢 Alert event appears in the stream. The classic UI still recovers (no crash).

---

## OPS-11: Disconnect / reconnect handling
**Action:** Ctrl-C the OPS-4 terminal, then re-run the same `curl -N` command. Send another chat.
**Expected:** New `event: ready` arrives. Events from the *new* chat are visible. (New subscribers do NOT see history per the routes/ops.py contract — `/api/ops/feed` is the seeding source.)
**Pass criteria:** 🟢 Stream reconnects cleanly. No duplicates from before the disconnect.

---

## Cleanup
- Delete the throwaway test sessions via `DELETE /api/sessions/<id>` if you don't want them in the history list.
- Trace files under `command_center_service/data/traces/<user_id>/<session_id>/<trace_id>.jsonl` can be pruned with the existing rotation script (none is automated as of this writing — see TODO in `routes/inspect.py`).

## Known gaps (for the runner to be aware of)
- `/api/ops/*` is **unauthenticated** by design today (see `routes/ops.py` docstring). The reverse proxy is the gate. A non-admin user can read KPI counts and recent trace IDs.
- `map_pts_session` only counts markers for the **session_id passed in the query string**. There is no "all sessions" aggregate — the Ops Room tile reflects only the currently-selected session in the side chat.
- The trace scan is capped at `_TRACE_SCAN_CAP = 5000` files. On very busy servers the 24h count may under-report; this is documented but not flagged in the response payload today.
