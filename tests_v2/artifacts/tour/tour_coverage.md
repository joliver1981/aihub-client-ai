# Full Feature Tour — Coverage Report

This document captures what the new **Level 2 — Full Feature Tour** covers
vs. what it doesn't, and what the latest run found.

Source test: `tests_v2/journeys/test_full_feature_tour.py`
Page catalogue: `tests_v2/journeys/full_tour/pages.py`
Per-page actions: `tests_v2/journeys/full_tour/actions.py`
Auto-regenerated report: `tests_v2/artifacts/tour/tour_report.md`

---

## What the tour does, per page

Two phases per page:

1. **Reachability** (always runs)
   - Navigates with the journey auth state
   - Asserts HTTP status < 400 (any 4xx or 5xx = fail)
   - Asserts no JS console error of severity `error` during load
     (filters known third-party noise: tailwind, OneTrust, GA, etc.)
   - Asserts the primary control selector renders visible

2. **CRUD action** (only on pages with a registered handler)
   - Create via API
   - Verify the new entity appears in the page's UI
   - Delete via API
   - All artifacts named `TOUR_*` so a pre-sweep can clean them up
     if the test crashed last run

A markdown + JSON report is regenerated every run.

---

## Coverage snapshot (latest run)

| Metric | Value |
|---|---:|
| Pages catalogued | **50** |
| Pages reachable (status < 400, ctrl visible, no console errors) | **46** |
| Pages with a registered CRUD action | **8** |
| CRUD actions passing | **8** |
| Page-level failures surfaced as real bugs | **4** |
| Wall time | ~5 min 13 s |

### CRUD actions implemented (all passing)

| Page | Entity exercised | Verifies |
|---|---|---|
| `/custom_agent_enhanced` | agent | create → list visible (best-effort) → POST /delete/agent |
| `/jobs` | scheduled job + interval schedule | create job, create schedule, list visible, delete both |
| `/workflow_tool` | workflow | create with `/save/workflow` → builder lists it → delete |
| `/compliance` | retailer | create → /compliance page lists it → delete |
| `/integrations` | integration | discover available template → create → list → delete |
| `/mcp_servers` | MCP server config | create → list visible → delete |
| `/data_chat` | NL query round-trip | prompt → SSE / chat response observed |
| `/chat` (knowledge full) | agent + docx + xlsx + grounded chat | create agent → upload `.docx` + `.xlsx` → wait for indexing → ask grounded question → verify answer contains founder fact |

The `/chat` action is the **biggest test in the suite** — it spans the
upload pipeline (Document API on :5011), the indexer, vector retrieval,
and the LLM grounding loop. When this passes, the whole knowledge stack
is healthy. Latest run: `grounded_fact_seen=True`, agent answered
"Helix Innovations was f[ounded]…" from the uploaded `.docx`.

---

## Pages with reachability coverage but no CRUD action

These load cleanly but don't yet have a write-action wired up. Reachability
catches "the page broke" but doesn't catch "the primary feature on the
page is broken in a subtle way." Adding actions here is the next layer.

Top candidates (highest user-traffic first):
- `/data_dictionary` — would create + delete a synthetic table dictionary entry
- `/document_processor`, `/document_processor/job/new` — create-and-delete a document job
- `/document_scheduler`, `/document_summarizer` — schedule + summary lifecycle
- `/connections` — create a fake DB connection + connectivity test
- `/groups`, `/users` — create + delete a synthetic group / user (admin-only)
- `/admin/api-keys` — round-trip a fake API key save+delete (uses local secrets vault)
- `/local-secrets` — write/read/delete a secret named `TOUR_*`
- `/solutions/author/new` — author-and-discard a draft solution
- `/email-processing/history` — read-only verification
- `/preferences/` — change a preference, read it back, restore default

---

## Pages NOT in the catalogue (deliberately)

