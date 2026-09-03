# Research — running the regression suite against an INSTALLED build

**Question:** how do we test `10.0.0.6` (installed from the installer) with the
same human-grade suite we run against the local dev tree?

**Short answer:** most of it already works, and the remaining gap is not really
about the packs — it is that a runner sitting on a *different machine* cannot
see the target's disk, logs, internal ports, or fixtures. The cheapest large win
is seeding fixtures on the target; the structurally correct fix is running the
runner **on the target box**.

Everything below is measured, not assumed — from remote runs on 2026-09-02.

---

## 1. Where we actually are

A driver exists: `test_human/_scripts/run_regression_suite.py`.

```bash
python run_regression_suite.py --target local
python run_regression_suite.py --target 10.0.0.6 --api-key-file <file>
```

It owns the target decision in one place (`REGP_BASE`, `CC_BASE`,
`AIHUB_TARGET_HOST`, `SERVICE_HOST`, `API_KEY`) and picks the right interpreter
per pack — pack 20 needs the `aihub-agent` env for `claude_agent_sdk`, and under
`aihub2.1` it **crashes the pack** rather than failing a test, which is how it
silently reported nothing on an earlier run.

Measured result of the first full remote sweep:

| Pack | Remote result | Reading |
|---|---|---|
| 15 Platform Regression | 49 PASS / 1 FAIL / 51 SKIP | FAIL is the SFTP rig (§3.5) |
| 24 Installed Smoke | 3 PASS / 1 FAIL | FAIL is The Agent, unconfigured key |
| 16 CC Agent Matrix | 15 PASS / 8 FAIL | 4 missing fixture, 1 rig, 1 bad grader, 2 real |
| 17 Scheduling Matrix | 11 PASS / 5 ERROR | transient connect timeouts; box fine on retry |
| 18 AuthZ Matrix | ran | — |
| 19 CC Tier C | ran | 3 FAIL, pre-existing, also fails locally |
| 20 The Agent | 1 FAIL | no Anthropic key on that box |
| 22 Code Interpreter | ran | local-stack-only by design |

So the suite *runs* remotely today. What it cannot do is see enough of the box
to make every check meaningful.

## 2. The one real defect this found

Worth stating separately, because it is the thing remote testing exists to catch
and local testing structurally cannot:

```
CC -> agent 2 on 10.0.0.6
"Agent returned status 500: No module named 'command_center.artifacts.data_export'"
```

The same request direct to `/api/agents/2/chat` on that box returns **200** with
real data. So the agent is fine and Command Center is up. The first reading —
"the packaged CC is missing a module, the `command_center.orchestration`
shadowing family again" — turned out to be wrong: the 500 is raised by the
**main app**, on `/data_explorer/internal/query` (the endpoint CC's delegator
uses for data agents), because `app.py` loads `routes/data_explorer.py` by file
path and PyInstaller therefore never bundled a module only that file imports.
Root cause, proof and fix: `docs/handoff-cc-artifacts-data-export-missing.md`.
**It cannot reproduce from source**, which is exactly why a second path is
worth building — and pack 24 now carries `cc_delegation_endpoint` /
`cc_data_agent_turn` so the exact surface is checked on every installed box.

## 3. The blockers, each with options

### 3.1 Fixtures do not exist on the target — *highest value to fix*

Packs 15/16/17 reference `agent 281` (the AIRDB2 oracle) and an agent named
"Retail Demo - AIRDB2". Neither exists on a fresh install.

- Pack 15 handles this correctly: detects it, SKIPs, names the reason.
- Pack 16 hardcodes `281` in **14 places** and hard-FAILs — 4 of its 8 remote
  failures are this and nothing else.

**Options**

| Option | Effort | Notes |
|---|---|---|
| **A. Seed script** — create the connection + data agent + run schema discovery on any target via the platform's own API | ~half a day | Unlocks pack 15's NLQ checks, pack 15's NLQ competency tier, and 4 of pack 16's failures at once. Repeatable, idempotent, works on any future client box. **Recommended.** |
| B. Teach packs to SKIP when the fixture is absent | small | Removes false failures but buys no coverage — the checks still never run. Worth doing *as well*, so a missing fixture never reads as a defect. |
| C. Point remote runs at a shared oracle DB | small | The box already reaches SQL at `10.0.0.6:1433`; the missing piece is the *agent* row, not the data. Doesn't avoid needing A. |

