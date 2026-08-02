# Pack 18 — Authentication & Authorization Matrix

**Why this area.** Five of the seven tripwires standing open across packs 14/15/17 were
missing-authorization findings, in three unrelated subsystems — approvals, agent creation,
the scheduler. That isn't five bugs. It's one bug wearing five hats, and no pack owned the
question *"who is allowed to do what?"*

## The root cause

`auth_middleware.py` implements exactly the right thing: a global `before_request` that
blocks every unauthenticated request except an 11-entry allowlist. It ships with a full unit
test suite (`tests/security/test_auth_middleware.py`) and **those tests pass.**

**It is never wired into the application.** `init_auth_middleware` appears in no module
outside its own file and its tests — `app.py` never calls it. The only `@app.before_request`
in `app.py` (~line 1370) sets user-tracking context and checks nothing.

So the app's only protection is per-route decorators, and ~349 of ~929 routes carry none.
The unit tests pass because they exercise the middleware in isolation on a Flask app the test
itself constructs. **Nothing tested the wiring.** This pack tests the wiring.

Compounding it: `AUTH_MIDDLEWARE_DRY_RUN=true` is set at **Machine** scope on this box, so
even if the middleware were wired tomorrow it would come up in log-only mode and still block
nothing. Auth enforcement has therefore never actually run here.

## Running

```bash
python runner.py                  # Tier A — ~1.5 min (the sweep dominates)
```

```bash
python runner.py --competency     # Tier A + B — ~2 min
```

## Tiers

- **Tier A (regression).** The allowlist hasn't grown, dry-run is off, login/logout/session
  behave, the role ladder holds, and **the sweep**: probe every parameterless GET route
  anonymously and count how many answer.
- **Tier B (competency).** Whether the gap is theoretical or exploitable — can a low-role user
  escalate itself? can user A read user B's data? does an anonymous write actually **persist**,
  or merely return 200? Graded on real state changes, never status codes alone.

## The ratchet

`a11` is a **ratchet, not a clean bill of health**. `SWEEP_BASELINE = 29` is today's count of
anonymously-reachable routes. PASS means *no new doors opened*; it does **not** mean zero.
Lower the constant as routes get closed and the check holds the new floor.

It is deliberately **not** an XFAIL: an xfail would flip to XPASS the moment the ratchet held,
which reads as "fixed" when nothing was fixed.

## Safety

The sweep is GET-only, parameterless, and skips any path matching `DANGEROUS_PATH`
(delete/reset/restart/…). Some GETs still write — `GET /api/scheduler/jobs/<id>/schedules`
INSERTs a row (pack 17 s13) — so the runner snapshots the scheduler job table before the sweep
and deletes anything it minted.

## Findings (open — owner decision pending)

| id | finding |
|---|---|
| `a1` | The global auth middleware is **never wired in**. Root cause of the whole cluster. |
| `a3` | `AUTH_MIDDLEWARE_DRY_RUN=true` at Machine scope — enforcement would be log-only anyway. One command to fix: `setx /M AUTH_MIDDLEWARE_DRY_RUN false`. |
| `b3` | An anonymous `POST /api/scheduler/jobs` creates a **real, listable** row. The 200 is not cosmetic. |
| `b4` | `/api/scheduler/jobs` (~31 KB) and `/api/workflow/approvals` (~300 KB, the whole pending queue) return real records anonymously. |
| `b5` | A role-1 user lands **2 privileged writes**: creates an agent and a scheduler job. |
| `b6` | Reachable-but-not-public: `/admin/caution-settings` renders a full admin page (~25 KB); `/api/caution/user` exposes per-user context; four `/test*` debug routes should not exist in a shipped build. |

## What is genuinely healthy

Worth stating plainly, because the findings above are loud: the **decorator-based** protection
works wherever it is applied. `/get/users` and `/get/connections` 401, `/get/agents` 302s, the
`/types/<job_type>/` scheduler routes 401, bad passwords and bogus API keys are rejected,
logout truly invalidates the session, role-1 is blocked from the users page / save-workflow /
automations-create, a role-1 user **cannot** escalate its own role, and user A **cannot** read
user B's agent. The gap is coverage, not a broken mechanism — which is exactly why wiring the
middleware is the high-leverage fix.

## Two harness bugs caught before reporting

Recorded because both would have produced *false reassurance*:

1. `/get/agents` returns `{'data': [...]}` while `/get/users` returns a JSON **string** holding
   a list. Iterating the raw body yielded dict keys and crashed b2/b5 — now normalized by
   `App.rows()`.
2. The agent-creation payload keys on `agent_description`, not `agent_name`. With the wrong
   payload, b5 read a **payload rejection** as "role-1 was blocked" and reported 1 privileged
   write instead of 2.
