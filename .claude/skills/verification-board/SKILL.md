---
name: verification-board
description: Regenerate/update the AI Hub Verification Status Board — the artifact page showing what is live-verified vs partial vs untested per platform area — and snapshot it against the current app version. Trigger when james says "run/update the verification status board", "refresh the verification board", "update the test status board", or asks what's verified vs untested. NOT for running the test packs themselves (it only reads their reports; run packs first if he wants fresh results).
---

# Verification Status Board

One page, one job: where does the risk live — what is proven, what is provisional,
what has never been exercised. Grounded in evidence, never vibes.

## Canonical locations

- **Master HTML (edit this)**: `docs/verification/board.html` in this repo.
- **Published artifact (same URL forever)**: https://claude.ai/code/artifact/c8d2ea02-14f2-4d6b-a6b8-30be4d82cf8f
  — always republish with this as the `url` parameter of the Artifact tool. Publishing
  without `url` from a new conversation creates a duplicate artifact; never do that.
  Favicon stays 🧪.
- **Version snapshots**: `docs/verification/v<APP_VERSION>/board-<YYYYMMDD>.html`
  (APP_VERSION from `app_config.py`, e.g. v2.0). Also update
  `docs/verification/VERSIONS.md` — one line per snapshot: date, version, headline
  counts, gate verdicts. Snapshots are append-only history; never rewrite old ones.

## Evidence sources, in trust order

1. **Pack reports on disk** — `test_human/<NN>_*/REPORT_LATEST.md` headline/verdict
   lines (14 = workflow nodes, 15 = whole-platform gate, 16 = CC matrix, 20 = The
   Agent, 21 = Agent competency). ALWAYS read the report date: a CLEAN verdict older
   than major code changes in its area is **Partial (stale)**, not Verified.
2. **This session's / memory's live verifications** — `document-search-v3-plan.md`
   and sibling memory files record what was live-proven with dates and commits.
3. **Unit suites** — run them if cheap (`tests/unit/test_schema_*`, `tests_v2/unit/`),
   otherwise cite the last green run with its date.

## Status taxonomy (chips in the HTML)

- `ok` **Verified** — live-proven end-to-end and/or green suites, WITH date + evidence.
- `warn` **Partial** — works but has open findings, thin coverage, a stale gate run,
  or was never live-verified (unit-only).
- `bad` **Untested / known broken** — shipped with zero verification, or a confirmed
  unfixed defect.
- `hold` **Parked** — deliberately not pursued by james's decision (e.g. min-rows
  floor OFF, column evolution deferred, bulk backfill rejected). Not a gap; do not
  nag about these.

Discipline: a row only moves to Verified with NEW evidence (name it in the Evidence
cell). Downgrade rows whose area changed materially since their evidence date. The
summary tiles compute themselves from the chips via the page's script — never
hand-edit the counts.

## Update procedure

1. Read `docs/verification/board.html` (the current truth).
2. Pull fresh verdict lines from every `REPORT_LATEST.md` + scan recent memory for
   new live verifications or new decisions since the board's compile date.
3. Edit rows: statuses, evidence (keep the mono, terse `proof · date` style), the
   compile date in the header, and the stale-gates warning note if gate dates moved.
4. Add rows for genuinely new capabilities; move decision-parked items to `hold`.
5. Save; copy to `docs/verification/v<APP_VERSION>/board-<YYYYMMDD>.html`; append the
   VERSIONS.md line; commit both (this repo commits promptly — live tree).
6. Publish with the Artifact tool: `file_path` = master, `url` = the canonical URL
   above, favicon 🧪.
7. Tell james the DELTAS (rows that changed state and why), not the whole board.

## Version-attachment rule

When james bumps APP_VERSION (app_config.py), the next board update creates the new
`docs/verification/v<new>/` folder — snapshots always land under the version that was
current when the evidence was gathered. A release build should be cut only from a
version whose latest snapshot shows the pack 15 gate Verified-fresh.
