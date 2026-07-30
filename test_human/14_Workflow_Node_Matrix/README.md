# 14 — Workflow Node Regression Matrix

**Why this exists.** The Database→Excel Export pairing was broken for months and *nothing could have
told you*: no test ever **executed** that pairing and checked the real artifact. Feature-level smoke
(pack 11) opens pages and runs happy paths; unit tests exercise functions. Neither catches "this node
type quietly stopped producing output at some point." This pack does: it **builds a minimal workflow
per node type and per common node pairing, executes it through the real engine, and verifies the
actual artifact against an exact oracle** — then **diffs every run against the previous run**, so a
check that was PASS and is now FAIL screams **REGRESSION** in the report.

**Where it sits.** Run it alongside `11_Regression_Suite` before a release:
- Pack 11 = "the 9 basic features work, driven through the UI like a human."
- Pack 14 = "every workflow building block still executes correctly" (deep, per-node, oracle-exact).

---

## How to run

```bash
cd C:\src\aihub-client-ai-dev\test_human\14_Workflow_Node_Matrix
C:\Users\james\miniconda3\envs\aihub2.1\python.exe runner.py
```

Useful flags:

| Flag | Meaning |
|---|---|
| `--tier 1` | core engine only (no AIRDB/SFTP/approvals deps); `--tier 2` (default) adds integrations |
| `--only substr` | run only checks whose id contains the substring |
| `--cleanup` | delete the `NODEREG-*` workflows afterwards (default: keep & reuse by name) |
| `--list` | print the check catalog and exit |
| `--timeout 90` | per-check execution timeout (approval checks use 150s) |

Prereqs: main app on `:5001` (admin/admin). Tier 2 wants: an **AIRDB** connection registered, the
**SFTP test server** on `127.0.0.1:2222` + a secret (`SFTP_TEST_PASSWORD` or `AUTODEMO_SFTP`). Missing
deps → those checks **SKIP with the reason recorded** (never silently).

## What a run does

