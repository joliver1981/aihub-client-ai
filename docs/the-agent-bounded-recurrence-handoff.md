# Handoff: two related upgrades to The Agent's scheduled tasks

This spec is self-contained: paths, seams, the exact gaps, the build, tests, and
the verification gate. Repo root: `C:\src\aihub-client-ai-dev`. The Agent
service is `agent_service/` on port 5111 (conda env `aihub-agent`).

**Two changes, both touching `schedule_agent_task` (`agent_service/work_tools.py`)
and the headless-run path (`agent_service/main.py` `/api/run`) — do them
together:**

- **Part 1 — bounded recurrence:** honor *"do X every 10 minutes for the next
  hour"* — a sub-hourly interval **and** an automatic stop — as ONE scheduled
  task, instead of an unbounded cron that runs forever.
- **Part 2 — surface deferred results in the chat (Level 1):** when a scheduled
  or delayed task fires, its result should land **in the conversation the user
  asked from** (and ping them), not only as a quiet FYI in My Work.

Ship both behind flags, additive, with today's behavior as the fallback.

---

# Part 1 — bounded recurrence

---

## Why (the gap, verified)

`schedule_agent_task` in `agent_service/work_tools.py` (~lines 238–377) accepts:
`cron_expression` (+`timezone`), `every_hours`, `every_days`, or `run_in_minutes`
(one-shot). It builds a `schedule` dict and POSTs it to the main app's
`/api/scheduler/jobs` as a job of type `agent_session` (each firing runs the
prompt headlessly as the user and drops an FYI in My Work).

Two limitations make "every 10 min for an hour" impossible today:
1. **No sub-hourly interval knob.** `every_hours`/`every_days` can't express 10
   minutes; the only route is a raw cron `*/10 * * * *`.
2. **No bound.** Nothing accepts an end time or a run count, so any recurring
   job runs until manually cancelled. The Agent cannot self-limit to an hour.

(Technically the tool *could* fake it by scheduling six one-shot `run_in_minutes`
tasks, but the model isn't taught that and won't do it reliably. We want a clean
native capability.)

## What to build

Add to `schedule_agent_task` two new inputs and the wiring behind them:

- **`every_minutes`** (int ≥ 1): interval schedule at minute granularity.
- **A bound — pick ONE shape after the investigation below:**
  - `for_minutes` (int): stop firing this many minutes from now, **or**
  - `occurrences` (int): stop after this many runs.

Recommended UX: support `every_minutes` + `for_minutes` as the primary pair
("every 10 minutes for 60 minutes"), and accept `occurrences` as an alternative
bound. Keep all existing params working unchanged.

## Investigate FIRST (do not skip — the engine decides the clean path)

The bound has to be enforced by the scheduler engine, not faked. Before coding,
read these and decide the mechanism:

1. `scheduler_routes.py` — the `POST /api/scheduler/jobs` handler and how it
   parses the `schedule` dict (grep for `interval`, `cron`, `date`, `start_date`,
   `end_date`, `interval_hours`, `interval_minutes`). Determine:
   - Does the interval builder already accept `interval_minutes`? (APScheduler's
     `IntervalTrigger` supports `minutes=`.) If not, add it alongside
     `interval_hours`/`interval_days`.
   - Does it pass an **`end_date`** through to the trigger? APScheduler triggers
     (`interval`, `cron`) accept `end_date` and stop firing after it — this is
     the cleanest bound. If the route already forwards `end_date`, `for_minutes`
     is trivial (`end_date = now + for_minutes`). If not, add end_date support.
2. `job_scheduler.py` — how interval jobs are constructed/registered and whether
   anything re-anchors or re-creates them (there's a known "interval needs an
   anchored `start_date` or the next fire is pushed forever" lesson — preserve
   that: keep setting `start_date` = now for intervals).
3. Confirm the engine accepts cron **step syntax** (`*/10`) if you also want to
   allow `cron_expression` + a bound (nice-to-have, not required).

