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

---

## 2. Order of work from here

1. **DONE** — seed fixtures on the target (§3.1 A).
2. **DONE** — missing fixtures SKIP, never FAIL (§3.1 B), packs 14–22.
3. **DONE** — per-target history in every pack (§3.6).
4. **DONE (script)** — SFTP rig LAN bind. Still needs the one-time elevated
   firewall rule on the dev box before a remote File Transfer check can pass:
   `netsh advfirewall firewall add rule name="AIHub SFTP test rig" dir=in action=allow protocol=TCP localport=2222,2121,60000-60099`
5. **Port the 12 disk-verified pack-14 checks to the variables API** (§1.5).
   Half a day; removes the last `engine-box disk not reachable` SKIP class.
6. **Configure an Anthropic key on the test-install box** (BYOK or the relay)
   — pack 20 is BLOCKED until then, and so is The Agent for any tester there.
7. **NEW — pack 20 coverage for The Agent's recent features.** Pack 20 today
   has no check that touches maps, generated images, charts/KPI fences, vision,
   data export or PDF manipulation (passes 2–4 shipped with live probes but no
   regression rows). Add a `R-*` (rich output) group, graded on the persisted
   block/artifact, never on prose:
   - `R-1` `render_map`: a "map of our 15 stores" turn yields a stored map block
     (Leaflet fence) with ≥15 markers; offline mode (`AGENT_GEOCODING=false`)
     uses state centroids, so it must PASS without Nominatim.
   - `R-2` `geocode_places`: online → real lat/lon for a known city; offline →
     honest "geocoding disabled" with no invented coordinates.
   - `R-3` choropleth: a per-state metric request produces a choropleth block
     keyed by state, values matching the AIRDB2 oracle.
   - `R-4` `generate_image`: with a BYOK OpenAI key an image block is stored
     under `/api/files` and renders inline; without the key the tool refuses
     honestly (no placeholder image, no fake URL).
   - `R-5` `aihub-chart` / `aihub-kpi` fences: a "chart sales by category"
     turn stores a Chart.js fence whose dataset matches the oracle; the
     category-axis `callback: undefined` trap must not reappear.
   - `R-6` `read_file` vision: an attached PNG is described via image blocks
     (the SDK forwards them); a text file is still read whole.
   - `R-7` `export_data` rows and SQL lanes: a CSV/XLSX export is a real file
     with the oracle row count; a non-SELECT statement is refused (the
     interpreter lane's SELECT-only gate).
   - `R-8` `manipulate_pdf`: merge/split/rotate produce a valid PDF with the
     expected page count.
   Each row: tool used (from the SSE `tool` events) **and** artifact verified
   through the API, same rule as A1/PT. Run in both the dev tree and the
   installed box; the installed box needs step 6 first.
8. **Evaluate on-box execution as the target state (§4).** Cheaper than the
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
