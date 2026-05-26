# Command Center · Ops Command Room — design notes

Experimental "next-gen" UI for the Command Center agent. Lives side-by-side
with the classic UI; an env var picks which one renders at `/`.

## Toggle

The selector lives in `command_center_service/cc_config.py`:

```
CC_UI = os.getenv("CC_UI", "classic").strip().lower()
CC_UI_NEXT_GEN = CC_UI == "next_gen"
```

Defaults to `classic` so an unset / empty / mistyped value preserves the
historical behavior. The `@app.get("/")` handler in
`command_center_service/main.py` consults `CC_UI_NEXT_GEN` and serves
`static/ops_room.html` when true; otherwise it serves `static/index.html`
exactly the way it always did.

Two always-on routes are also exposed for QA:
- `GET /classic` — always serves `index.html`
- `GET /ops`     — always serves `ops_room.html`

This lets you A/B the two pages on the same running server without flipping
the env var.

`.env.template` documents `CC_UI` near the other `CC_*` settings.

## Files

```
command_center_service/
  cc_config.py                          # +CC_UI / CC_UI_NEXT_GEN  (modified)
  main.py                               # +CC_UI branch in /, new /classic, /ops  (modified)
  static/
    index.html                          # CLASSIC — UNCHANGED
    css/command-center.css              # CLASSIC — UNCHANGED
    js/command-center.js                # CLASSIC — UNCHANGED
    js/cc-renderers.js                  # CLASSIC — UNCHANGED  (reused)
    js/cc-memory.js                     # CLASSIC — UNCHANGED  (reused)
    ops_room.html                       # NEW
    css/ops-room.css                    # NEW
    js/ops-room.js                      # NEW
    CC_OPS_ROOM_DESIGN.md               # this file
.env.template                           # +CC_UI section  (modified)
```

## Layout (post-wiring)

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ⌬ COMMAND CENTER · OPS ROOM    SESSION: …    READY  + NEW  SESSIONS      │  48px
│                                       ← SWITCH TO CLASSIC   CC_UI=classic│
├──────────────────────────────────────────────────────────────────────────┤
│ SESSIONS │ TRACES · 24H │ IN FLIGHT │ MAP PTS · SESSION                  │  82px
├────────────────────────────────────────────────────┬─────────────────────┤
│                                                    │ CONVERSATION │TRACE│
│            ┌─[▤ LAYERS]                            │─────────────────────│
│                                                    │                     │
│             FULL-BLEED LEAFLET MAP (dark)          │                     │
│           (markers from current session's          │      messages       │
│            persisted map blocks ONLY)              │      (CCRenderers   │
│                                                    │       blocks render │
│                  • • • points                      │       inline here)  │
│                                                    │                     │
│   ┌──── selection drawer (slides up on click)      │                     │
│   │ POINT: <name>                                  │                     │
│   │ id … kind … lat,lng … detail … from … session  │                     │
│   │ [Ask about this point] [Copy as JSON] [Trace]  │                     │
│   └──────────────────────────────────────────────  │                     │
│                                                    │ [📎] [textarea] SEND│
├────────────────────────────────────────────────────┴─────────────────────┤
│ ● LIVE FEED  12:14:02 [info] trace · ab12c… · "show…"  …                47│  46px
└──────────────────────────────────────────────────────────────────────────┘
```

The KPI strip is 4 tiles wide (was 6). The two cut tiles (`ALERTS · 1H`,
`COVERAGE`, `ACTIVE POINTS`, `ENRICHMENTS · 24H`) had no real source. The
remaining four are computed in `routes/ops.py: kpis()`:

* `SESSIONS`        — `len(session_mgr.list_sessions())`
* `TRACES · 24H`    — count of `*.jsonl` under `data/traces/` modified in the last 24h
* `IN FLIGHT`       — process-local counter incremented by `routes/chat.py` while a chat SSE is open
* `MAP PTS · SESSION` — distinct lat/lng markers found in the current session's persisted map blocks

`AGENT RUNS` is no longer a top-bar KPI; the existing TRACE pane already
shows the task graph in detail and `_showTasks` still flips the sidebar
to that pane on demand.

Rough proportions on a 1440px-wide viewport:
- Map stage: ~71% of horizontal space (1440 − 420 sidebar)
- Chat sidebar: 420px (collapses to 360 at <1100px wide; stacks below map at <900px)
- Map vertical share: 100% of the middle row (`1fr` between the 82px KPI strip and the 46px ticker)

## Component contract

The page is a thin shell that **reuses** the classic CC orchestrator.
`ops_room.html` mounts every DOM ID `command-center.js` and `cc-memory.js`
expect, so the classic stack runs **unchanged** under it.

| Existing piece              | How ops room reuses it                                    |
| --------------------------- | --------------------------------------------------------- |
| `CC` (command-center.js)    | Drives chat, sessions, SSE; mounted as-is. ops-room.js monkey-patches `CC._handleEvent`, `CC._setStatus`, `CC._showTasks` additively to mirror events into the ticker / KPI tiles. Original logic always runs first. |
| `CCRenderers`               | Renders chart / table / kpi / map / artifact / image content blocks inside the chat sidebar `#messages` container. Identical styling. |
| `CCMemory`                  | Suggestion chips render into `#suggestion-chips` inside the TRACE tab. Memory modal is mounted as a top-level overlay. |
| `command-center.css` tokens | Reused via `<link>`; ops-room.css introduces new `--ops-*` tokens but keeps `--cc-accent` (cyan) so renderer styles stay consistent. |

