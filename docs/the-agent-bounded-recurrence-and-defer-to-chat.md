# The Agent — bounded recurrence + deferred results in the chat (shipped 2026-08-22)

Companion to `docs/the-agent-bounded-recurrence-handoff.md` (the spec). This is what was
actually built, the seams it touched, the one bug found on the way, and how to verify it.

## Part 1 — bounded recurrence (`schedule_agent_task`, `agent_service/work_tools.py`)

**New inputs** (all existing ones unchanged):

| input | meaning |
|---|---|
| `every_minutes` (int ≥ 1) | interval at minute granularity → `schedule.interval_minutes` (engine `IntervalTrigger(minutes=)`) |
| `for_minutes` (int ≥ 1) | BOUND: stop this many minutes from now → `schedule.end_date` (trigger `end_date`, UTC) **and** `max_runs = floor(window / interval)` |
| `occurrences` (int ≥ 1) | BOUND: stop after this many runs → `schedule.max_runs` (engine `MaxRuns`/`CurrentRuns`) **and** a derived `end_date` for intervals |

"Every 10 minutes for the next hour" = `every_minutes=10, for_minutes=60` → ONE job:
`interval_minutes=10, start_date=now, end_date=now+60m+30s, max_runs=6`. "Every 5 minutes,
12 times" = `every_minutes=5, occurrences=12` → `max_runs=12, end_date=now+60m+30s`.
Both bounds together → the tighter one wins. Bounds also work on a cron (`for_minutes` →
`end_date` only; `occurrences` → `max_runs` only — no croniter in the agent env to count a
cron). A one-shot (`run_in_minutes`) refuses a bound. `for_minutes` shorter than one interval
is refused ("nothing would ever fire"). No fan-out of one-shots anywhere.

**Why both `end_date` and `max_runs` on an interval:** `end_date` is the hard time stop the
trigger enforces by itself; `max_runs` gives the exact count, a "6/6 executions completed"
readout in the job panel, and a clean `IsActive=0` once reached (the sync loop deactivates the
row). `end_date` carries 30 s of slack (`_BOUND_SLACK_SECONDS`) so the last planned fire is
never lost to second rounding; `max_runs` is what stops the count at N. Engine facts verified:
`_create_schedule` persists `interval_minutes`/`end_date`/`max_runs`; `_create_trigger` passes
`end_date` to Interval/Cron triggers; a fresh `ScheduleDefinitions` row has `CurrentRuns=0`
(so the `current_runs >= max_runs` check can never hit `None`).

**Honesty:** POST → read-back → the active schedule row must carry the requested bound
(`end_date` / `max_runs`); if the engine dropped it the tool DELETES the job and reports NOT
scheduled (a bounded ask must never leave a job that runs forever). The success text states
cadence, first-run time, stop time, and "about N run(s)", e.g.
`every 2 minutes, first run ~2 min from now (≈00:45 UTC), stops by ≈00:49 UTC (~6 min from
now), about 3 run(s) in total; the engine stops it on its own`.

### Bug fixed on the way: cron timezone double-shift (live-verified)

The engine has fired CRON triggers in the per-schedule `timezone` job parameter since
2b15fd3 (2026-06-29, `job_scheduler._create_trigger` → `schedule_tz.to_tzinfo`, DST-aware).
`schedule_agent_task` (83bc844, 2026-08-20) ALSO pre-converted the cron's hour to UTC and
stored `timezone=<IANA>` — so the engine re-applied the zone to an already-shifted cron.
Live repro: job 453 `0 7 * * 1-5` Eastern was stored as `0 11 * * 1-5` + `America/New_York`
and the engine's next run was `15:00 UTC` = **11am Eastern, four hours late**.
`schedule_portal_workflow` shared the same helper and the same bug.

Fix: both tools now store the cron **as written** plus an engine-canonical zone label (IANA,
`UTC`, or `UTC±HH:MM`; the server-local default becomes a fixed `UTC±HH:MM` offset because
Windows exposes no trustworthy IANA name — pass a zone or set `AGENT_DEFAULT_TZ` for DST-correct
firing). `_cron_local_to_utc` / `_shift_field` are gone. Pack-20 `M-2` now asserts the
engine's own `NextRunTime` lands at 07:00 America/New_York (the old M-2 pinned the buggy
contract). ⚠ Any agent/portal cron job created 2026-08-20 → 2026-08-22 with a non-UTC zone is
still double-shifted until re-saved (job 453 itself looks like a leftover seam check).

