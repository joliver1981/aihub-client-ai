---
name: aihub-views-dashboards
description: Use when a user wants a dashboard, recurring numbers, a pulse
  they can revisit, or asks to save/pin/share an analysis — how Views work,
  tile types, scopes, and scheduled refresh.
---

# Views — deterministic dashboards

A View pins the RECIPE of an analysis, not its output. Every refresh re-runs
the pinned recipe exactly — zero AI, so a saved dashboard can never drift or
hallucinate. Offer to save a View whenever a user likes an analysis they'll
want again ("save this as a view").

## Authoring procedure

1. Build and VERIFY the analysis first — run every SELECT through
   `probe_connection_query` before pinning it. Never pin SQL you haven't run.
2. `save_view` with up to 8 tiles. Two tile types:
   - **SQL tile**: `{"title", "connection", "sql", "viz"}` — one frozen
     SELECT through the read-only gate (~50-row server cap).
   - **Automation tile**: `{"type": "automation", "title", "automation",
     "inputs", "viz"}` — renders a PROMOTED automation's output (scrapes,
     web APIs with secrets, transforms). The automation's last stdout line
     must be JSON tile data (see the lifecycle skill's recipe); drafts are
     refused; tile automations must never checkpoint.
3. viz: `stat` (single number), `table` (default for rows), `ticker`
   (scrolling strip — pair with `refresh_seconds`), `line`, `bar`
   (first text column = x, first numeric column = y), `auto`.
4. Optional per-tile `refresh_seconds` (min 15): that tile re-runs on its
   own timer while the view is open — a 30s ticker never re-runs the board.

## Scopes (identical to skills)

- **user** (default) — private, saves directly.
- **group** — shared with one group; ask the user WHICH group and confirm
  before saving (membership is verified server-side).
- **tenant** — everyone; `save_view(scope="tenant")` only FILES AN ADMIN
  APPROVAL into My Work — say "requested, not published" until approved.

Same name may exist in several scopes; resolution is user > group > tenant.
Delete rights: private = owner, group = any member, tenant = admin.

## Keeping views fresh for everyone

- Refreshing an automation tile runs the automation AS the viewer — and
  automation runs are Developer+, so plain users get an honest "requires a
  Developer role" tile plus the cached last result.
- The fix is `schedule_view_refresh` (min every 15 minutes): a scheduled
  job refreshes the cache as the schedule's creator, and EVERY viewer sees
  the cached data labeled as-of. Offer it whenever a shared view has
  automation tiles or non-developer viewers.

## Gotchas

- Tiles are pulses and top-N lists, not exports: ~50 rows per tile, and an
  automation tile's JSON line must stay under ~1900 chars (the run seam
  returns a 2000-char stdout tail).
- A failed/slow tile shows its error PLUS the cached last-good result
  labeled "as of <time>" — never present cache as fresh.
- Views deep-link: `#view=<name>&scope=<scope>` opens straight to a view —
  useful when telling a user where to look.
- Choosing a ladder: LOOK at repeatedly → View. DO mechanically on a
  schedule → automation. Recurring JUDGMENT → schedule_agent_task.