**Decision rule:** prefer engine-native `end_date` for `for_minutes`. Use
`occurrences` only if you also want a count bound — implement it as
`end_date = now + occurrences * every_minutes` (simplest, stays engine-native),
or via APScheduler's run-count if the route exposes it. Do NOT implement the
bound by fanning out N one-shot jobs unless the engine truly can't bound a job
(document the finding if you fall back to that).

## Implementation notes (match the existing style)

In `agent_service/work_tools.py`, inside `schedule_agent_task`:

- Add the interval branch: when `every_minutes` is given, build
  `schedule = {"type": "interval", "start_date": <utcnow "%Y-%m-%d %H:%M:%S">,
  "interval_minutes": int(every_minutes)}` (mirror the existing every_hours/days
  branch, which anchors `start_date`).
- Apply the bound to the `schedule` dict: `schedule["end_date"] = (utcnow +
  timedelta(minutes=for_minutes)).strftime("%Y-%m-%d %H:%M:%S")` (or the
  occurrences-derived end). Put `end_date` in UTC to match the engine clock
  (the tool already converts cron local→UTC via `_resolve_tz_offset_minutes` /
  `_cron_local_to_utc`; reuse those if you allow a bounded cron).
- Keep the **read-back verification** exactly as the existing code does: POST,
  then `GET /api/scheduler/jobs/{id}`, assert an active schedule row exists, and
  return only the real `job_id`. Never claim success without the read-back.
- Report the bound honestly in the success text: e.g. *"every 10 min, first run
  ~10 min from now, stops after ~60 min (about 6 runs) — job #N, verified
  active."* Compute the "about N runs" from the bound for the user.
- Preserve the gotchas already in the file: `target_id` must be the string
  `"0"` (the route treats int 0 as missing); the scheduler returns the job id as
  a **string** (compare as int); each firing runs as the stored principal.
