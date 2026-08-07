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

## Gotchas that cost real debugging time

- Probe the schema before writing SQL (`get_connection_schema`); never trust
  remembered table or column names — verify with a live probe.
- Zero rows from a probe usually means a filter value that does not exist —
  verify values before concluding data is missing.
- Scheduled/API runs execute ONLY the pinned version; dry-run tests the
  latest saved version.
- Interval schedules need an anchored start date (the tools handle this).
