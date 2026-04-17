# Fix: Schedule Update/Delete/RunNow Routes in Builder Agent

## Problem
The builder agent's `schedules.update`, `schedules.delete`, and `schedules.run_now` actions use incorrect parameter names in their route mappings.

The scheduler routes in `scheduler_routes.py` (Blueprint prefix: `/api/scheduler`) use `job_id` as the first path parameter, which is the **ScheduledJob ID** (the scheduler's own job record ID), NOT the workflow_id.

But the builder's `platform_actions.py` maps `workflow_id` to the `<job_id>` position in the URL path. This causes "no active scheduler job found" errors.

## Routes in scheduler_routes.py

The correct route patterns are:
- PUT `/api/scheduler/jobs/<job_id>/types/<job_type>/schedules/<schedule_id>` — Update schedule
- DELETE `/api/scheduler/jobs/<job_id>/types/<job_type>/schedules/<schedule_id>` — Delete schedule
- POST `/api/scheduler/run/<job_id>` — Run now (this uses the ScheduledJob ID directly)

Where `job_id` is the **ScheduledJob table ID** (e.g., 62), not the workflow ID (e.g., 393).

## Files to Fix

`builder_agent/actions/platform_actions.py`

### schedules.update (around line 2340)
Current: `path="/api/scheduler/jobs/<workflow_id>/types/workflow/schedules/<schedule_id>"`
Should be: `path="/api/scheduler/jobs/<job_id>/types/workflow/schedules/<schedule_id>"`
And the first `path_params` entry should be `job_id` (ScheduledJobId), not `workflow_id`.
Also update the input_fields: rename the first field from `workflow_id` to `job_id` with description saying it's the ScheduledJob ID (from schedules.list, the "id" column).
IMPORTANT: Keep `workflow_id` as a separate field (not path param) for reference context, but the URL path param must be `job_id`.

### schedules.delete (around line 2419)
Same issue: Replace `<workflow_id>` with `<job_id>` in the path and update path_params/input_fields accordingly.

### schedules.run_now (around line 2455)
This one already correctly uses `<job_id>` in the path — just verify it passes the ScheduledJob ID correctly.

### Notes for the builder agent
The `schedules.list` response returns both `id` (ScheduledJob ID) and `workflow_id`. The builder needs to use `id` for the `job_id` path parameter. Update the `notes` field in the ActionDefinition to clarify: "The job_id is the ScheduledJob.id from the schedules list, NOT the workflow ID."

Also update `schedules.create` notes to document that it returns a `schedule_id` and a `scheduled_job_id` — the `scheduled_job_id` is what's needed for update/delete operations.
