# Module 33: Workflow Execution — Live Pipeline
**Purpose:** Exercise the workflow execution engine against a running AI Hub instance. Pick a small set of known-safe workflows from the database, start them, poll until terminal, and verify step output and variable persistence.

**Time estimate:** 30–45 minutes
**Prerequisites:**
  - AI Hub running on `http://10.0.0.7:5001` (or wherever `HOST_PORT` points)
  - Logged in as a developer-role user (role >= 2), OR a valid `X-API-Key` header
  - At least one workflow with ID 285 (`E2E Test - File Read`) exists in the tenant — this is the canonical safe workflow
  - SQL Server reachable and migrations applied

The companion JSON file [`module33_workflow_tests.json`](./module33_workflow_tests.json) holds the exact request payloads and the per-test pass-fail predicates.

---

## WFE-0: Pre-flight — list workflows and confirm safe IDs
**Action:** `GET /api/workflows/list`. Confirm that the workflow IDs listed in `module33_workflow_tests.json` exist in the tenant.
**Expected:** 200 status; `workflows` list contains an entry for every `workflow_id` in the test plan. If any are missing, mark this test BLOCKED and stop.
**Pass criteria:** :green_circle: every required workflow ID is present.

---

## WFE-1: Run workflow 285 — File Read smoke test
**Action:** `POST /api/workflow/run` with `{"workflow_id": 285, "initiator": "live-test"}`.
**Expected:** 200 status; response includes `execution_id` (UUID string).
**Pass criteria:** :green_circle: `execution_id` is a valid UUID; in-flight count `GET /api/workflow/stats/counts` shows it as Running.

---

## WFE-2: Poll workflow 285 until terminal
**Action:** Poll `GET /api/workflow/executions/{execution_id}` every 2 seconds for up to 60 seconds.
**Expected:** Status transitions Running → Completed (or Failed). The execution row exists in `WorkflowExecutions`.
**Pass criteria:** :green_circle: terminal status reached within timeout; no unhandled exceptions in `workflow_log.txt`.

---

## WFE-3: Verify step output for workflow 285
**Action:** `GET /api/workflow/executions/{execution_id}/steps`.
**Expected:** All steps reach status `Completed` (or one terminal step if branching). Each step has `output_data` populated for non-control nodes.
**Pass criteria:** :green_circle: number of completed steps > 0; no `Failed` statuses unless explicitly testing a fail branch.

---

## WFE-4: Verify variables persisted for workflow 285
**Action:** `GET /api/workflow/executions/{execution_id}/variables`.
**Expected:** Variables defined in the workflow's `variables` map appear with their final values. For workflow 285 specifically, the `${file_content}` variable should be populated from the File Read node.
**Pass criteria:** :green_circle: at least one variable has a non-empty value matching the expected pattern from `module33_workflow_tests.json`.

---

## WFE-5: Verify execution logs
**Action:** `GET /api/workflow/executions/{execution_id}/logs`.
**Expected:** Logs show the workflow lifecycle: "Workflow execution started" → "Executing node …" entries → final "Workflow execution completed" or equivalent. No `error` level logs unless intentional.
**Pass criteria:** :green_circle: at least one `info`-level log per executed node.

---

## WFE-6: Run additional safe workflows
**Action:** For each `workflow_id` in `module33_workflow_tests.json` that isn't 285, repeat WFE-1 through WFE-5.
**Expected:** Each workflow reaches a terminal state within its declared `expected_runtime_seconds`. Steps and variables align with the test plan's expectations.
**Pass criteria:** :green_circle: all five workflows complete successfully (or follow their declared expected fail branch).

---

## WFE-7: Pause / resume on a long-running workflow
**Action:** Start one of the workflows declared as `pausable: true` in the JSON. Within 5 seconds, `POST /api/workflow/executions/{execution_id}/pause`. Verify status is `Paused`. Then `POST .../resume` and verify it continues to terminal status.
**Expected:** Pause returns success; subsequent GET shows `Paused`. Resume returns success; final GET shows `Completed`.
**Pass criteria:** :green_circle: pause+resume cycle works without dropping state; final variables match a single-run baseline.

---

## WFE-8: Cancel a running workflow
**Action:** Start a long-running workflow and immediately `POST /api/workflow/executions/{execution_id}/cancel`.
**Expected:** Cancel returns success; execution status becomes `Cancelled` within 10 seconds; no orphaned step rows in `Running` state.
**Pass criteria:** :green_circle: status is `Cancelled`; no zombie step executions.

---

## WFE-9: Verify approval lifecycle (if any of the workflows include Human Approval)
**Action:** Run the workflow declared with `requires_approval: true` in the JSON. After the approval node pauses execution, `GET /api/workflow/approvals` and locate the request. `POST /api/workflow/approvals/{request_id}` with action `approve`.
**Expected:** Workflow resumes within 30s of the POST; final status is `Completed`.
**Pass criteria:** :green_circle: approval round-trip works; the post-approval branch ran.

---

## WFE-10: Cleanup
**Action:** None required — successful workflow executions persist for analytics. If a test workflow was created during the run, soft-delete it via `DELETE /delete/workflow/{id}` (developer role required).
**Pass criteria:** :green_circle: tenant state is acceptable.
