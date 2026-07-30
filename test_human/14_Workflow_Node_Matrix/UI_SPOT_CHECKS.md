# 14 — UI spot-checks (the legs only a browser can prove)

~10 minutes, after `runner.py` has run (it leaves the `NODEREG-*` workflows in place — don't use
`--cleanup` before this). Run as a human, or as an agent driving the browser. Score ✅/❌ into the
run report.

## UI-1 — Designer loads and runs a matrix workflow

1. Open **Workflow Designer** (`/workflow_tool`, new tab), pick **`NODEREG-setvar_to_excel`** from
   the workflow dropdown.
2. ✅ The canvas renders 2 nodes (Set Variable → Excel Export) wired left-to-right, no overlap, and
   the node config panels open with the saved values (`inputVariable = ${rows}`, operation `new`).
3. Click **Run**. Wait for completion.
4. ✅ The run completes and a **new** `setvar_excel.xlsx` exists under the newest
   `C:\temp\aihub_test\nodereg\<stamp>\` (2 rows: Manhattan/1000/30000, Brooklyn/770/23100).
   *(This proves the UI Run button path end-to-end, not just `/api/workflow/run`.)*

## UI-2 — Monitor shows the matrix executions

1. Open **Workflow Monitor** (`/monitoring`, new tab) → **Executions**.
2. ✅ The recent-executions list contains the `NODEREG-*` runs from the latest runner pass, with the
   same completed/failed statuses the report shows (spot-check 2–3 rows against `REPORT_LATEST.md`).

## UI-3 — Approval appears in My Approvals (human path of the approvals check)

1. Re-run just the approval check but **don't** let the runner decide it:
   start `runner.py --only human_approval_approve` and, while it polls, open **My Approvals**
   (`/approvals`) in the browser.
2. ✅ The pending "NODEREG ok gate" approval is visible in the queue with title/description
   (the runner will approve it via the API moments later; watching it appear is the check).

## UI-4 — CC-native build quality tripwire (the Defect-A guard)

1. In **Command Center** chat (as admin):
   > Build a workflow called **NODEREG-cc-excel** that sets a variable `rows` to a small JSON list of
   > two objects (store/units/revenue) and exports it to an Excel file at
   > `C:\temp\aihub_test\nodereg\cc_excel.xlsx`. Don't run it — just build it.
2. Re-run the lint: `runner.py --only lint` *(or read the "Config lint" section of the next full run's
   report)*.
3. ✅ **`NODEREG-cc-excel` does NOT appear** in the Excel-config lint list (i.e. CC wrote
   `inputVariable` + a valid `excelOperation`). Today this is expected to **fail** (CC-native writes
   `dataVariable`/`"create"` — the known prompt-catalog defect); it flips green when the CC prompt or
   save-validator fix lands. Track it like an XFAIL.
4. Delete `NODEREG-cc-excel` afterwards (Designer or `/delete/workflow/<id>`).

## Scorecard

| Check | ✅/❌ | Evidence |
|---|---|---|
| UI-1 Designer render + Run button e2e | | |
| UI-2 Monitor lists matrix runs | | |
| UI-3 approval visible in My Approvals | | |
| UI-4 CC-native Excel config lint (known-fail today) | | |
