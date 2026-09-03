# Regression testing against an INSTALLED build — plan and state

**Owner:** james · **Built:** 2026-09-02 · **Companion:** the research write-up this
plan answers (blockers §3.1–3.6, on-box option §4) and
`test_human/_scripts/run_regression_suite.py` (the two-target driver).

The write-up's diagnosis holds: the suite already runs against `10.0.0.6`; what
it could not do was *see enough of the box* to make every check meaningful, and
the packs themselves reported rig limitations as product failures. Everything
in §1 is now built and live-verified against `10.0.0.6`; §2 is the remaining
order of work, including the new pack-20 step.

---

## 1. What was built (2026-09-02)

### 1.1 Fixture seeding on any target — `_scripts/seed_target_fixtures.py`

Idempotent, API-only (nothing touches the target's disk or database directly),
resolves everything by **name** so ids never matter again:

| fixture | how | why a pack needs it |
|---|---|---|
| connections `AIRDB2`, `AIRDB` | `/add/connection` + `/api/connections/<id>/test` (aborts if the target cannot reach SQL) | NL→SQL oracle; pack 14 Database nodes look up `AIRDB` by name |
| data agents `Retail Demo - AIRDB2 (15 stores)`, `Demo AirDB Agent 4` | `/add/data_agent` bound to `AIRDB2` | packs 15/16 oracle (15 stores, 75 employees); pack 16's two-agent reference |
| AIRDB2 data dictionary, 10 tables / 94 columns | `/api/table/update` + `/api/column/update` from `_scripts/fixtures/airdb2_dictionary.json` (exported from the dev tree with `--export`) | without it the NLQ engine answers *"no documented schema"* and never writes SQL |
| secrets `AUTODEMO_SFTP`, `SFTP_TEST_PASSWORD` (= the throwaway rig password) | `/api/local-secrets`, never overwritten | File Transfer node + pack 16 b6 name the secret |
| general agent `Regression Sim Agent` | `/add/agent`, prompt-only | pack 19's simulated user/judge (dev tree uses `Master Agent`, id 84) |

`--verify` closes the loop by asking the seeded oracle the pack-15 question
through `/chat/data` and requiring **15**. Measured on `10.0.0.6`: first run
created all of the above and verified PASS in 15 s; the second run created
nothing. The driver runs it for you: `--seed` / `--seed-verify`.

### 1.2 Packs resolve fixtures by name and SKIP honestly (§3.1 B, §3.4, §3.5)

- **Pack 16** — `ORACLE_DATA_AGENT = 281` (14 sites) is gone. The oracle and the
  second agent are resolved at startup (`REGP_ORACLE_AGENT=<id|name>` overrides);
  b1/b2/b3/b5/b12 SKIP with the seed hint when absent. a3/a4/a5 (which read the
  *dev tree's source*) and a7 (which reads the *local* CC log) SKIP on a remote
  target — they said nothing about the box. b6 SKIPs without `AUTODEMO_SFTP`.
  b7's grader now accepts "couldn't find" (it missed an honest reply on 09-02).
- **Pack 15** — the three `281` sites (NLQ chat, honesty probe, Data Explorer
  browser lane) use the same resolver; pack 14's rows are read from the
  per-target history.
- **Pack 19** — resolves the sim/judge agent by name (`Master Agent`, then
  `Regression Sim Agent`, or `TIERC_SIM_AGENT`) and SKIPs the whole pack when
  none exists, instead of silently judging with empty verdicts.
- **Pack 20** — health is retried; when the target reports
  `anthropic_key_present=false` the pack stops with a single **BLOCKED** row
  (exit 3) instead of thirty FAILs. The driver surfaces BLOCKED as an
  environment state, not a product failure.
- **Pack 22** — refuses a remote target outright (its lane attribution reads
  the local ledger); the driver marks it local-only.
- **Pack 14** — probes the SFTP rig at the address the *engine* will dial
  (the dev box's LAN IP in remote mode) and SKIPs with an actionable reason
  when the rig is loopback-only. `_sftp_test_server/Start_SFTP_Server_LAN.bat`
  binds it to `0.0.0.0` and checks for the inbound firewall rule (adding the
  rule needs an elevated prompt; the script prints the exact command).

### 1.2b Command Center tokens carry the target's admin id (found 2026-09-03)

Packs 16 and 19 signed their CC tokens as user 13 — the dev tree's admin. The
installed box's admin is user 1 and has no user 13, and CC stamps the token's
user id on everything a turn creates, so every automation or schedule CC built
on the box belonged to a nonexistent owner. Pack 19's judge reported an
"owner-account configuration error" on all three scenarios and it looked like
a product defect. Both packs now resolve the admin id from the target's
`/get/users` before signing (fallback 13); packs 20 and 24 already signed as
user 1.

### 1.3 Transient connect failures no longer read as regressions

On 09-02 five pack-17 checks and pack 20's health check ERRORed on a single
`ConnectTimeout` each while the box answered every retry; packs 18/19/22 died
in `App()` the same way and reported bare `rc=1`. Every pack's HTTP chokepoint
(`App.get/post/put/delete`, `login_as`, health) now retries a **connect**
failure three times with backoff. Read timeouts are deliberately not retried —
the request may already have been applied.

### 1.4 Per-target history in every pack (§3.6)

Each runner derives the target from `REGP_BASE` and writes an installed box's
results to `results_history/host_<ip>/` and its report to
`REPORT_LATEST_<ip>.md`. `REPORT_LATEST.md` and the flat history are the local
chain only. The driver's quarantine stays as a safety net. The remote runs that
had already landed in packs 14 and 18's flat histories were moved.

### 1.5 Decision on §3.2 (engine-box disk): verify through the product

Do **not** grant the runner account the admin share; it is fragile across
client sites and proves nothing a customer could see. The engine's File node
supports `operation: read` with `outputVariable`, and pack 14 already reads
`/api/workflow/executions/<eid>/variables`. Recipe for each of the 12
`disk=True` checks: append `file_node("node-v", "read", p,
extra={"outputVariable": "verify_content"})` and assert
`api.variables(eid)["verify_content"]` instead of `read_file(p)`. That is a
*better* check (the product proves the file) and it is portable to any client
box. Not yet applied — it is step 5 below.

### 1.6 Measured: the same box, before and after (2026-09-02)

Full sweep, competency tier on, driver `--target 10.0.0.6`. "Before" is the
morning run from the write-up; "after" is with the fixtures seeded and the
pack changes above (`suite_runs/SUITE_10.0.0.6_20260902_212522.md`).

| pack | before | after | what changed |
|---|---|---|---|
| 15 Platform Regression | 49 PASS / 1 FAIL / 51 SKIP | 52 PASS / 2 FAIL / 47 SKIP | NL→SQL oracle, NLQ honesty probe and the pack-14 `AIRDB` Database checks now RUN. Both FAILs are `de_pin_dashboard`/`de_pin_live`: the installed build predates the Data Explorer pin fix (fbda297) — a real finding, not a rig artefact. SFTP SKIPs with the LAN-bind instruction. |
| 24 Installed Smoke | 3 PASS / 1 FAIL | 3 PASS / 3 FAIL | the other session's two new checks pin the `command_center.artifacts.data_export` packaging defect; The Agent FAIL = no key on the box |
| 16 CC Agent Matrix | 15 PASS / 8 FAIL / 1 SKIP | 15 PASS / 4 FAIL / 5 SKIP | 4 fixture FAILs gone. The 4 left (b1/b2/b5/b12) all hit the **real** `data_export` 500 through CC → seeded agent 7019, each row saying `delegation-failed=True`. 5 SKIPs = local-only checks, honestly labelled. Two grader defects were caught on the way: b6 missed the honest "which connection / which SQL?" ask (now PASS), and b5 *passed* on the "15" inside the agent's own name in an error reply — the graders now fail any reply that reports the delegation itself failed (rerun `host_10.0.0.6/results_20260902_212554`). |
| 17 Scheduling Matrix | 11 PASS / 5 ERROR | 16 PASS / 2 XFAIL / 1 XPASS — CLEAN | the five ERRORs were single flaky connects; every competency check (does it fire, does disabled stay silent, does delete stop it) ran and passed on the install |
| 18 AuthZ Matrix | rc=1, no report | 9 PASS / 6 SKIP / 1 XFAIL / 1 XPASS — CLEAN | crashed in login on one connect; retried now |
| 19 CC Tier C | rc=1, no report | 3 FAIL (13 min) | ran with the seeded judge; all three scenarios end with **no artifact**, same as locally, and on this box CC keeps retrying the data agent that 500s |
| 20 The Agent | 0/1 (health connect timeout) | **BLOCKED** — no Anthropic key on the box | one honest row instead of a red pack; unblocks when BYOK/relay is configured there |
| 22 Code Interpreter | rc=1 | SKIPPED — local-only | says why |

Net: every remaining red on the installed box is a product or configuration
finding with the reason in the row, and none of them is the rig.

### 1.7 Latest7 on the same box (2026-09-03, fixtures kept across the upgrade)

Seed dry-run after the upgrade: everything still present, nothing created.
Sweep with `--seed-verify --competency --skip 20_The_Agent`, pack 20 run
separately with the new `R-*` rows (§2.7).

| pack | Latest5 (09-02) | Latest7 (09-03) | reading |
|---|---|---|---|
| 15 Platform Regression | 52 PASS / 2 FAIL / 47 SKIP | **CLEAN** 54 PASS / 47 SKIP / 2 XFAIL / 3 XPASS | the Data Explorer pin FAILs are gone — Latest7 carries fbda297 |
| 24 Installed Smoke | 3 PASS / 3 FAIL | **CLEAN** 6 PASS | `data_export` packaging defect fixed on the box; The Agent answers over the relay |
| 16 CC Agent Matrix | 15 PASS / 4 FAIL / 5 SKIP | **CLEAN** 19 PASS / 5 SKIP | CC → seeded data agent works; b1/b2/b5/b12 now PASS on the install |
| 17 Scheduling Matrix | CLEAN | **CLEAN** 16 PASS / 2 XFAIL / 1 XPASS | |
| 18 AuthZ Matrix | CLEAN | **CLEAN** 9 PASS / 6 SKIP / 1 XFAIL / 1 XPASS | |
| 19 CC Tier C | 3 FAIL | 3 FAIL (no artifact in any scenario) | same result as the dev tree; rerun with the admin-id fix (§1.2b) changed nothing, so it is the pre-existing tier-C competency gap, not the rig |
| 20 The Agent | BLOCKED (wrong gate) | 61 / 96 PASS, `R-*` 9 / 11 | relay confirmed (`A0-4k` = relay). Rich output on the install: maps, choropleth, geocoding, chart+KPI, grounded chart, vision, SELECT-only gate all PASS; **R-7 export_data and R-9 manipulate_pdf deliver no file** on the box (both fell back to run_python) — the per-tool smoke (§2.8) captures their tool errors. The other 33 FAILs are one cascade (the agent's `save_view` did not persist tiles, so ten view rows 404 on a view that never existed), local-only checks (a SKILL.md on disk, log files), and shipped-off posture (email not set up, role-1 access off, model defaults differ) — triage in §2.9. |

Local dev tree, `R-*` standalone (haiku, byok): 10 / 11 PASS — R-1..R-4 and
R-6..R-9 real, R-10 opt-in SKIP; R-5 fails only because the local main app was
down at the time (the probe cannot reach a connection without it).

### 1.8 Per-tool smoke on Latest7: two whole-feature defects in five minutes

`tool_smoke_checks.py` standalone against the box, 95 tools, 310 s:
**87 / 97 PASS**, every tool was called, and the ten reds split cleanly.

| tools | result on the install | root cause |
|---|---|---|
| `run_python`, `export_data`, `manipulate_pdf` | handler crashes: `No module named 'code_exec'` | `code_exec/` is a repo-root package that `agent_service/code_tools.py` imports for the interpreter lane. Neither `scripts/build_agent_service.ps1`, `stage_cc_tools_subset.ps1` nor the v5 `.iss` stage it, so **The Agent's code-interpreter lane (and everything built on it: exports, PDF tools, inline plots) is dead on every install**. This is R-7/R-9's root cause. Fix shape: stage a service-local copy like the `command_center.tools` subset, with the same closure guard. |
| `list_agents`, `get_agent_builder_options`, `create/update/delete_general_agent` | ODBC `Login failed for user ''` | `agent_service/readthrough.py::_db()` opens the app database directly from `DATABASE_SERVER/NAME/UID/PWD` env vars. The installed service does not see those (the main app keeps its DB credentials elsewhere), so UID is empty. **The agent-builder tools are dead on installs.** Eight modules import `readthrough`; only the builder tools failed in the smoke, so the others fall back or take another path — worth a look. |
| `wire_steps`, `unwire_steps` | honest 400 "both from and to must be existing step ids" | my probe passed step names; the tools take step ids. Probe fixed (placeholders now say "step id of s1"). |

The other 84 tools answered with their documented shape, including the honest
not-found on every nonexistent-id probe. Neither defect reproduces from source
(the dev tree has `code_exec/` on `sys.path` and the DB variables in `.env`),
which is the whole argument for testing the installed build.

### 1.9 Both defects fixed (2026-09-03) — what changed, and what did not

**Defect 1, `code_exec` (interpreter lane dead on installs).** The Agent's
build now stages a private copy of the package: `scripts/stage_code_exec.ps1`
(same pattern and closure guard as the `command_center.tools` subset) is
called from `build_agent_service.ps1`, so `dist\agent_service\code_exec\`
ships with no `.iss` change. Two guarded fallbacks inside `code_exec/sdkwire.py`
let the copy work without `CommonUtils` (source-run services never have it):
the SDK dir is also looked up under `APP_ROOT\automations\sdk`, and the
platform base URL falls back to the same `PROTOCOL/SERVICE_HOST/HOST_PORT`
rules the other source-run modules use. Frozen exes are unaffected (their
`CommonUtils` path is tried first and resolves to the same values). Proof:
`tests_v2/unit/test_stage_code_exec.py` (4/4) and a scratch client layout
running the STAGED copy with no repo root on the path: `code_exec` resolved
from `dist\agent_service\code_exec`, `CommonUtils` absent, sandbox printed 42.

**Defect 2, database credentials (builder tools, group visibility, pending
approvals, user directory dead on installs).** Root cause, to answer the
"same root folder?" question: the Agent has the same `APP_ROOT` and loads the
same `{app}\.env` as every other service. A fresh install's `.env` simply has
no `DATABASE_*` keys; the compiled services get them from a `_build_config`
module baked inside each exe, and the loose copy on disk is deliberately
trimmed to LLM keys. A source-run service therefore has no legitimate
credential source, and copying the database password anywhere readable would
have been a security regression. So the fix removes the need:
- the main app gains one read-only internal endpoint,
  `POST /api/internal/readthrough` (`@internal_api_key_required`, the
  machine-bound key the services already use for the other `/api/internal/*`
  routes), whose eight named ops run the SAME SELECTs the service used to run
  itself, under the app's own credentials and tenant context. Fixed SQL with
  bound parameters; nothing from the request reaches SQL text.
- the service (`readthrough.fetch_or_sql`) asks that endpoint first and falls
  back to its old direct-SQL path only when the endpoint is absent (404: an
  older main app), the key is rejected (401) or the app is unreachable. A
  server-side failure surfaces as an error, never a silent fallback.
  `AGENT_READTHROUGH_HTTP=false` is the rollback switch.
- callers changed: the five builder reads (`_all_agents`, `_fetch_agent`,
  groups, group membership, knowledge row), `readthrough.user_group_ids` and
  `workflow_pending`, `work_tools._user_directory`,
  `platform_tools.find_user_contact`. The write paths already went over HTTP.

Behaviour matrix: dev tree today → HTTP path, identical rows by construction;
an install on the next build → HTTP path, works; an install whose main app is
older (Latest7 today) → direct SQL exactly as before. No credentials move, no
schema changes, no other service touched. Pinned by
`tests_v2/unit/test_agent_readthrough_http.py` (8/8: HTTP wins, 404/401/
unreachable → SQL, 500 → error, rollback switch).

Live proof on the dev tree (2026-09-03, main app started by the standard v3
batch on the patched code): the endpoint answered every op with the internal
key (agents 309 rows, users 14, groups 18, pending approvals 74), 400 on an
unknown op, 401 on a wrong key; the builder and directory tools driven through
the Agent passed **15 / 15 with the HTTP path** and **15 / 15 with
`AGENT_READTHROUGH_HTTP=false`** (direct SQL), including a real
create → update → set tools → delete (confirmed, read back) of the throwaway
agent. Same answers both ways, which is the no-regression bar.

---

### 1.10 Batch 2 (2026-09-03, james's decisions on the open-issues table)

| where | change | how it is pinned |
|---|---|---|
| Command Center | one 28-word sentence in the build prompt: when the user named no connection or table, discover first and propose the best match; ask only when nothing fits | `test_batch2_install_fixes.py` (single occurrence, ≤ 30 words); live effect to be measured with pack 19 repeats |
| Main app, auth middleware | Portal Workflows internal endpoints (`internal_run`, `internal_notify_takeover`) are self-authenticating: the Browser Use take-over notification (`X-AIHub-Internal`) was 401'd by an ENFORCING install before the route ran. Ships dry-run (`AUTH_MIDDLEWARE_DRY_RUN=true`); the test box enforces. Startup prints are ASCII now. | unit test on the prefix set and on every print encoding to cp1252 |
| Main app, Data Dictionary | the Import column is hidden (no `/api/import/dictionary` route); JS kept | unit test |
| Main app startup | one 3-second probe of the Agent API; when it is down, adapters load without the per-agent display-name lookup (cosmetic; agents work either way). Same in `load_agents()`. | adapter + probe unit tests |
| The Agent front door | `THE_AGENT_ENABLED=true` in the shipped `.env` for testing; nav entry and `/the-agent` gated to Developers/Admins unless `AGENT_ALLOW_ALL_USERS=true`, matching the service's own gate; installer seeds `AGENT_ALLOW_ALL_USERS=false`. Upgrades keep their existing `.env`, so the 10.0.0.6 box needs `THE_AGENT_ENABLED=true` flipped by hand. | template parse + route/context tests |
| Pack 20 | I-2 is local-only (in-process handler, this machine's internal key) and SKIPs on a remote target | — |

Not changed: `/get/*` double-encoded JSON (workaround stays), pack 19 dimension
grading, and anything about how services start.

## 2. Order of work from here

1. **DONE** — seed fixtures on the target (§3.1 A).
2. **DONE** — missing fixtures SKIP, never FAIL (§3.1 B), packs 14–22.
3. **DONE** — per-target history in every pack (§3.6).
4. **DONE (script)** — SFTP rig LAN bind. Still needs the one-time elevated
   firewall rule on the dev box before a remote File Transfer check can pass:
   `netsh advfirewall firewall add rule name="AIHub SFTP test rig" dir=in action=allow protocol=TCP localport=2222,2121,60000-60099`
5. **Port the 12 disk-verified pack-14 checks to the variables API** (§1.5).
   Half a day; removes the last `engine-box disk not reachable` SKIP class.
6. **CORRECTED (james 2026-09-03): The Agent on an install uses the RELAY,
   never a local Anthropic key.** `/health` reports `anthropic_key_source:
   "none"` until the first turn arms the relay; after a turn on Latest7 it
   reads `relay` and the turn answers. The BLOCKED gate that keyed on the
   pre-turn health was wrong and is gone; pack 20 now records the key source
   *after* its first turn (`A0-4k`: relay | byok | env | encrypted, never
   none). The Agent also ships OFF on installs (front door, all-users) — the
   packs drive the service directly on :5111 and grade the shipped posture.
7. **BUILT (2026-09-03) — pack 20 `R-*` rich-output rows**
   (`test_human/20_The_Agent/rich_output_checks.py`, called by the runner
   before its report; runnable standalone against local or a box). Graded on
   the persisted block/artifact, never on prose; posture-aware, so an honest
   refusal is the PASS where a capability is legitimately off:
   - `R-0` posture: a real turn answers; key source after it (relay on an
     install), `allow_all_users`, model — informational.
   - `R-1` `render_map` from place names → stored block (`/api/blocks`) with 3
     CONUS markers and an "approximate/geocoded" disclosure; offline posture
     (`AGENT_GEOCODING=false`) → honest refusal, no invented positions.
   - `R-2` choropleth: NJ/TX/CA normalized to state names, Ontario carried as
     `unmapped` and disclosed in the reply (bundled GeoJSON, no geocoder).
   - `R-3` `geocode_places`: two real CONUS coordinate pairs online; offline →
     refusal with **no** decimal coordinate pairs anywhere in the reply.
   - `R-4` `aihub-chart` + `aihub-kpi` fences carry the user's exact four
     numbers (labels set and sorted series compared, KPI cards present).
   - `R-5` grounded chart: `probe_connection_query` on the seeded `AIRDB2`
     connection, stores per state, series sums to the oracle 15.
   - `R-6` vision: the committed `fixtures/r6_bars.png` is uploaded through
     `/api/uploads`, attached to the turn, read via `read_file`, and "Beta"
     (the tallest bar) is named.
   - `R-7` `export_data(rows_json)` → the delivered `.xlsx` is downloaded and
     parsed (zip + sheet XML): 4 rows × 2 columns.
   - `R-8` SELECT-only gate: a `CREATE TABLE` through `export_data` is refused,
     no CSV is delivered, and the probe table is proven absent on the database
     through the main app's connection API (and dropped if it ever appears).
   - `R-9` `manipulate_pdf`: the 4-page fixture is uploaded; info says 4 and
     the extracted 1-2 PDF parses to 2 pages.
   - `R-10` `generate_image`: opt-in with `PACK20_IMAGE=1` (real money);
     otherwise SKIP. With the flag: inline image line + PNG > 5 KB, or the
     honest "no OpenAI API key" refusal on a box without one.
   Every row: tool call seen in the SSE `tool` events **and** artifact verified
   through the API, same rule as A1/PT.
8. **BUILT (2026-09-03) — pack 20 `T-*` per-tool smoke**
   (`test_human/20_The_Agent/tool_smoke_checks.py`, runs after `R-*`;
   `PACK20_SKIP_TOOL_SMOKE=1` leaves it out). Every one of the 95 mounted
   tools is called once with fixed, harmless arguments and graded on its own
   `tool_result` event: not called, or a traceback / "No module named" /
   internal error in the result, is a FAIL; an honest not-found on a
   nonexistent id is a PASS. Read tools are called for real; mutating tools
   are called on nonexistent ids (nothing changes); creates run as
   create→use→delete of `pack20-smoke-*` objects; the eight tools that send
   mail, raise work, save skills/portals or spend money are marked
   "exercised by A2/A3/A6/PT/R-10" so nothing is unaccounted for. `T-0`
   compares the target's mounted inventory with the expected set — builds
   after 2026-09-03 emit it from the SDK's init message (`brain.py`, names
   only); older builds say the event is not available.
9. **Pack 20 remote triage.** Pack 20 was written for the dev box; on an
   install, three kinds of row need the same treatment packs 15/16 got:
   local-only rows (SKILL.md on disk, log files) SKIP remotely; posture rows
   (agent email not configured, role-1 access off, model defaults) grade the
   shipped posture instead of assuming the dev tree's; and cascades (ten view
   rows that 404 because one `save_view` failed) should collapse to the one
   root row. Until then, read the 61/96 as "35 rows to triage", not "35
   defects".
10. **Evaluate on-box execution as the target state (§4).** Cheaper than the
   write-up assumed: every install already ships `{app}\python-bundle`
   (Python 3.11 with `requests`), and packs 15/16/17/18 need nothing else.
   Recommended shape: copy `test_human/` to the box, run
   `run_regression_suite.py --target local` with that interpreter, pull
   `results_history/` back. Pack 20 still needs `aihub-agent` (a `conda-pack`,
   ~2.5 min in the build chain) and pack 15's browser lane needs Playwright's
   Chromium. It dissolves §3.2–3.5 in one move; whether a client box may carry
   the packs is james's packaging decision.

## 3. What not to do (unchanged from the write-up)

- Never rebind internal `127.0.0.1` services on a client box to satisfy a test.
- Never let a rig limitation report as a product failure; every SKIP names its
  reason and, where one exists, the command that removes it.
- Never compare a remote run against a local baseline (now structurally
  prevented in every pack).

## 4. Running it

```bash
# installed box, fixtures seeded first, competency tier on
python test_human/_scripts/run_regression_suite.py --target 10.0.0.6 --api-key-file C:\Users\james\.secrets\aihub-10.0.0.6-api-key.txt --seed-verify --competency
```

```bash
# local dev tree
python test_human/_scripts/run_regression_suite.py --target local --competency
```

Per-pack reports for an installed box: `test_human/<pack>/REPORT_LATEST_<ip>.md`;
suite summary: `test_human/_scripts/suite_runs/SUITE_<ip>_<stamp>.md`.
