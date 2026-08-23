# The Agent — timezone analysis (read-only, 2026-08-22)

Question from James: users usually won't name a timezone; the browser's zone should be the default,
times should display in the browser's local zone, the server zone is the fallback, and the Azure SQL
database is in UTC. Does this actually work now, end to end, and are we done circling? **No code
was changed for this analysis.** Everything below was verified on the live box (ports 5001/5111,
JSS `app_jss_main.py`) unless marked "by reading".

## 1. Verdict in four lines

1. **Engine + database layer: correct and stable** — all schedule instants are UTC, cron wall-clock
   times fire in a per-schedule zone (DST-aware), the DB server's own zone never enters. Verified live.
2. **The Agent's scheduling tools now follow that contract** (fixed today in 23f3575; gate M-2 proves
   the engine's next run for a 7am-Eastern cron is 07:00 America/New_York). Verified live.
3. **But your stated requirement is NOT met yet — and it never was built, it is not a regression:**
   The Agent does not know the user's browser timezone (the Agent UI never sends it; Command Center
   does), the model has no clock (it knows the date, not the time) and no absolute one-shot, and the
   Agent UI / tool texts show UTC, not browser-local.
4. **One platform-wide display bug** (all job types, not Agent-specific): `LastRunTime` is written in
   app-server LOCAL time but every reader treats it as UTC → "Last run" shows 4 h off.

## 2. The contract as it stands today (what each layer does, with evidence)

| Layer | Stores / does | Zone | Evidence |
|---|---|---|---|
| `ScheduleDefinitions.StartDate / EndDate / NextRunTime` | instants | naive **UTC** | routes bind datetime objects, `getutcdate()` for audit columns; JSS normalizes aware→naive UTC before writing NextRunTime (`job_scheduler._update_next_run_time`). Live: job 428 (daily 9am ET) `next_run_time=2026-08-23T13:00:00` = 09:00 EDT ✓ |
| `CronExpression` + `ScheduledJobParameters.timezone` | cron **as written** + zone label | engine `CronTrigger(timezone=schedule_tz.to_tzinfo(params.timezone))`; IANA / `UTC` / `UTC±HH:MM`; no param → UTC | 2b15fd3 (2026-06-29). Live: job 428 cron `0 9 * * *` + `America/New_York` → 13:00 UTC ✓; job 425 (`0 7 * * *`, NO param) → 07:00 UTC = 3:00 AM ET, last run `03:00:09` local ✓ (fires in UTC as designed) |
| Interval / one-shot (`date`) triggers | `IntervalTrigger`/`DateTrigger` on `JOB_SCHEDULER_TIMEZONE='UTC'` (`config.py:289`) | UTC | b6eb560 (interval needs a StartDate anchor). Agent tool anchors with `utcnow()` ✓ |
| Azure SQL "in UTC" | irrelevant to scheduling | — | `scheduler_routes.py` / `job_scheduler.py` use `getutcdate()` only (no `getdate()`/`SYSDATETIME`), and bind Python datetimes — the DB server's clock/zone is never consulted. The only local-clock write is the APP server's `datetime.now()` into `LastRunTime` (see §5) |
| Legacy platform UI (Scheduling panel, approvals, monitor) | renders DB stamps as UTC→browser-local | browser | `static/js/timezone_utils.js` (`moment.utc(...).local()`), `monitoring.js normalizeUtcDateString` (76e34de) |
| Typed schedule routes (workflow/automation create+edit) | browser-local wall time + `timezone_offset` → UTC | browser (offset at save time) | 76e34de `_bind_schedule_date_utc`; known caveat: offset frozen at save (EDT/EST) |
| Command Center scheduling tools | cron as written + `timezone` = named zone **else the browser zone** | browser default | `command-center.js:221` sends `body.timezone = Intl…timeZone`; `routes/chat.py:317` stamps `user_context.browser_timezone`; `nodes.py _resolve_schedule_tz` ("named zone wins, else browser tz"); automations parity f398842 |
| **The Agent** `schedule_agent_task` / `schedule_portal_workflow` (after 23f3575) | cron as written + engine-canonical zone; intervals/one-shots UTC-relative | explicit zone → else `AGENT_DEFAULT_TZ` (unset on this box) → else **server zone as a FIXED offset** (`UTC-04:00` now) | `work_tools._build_schedule`; live: M-2 engine next run `2026-08-24T07:00:00-04:00` ✓; server = Windows "Eastern Standard Time" |
| The Agent `schedule_view_email` | cron as written + `timezone` via the platform resolver **only if the user named one**; else UTC + a "tell the user" note | explicit → UTC | `views_tools.py:692-712`; live job 428 was created with `timezone=America/New_York` ✓ |
| The Agent `schedule_view_refresh` | cron as written, **no zone ever** | UTC silently | `views_tools.py:545`; live job 425 fires 3 AM ET |
| The Agent UI timestamps (history list, My Work queue + detail "raised") | `updated_at/requested_at.slice(0,16)` / raw ISO | **UTC shown as-is** | `index.html:1881, 2028, 2067`; stores stamp `datetime.now(timezone.utc)` |
| The Agent tool texts | "first run ≈00:45 UTC", "stops by ≈00:49 UTC", scheduled-run header "fired … UTC", portal "next run <raw>" | **UTC** | `work_tools._bound_text`, `chat_history.build_deferred_prompt`, `portal_tools.py:652` |
| The model's own clock | knows today's **date** only | — | live probe (no tools): *"I don't actually know the real current time — I only have the date from context … for '9am tomorrow' I'd assume your local timezone, but I don't actually know what that is — I'd ask you or fall back to the server's default zone."* |

