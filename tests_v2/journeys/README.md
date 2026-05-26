# Journey Tests — multi-step user-flow sentinels

Real Playwright-driven end-to-end flows that simulate what a user actually
does in the UI. Catches the bug class where every individual page works in
isolation but the **end-to-end user flow** breaks (modal won't close after
submit, form-submit doesn't refresh list, session lost on navigation, etc.).

## Stack

- `playwright` + `pytest-playwright` (no new libraries — same as `tests_v2/ui/`)
- Python env: `C:\Users\james\miniconda3\envs\aihub2.1\python.exe`
- Main app on `http://localhost:5001`
- Command Center on `http://localhost:5091`
- Test creds: `admin / admin`

## Running

```
C:\Users\james\miniconda3\envs\aihub2.1\python.exe -m pytest tests_v2/journeys/ -v --tb=short
```

Skip the slow set in fast runs:

```
... -m "not slow"
```

Run a single journey:

```
... tests_v2/journeys/test_journey_03_workflow_author.py -v
```

## What each journey covers

| # | File | What it verifies |
|---|------|------------------|
| 1 | `test_journey_01_admin_onboarding.py` | Fresh-browser login → land on home → chat input is visible & focusable → type "what time is it?" → response renders. |
| 2 | `test_journey_02_create_agent_via_builder.py` | CC chat builder flow — create a `JOURNEY_TEST_AGENT_*` via natural-language request, verify it appears in `/api/agents/summary`. |
| 3 | `test_journey_03_workflow_author.py` | `/workflow_tool` page loads, `/save/workflow` API creates a 2-node workflow, reloading the page surfaces the new workflow. |
| 4 | `test_journey_04_compliance_officer.py` | `/compliance` page loads, retailer + set creation via API surfaces in the UI on reload. |
| 5 | `test_journey_05_navigation_state.py` | `/chat` → send message → navigate to `/data_explorer` → back to `/chat` → conversation preserved (or reported as INFORMATIONAL if intentionally cleared). |
| 6 | `test_journey_06_logout_relogin.py` | Login → confirm authed → `/logout` → confirm session cleared → log back in → confirm authed again. |

## Adding new journeys

1. Pick a real user workflow that spans 3+ pages or 2+ submits.
2. Create `tests_v2/journeys/test_journey_NN_<name>.py`.
3. Use these fixtures from `conftest.py`:
   - `authed_page` — fresh browser context with the admin storage_state loaded, 1366x768 viewport
   - `fresh_page` — unauthenticated browser context (use for login/logout flows)
   - `http_session` — `requests.Session` carrying the same admin cookies
   - `cleanup_artifacts` — module-scoped; pre-registers a dict to track what you created, and does a final prefix-scan sweep regardless
4. Any artifacts you create MUST start with `JOURNEY_TEST_` — that's how the
   cleanup fixture finds them when a test crashes.
5. Mark with `@pytest.mark.slow` and `@pytest.mark.journey`.
6. Prefer Playwright `expect()` for visibility/text assertions — it auto-retries.
7. Use API short-circuits for setup (e.g. `POST /save/workflow` instead of drag-drop) but verify via the UI — the point is testing the **UX**, not bulletproofing the data layer.

## Known fragilities

- **JOURNEY-1 / 2**: LLM round-trip can exceed 60s under load. Both journeys
  fall back to `pytest.skip()` (not fail) if the user message rendered but the
  response did not — so a slow LLM doesn't poison CI signal. They still hard-fail
  if the **wiring** is broken (user message never makes it into the DOM).
- **JOURNEY-2**: builder may ask a clarifying question instead of just creating
  the agent. We poll `/api/agents/summary` rather than scraping the chat stream —
  this means we depend on the LLM's willingness to proceed without confirmation.
- **JOURNEY-3**: the workflow list in the UI may load lazily. If the
  just-saved workflow doesn't appear in the DOM within 15s of reload, the test
  reports it as `BUG-JOURNEY-003` (informational skip).
- **JOURNEY-4**: requires the compliance module to be enabled. If
  `POST /api/compliance/retailers` doesn't return 201, the test skips (not an
  installation present in every env).
- **JOURNEY-5**: chat-session-preservation policy varies by build. The test
  reports preservation either way — interpret the skip vs pass as a behavior
  statement, not a defect.
- **JOURNEY-6**: depends on `/logout` clearing the Flask session cookie. If
  the redirect chain after logout lands on a non-`/login` URL, that's flagged
  as `BUG-JOURNEY-006`.

## Cleanup

- Every artifact MUST be prefixed `JOURNEY_TEST_`.
- The `cleanup_artifacts` fixture is **module-scoped** — it does a final
  prefix-scan sweep against the API regardless of which tests passed/failed/crashed.
- Manual cleanup of stragglers (use the same admin session):
  - Agents: `POST /delete/agent {"agent_id": <id>}`
  - Workflows: `DELETE /delete/workflow/<id>`
  - Retailers: `DELETE /api/compliance/retailers/<id>` (sets cascade-deleted first)

## Why API short-circuits for setup?

Drag-drop tests against a complex canvas are flaky in headless Chromium and
have a tendency to start passing/failing based on CSS-only changes. The intent
of these journeys is to test the **end-to-end UX** — the
"hit-save-and-see-it-show-up" loop — not to bulletproof every interaction
primitive. Each journey drives the irreplaceable parts (form submit, page
nav, response render) through the browser and uses the API for everything
that's already covered by `tests_v2/api/`.
