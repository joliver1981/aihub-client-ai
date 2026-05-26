# Document Processor Job Schedules (`/document_scheduler`)

Schedule recurring runs of **document-processor jobs** — the ingestion/processing pipelines configured on the Document Manager / Document Processor side. This is where you set "this job runs every Monday at 6am" rather than running it on demand.

> **Companion pages:**
> - **Document Manager** / Document Processor — where jobs are *defined* and can be run on demand. The "Back to Jobs" button at the top of this page returns there.
> - This page is for **scheduling** existing jobs, not creating them.

---

## Page Layout

Two side-by-side cards:

### Left: Document Jobs
- **Select a Document Job** dropdown — pick from existing jobs configured in the Document Processor.
- Once selected, the **Job Details** section appears with:
  - Name
  - Description
  - Status (active / inactive)
  - Last Run timestamp
- **Run Now** button — execute the job immediately (without scheduling).
- **Add Schedule** button — create a new recurring schedule for this job.

### Right: Schedules
- Lists all existing schedules for the selected job, with cadence (cron / interval), next run time, and management actions.

## Common Tasks

### "Run this job every weekday morning"
1. Pick the job from the dropdown on the left.
2. Click **Add Schedule**.
3. Configure the cadence in the schedule dialog (cron expression or interval pick).
4. Save — the schedule appears in the right-hand list.

### "Run this job once, right now"
Pick the job and click **Run Now**. This doesn't create a schedule; it kicks off a one-time run.

### "Stop a recurring job from running"
Find its schedule in the right-hand list and disable or delete it. Disabling preserves the schedule definition; deleting removes it entirely.

### "I don't see my job in the dropdown"
The dropdown only lists jobs already defined in the Document Processor. If your job isn't there, create it first via the Document Processor / Document Manager workflow, then return here to schedule it.

## What This Page Doesn't Do

- It doesn't create or edit document jobs — that's the Document Processor.
- It doesn't schedule **workflows** — that's the Schedules tab on `/workflow_monitor`.
- It doesn't show run history with errors and detailed logs — the Document Processor / Document Manager surfaces are where job-run results live.