## 3. Why this felt like going in circles — and what is different now

Timeline (all from `git log`): b6eb560 (06-19, interval anchor + local display) → **2b15fd3 (06-29, per-schedule
cron zone in the engine; CC defaults to the browser zone)** → f398842 (07-22, automations honor the
user's zone) → 76e34de (08-08, typed routes dropped the offset; next-run rendered UTC as local) →
15d248b/f8ee759 (08-13/14, view email zone + DOW names) → e5e2207 (08-15, cron DOW) → **83bc844 (08-20,
The Agent's tool "converts at the seam" — wrong: it pre-shifted the cron to UTC AND stored the zone,
so the engine shifted it again; live job 453 7am ET → 11am ET)** → 23f3575 (today, engine-native).

Root cause of the circling: six surfaces (workflows, automations, CC, view email, agent tool, portal
tool) each implemented timezone handling at different times; the agent/portal tools were written under
the belief "the engine pins crons to UTC", which stopped being true on 06-29; and the gate check (M-2)
asserted the *stored expression*, so it passed while the *fire time* was wrong.

What now prevents a repeat: one shared builder for both agent tools (`work_tools._build_schedule`),
unit tests that assert no pre-shift, M-2 rewritten to assert the **engine's computed NextRunTime**,
and the contract written down (`docs/the-agent-bounded-recurrence-and-defer-to-chat.md` §"Bug fixed").

## 4. Gap analysis against the requirement

**A. "Browser timezone by default when none is named; server zone as the fallback."**
NOT MET in The Agent. Nothing carries the browser zone to the service: the Agent UI sends no
`timezone` (CC does), the JWT has no zone claim, `/api/me` returns none, and `CURRENT_USER` has no
`browser_timezone`. Today's default is the **server zone as a frozen offset** (`UTC-04:00`) — right
for you only because you and this server are both Eastern; wrong for a user in another zone; and 1 h
off after the November DST flip until the job is re-saved. `schedule_view_email` defaults to UTC with
a note; `schedule_view_refresh` defaults to UTC silently. (Intervals and "in N minutes" one-shots are
zone-free and correct everywhere.)

**B. "Dates/times displayed in the browser's local zone."**
NOT MET in The Agent surfaces: history popup, My Work queue, item detail, tool confirmations, the new
scheduled-run header, portal "next run" — all UTC (unlabeled or labeled). The legacy platform panel is
correct except for the `LastRunTime` bug (§5). The scheduled View email's "as of" is a relative phrase.

**C. (implicit) "Schedule something for 9am tomorrow / at 3pm" without naming a zone.**
NOT reliably possible: the model knows the date but not the time or the zone, and `schedule_agent_task`
has no absolute one-shot (only `run_in_minutes`), so an absolute time needs the model to *guess*
"now". It honestly said it would ask — which is the current behavior, not a silent error.

## 5. Platform-wide defect found during this review (not Agent-specific, not fixed)

`job_scheduler.py` writes `LastRunTime` with `datetime.now()` (app-server local) at all 9 executor
call sites, while `NextRunTime` is UTC and every reader (`monitoring.js`, `TimezoneUtils`) treats both
as UTC → the Scheduling panel shows "Last run" 4 h early (job 428: last run 09:00 EDT displays as
05:00 AM). One-line fix (`datetime.utcnow()` ×9, JSS restart); harmless to firing logic.

## 6. Minimal plan to make the requirement true (for your go-ahead — nothing done)

1. **Plumb the browser zone exactly like CC** — Agent UI sends `body.timezone =
   Intl.DateTimeFormat().resolvedOptions().timeZone` on `/api/chat`; `main.py` stamps
   `user["browser_timezone"]`; the three cron-capable tools default **explicit zone > browser zone >
   `AGENT_DEFAULT_TZ` > server zone**, using the platform resolver `schedule_tz.resolve_timezone`
   (international + ambiguity notes) instead of the agent's smaller alias table. Headless runs reuse
   the zone stored on the job. Unit tests pin the order; pack-20 gains "no zone named → browser zone".
2. **Give the model a clock + an absolute one-shot** — prepend one line per turn ("Now: 2026-08-22
   21:40 EDT · your zone America/New_York") and add `run_at` (+ the zone) to `schedule_agent_task`
   (the engine's `date` trigger already supports it). Then "at 3pm" / "9am tomorrow" are exact.
3. **Display in browser-local** — Agent UI formats every stamp with `toLocaleString()` (3 sites + the
   scheduled-run header carries ISO and is rendered locally); tool texts state times in the user's
   zone once the zone is known (or label UTC explicitly until then); JSS `LastRunTime` → UTC (§5).
4. **Interim, zero-code:** set `AGENT_DEFAULT_TZ=America/New_York` in `.env` on this box so the
   server-zone fallback is DST-aware (today it is a frozen offset).
5. `schedule_view_refresh`: same defaulting, or state plainly that refresh crons are UTC.

Acceptance: a user whose browser is in America/Chicago says "every weekday at 9am" → stored cron
`0 9 * * 1-5` + `timezone=America/Chicago`, engine NextRunTime 14:00 UTC (CDT), the confirmation
says "9:00 AM Central", and My Work / history show Central wall-clock times.

## 7. Evidence log (this session)
- Live M-2 (gate run 2): stored `0 7 * * 1-5`, tz `America/New_York`, engine next run
  `2026-08-24T07:00:00-04:00`; unknown zone refused.
- Live job 428: `0 9 * * *` + `America/New_York`, next `2026-08-23T13:00:00` (UTC), last
  `2026-08-22T09:00:07.2` (LOCAL — §5). Live job 425: `0 7 * * *`, no zone, next `07:00:00` UTC.
- Live model probe quoted in §2 (no tools, new session).
- Server: Windows zone "Eastern Standard Time"; agent default label `UTC-04:00`; `.env` has no
  `AGENT_DEFAULT_TZ`.
- Agent UI: no `Intl`/`timeZone` usage; stamps rendered raw (`index.html:1881/2028/2067`).
- CC: `command-center.js:221`, `routes/chat.py:227/317`, `nodes.py:190-216`, automations `api.py:865`.

---

## 8. Status after implementation (same day, 2026-08-22 evening)

The plan in §6 was implemented (analysis above left as written for the record):

| Gap | Now |
|---|---|
| A. browser zone default | The Agent UI sends `timezone` = `Intl.DateTimeFormat().resolvedOptions().timeZone` on chat / edit-chat / work-thread turns; `main._turn_envelope` validates it and stamps `user["browser_timezone"]`; all three cron-capable tools (`schedule_agent_task`, `schedule_portal_workflow`, `schedule_view_email`, and now `schedule_view_refresh`) default **named zone > browser zone > AGENT_DEFAULT_TZ > server zone** (`work_tools.default_zone_label`); the server zone is the Windows zone mapped to IANA (DST-aware), fixed offset only as a last resort; the confirmation says which zone was assumed and why. Named zones also accept the platform's abbreviation table (BST, AEST, …); ambiguous ones (IST) are refused with the choices. |
| B. local display | Agent UI renders every stamp with `fmtLocal()` (history popup, My Work queue, item detail); tool texts state first-run / stop / one-shot times in the user's zone ("≈21:45 EDT"); the scheduled-run header is stamped in the user's zone; portal "next run" too; JSS `LastRunTime` is now written in UTC (×9 call sites) so the platform panels stop showing it 4 h early. |
| C. clock + absolute one-shot | Every turn starts with `[Context: now Saturday 2026-08-22 21:45 EDT (America/New_York) — …]` (replay strips it); `schedule_agent_task` gained `run_at` ("YYYY-MM-DD HH:MM" in the user's zone → engine `date` trigger; past times refused). Live probe after restart (zone America/Chicago sent): *"2026-08-22, 8:49 PM — assuming your timezone, America/Chicago (CDT)."* |
| headless runs | `schedule_agent_task` stores `user_timezone` on the job; the JSS forwards it as `timezone`; `/api/run` stamps it so the run states times in the user's zone and chained schedules default to it. |

Tests: `test_agent_schedule_bounded.py` 31, `test_agent_defer_to_chat.py` 13, `test_view_email_schedule.py` 13,
`test_jss_agent_session_forward.py` 3 (jss env); pack 20 gained **M-3** (no zone named → browser zone
America/Chicago stored, engine NextRunTime 09:00 Chicago) and PT-12 asserts the header is local;
`chat_turn` now sends the browser zone like the UI. Browser-verified: history "Aug 22, 2026, 09:50 PM",
queue/detail stamps local, no console errors.