Net new code (built fresh in ops room):
- KPI tile strip (`OpsRoom._renderKpis` + `_setKpi` + `_refreshKpis`)
- Dominant Leaflet map (`OpsRoom._initMap`) — separate map instance from the per-message renderer maps; uses the same Leaflet library
- Layer / filter overlay (`OpsRoom._initLayerPanel` + `_applyLayerFilters`)
- Selection drawer (`OpsRoom._showSelection` / `_handleDrawerAction`)
- Live ticker (`OpsRoom._tickerInit` / `_addTick`)
- Tab switching (`OpsRoom._showChatTab`)
- Sessions overlay (`OpsRoom.toggleSessionList`) — replaces the always-on left rail with an on-demand panel

## Required DOM IDs (compat surface)

If you refactor the layout, keep these IDs reachable somewhere in the page
so `command-center.js` and `cc-memory.js` keep working:

```
#chat-title          — <span> in topbar
#status-indicator    — <span class="cc-status"> in topbar
#messages            — chat scroll container
#user-input          — <textarea> for chat input
.cc-btn-send         — send button (class hook used by CC)
#btn-attach          — file attach button
#file-input          — <input type=file>
#staged-files        — staged-uploads container
#drop-overlay        — drag-drop overlay container
#task-list           — task graph mount (in TRACE tab)
#builder-log         — builder log mount (in TRACE tab)
#builder-log-section — builder log section wrapper
#suggestion-chips    — suggestion chips mount
#suggestions-clear-all — clear-all button
#session-list        — session list mount
#sidebar             — kept as offscreen shim (CC reads it)
#right-panel         — kept as offscreen shim (CC reads it)
#memory-modal        — memory manager modal root (with #memory-modal-body, #memory-clear-all-btn)
```

The `ops-compat-hidden` block at the bottom of `ops_room.html` parks the
IDs that no longer have a visible home (`#sidebar`, `#right-panel`,
`#memory-modal`). The memory modal CSS lifts itself back to a fixed
overlay when `display:flex` is set on it by `CCMemory.openManager()`.

## CC event mirror

```
CC._handleEvent(data)   →  ticker entry per `data.phase`
                        →  ticker entry per blocks types list
                        →  KPI bump (enrichments_24h) on map/kpi/chart blocks
                        →  ticker entry per builder_log / trace events

CC._setStatus(state, t) →  IN FLIGHT KPI = 1 / 0

CC._showTasks(tasks)    →  AGENT RUNS KPI = total, "<n> in flight"
                        →  auto-switches the chat sidebar to the TRACE tab
```