- Add `every_minutes`/`for_minutes`/`occurrences` to the tool's JSON schema with
  clear descriptions, and update the tool DESCRIPTION so the model reaches for
  the bound (tool descriptions outrank skills — spell out "every N minutes for
  M minutes" as a supported shape).
- `schedule_agent_task` is already in `MUTATING_TOOLS` (brain.py) — no list
  change needed (it's a mutation, not a read).

**Stretch (optional):** `agent_service/portal_tools.py` `schedule_portal_workflow`
has the same shape and the same `_portal_schedule_jobs` helper; the same
`every_minutes`/`for_minutes` bound could be added there. Scope to
`schedule_agent_task` first; only extend to portals if time allows.

## Tests

Unit — `tests_v2/unit/` (⚠ `.gitignore` hides `test*.py`; `git add -f`):
- `every_minutes` builds an interval schedule with `interval_minutes` and an
  anchored `start_date`.
- `for_minutes` sets `end_date` = now + N (UTC); `occurrences` derives the right
  `end_date`.
- Existing params (cron, every_hours, run_in_minutes) still build unchanged.
- Honesty: a POST that returns no active schedule row → tool reports NOT
  scheduled (mock the scheduler HTTP like the other agent tests do; see
  `tests_v2/unit/test_agent_portal_tools.py` for the MockTransport pattern).

Gate — `test_human/20_The_Agent/runner.py` (run under `aihub-agent` python):
- Add a check (next free id, e.g. `PT-11`): live-schedule "every 2 minutes for 6
  minutes" via a real model turn, assert `schedule_agent_task` was used, a real
  job id came back, and the job's read-back shows the interval + an `end_date`
  ≈ now+6min (you don't need to wait out the window — assert the bound is
  recorded, not that it fired 3× then stopped). Cancel/clean up the job after
  (use a throwaway — do NOT leave a live recurring job on the box).

## Verify + ship (the project's rhythm)

- Targeted restart of :5111 only (don't run the full V3 unless other services
  changed): kill the PID owning 5111, then launch
  `agent_service/start_agent_service_dev.bat` **detached** (PowerShell
  `Start-Process`; never pipe it — the spawned window inherits stdout and
  hangs), then confirm `GET http://127.0.0.1:5111/health`.
- Run the unit suites (they self-skip outside the `aihub-agent` env) and the
  full pack 20 gate; the gate is long (~30 min) — launch it detached and watch
  the log for `Report written`.
- ⚠ Environmental flakes to check BEFORE blaming code: the on-prem SQL box
  `10.0.0.6:1433` (many pack checks need it) and the Meridian demo portal on
  `:3000` (`aihub2.1 python test_human/_portal_test_server/portal_server.py`,
  not in the V3 fleet — start it manually).
- Commit only your own files (`git add` explicit paths, never `git add .`;
  verify each `git diff` is yours — another agent may share this tree). Push is
  publish-only to a PUBLIC GitHub repo (no auto-deploy): `git fetch` first,
  secret-scan the range, push without force. Ask the owner before pushing.

## Acceptance (Part 1)

"Every 10 minutes for the next hour" (and "every 5 minutes, 12 times") produces
ONE bounded recurring job, verified active by read-back, that the engine stops
on its own after the bound — with the tool honestly stating the cadence, the
first-run time, and roughly how many runs. No unbounded-forever job, no
fabricated ids.

---

# Part 2 — surface deferred results in the chat (Level 1)

**Goal:** when a delayed/scheduled task fires ("wait 5 minutes and do X", or a
recurring run), its result appears as the next turn **in the conversation the
user asked from**, and the user gets pinged — instead of only a quiet FYI in
My Work.

## Why this is the right home (and the constraint that shapes it)

Today a scheduled/delayed task runs headless in a FRESH session and drops an
`acknowledge` (FYI) work item in My Work (`agent_service/main.py` `/api/run` →
`run_turn(prompt, None, user_ctx)` → `workitem_store.create_item("acknowledge",
…)`). It never touches the chat the user asked from. The chat is **stateless
per-turn**: the SSE connection is open only during a turn, so there is NO open
channel to push a message into an idle chat tab later. That's why this is
"Level 1" — attach the result to the right conversation and notify; do NOT build
a persistent push channel (that's a much bigger, riskier change; out of scope).

The good news: the chat already **replays a conversation from its SDK
transcript** (`agent_service/chat_history.py`, `GET /api/chat/history/{sid}` in
`main.py`). So if the deferred run appends its turn to the SAME session
transcript, it simply *appears* when the user opens that conversation — no new
rendering code needed.

## What to build

1. **Thread the originating `session_id` through the schedule.**
   - ⚠ FIRST: the current `session_id` is NOT exposed to tools today.
     `schedule_agent_task` reads identity from the `CURRENT_USER` contextvar
     (`platform_tools.py`), which has no session id. In `brain.run_turn`, the
     session id is known (`new_session_id` / the SDK init message). Expose it to
     tools — add it to the session envelope or a dedicated contextvar set at the
     top of `run_turn` — so the tool can capture "the conversation I'm being
     asked from."
   - In `schedule_agent_task`, store that `session_id` in the job `parameters`
     (alongside the existing `prompt`/`user_id`/`role`/`username`).
   - The JSS `agent_session` executor (`job_scheduler.py`
     `_execute_agent_session_job`, ~lines 1404–1470) POSTs to `/api/run` — have
     it forward the stored `session_id` in the body.

2. **Resume that session in the headless run.** In `main.py` `/api/run`
   (`headless_run`), if the body carries a `session_id`, call
   `run_turn(prompt, session_id, user_ctx, …)` (resume) instead of `None`
   (fresh). The deferred work becomes the next turn in that thread; history
   replay renders it on open. Keep creating the My Work item too (see notify).

3. **⚠ Guard the resume against a live-session race (the one real risk).**
   Resuming an SDK session the user might be actively typing in = two writers on
   one transcript → possible corruption/lost turns. Mitigate:
   - Resume ONLY if that session is not currently active; else fall back to
     today's fresh-session + My Work FYI (never corrupt a live chat). A simple
     per-session "in-flight" marker in the service (set at the top of a chat
     turn for that sid, cleared at the end) is enough to detect the collision.
   - If you'd rather avoid session mutation entirely, the alternative design is
     to record the result as a "card" linked to the session_id (a small ledger +
     a UI element) instead of resuming — zero concurrency risk, but the deferred
     turn does NOT carry the prior chat's context into the model. **Pick one and
     say which; resume-with-guard is recommended for the continuous feel.**

4. **Notify + deep-link.** Keep the My Work item, but mark it as linked to the
   chat `session_id` so acting on it (or its notification) opens that
   conversation with the result already in it. The Agent UI already loads past
   conversations (`/api/chat/history`, the history popup) — reuse that as the
   deep-link target; add a small "new result in your conversation" affordance
   (a badge/toast is fine — no live push).

5. **Flag it.** Gate the chat-surfacing behind an env switch (e.g.
   `AGENT_DEFER_TO_CHAT`, default on if you're confident, else off). Flag off =
   exactly today's My-Work-only behavior. This keeps it additive and reversible.

## Do NOT (scope guard)

- Do NOT add a persistent WebSocket/SSE push channel or a client poll loop
  (those are Level 2/3 — bigger surface, higher risk). Level 1 surfaces on
  open/click, which is the low-risk 80%.
- Do NOT change the durable timer/scheduler mechanics — this is a
  delivery/rendering change only; scheduling reliability must stay untouched.

## Tests (Part 2)

Unit (`tests_v2/unit/`, `git add -f`):
- `schedule_agent_task` captures + stores the current `session_id` in job
  params (mock the scheduler HTTP; assert the param is present).
- `/api/run` with a `session_id` resumes that session (assert `run_turn` is
  called with the id, not `None`) and still creates the My Work item; without a
  `session_id` it behaves exactly as today (fresh session).
- The live-session guard: when the target session is marked in-flight, the run
  falls back to fresh + FYI (assert no resume attempted).

Gate (`test_human/20_The_Agent/runner.py`, e.g. `PT-12`): schedule a one-shot
`run_in_minutes=1` from within a chat session, let it fire, then
`GET /api/chat/history/{that_sid}` and assert the deferred turn was appended to
that conversation (and a linked My Work item exists). Clean up.

## Acceptance (Part 2)

"Wait 5 minutes and do X" (and a recurring run) appends its result as the next
turn in the originating conversation and pings the user via a My Work item deep-
linked to that thread — visible when they open it, with today's fresh-session +
FYI behavior preserved as the fallback and behind a flag. No persistent push
channel; no corruption of a session the user is actively using.

---

## Shared notes for both parts (verify + ship)

- Targeted restart of :5111 only: kill the PID owning 5111, launch
  `agent_service/start_agent_service_dev.bat` **detached** (PowerShell
  `Start-Process`; never pipe it — the spawned window inherits stdout and
  hangs), confirm `GET http://127.0.0.1:5111/health`.
- Run the unit suites (they self-skip outside `aihub-agent`) + the full pack 20
  gate (~30 min; launch detached, watch the log for `Report written`).
- ⚠ Before blaming code for a gate flake: the on-prem SQL box `10.0.0.6:1433`
  and the Meridian demo portal on `:3000`
  (`aihub2.1 python test_human/_portal_test_server/portal_server.py`, started
  manually — not in the V3 fleet).
- Commit only your own files (explicit `git add`, never `git add .` — another
  agent may share this tree; verify each `git diff` is yours). Push is
  publish-only to a PUBLIC GitHub repo (no auto-deploy): `git fetch` first,
  secret-scan the range, push without force — and ask the owner before pushing.
