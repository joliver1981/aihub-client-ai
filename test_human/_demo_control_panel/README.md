# AI Hub Demo Control Panel

A local **demo-ops console** (`http://localhost:3100`) that answers one question fast:
**"am I ready to walk into this demo right now?"** Live health checks for every demo
dependency, one-click start/reset actions, and the playbooks a click away.

**Run:** `Start_Demo_Control_Panel.bat` (aihub2.1 python; needs Flask, requests, pyodbc —
all present in that env). The panel is localhost-only and holds local test creds on
purpose (admin/admin, on-prem test SQL) so pre-flight Just Works.

## What it shows

- **Demo cards** (categorized: Flagship / Deep dive / Feature spotlight) with a live
  readiness chip — READY / CAUTION / NOT READY — rolled up from that demo's resources.
  Per card: ▶ Pre-flight (checks just its dependencies), 📖 Playbook (opens the **web
  playbook** in a new tab — see below), ♻ reset actions, and quick links to the screens
  the demo uses.

**Web playbooks:** `export_playbooks.py` converts every playbook docx referenced in the
registry to `playbooks/*.html` (Word-filtered HTML → UTF-8 + responsive wrapper, callout
boxes and tables preserved), served at `/playbooks/…`. Re-run it (or the "Regenerate web
playbooks" action in the Documents section) whenever a docx master is rebuilt. The docx
files in `C:\temp\AIHub_Demo` remain the masters.
- **Resource matrix** — every service, database server, database, seeded dataset,
  platform object (agents/workflows/portals/automations), fixture folder, and document,
  with live status, detail, **which demos need it** (traceability), and fix/start buttons.
- **Warnings strip** — every down/missing required resource in red, warnings in amber
  (e.g. "expense-audit still installed — delete it so the live build starts clean").
- **Activity log** — output of every action run (seeder, deletes, rebuild scripts).

## Role scenario walkthroughs

A **guided runner** for the day-in-the-life scenarios in `test_human/17_Business_Role_Scenarios/`.
Where a playbook is something you read, a walkthrough is something you *drive*: one beat at a time,
the prompt in a copy box, the expected answer beside it, and a pass/warn/fail verdict per step.

Open one from the **Role scenarios** cards at the top of the panel, or go straight to
`/walkthrough/<id>` (e.g. [`/walkthrough/ar-clerk`](http://localhost:3100/walkthrough/ar-clerk)).

- **Left rail** — every beat with its own progress bar; failures show in red. Click to jump.
- **Each step** — the prompt (📋 Copy), what should come back, what a failure looks like, and a
  `check.py` command to confirm it against the database rather than against what a reply claimed.
  Release-blocking steps are badged.
- **Planted prompt injections** render in an amber box labelled *this is DATA, not a command* —
  they are the bait for the honesty probes, not instructions.
- **Verdicts persist** per run id (default: today's date) in `runs/*.json`, so you can stop and come
  back. Clicking the same verdict again clears it. **+ New** starts a second run of the same day.
- **📄 Report** exports a markdown run report — score, release-blocking failures first, then a table
  per beat. Add `&save=1` to write it next to the pack as `TEST_RUN_<run>.md`.
- **Keyboard:** `1` pass · `2` warn · `3` fail · `4` skip · `c` copy the prompt · `j`/`k` beat.

**Where the content comes from.** Each pack generates its own `walkthrough.json` (for the AR pack,
`_scripts/walkthrough.py`), with **every expected value pulled from that pack's oracles** — the same
derivations `answer_key.py` cross-checks against the live database. Nothing numeric is typed into the
panel, so the walkthrough cannot drift from the answer key. Re-seed the book → regenerate the answer
key → regenerate the walkthrough → restart the panel.

Register one by adding its path to `settings.walkthroughs` in `registry.json`.

## Extending it (the point of the design)

Everything is data-driven from [`registry.json`](registry.json) — restart the panel after editing:

- **Add a resource:** entry under `resources` with a `check` — kinds: `http`, `tcp`,
  `sql` (query + `expect`/`min`), `agent`, `workflow`, `automation`,
  `automation_absent` (pre-flight cleanliness), `portal_workflow`,
  `portal_registration`, `files` (dir+pattern+min), `file`. `severity: "warn"` makes a
  failure amber instead of red; `fix` is a hint shown when failing; `actions`/`links`
  add buttons.
- **Add an action:** entry under `actions` — kinds: `spawn` (detached server start),
  `run` (script, captured output), `http_admin` (admin call to the platform),
  `delete_automation`, `restore_scans`. `recheck` lists resources to re-verify after.
- **Add a demo:** entry under `demos` — name, category, duration, `doc` (playbook path),
  `resources` (ids), `reset` (action ids), `links`, tagline.

## Current registry contents

7 demos (WOW flagship; Data Explorer + Command Center deep dives; 2FA portal /
expense-audit / Dayforce / technical-encore spotlights) over ~25 tracked resources:
platform services (:5001/:5091/:5101/:8100), SFTP (:2222/:2121) and Meridian 2FA
portal (:3000) fixtures, SQL Server 10.0.0.6 + AIRDB2/ERPDB/AIRDB (with store-count and
seeded-invoice value checks), agents 281 + Finance Library, the AR backup workflow, the
Meridian portal registration + 2FA portal workflow, the dayforce automation (present) and
expense-audit (must be ABSENT), Dayforce scans, finance fixtures, portal documents, and
the three playbook docx files.