### Shared builder
`work_tools._build_schedule(args, now)` returns `{schedule, kind, params, interval_seconds,
first_run_at, end_at, max_runs, expected_runs, tz_label, local_cron, note}` and raises
`ValueError` with the user-facing reason. `schedule_portal_workflow` (`portal_tools.py`) uses it
too, so portals get `every_minutes`/`for_minutes`/`occurrences` and the timezone fix for free.

## Part 2 — deferred results land in the chat (Level 1, flag `AGENT_DEFER_TO_CHAT`, default on)

Design: **resume-with-guard** (the spec's recommended option). No push channel, no polling.

1. `brain.run_turn` now exposes the conversation id to tools: `user_ctx["session_id"]` (the
   resume id, or the SDK init message's id on a new session — the `CURRENT_USER` contextvar
   holds that same dict, so tools called later in the turn see it).
2. `schedule_agent_task` stores it as job parameter `session_id` (only from a chat turn; a
   headless run stores the chat it RESUMED via `user_ctx["chat_session_id"]`, so chained
   schedules keep the thread).
3. JSS `_execute_agent_session_job` forwards `session_id` in the `/api/run` body (empty for
   legacy jobs — never missing).
4. `/api/run` (`main.py`): `_resume_target()` fails closed — flag on, well-formed id,
   `chat_history.owns_session(user, sid)`, and `brain.is_inflight(sid)` false → RESUME that
   SDK session with `chat_history.build_deferred_prompt()` (a `[SCHEDULED RUN] '<job>' fired
   <utc> …` header + `---` + the task; tells the model the user is absent, do it now, do NOT
   reschedule, raise a work item if a human is needed). Any other case → exactly the old fresh
   session. A resume that errors before any text/tool → fresh retry (the task still runs). The
   My Work FYI is still filed, now with `payload.chat_session_id` (deep-link); the ledger is
   touched so the conversation floats to the top of history.
5. **Race guard** (`brain._INFLIGHT`, counter + stale-expiry): `run_turn` marks the session it
   drives for the whole turn; `/api/run` claims the session before its first await and skips
   the resume if it is busy; `/api/chat` WAITS (bounded, `AGENT_CHAT_BUSY_WAIT_SECONDS`=90,
   emits a `status` SSE line "A scheduled task is adding its result…") while a deferred run
   holds the same conversation, then proceeds — so the user's next message sees the result in
   context.
6. UI (`static/index.html`): history replay renders `kind="scheduled_run"` turns as
   "⏰ Scheduled run … fired" + the task (never as the user's words); My Work FYIs with
   `chat_session_id` get **Open the conversation**; a one-time toast "⏰ "<job>" finished — its
   result was added to your conversation [Open] [Later]" on open/after turns; `#chat=<sid>`
   deep link; raw session ids hidden from the Details list.

Scope guards honored: no WebSocket/SSE push, no client poll loop, no change to the
timer/scheduler mechanics. Known Level-1 limits: an OPEN chat tab does not repaint by itself —
the result is there on the next open/replay (toast nudges); a long-lived recurring job grows
its conversation (SDK auto-compacts; consider `occurrences` bounds for chatty jobs).

## Tests / gate
- `tests_v2/unit/test_agent_schedule_bounded.py` (23) — builder shapes, bounds, cron
  as-written + engine zone labels, honesty paths (no active row / bound not recorded → delete
  / HTTP 500), session_id param, schema + portal sharing. aihub-agent env.
- `tests_v2/unit/test_agent_defer_to_chat.py` (11) — run_turn session exposure + in-flight,
  `/api/run` resume / fallbacks (flag off, busy, unowned, malformed, resume-failure retry),
  `/api/chat` bounded wait, replay tagging. aihub-agent env (FastAPI TestClient, no lifespan).
- `tests_v2/unit/test_jss_agent_session_forward.py` (3) — executor forwards session_id. jss env.
- Pack 20: `M-2` corrected (engine NextRunTime = 07:00 America/New_York), `PT-11` live
  "every 2 minutes for 6 minutes" → one job with `interval_minutes=2`, `end_date≈+6m`,
  `max_runs=3`, no fan-out (deleted after read-back), `PT-12` live one-shot from a chat →
  the result replays in that conversation + deep-linked FYI.

## Ops
Restart 5111 only (targeted recipe) for agent-side changes; the JSS (`app_jss_main.py`,
conda env `jss`) must be restarted for the executor change. Flags: `AGENT_DEFER_TO_CHAT`
(default true), `AGENT_CHAT_BUSY_WAIT_SECONDS` (90; 0 disables), `AGENT_INFLIGHT_STALE_SECONDS`
(7200), `AGENT_DEFAULT_TZ` (zone for crons given without one).