These patches are additive: the original method runs first inside a
captured closure (`origHandle`, `origSetStatus`, `origShowTasks`), so
classic behavior is preserved 1:1.

## Wiring summary (every panel → its real CC source)

The audit + wiring pass replaced the original `TODO(ops-wire)` stubs.
There are no `TODO(ops-wire)` tags left in `ops-room.js`. Each visible
element traces back to a concrete CC primitive:

| Element                       | Wired to                                              | Source primitive                              |
| ----------------------------- | ----------------------------------------------------- | --------------------------------------------- |
| KPI · SESSIONS                | `GET /api/ops/kpis`                                   | `SessionManager.list_sessions()`              |
| KPI · TRACES · 24H            | `GET /api/ops/kpis`                                   | `data/traces/**/*.jsonl` mtime scan, 24h cutoff |
| KPI · IN FLIGHT               | `GET /api/ops/kpis` + SSE `ops` events                | In-process counter bumped by `routes/chat.py` |
| KPI · MAP PTS · SESSION       | `GET /api/ops/kpis?session_id=…`                      | Markers parsed out of session's persisted assistant messages |
| Map markers                   | `GET /api/ops/session-points?session_id=…`            | `map`-typed content blocks in this session    |
| Marker categories (filter)    | derived from point.kind values returned above         | (no separate config — categories appear as map blocks land) |
| Selection drawer fields       | the point object returned by `/api/ops/session-points` | message_index + block title + lat/lng         |
| Selection drawer provenance   | per-marker entries from the block's `_provenance` map | `provenance.attach_to_block()` (WS4)         |
| Selection · Open trace        | `/static/inspect.html?trace_id=<CC.lastTraceId>…`     | Existing inspector page                       |
| Live ticker (initial)         | `GET /api/ops/feed?limit=10`                          | First-line scan of recent trace files         |
| Live ticker (live)            | `GET /api/ops/stream` (SSE)                           | `routes/ops.py: ops_broadcaster.publish` from the chat pipeline |
| Live ticker (per-tab phases)  | Mirror of `CC._handleEvent`'s phase / blocks events   | The classic CC orchestrator                   |
| Sessions overlay              | `CC.loadSessions()` (already wired in classic)        | `/api/sessions`                               |
| CONVERSATION + TRACE tabs     | classic CC's `#messages`, `#task-list`, `#suggestion-chips` | `command-center.js` + `cc-memory.js`     |
| `↪ SWITCH TO CLASSIC` button  | hard link `<a href="/classic">`                       | `main.py: @app.get("/classic")`               |
| Reciprocal "Try Ops Room"     | `<a href="/ops">` in classic header                   | `main.py: @app.get("/ops")`                   |

### REMOVED

* Time-window selector — there is no historical store of points; the
  CC's map blocks live per-message in chat history. A "last 24h" toggle
  would have been theatrical.
* "Density heat" / "Agent reach" / "Alert pulses" overlays — none of
  these are CC capabilities today.
* "Run enrichment" drawer action — there is no per-field
  re-enrichment-on-demand endpoint. WS3's auto-enrichment runs as part
  of the next conversation turn, not as a per-point trigger. Replaced
  with "Copy as JSON" so the user can paste the raw point payload into
  a follow-up question.
* "Linked agents" drawer row — not tracked anywhere.
* `ACTIVE POINTS` KPI tile — would have been a sum across all sessions,
  which would require either a parallel store (no) or a full scan
  (expensive). Replaced with `MAP PTS · SESSION`.
* `ALERTS · 1H` KPI tile — no alerting subsystem exists.
* `ENRICHMENTS · 24H` KPI tile — enrichments today are inline in the
  agent turn, not separately counted.
* `COVERAGE` KPI tile — coverage of what against what?
* `AGENT RUNS` KPI tile — duplicates the TRACE pane's task graph.
* Mock heartbeat that pushed fake "all services nominal" lines into the
  ticker every 60s.

## Aesthetic decisions

- Dark, mil-spec, flat — no gradients, no rounded corners, hairline borders
  using `--ops-grid-line` tokens.
