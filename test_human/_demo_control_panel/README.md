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
  Per card: ▶ Pre-flight (checks just its dependencies), 📖 Playbook (opens the docx),
  ♻ reset actions, and quick links to the screens the demo uses.
- **Resource matrix** — every service, database server, database, seeded dataset,
  platform object (agents/workflows/portals/automations), fixture folder, and document,
  with live status, detail, **which demos need it** (traceability), and fix/start buttons.
- **Warnings strip** — every down/missing required resource in red, warnings in amber
  (e.g. "expense-audit still installed — delete it so the live build starts clean").
- **Activity log** — output of every action run (seeder, deletes, rebuild scripts).

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