A and B together are the right answer: seed what we can, skip honestly when we
cannot.

### 3.2 The runner cannot see the target's disk

~12 pack-14 checks verify engine-written files and skip with
`engine-box disk not reachable via //10.0.0.6/c$`. SMB 445/139 are open but the
calling account is denied; `net view` returns access denied.

**Options**

| Option | Effort | Notes |
|---|---|---|
| A. Grant the runner account access to the share | minutes | Fastest. Ops change, no code. Fragile across client sites. |
| B. Verify through the platform's own file APIs instead of the filesystem | medium | The product already exposes file read/list endpoints; a check that asserts through the product is arguably a *better* check than one that peeks at disk. Portable to any client box. |
| C. Run the runner on the box (§4) | — | Dissolves the problem entirely. |

### 3.3 Internal services are not reachable

Open on the box: 5001, 5091, 5101, 5111, 8100, 1433. Closed: 5011, 5031, 5041,
5051, 5061, 5071 — these bind `127.0.0.1` by design. Consequence: the
document-pipeline probes skip, so knowledge/ingest coverage is thinner remotely
than locally.

Do **not** rebind these to `0.0.0.0` on a client box to make tests pass — the
binding is a deliberate security posture. Either accept the reduced surface, or
run on the box (§4).

### 3.4 Log-based checks

`a7_cc_log_observable` reads CC's log file from local disk and fails remotely
with `log-delta=0 chars`. Either expose a log-tail endpoint, or mark the check
local-only. Low value either way.

### 3.5 SFTP reverse connection

`wf14:file_transfer_sftp_upload` fails remotely because the local test SFTP
server binds `127.0.0.1`, so the engine on the target cannot dial back to it.
**This is a rig artifact and must never be reported as a product defect.** Fix:
bind `0.0.0.0` plus a firewall rule for 2222/2121, or run on the box.

### 3.6 Baseline segregation — *fixed, but only in the driver*

Only pack 15 segregates `results_history` by target. The rest write every run
into one folder and diff against the newest file there, so the first remote run
compared an INSTALLED box to a LOCAL baseline — every environmental difference
read as `REGRESSIONS DETECTED` (packs 16 and 17 both did this) — and the remote
result then became the baseline the next *local* run would be judged against.

The driver now quarantines remote results into `results_history/host_<target>/`
and relabels such a verdict `FIRST-REMOTE-RUN`. **The packs themselves still
have the flaw**; anyone running a pack directly with `REGP_BASE` set will
re-poison the local chain. Porting pack 15's `host_<h>` logic into the other
runners is the durable fix.

## 4. The structural option: run the runner ON the target

Every blocker in §3.2–3.5 is a symptom of one thing — the runner is on a
different machine. Copying `test_human/` to the box and running with
`--target local` there dissolves disk access, log access, internal ports, and
the SFTP reverse-connection problem in a single move, and needs no per-check
special-casing.

Costs: a Python environment on the client box (`aihub2.1` plus `aihub-agent` for
pack 20), a way to ship the packs, and a way to collect reports back.

Recommendation: treat this as the **target state** for post-install validation,
and the current remote-driver path as the pragmatic interim — it already covers
auth, pages, agents, knowledge ingest and retrieval, documents, automations,
portal workflows, approvals, scheduler, secrets, users, MCP, connections, CC and
NL→SQL, which is most of what "did anything obvious break" means.

## 5. Suggested order

1. **Seed fixtures on the target** (§3.1 A) — biggest coverage gain per hour.
2. **Make missing fixtures SKIP, not FAIL** (§3.1 B) — stops false alarms
   permanently, in packs 16/17.
3. **Port `host_<target>` history into the remaining packs** (§3.6) — removes
   the last way a remote run can corrupt local signal.
4. **Bind the SFTP rig to `0.0.0.0`** (§3.5) — one-line, removes a standing
   false failure.
5. Decide on §3.2: grant share access (fast) vs verify via product APIs
   (portable).
6. Evaluate running on-box (§4) as the target state.

## 6. What not to do

- Do not rebind internal services on a client box to satisfy a test.
- Do not let a rig limitation report as a product failure. Every check that
  cannot run remotely must SKIP with the reason named — a FAIL that means
  "my test rig cannot see that" costs more trust than the check was worth.
- Do not compare a remote run against a local baseline.