| Excluded | Reason |
|---|---|
| `/login`, `/logout` | covered by auth_e2e suite |
| `/document/serve/<path:filepath>` | requires an existing document path |
| `/document/view/<string:document_id>` | requires an existing doc id |
| `/document_processor/job/<int:job_id>` (+ sub-pages) | requires an existing job id; will be added once the action layer creates one |
| `/agent_knowledge/<int:agent_id>` | requires an existing agent id; the `/chat` action creates one but doesn't visit this URL — easy add |
| `/agent-email/config/<int:agent_id>`, `/agent-email/inbox/<int:agent_id>` | per-agent, same constraint as above |
| `/setup` (initial setup) | one-time first-boot only |
| `/test-template`, `/test-bare` | dev scratch routes |
| `/save`, `/custom`, `/api/cc-auto-token` | not browser-targeted (they're API endpoints that happen to render templates) |
| `/ops` (Command Center next-gen) | served on port 5091, not :5001 — separate tour |

---

## Bugs surfaced by the latest tour run

All four are tracked in `tests_v2/TEST_SUITE_INVENTORY.md` under the bug
ledger.

### 🔴 BUG-TOUR-SUMMARIZATION-DASHBOARD-500
- **URL:** `/admin/summarization-dashboard`
- **Status:** 500
- **Root cause:** Template calls `url_for('admin_dashboard')` — that endpoint
  doesn't exist. Server error reads:
  *"Could not build url for endpoint 'admin_dashboard'. Did you mean
  'agent_dashboard' instead?"*
- **Fix:** rename the `url_for` call in `admin_summarization_dashboard.html`
  to a real endpoint, OR register an `admin_dashboard` route.

### 🔴 BUG-TOUR-IDENTITY-SETTINGS-404
- **URL:** `/admin/identity_settings`
- **Status:** 404
- **Root cause:** `auth_identity_routes.py` defines `/settings` on a
  blueprint, but the blueprint either isn't registered or is mounted at a
  different prefix than `/admin/identity_settings`. A sidebar link
  references this URL.

### 🔴 BUG-TOUR-TELEMETRY-404
- **URL:** `/telemetry`
- **Status:** 404
- **Root cause:** `telemetry.py` defines the route but it isn't reachable.
  Same blueprint-registration class of issue as identity_settings.

### 🔴 BUG-TOUR-DATA-DICTIONARY-FETCH-ERROR
- **URL:** `/data_dictionary`
- **Status:** 200 (page loads) but raises:
  ```
  TypeError: Failed to fetch
    at sendMessage (http://localhost:5001/data_chat:2128:9)
    at HTMLButtonElement.onclick (http://localhost:5001/data_chat:1821:89)
  ```
- **Root cause:** `data_dictionary.html` appears to pull in a script from
  `data_chat` that auto-fires `sendMessage` on load. Either the wrong
  script is being included, or `sendMessage` is racing the page-ready
  event before the chat endpoint exists.

---

## Running the tour

```powershell
# Full tour with CRUD actions (~5 min)
& "$env:USERPROFILE\miniconda3\envs\aihub2.1\python.exe" -m pytest `
    tests_v2/journeys/test_full_feature_tour.py -v -s

# Reachability only (~2 min)
$env:TOUR_REACHABILITY_ONLY = "1"
& "$env:USERPROFILE\miniconda3\envs\aihub2.1\python.exe" -m pytest `
    tests_v2/journeys/test_full_feature_tour.py -v -s
Remove-Item Env:TOUR_REACHABILITY_ONLY

# Filter by tag (eg. admin only)
$env:TOUR_TAGS = "admin"
& "$env:USERPROFILE\miniconda3\envs\aihub2.1\python.exe" -m pytest `
    tests_v2/journeys/test_full_feature_tour.py -v -s
Remove-Item Env:TOUR_TAGS
```

Outputs:
- `tests_v2/artifacts/tour/tour_report.md` — per-page outcome table + failure detail
- `tests_v2/artifacts/tour/tour_report.json` — machine-readable for trend analysis

---

## What's still NOT covered by Level 2

Even with the tour, these classes of bug still slip through:
- **"Does the feature work *well*?"** — e.g., a chat answers but answers
  WRONGLY. The tour treats "agent returned 200" as success; correctness
  is the Level 3 (competency) suite's job.
- **Cross-tenant isolation** — the tour runs as one admin user. A
  separate matrix test (user A creates X, user B can't see X) is needed.
- **Race conditions / concurrent edits** — the tour walks pages serially.
- **Long-running pipelines** — workflows that take >60 s end-to-end are
  out of scope; the tour caps individual actions.
- **PyInstaller-bundled drift** — the tour hits the running Flask, which
  may serve different templates than the on-disk source if a bundled exe
  is being served. (See BUG-CHAOS-002 investigation.)

These belong in the **Level 3 — Competency suite**, which is the next
build target. Start with:
- `tests_v2/competency/test_competency_agent_knowledge_excel.py`
- `tests_v2/competency/test_competency_agent_knowledge_pdf.py`
- `tests_v2/competency/test_competency_data_assistant_nl_to_sql.py`

---

## Adding new pages / actions

1. Append a new `Page(...)` entry to `pages.PAGES` (just the URL + title
   gets you free reachability coverage).
2. To exercise CRUD: write a `action_<name>(page, ctx) -> str` function
   in `actions.py`, register it in `ACTIONS = {url: handler, ...}`, and
   name created artifacts with `_tour_name(ctx.prefix, "<entity>")` so
   the pre-sweep can clean up.
3. The action returns a one-line string for the report. Return
   `"SKIPPED ..."` to mark it as skipped (e.g. when prerequisites are
   missing on this install).
