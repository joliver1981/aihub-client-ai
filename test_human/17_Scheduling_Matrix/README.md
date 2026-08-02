# Pack 17 — Scheduling & Jobs Matrix

**Why this area.** Scheduling fails **invisibly**. A workflow that stops firing produces no
error, no alert, and no user complaint until the business impact lands weeks later. Before
this pack, the platform's only scheduling coverage was one shallow liveness check ("does the
backend endpoint answer?"). The area also has a documented history of exactly the silent kind:

| history | what broke | how it surfaced |
|---|---|---|
| `f84f5c8` | `portal_workflow` job type missing from the scheduler | schedules stored, never ran |
| AIHUB-0065 | Schedule button posted a slug STRING into an INT `TargetId` | raw SQL 500 |
| AIHUB-0061 | scheduled automations never appeared in the CC panel | invisible jobs |

Six job types (`document`, `agent`, `workflow`, `command_center`, `portal_workflow`,
`automation`) share one scheduler, so a type mismatch hides easily.

## Running

```bash
python runner.py                  # Tier A (regression) — ~6s
```

```bash
python runner.py --competency     # Tier A + B — adds ~8 min of wall-clock waiting
```

Requires the aihub2.1 env and the app on `localhost:5001` (override with `REGP_BASE`).
Reports land in `results_history/` plus `REPORT_LATEST.md`; each run diffs against the
previous JSON and exits **2** on a regression (a check that was PASS and is now FAIL/ERROR).

## Tiers

- **Tier A (regression, default).** Deterministic contract checks — job CRUD, the
  integer-`TargetId` guard, interval + cron schedule creation and read-back, listing by job
  and by type, delete cleanup, and the route-auth contract. No waiting.
- **Tier B (competency, `--competency`).** The questions that actually matter: **does a
  schedule FIRE?** does a disabled one stay silent? does deleting the job stop it firing?
  Graded on **real execution rows**, never on the schedule record alone — a stored schedule
  that never fires is the precise silent failure this pack exists for.

## ⚠️ Route contract — the trap that ate the first draft

The scheduler blueprint exposes **two parallel route families, and they are not aliases**:

| family | `<id>` means | guard | behavior |
|---|---|---|---|
| `/jobs/<id>/types/<type>/schedules` | the **TARGET** id (workflow id, agent id, …) | `min_role=2` | finds-or-creates the `ScheduledJobs` row for (type, target) and attaches the schedule. **The real path.** |
| `/jobs/<id>/schedules` | a **document** target id, always | none | mints a phantom `document` job for whatever id you pass |

Passing a workflow id (or a `ScheduledJobId`) to the legacy route silently attaches the
schedule to a **phantom document job**. The workflow then never fires — and because the
matching GET performs the *same* find-or-create, every read-back still looks correct. The
first draft of this pack fell into exactly that trap and produced two confident, wrong
findings. `runner.py` now uses the type-aware family exclusively; `App.add_schedule()` and
`App.schedules_for()` take a **target id + job type**, never a `ScheduledJobId`.

Corollary for cleanup: because the route creates its own job row, tests tear down by
**(type, target)** via `drop_jobs_for_target()`, not by the id they created.

## Timing budget

The scheduler re-reads the DB every `poll_interval` (60s default, `job_scheduler.py:74`) and
only then arms the trigger. So a 60s interval schedule has an honest worst case of ~120s;
`c1` allows 240s. Observed: **130.4s** to first execution row.

## Findings (open — owner decision pending)

All three are encoded as **XFAIL tripwires**: they document current behavior and will flip to
**XPASS** the moment it is fixed, so nothing gets silently forgotten.

| id | finding |
|---|---|
| `s11` | `POST /api/scheduler/jobs` accepts **any** job type string (201). The scheduler implements 6 and logs "Unsupported job type" at run time — the row is created but can never execute. |
| `s12` | **Every legacy scheduler route is unauthenticated** — 6 unauth writes + 5 unauth reads. Verified credential-free: created, renamed and deleted a scheduled job and enumerated all 125. The `/types/<job_type>/` twins are correctly guarded. |
| `s13` | `GET /api/scheduler/jobs/<id>/schedules` **INSERTs** a `ScheduledJobs` row as a read side effect (`scheduler_routes.py:464-489`). Unauthenticated and unbounded; 54 such rows already exist. |

**Retracted 2026-08-01:** an earlier draft reported "DELETE leaves orphaned schedule rows."
That was an artifact of the phantom-job trap above. With the correct route, `s10` passes and
`c3` confirms empirically that a deleted job stops firing — the FK cascade works.
