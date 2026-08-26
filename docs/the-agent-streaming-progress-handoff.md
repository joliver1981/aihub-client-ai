# Handoff: streaming "working in the portal…" progress for The Agent

> ## ⚠ PARKED — DO NOT BUILD WITHOUT RE-DECIDING. Read this first.
>
> **Status (2026-08-22): shelved as backlog by the owner.** This is the
> lowest-value item on The Agent's list (pure cosmetics — a livelier wait; the
> running-chip pulse already signals "working") but it has the **highest blast
> radius** of any pending change. The risk/reward is upside-down. Do not pick it
> up as routine work.
>
> **Where the risk is:** the tempting design (and the one sketched below in
> "What to build") restructures `brain.run_turn` from a simple
> `async for message in query(): yield` into a concurrent producer-task + queue
> drain. `run_turn` is the single most load-bearing function in the service —
> EVERY path goes through it (interactive chat, headless scheduled runs,
> email-triggered turns, read-only side-threads, view-edit chats). Making it
> concurrent introduces failure modes that don't exist today: a swallowed
> `pump()` exception or a missing `__end__` sentinel → the turn hangs forever on
> `q.get()` (a hung chat, not a clean error); messier cancellation on client
> disconnect.
>
> **The most dangerous part:** the **mutation-claim guard** (the anti-fabrication
> honesty control) lives inline in that loop (`all_text`, `mutation_succeeded`,
> `tool_names`). Moving it into a background task and sharing state is exactly
> the kind of refactor that SILENTLY regresses a safety check — and if it breaks,
> the symptom is The Agent claiming a change succeeded when it didn't, which a
> cosmetic-feature test suite would very plausibly not catch. You'd be risking a
> correctness/honesty control to make a progress indicator prettier.
>
> **If it is EVER built, do NOT touch `run_turn`.** Use the isolated-channel
> design instead: the tool emits progress to a separate per-session store (CC's
> `graph/progress.py` `_active_queues` pattern) and the UI drains it over a
> SEPARATE lightweight poll/SSE endpoint — leaving `run_turn` and the mutation
> guard completely untouched. More transport plumbing, but zero risk to the
> critical path. For a cosmetic feature, isolation beats elegance. (The
> "cleaner" in-`run_turn` queue below is the WRONG call — it is documented for
> completeness, not as the recommended path.)
>
> **Recommended default: leave it parked.** Nothing is broken without it.

---

**Goal (if ever un-parked):** while a long tool runs (a portal login/download of
1–3 min, a big import, a dry-run), stream step-by-step status into the chat —
"Opening Meridian and signing in…" → "Working in the portal…" → "Waiting for you
to finish verification…" — instead of one static pulsing chip with only a timer.
Cosmetic/advisory only: it must NEVER be mistaken for the result.

Self-contained spec. Repo root: `C:\src\aihub-client-ai-dev`. The Agent service
is `agent_service/` on port 5111 (conda env `aihub-agent`). Ship behind a flag,
additive, today's behavior as the fallback.

## The gap (verified) — why this needs a harness change, not just a string

The Agent's chat is SSE: `POST /api/chat` in `agent_service/main.py` returns a
`StreamingResponse` that drains `brain.run_turn(...)`, an async generator which
iterates the SDK `query()` message stream and yields UI events (`text`, `tool`,
`tool_result`, `result`, `guard`, `error`, then `done`). The in-process MCP
tools (e.g. `portal_tools.portal_fetch` → its `_poll_run` loop) run INSIDE that
`query()` call and **return a single value** — a tool body has no way to push an
intermediate event up to the SSE stream mid-execution. So a 2-minute portal run
renders as exactly two events (tool dispatch, tool return) and a client-side
timer. Surfacing progress DURING a blocking tool call inherently needs a
concurrent consumer — which is the source of the risk above. This benefits
EVERY long tool, not just portals — build the channel generic, make portals the
first consumer.

## Reference implementation to port (Command Center already does this)

- `command_center_service/graph/progress.py` — `ProgressQueue`, module-level
  `_active_queues` keyed by session_id, `emit(...)`. **This is the isolated
  pattern to prefer.**
- `command_center_service/routes/chat.py` — drains that queue concurrently and
  forwards `status` SSE events.
- `command_center_service/graph/nodes.py` — the portal tool calls
  `get_queue(session_id).emit("status", {...})`; the canonical
  `"Working in the portal…"` heartbeat is at ~line 4418; sibling strings
  "Opening {portal} and signing in…", "Waiting for you to finish the
  verification step…".
- `command_center_service/static/js/command-center.js` (~lines 333–342) renders
  those status events.

## What to build (⚠ see PARKED banner — prefer the isolated-channel variant)