1. Logs in as admin and probes the environment (AIRDB connection id, SFTP port, secrets, admin user id).
2. For each check: **saves** a `NODEREG-<check>` workflow via `POST /save/workflow` (the Designer's
   own save endpoint), **runs** it via `POST /api/workflow/run` (the Run button's endpoint), polls
   `GET /api/workflow/executions/<id>`, then **verifies the real artifact** — file content on disk,
   execution variables, xlsx rows via pandas, files landing on the SFTP server, approval-pipeline
   round-trips via `POST /api/workflow/approvals/<request_id>`.
3. Runs a **config lint** over the newest 60 workflows (+ all NODEREG ones): Excel Export nodes
   missing `inputVariable` / invalid `excelOperation`, and unknown node types. (This is the tripwire
   for the CC-native "wrote the wrong config keys" defect class.)
4. Writes `results_history/results_<ts>.json` + `REPORT_<ts>.md` and updates **`REPORT_LATEST.md`**,
   diffing against the previous run.

**Exit codes:** `0` clean · `1` failures (no baseline regression) · `2` **regressions vs baseline**.

## Reading the report

- **🔴 REGRESSIONS** — was PASS in the previous run, now FAIL/ERROR. This is the section that catches
  "something broke at some point." Investigate before release.
- **🟡 XPASS** — a registered known bug (**XFAIL**) started passing: the fix landed. Update the matrix
  (remove the xfail) and close the tracked task.
- **⚠️ XFAIL** — known bug, still failing, tracked; does **not** fail the run.
- **⏭ SKIP** — environment dependency missing or tier/flag excluded; the reason is always recorded.
- **Coverage map** — all **21 engine node types** and which check covers each; `NOT COVERED` /
  `planned` rows are listed explicitly so coverage gaps are visible, never implied away.

## Check catalog (v1)

Tier 1 — core engine: `setvar_file_write`, `file_write_append`, `file_check_delete`,
`conditional_true`, `conditional_false`, `loop_list_append`.

Tier 2 — integrations: `database_select_vars`, `database_fail_edge` (fail-edge honesty),
`setvar_to_excel`, **`database_to_excel` (XFAIL — the historically-broken pairing; flips to XPASS when
fixed)**, `human_approval_approve`, `human_approval_reject`, `folder_selector_count`,
`file_transfer_sftp_upload`.

### Tier 3 — registered but not yet automated (the v2 backlog)

These 12 node types appear in the report as SKIP so the coverage map stays honest. Each needs one
piece of setup before it can be tested automatically; until then, **breakage in these nodes is
invisible** — exactly the "Database node" failure mode. In plain terms:

| Check | What's missing to automate it | What breakage it would catch |
|---|---|---|
| Alert (email) | The node sends a **real email** every run. We need a throwaway local mailbox (a tiny "mail catcher" server, like our local SFTP test server but for email) so runs don't spam a real inbox. | Alert is what workflows use on **fail edges** to tell you something broke. A dead Alert node = failures nobody hears about. |
| AI Extract / AI Action | Nothing — they work today, but each run **calls the LLM** (real cost, ~30–60s, slightly variable output). Plan: include them but only when the runner is started with an opt-in flag, so the everyday run stays fast and free; before a release you run once with the flag on. | AI Extract powers invoice/onboarding extraction — some of the most demo-critical flows. Zero execution coverage today. |
| Document | A document-pipeline fixture (upload + processing wait). | The workflow↔document-engine seam. |
| Excel Update | A template .xlsx fixture to update. | The second Excel pathway (updates vs exports). |
| Execute Application | A harmless fixture program (e.g. a one-line .bat that writes a file) so the test never runs anything real. | The run-an-app node. |
| Integration | A configured integration instance to call. | The Integration node seam. |
| Compliance ×2 | A retailer document set. | The compliance pipeline nodes. |
| Automation | A promoted automation to invoke (pack 08 creates one — reusable). | Workflow→Automation handoff. |
| Code Step | A saved code flow to invoke (pack 09 has one). | Workflow→Code Flow handoff. |
| Portal | Browser-use service + a reachable portal (the localhost test portal exists). | The Portal node — **never covered by any test, anywhere**. |

### A note on the in-browser "simulator" (deprecated)

`static/js/workflow.js` still contains an old **in-browser simulate/execute engine**
(`shouldFollowPath`, `executeNodeAction`, …). Per james (2026-07-30) it is **deprecated dead code
with no paths to it — all workflows run through the backend `workflow_execution.py` engine.** Its
edge semantics differ from the backend's; ignore it, and don't file divergence findings against it.
The backend contract is the only one that matters: edges are **pass / fail / complete**; anything
else is a dead edge the engine silently never follows (the lint flags those).

## UI legs

The runner drives the same HTTP endpoints the Designer UI calls — but a few things only a browser can
prove (canvas render, Run button, Monitor page, CC-native build quality). Those are in
[UI_SPOT_CHECKS.md](UI_SPOT_CHECKS.md) — ~10 minutes, run by a human or an agent driving the browser.

## Maintaining the matrix

- **New node type shipped?** Add a check (copy an existing builder; the node/connection JSON shape is
  documented in `runner.py`'s builders) **and** add the type to `ALL_NODE_TYPES` + `COVERAGE`.
- **Bug found?** Add/keep the check and register it as `xfail="<reason / task id>"` — the matrix then
  *tracks* the bug and announces the fix (XPASS) automatically.
- **Baseline hygiene:** `results_history/` is the memory. Commit it — the diff is only as good as the
  last kept run. First run after a wipe reports "none (first run)".
- Workflows are namespaced `NODEREG-*` and re-saved (merged by name) every run; outputs go to
  `C:\temp\aihub_test\nodereg\<stamp>\` (fresh dir per run — no stale-file false positives).
