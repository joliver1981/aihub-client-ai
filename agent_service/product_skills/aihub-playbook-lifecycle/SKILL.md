---
name: aihub-playbook-lifecycle
description: Use when building, changing, scheduling, or debugging automations
  and code flows in AI Hub — the lifecycle, the honesty rules, and the aihub
  SDK contract.
---

# AI Hub playbook lifecycle

The lifecycle is fixed: **draft (create + save) → dry-run (real execution,
live credentials) → promote (pin the proven version) → schedule (runs the
pinned version)**. Never promote or schedule code that has not dry-run
successfully in the current conversation unless the user explicitly insists.

## Writing automation code

Start every script with the explicit import (`aihub` is not pre-bound):

    import aihub_runtime as aihub

- `aihub.query("CONNECTION_NAME", "SELECT ...", [params])` → list of dicts.
  Use `?` placeholders; never format values into SQL.
- `aihub.input(name, default)`, `aihub.log(msg)`, `print()` for output.
- `aihub.checkpoint("message")` blocks until a human decides in My Approvals /
  My Work — a paused run is neither failed nor timed out.
- `aihub.send_email(to, subject, body)`, `aihub.llm(prompt)`,
  `aihub.ai_extract(...)` are the governed AI/comms seams.
- Declare every connection/secret in the manifest; hard-coded credentials are
  rejected at save time.

## Editing a code flow in place

A code flow is not build-forward-only. After a dry-run shows a step failing:

- `update_step_code(name, step_id, code)` replaces that step's source
  (credential scan re-runs; hard-coded secrets are rejected).
- `remove_code_step(name, step_id)` drops a step and every edge touching it;
  if it was the start, the start moves to the first remaining step.
- `unwire_steps(name, from_step, to_step, on?)` removes an edge. To INSERT a
  step between A and B: add NEW, wire A->NEW and NEW->B, then unwire A->B —
  two competing `pass` edges are rejected at dry-run.
- `delete_code_flow(name, confirmed)` is two-step (confirm first).

Every edit is verified by read-back (`get_code_flow`). Dry-run again after
editing; scheduled runs keep the pinned version until you promote.

## Building a View tile from an automation

A View tile can render an automation's output (scrapes, web APIs with
secrets, Python transforms). Contract:

- The automation's **last stdout line** must be one JSON value:
  `{"columns": ["a","b"], "rows": [[1,2], ...]}`, a list of objects, or
  `{"value": 42, "label": "open orders"}` for a stat tile.
- The tile runs the **pinned version** — promote before saving the view
  (save_view refuses drafts).
- **Never call `aihub.checkpoint()` in a tile automation** — a dashboard
  refresh cannot wait on approvals; the refresh aborts the run and the tile
  errors.
- Keep it fast (tile budget ~120s) and small (~50 rows — pulse numbers and
  top-N lists, not exports). The run seam returns only the final ~2000 chars
  of stdout, so the JSON line must stay under ~1900 chars — print fewer
  rows/columns rather than a wide dump.

## Gotchas that cost real debugging time

- Probe the schema before writing SQL (`get_connection_schema`); never trust
  remembered table or column names — verify with a live probe.
- Zero rows from a probe usually means a filter value that does not exist —
  verify values before concluding data is missing.
- Scheduled/API runs execute ONLY the pinned version; dry-run tests the
  latest saved version.
- Interval schedules need an anchored start date (the tools handle this).
