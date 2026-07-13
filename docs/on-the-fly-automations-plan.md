# On-the-fly Automations — "do it, run it, own it" — Plan

**Status:** P0 + P1 + P2 + P3 BUILT 2026-07-13. Migration 014 APPLIED to the platform DB by James; full lifecycle live-verified against the real platform DB.

**P2 (credential security spine):** `automations/sdk/aihub_runtime/` — a stdlib-only SDK PYTHONPATH-injected into every run; generated code calls `aihub.connection("ERPDB")` / `aihub.secret("NAME")` / `aihub.input("period")`. The runner signs a run-scoped token (`shared_auth.sign_automation_run_token`, aud `automation-run`, carries the manifest's connection/secret ALLOWLISTS); the SDK exchanges it at `/automations/api/runtime/resolve`, which enforces signature + allowlist + run-still-running (a leaked token dies with the run). Credential VALUES are no longer placed in the subprocess env — the legacy env-var path survives behind `AUTOMATIONS_ENV_CRED_INJECTION` (default off).

**P3 (CC front door):** 9 CC tools in `command_center_service/graph/nodes.py` (list/create/get/save_code/dry_run/promote/run/runs/schedule) + `graph/automation_tools.py` HTTP client → main-app `POST /automations/api/internal/manage` (X-API-Key + user_context; **role ≥ 2 re-enforced server-side at the chokepoint**). Gates: `CC_AUTOMATIONS_TOOLS_ENABLED` (default on) / `CC_AUTOMATIONS_ALLOW_ALL_USERS` (default off → Developer+). System-prompt section teaches the build flow: gather → create → save (manifest declares creds/outputs) → dry-run → user confirms → promote → run/schedule; outcomes reported verbatim.

68 unit tests green. Previous status (P0+P1): P0: asset + runner + API (`automations/` package, `migrations/014_automations.sql`, blueprint in app.py, flag `AUTOMATIONS_ENABLED`). P1: **remote-output verification** (`automations/remote_verify.py` — SFTP via paramiko / FTP+FTPS via ftplib, independent post-run listing check, live-tested against the local test server incl. a full runner e2e where a real SFTP upload verified `success` and a claimed-but-absent upload turned exit-0 into `failed`), **`automation` scheduler job type** (`job_scheduler.py` → POSTs `/automations/api/internal/run`; skipped/unverified recorded verbatim), **schedule endpoints** (`POST /automations/api/<id>/schedule` — requires a promoted version, GUID travels in ScheduledJobParameters), and the **`run_python_code` hardening** (GeneralAgent bare `exec` → same-interpreter subprocess + timeout). 54 unit tests green. **Live-DB verified on a local SQL Server scratch DB: migration 014 applies as written and the full lifecycle (create→save→promote→run→history→skip-guard→cascade) works over real pyodbc.** ⚠ On the Azure platform DB, `TenantAppUser` has **no CREATE TABLE permission** — `ensure_tables` cannot self-provision there; **apply `migrations/014_automations.sql` with the admin path used for 001–013** (client installs on local SQL with a fuller login still self-provision). Needs main-app + scheduler-service restarts. Remaining: P2 run-token SDK (retire env-var creds), P3 CC builder tools, P4 workflow node/triggers, P5 Solutions Author distribution; runs UI still open (API-only today).
**Goal:** let a user (initially: an AI Hub developer) *describe* a custom business process to an agent — "read these PDFs, pull the employee number, look up X in the database, produce a CSV in this format, upload it to this SFTP server" — and have the agent build a **persisted, versioned, deterministic Python automation** wired to real connections/secrets, running in a managed Python environment, on a schedule, with honest verified outcomes. The platform's job shifts from *configuring* solutions in a UI to **owning and running** solutions that AI writes.

Related: [cc-silent-success-remediation-plan.md](cc-silent-success-remediation-plan.md) (verify-on-run philosophy), [agent-artifact-sharing-plan.md](agent-artifact-sharing-plan.md) (storage precedents, artifacts), Workflow Builder Option-A hardening (structured builder-agent patterns, confirm-before-build gate).

---

## 1. The thesis

AI writes working Python faster than a human clicks nodes in a canvas. The workflow engine is deterministic but **closed-vocabulary** (~18 node types, `workflow_execution.py:510–566` — no code node); the CC code interpreter can say anything but is **ephemeral** (code vanishes after the chat turn, never versioned, never scheduled). The missing product is the asset in between: **generated code as a first-class, owned, runnable platform object.**

Naming: "Solution" is taken (Solutions Author packaging, `solution_manifest.py`). The new asset is an **Automation**.

An Automation = **script(s) + manifest + environment + connections + schedule + run history.** Five of those six are already built:

| Component | Status | Provided by (verified) |
|---|---|---|
| Runtime (per-solution Python + pip libraries) | ✅ exists | Agent Environments: real venvs off the shipped python-bundle (no conda needed on clients), packages tracked in `AgentEnvironmentPackages` w/ tenant RLS (`agent_environments/environment_manager.py`); full API — create/clone/delete, package add/remove, assign, templates, sandbox test (`agent_environments/environment_api.py:336,446,571,924`) |
| Execution (subprocess, timeout, output capture, artifacts) | ✅ exists | `run_python(code, workdir, python_exe=None, timeout=None)` **already accepts an interpreter override** (`command_center/tools/code_interpreter.py:239`) + `AgentEnvironmentExecutor.get_python_executable(env_id, base_path)` (`agent_environment_executor.py:161`) — the marriage is one argument |
| Connections & secrets | ✅ exists | Connections subsystem + encrypted credential store; builder agents already create connections conversationally |
| Scheduling | ✅ exists | `job_scheduler.py:99` — dict of 5 job types; adding one is mechanical (pattern: `_execute_command_center_job` :1045 — execution record → POST internal endpoint w/ X-API-Key → update record) |
| File transport / triggers | ✅ exists | CC SFTP/FTP tools, File node, inbound-email workflow triggers |
| Gating | ✅ exists | `AGENT_ENVIRONMENTS_ENABLED` flag + role ∈ {2,3} + `tier_allows_feature` (`environment_api.py:188–227`) |
| **Persisted automation asset** (versioned code + manifest, CRUD, run history) | ❌ **gap** | — |
| **Runtime SDK** (`aihub.connection("ERPDB")` etc. — creds resolved at run time, never in code) | ❌ **gap** | — |
| **Verify-on-run** (manifest declares outputs; runner checks they happened) | ❌ **gap** | — |

## 2. Design principles

1. **Deterministic code, agentic build.** The LLM writes/edits the code *at build time*; runs are plain subprocess executions of frozen, versioned code. (The rejected alternative — schedule a CC prompt and re-derive the process per run — is non-deterministic, slow, costly, and the "silent success" audit class incarnate.)
2. **Credentials never enter generated code.** Scripts call a runtime SDK; the runner resolves credentials just-in-time from the encrypted store. Save-time scan rejects code containing connection-string/password patterns (egress-masking precedent from the connections masked-password fix).
3. **Honest outcomes.** Every run ends in a tri-state (`success` / `failed` / `unverified`) computed from exit code **and** declared-output verification — file exists, row count ≥ N, remote listing shows the uploaded file. Never report "done" from the absence of an exception (dominant finding of the builder/workflow audit: 65 findings, dominant class "silent success").
4. **Developers first.** Ship gated Developer+ (role ∈ {2,3}) behind `AUTOMATIONS_ENABLED` + an `AUTOMATIONS_ALLOW_ALL_USERS`-style rollout flag (mirrors `CC_BUILD_ALLOW_ALL_USERS`). Internal use builds the solutions library and tells us which guardrails matter before end-user polish.
5. **Reuse the platform's own plumbing.** Env manager for runtimes, code-interpreter runner for execution, connections for creds, scheduler for cron, artifacts for outputs, Solutions Author for distribution. New code is glue + the asset + the SDK.
6. **Main app owns it.** The env manager, connections, scheduler tables, and auth all live in the main app (aihub2.1 env). The automation asset/runner lands there; CC gets *tools that call it* — CC is the front door, not the owner. (Also: the run endpoint must NOT repeat the builder_service mistake of unauthenticated 0.0.0.0 trusting body-supplied role — X-API-Key + signed user assertion like CC's `/api/scheduled/run`.)

## 3. Target architecture

```
User ⟷ CC / builder conversation ("automate this process…")
          │  create_automation / update_code / dry_run / install_packages / create_connection
          ▼
Main app: Automation asset  ──────────────────────────────────────────────┐
  automations/tenant_<id>/<automation_id>/                                │
    manifest.json   (inputs, connections, packages, outputs, verify)      │
    main.py         (generated, versioned)                                │
    versions/vN/…   (immutable history)                                   │
          │ run (manual / API / scheduled / workflow node / email)        │
          ▼                                                               │
Runner: seed workdir → inject run-context → subprocess via the            │
  automation's agent-environment python.exe (run_python(python_exe=…))    │
          │                                                               │
          ├── aihub_runtime SDK ⇄ 127.0.0.1 main app (short-lived run     │
          │     token → connection/secret resolution; nothing on disk)    │
          ▼                                                               │
Verify declared outputs → tri-state outcome → AutomationRuns history ─────┘
  outputs registered as artifacts (shared-folder store, sidecar registry)
```

## 4. The asset

**Storage:** filesystem-first (mirrors agent-environments' tenant folder layout) + one small `Automations` table for listing/authz/scheduler joins:

- `Automations(automation_id, tenant_id, name, description, owner_user_id, environment_id, current_version, status, created/updated)` — tenant RLS like `AgentEnvironments`.
- `AutomationRuns(run_id, automation_id, version, trigger {manual|schedule|api|workflow|email}, started/finished, outcome {success|failed|unverified}, exit_code, verify_report_json, log_path, artifact_ids)`.
- Code + manifest on disk under `automations/tenant_<id>/<automation_id>/`; every save bumps `versions/vN/` (immutable). Code is diffable — that's the governance advantage over canvas JSON.

**Manifest (`manifest.json`):**

```json
{
  "name": "payroll-pdf-to-sftp",
  "version": 3,
  "entrypoint": "main.py",
  "environment_id": "env_ab12",
  "inputs":  [{"name": "pdf_folder", "type": "path"}, {"name": "period", "type": "string", "default": "current"}],
  "connections": ["ERPDB"],
  "secrets": ["acme_sftp"],
  "packages": ["pdfplumber==0.11.*", "paramiko"],
  "timeout_seconds": 600,
  "outputs": [
    {"kind": "file", "path": "out/payroll_{period}.csv", "verify": {"exists": true, "min_rows": 1}},
    {"kind": "sftp_upload", "secret": "acme_sftp", "remote_dir": "/inbound", "verify": {"remote_listing": true}}
  ]
}
```

The manifest is the **contract**: what the script is allowed to resolve (connections/secrets allowlist for the run token), what it must produce (verification), and what the env needs (packages — reconciled against `AgentEnvironmentPackages` at save time via the existing package API).

## 5. The runtime SDK (`aihub_runtime`)

A small pure-Python package available in every automation environment (installed into the venv at env-provision time, or `sys.path`-injected by the runner — decide in P2; injection avoids per-env installs).

```python
import aihub_runtime as aihub
conn   = aihub.connection("ERPDB")        # pyodbc/SQLAlchemy handle, resolved server-side
sftp   = aihub.secret("acme_sftp")        # dict of fields from the encrypted store
period = aihub.input("period")            # manifest-declared input, value from trigger
aihub.output_file("out/payroll_2026-07.csv")   # registers + later verified
aihub.log("extracted 214 employees")            # structured run log
```

**Resolution mechanics:** the runner mints a **short-lived signed run token** (reuse `shared_auth.py` JWT machinery) scoped to `{automation_id, run_id, allowed_connections, allowed_secrets}` and passes it via env var; the SDK exchanges it at a 127.0.0.1 main-app endpoint for connection details, held in process memory only. Credentials never touch the script text, argv, or disk.
**P0 shortcut (explicit tradeoff):** runner resolves creds itself and passes them via subprocess env vars — acceptable for the Developer-only phase, replaced by the token flow in P2 before any wider rollout.

## 6. The build conversation (CC front door)

New CC tools (Developer+ gated at the chokepoint, mirroring the build-gating pattern):

- `create_automation(name, description)` / `get_automation` / `list_automations`
- `update_automation_code(automation_id, code)` — save-time secret-pattern scan; bumps version
- `set_automation_manifest(...)` — validates connections/secrets exist (create them conversationally first via the existing connection tools; REFERENCE-name validation per the build-honesty fixes)
- `install_automation_packages(...)` — thin wrapper over the existing env package API (`environment_api.py:446`)
- `dry_run_automation(automation_id, inputs, sample_files)` — full runner path against sample data, **verification included**, results + produced artifacts shown inline (artifact plumbing from the artifact-sharing work)
- `schedule_automation(...)` — **hard-gated on ≥1 passing dry-run + explicit user confirm** (the confirm-before-build gate pattern from Workflow Builder Option-A)

Flow for the canonical example: gather requirements → ensure/create the DB connection + store SFTP secret → create/reuse environment, install `pdfplumber`+`paramiko` → generate code against the SDK → dry-run on one sample PDF → show extracted values, the produced CSV, and the verify report → iterate → confirm → version frozen → schedule.

## 7. Running it

- **Manual/API:** `POST /api/automations/<id>/run` (main app; login/JWT for users, X-API-Key + stored user assertion for services).
- **Scheduled:** add `'automation': self._execute_automation_job` to `job_scheduler.py:99` — clone of the CC job shape: typed job params carry `automation_id`, inputs, stored user identity; create execution record; POST the run endpoint; record outcome. Per-schedule timezone etc. comes free.
- **Runner internals:** resolve venv python via `AgentEnvironmentExecutor.get_python_executable`; seed a per-run workdir (inputs, sample/trigger files); write script + preamble (reuse the DLL-dir/headless preamble, `code_interpreter.py:262–273`); subprocess with manifest timeout; sweep workdir for outputs → artifact store; run verification; write `AutomationRuns`.
- **Later triggers:** workflow "Automation" node (canonical node calling the run endpoint, outputs → workflow variables — the Portal-node pattern), inbound-email trigger (subsystem exists), file-watch/webhook (P4+).

## 8. Phases

| Phase | Deliverable | Notes |
|---|---|---|
| **P0** | Asset + runner: tables, folder layout, manifest, CRUD API, run endpoint (env-python subprocess, artifact sweep, run history), manual runs. Creds via env-var injection (documented shortcut). | Smallest end-to-end slice; a dev can hand-write an automation and run it |
| **P1** | Verify-on-run (tri-state + verify report) + `automation` scheduler job type + minimal runs UI (list, outcome, log, artifacts) | Honest outcomes before wider use, not after |
| **P2** | `aihub_runtime` SDK + run-token connection/secret resolution (retire P0 env-var creds); save-time secret scan | The security spine |
| **P3** | CC builder conversation: the tools in §6, dry-run gate, confirm-before-schedule | The product moment: describe → own → run |
| **P4** | Workflow "Automation" node; email/webhook/file-watch triggers | Composability |
| **P5** | Distribution: Solutions Author bundles an automation (code + manifest + package list + connection *references*) for per-client install; per-client env provisioning on install | Turns internal builds into a sellable library |

## 9. Known gotchas (from prior work — check before building)

- **`GeneralAgent.run_python_code` (`GeneralAgent.py:1113`) is a bare in-process `exec()`** — no isolation/timeout when the agent has no assigned environment; it shares the main app process. Fix opportunistically in P0 by routing it through the same runner (env python or bundle python).
- **Frozen onedir app-root:** environment/bundle path resolution must use the fixed app-root helpers (PyInstaller `_internal` broke `dirname/..` chains before). `environment_manager.py:1225–1251` already probes multiple bundle locations — reuse, don't reinvent.
- **aihubbuilder env is lean** — anything CC-side must not import pandas/pyarrow etc.; CC only *calls* the main app (same lesson as CSV-first in the artifact plan).
- **`.gitignore` hides `test*.py`** — new tests under `tests_v2/` need `git add -f`.
- **Egress is ungoverned by design** in the interpreter; unattended scheduled code raises the bar — log outbound destinations per run in P1 (parse at SDK level where possible), allowlisting later.
- **Restart discipline:** main app + scheduler restarts to go live; live tree runs from `aihub-client-ai-dev` (never propose dist sync as a runtime fix).

## 10. Open questions — DECIDED (James, 2026-07-13)

1. **Env granularity:** **one environment per automation.** Clean isolation; disk cost accepted. The automation's manifest records its `environment_id`; create-automation provisions (or clones) a dedicated env.
2. **Version pinning:** **frozen `vN` at schedule time, with an explicit "promote latest" action.** Schedules and API runs execute a pinned version; editing code never silently changes what a schedule runs.
3. **Concurrent runs:** **skip-if-running, always.** No manifest flag, no overlap handling — if a run for the automation is in flight, a new trigger records a `skipped` run and exits. Keep it simple.
4. **Dry-run samples:** **copied into `versions/vN/samples/`** so every version's test is reproducible.