1. **A progress channel.**
   - **PREFERRED (isolated, low-risk):** port CC's `graph/progress.py` — a
     per-session store the tool emits into, drained by a SEPARATE endpoint the
     UI polls/streams. `run_turn` is untouched. This is the design to use if
     this is ever built.
   - **NOT RECOMMENDED (in-`run_turn`, high-risk — documented only):** create an
     `asyncio.Queue` inside `run_turn`, set a `contextvars.ContextVar`
     (`PROGRESS_EMIT`) tools call, run the SDK-message translation as a task that
     pushes onto the queue, and yield from the queue until the task ends. ⚠ This
     is the path that endangers the hot loop and the mutation-claim guard (which
     must be moved into the pump task with shared state and STILL fire after
     `await task`). Do not choose this for a cosmetic feature.

2. **Make emit a no-op-safe global.** Tools call
   `progress("Working in the portal…")` via a helper that does nothing if the
   channel is unset. Headless runs (`/api/run`, email poller) have no consumer —
   emit must be harmless there.

3. **Emit from the portal tool.** In `agent_service/portal_tools.py` `_poll_run`
   (already polls `get_portal_result(run_id)` every 2s) and
   `run_portal_workflow`, emit on meaningful transitions — sign-in, a
   "working in the portal…" heartbeat throttled to ~10s (CC parity), the
   take-over wait. Drive the text from the browser service's real per-run
   status/agent_note (below); reuse CC's exact strings.

4. **Surface the step from the browser service.** Confirm
   `browser_use_service` `/portal/result/{run_id}` (running-poll response)
   includes a human-readable step. `run_registry.RunState.to_dict()` already
   carries `status`, `reason`, `agent_note`, `needs_human`, `elapsed_seconds` —
   include those in the running-poll payload if not already, and pass them
   through `portal_fetch.get_portal_result` so `_poll_run` can emit them. No new
   browser-side tracking.

5. **Render it in the UI.** `agent_service/static/index.html` `sendMsg()` keeps a
   `chips` map keyed by `tool_use_id` (~lines 1681–1800). Render progress as a
   sub-text line UNDER the matching chip, updated IN PLACE (not a new line per
   update); keep the accessible style + reduced-motion guard. The amber pulse +
   timer stay; progress is the sub-line.

6. **Flag it.** Gate behind `AGENT_TOOL_PROGRESS` (default off given the risk;
   flip on only after the isolated design is proven). Off = today's two-event
   behavior.

## Do NOT (scope guard)

- Progress is advisory. "downloading…" is NEVER "downloaded" — the honest RESULT
  still comes only from the tool's return value and the existing
  `result`/`tool_result` events. Keep the mutation-claim guard exactly as is.
- Don't add a persistent cross-turn push channel — this lives within a single
  turn's lifetime (the turn is open while the tool runs).
- Don't change tool return shapes, the scheduler, or the honesty layer.
- ⚠ Don't restructure `run_turn` for this (see PARKED banner) — use the isolated
  channel.

## Tests

Unit (`tests_v2/unit/`, ⚠ `.gitignore` hides `test*.py` → `git add -f`):
- The emit helper is a safe no-op when the channel is unset (headless).
- `_poll_run` emits the expected status strings given a mocked
  `get_portal_result` sequence (running+agent_note → needs_human → done) — mock
  the CC client like `tests_v2/unit/test_agent_portal_tools.py` and capture
  emits.
- If the (non-recommended) in-`run_turn` path is ever taken: a `run_turn` test
  with a fake tool that emits, asserting progress events interleave before
  `result` AND that the mutation-claim guard still fires correctly. (This test
  is mandatory for that path — the guard regression is the whole risk.)

Gate (`test_human/20_The_Agent/runner.py`, e.g. `PT-11`): during the existing
live portal run (PT-2 captures SSE events), assert ≥1 progress event carrying a
portal status string arrived between the portal tool event and its result. Needs
the Meridian portal on `:3000` up.

## Verify + ship

- Restart :5111 only: kill the PID owning 5111, launch
  `agent_service/start_agent_service_dev.bat` **detached** (PowerShell
  `Start-Process`; never pipe it — the spawned window inherits stdout and hangs),
  confirm `GET http://127.0.0.1:5111/health`. UI-only changes need just a
  browser refresh (static serves from disk); backend changes need the restart.
- Run the unit suites (self-skip outside `aihub-agent`) + the full pack 20 gate
  (~30 min; launch detached, watch the log for `Report written`).
- ⚠ Before blaming code for a gate flake: the on-prem SQL box `10.0.0.6:1433`
  and the Meridian demo portal on `:3000`
  (`aihub2.1 python test_human/_portal_test_server/portal_server.py`, started
  manually — not in the V3 fleet).
- Commit only your own files (explicit `git add`, never `git add .` — another
  agent may share this tree; verify each `git diff` is yours). Push is
  publish-only to a PUBLIC GitHub repo (no auto-deploy): `git fetch` first,
  secret-scan the range, push without force — and ask the owner before pushing.

## Acceptance (only if un-parked, via the isolated design)

A portal run shows live, in-chat status updating in place under the running chip,
driven by the browser service's real per-run state — with the honest result
unchanged, the mutation-claim guard provably intact, `run_turn` untouched,
headless runs unaffected, and the whole thing behind `AGENT_TOOL_PROGRESS`.