- Limited palette: charcoal/slate neutrals + cyan accent (kept consistent
  with classic `--cc-accent`), amber for warnings, red for alerts,
  green for OK.
- Body text 16px (slightly larger than classic 14-15px) for readability
  at distance — this is meant to be glanceable from across a room.
- Animations: only the alert marker pulse, the new-tick flash, and the
  drawer slide-up. No bouncy / elastic easings.
- Typography: Outfit (sans) for prose + JetBrains Mono for IDs, KPI
  values, and ticker timestamps. Both already loaded by the classic page.

## What changed in the existing CC UI

Nothing — `index.html`, `command-center.css`, `command-center.js`,
`cc-renderers.js`, `cc-memory.js` are byte-identical to before.

The only modifications outside the new files are:
1. `cc_config.py` — additive `CC_UI` / `CC_UI_NEXT_GEN` constants
2. `main.py` — `@app.get("/")` now branches on `CC_UI_NEXT_GEN`; added two
   new always-on routes `/classic` and `/ops` for side-by-side QA
3. `.env.template` — new `# COMMAND CENTER SERVICE` section documenting `CC_UI`

## Manual smoke test

```bash
# 1. Default (CC_UI unset) — classic UI must render at /
cd command_center_service
uvicorn main:app --port 5091
# open http://localhost:5091/  → classic UI
# open http://localhost:5091/classic → classic UI
# open http://localhost:5091/ops → ops room (always works)

# 2. Flip the toggle
CC_UI=next_gen uvicorn main:app --port 5091
# open http://localhost:5091/  → ops room
# open http://localhost:5091/classic → classic UI (unchanged)

# 3. Bad value falls back to classic
CC_UI=garbage uvicorn main:app --port 5091
# open http://localhost:5091/  → classic UI
```

In-page checks for the ops room:
1. Send a chat message — the IN FLIGHT KPI tile flashes 1 while the
   request is open and returns to 0 when it completes.
2. Each phase event from the SSE stream produces a flash on the live
   ticker; click a ticker entry to jump (to the trace pane, or to a map
   marker if the entry has a `pointId`).
3. Click any of the 12 seed markers — selection drawer slides up over
   the bottom 38vh of the map. ESC or the ✕ button dismisses it.
4. Layers panel collapses by default in the top-right; click "▤ LAYERS"
   to expand. Toggling category checkboxes hides the matching markers.
5. SESSIONS button opens the sessions overlay; selecting a session
   loads it into the chat sidebar via the existing `CC.loadSession` path
   and closes the overlay.

## v2 candidate list — recommended changes for classic CC UI

(Not implemented; flagged here from observations during recon.)

- The classic right panel (`#right-panel`) is shown/hidden imperatively
  in many places. Consider extracting a `RightPanel` controller object
  to centralize that state.
- `command-center.js` mixes init / rendering / SSE / file uploads into
  one ~810-line module. Splitting into `cc-app.js`, `cc-sse.js`,
  `cc-uploads.js`, `cc-sessions.js` would make patches like this one
  (the ops-room mirror) less brittle.
- The `_unwrapJsonContent` recursive parser in `command-center.js` is
  a bandaid for the LLM occasionally returning JSON-wrapped content
  outside of structured blocks. Worth pinning down at the
  `STRUCTURED_RESPONSE_FORMAT` prompt level instead.
- Tool calls and graph traces are rendered into a separate
  `inspect.html` page opened in a new tab (Inspect button). For the
  research-aid ethos, surface a compact tool-call card *inline* in the
  chat (we partially do this in the ops-room TRACE tab; classic could
  copy that). The CC graph already emits trace IDs per response.
- `loadPlugins()` looks up `#plugin-list` which is commented out in the
  classic HTML — the request to `/api/plugins` is wasted. Either ship
  the plugin list UI or short-circuit the call.
- `localStorage.setItem('cc_session_id', ...)` is the source of "ghost
  session" bugs when users switch users on the same machine. Scope the
  key by user_id or clear on logout.
- The session list lives permanently in the left sidebar; the ops room
  hides it behind a button because most operators don't switch sessions
  often. Worth A/B-testing the on-demand pattern in classic too.
