# tests_v2/data_lifecycle — CRUD round-trip suite

Parameterized `create -> read -> list -> update -> delete -> read-404 -> list-excluded`
checks for every major entity exposed over the main AI Hub HTTP API.
Catches bug classes like:

* "create succeeds but list misses the new row"
* "update silently no-ops"
* "delete returns 200 but the entity still appears in list"
* "GET-by-id and LIST use inconsistent auth decorators"

All artifacts are named `DLT_v2_<entity>_<uuid8>`. A session-level
pre-clean fixture sweeps leftovers from prior aborted runs before the
suite starts; a module-level cleanup tracker deletes everything created
during the run on teardown.

## Running

```
C:\Users\james\miniconda3\envs\aihub2.1\python.exe -m pytest tests_v2/data_lifecycle/ -v --tb=short
```

Requires the main AI Hub app running on `http://localhost:5001`. The
whole suite skips with a clear message if the app is unreachable.

API key: read from `AIHUB_API_KEY` env var, defaults to the dev key.

## Coverage matrix

Legend: ✅ full lifecycle / 🟡 partial / 🔴 broken / ⏭ out of scope

| Entity              | Create | Read | List | Update | Delete | Read-404 | List-excl | Status |
|---------------------|--------|------|------|--------|--------|----------|-----------|--------|
| Workflow            | ✅     | ✅   | ✅   | ✅     | ✅     | ✅       | ✅        | ✅ full |
| Compliance Retailer | ✅     | ✅   | ✅   | ✅     | ✅     | ✅       | ✅        | ✅ full |
| Compliance Set      | ✅     | ✅   | ✅   | ✅     | ✅     | ✅       | ✅        | ✅ full (nested under retailer) |
| Compliance Schema   | ✅     | ✅   | ✅   | ✅     | ✅     | ✅       | ✅        | ✅ full |
| Agent               | ✅     | ✅   | ✅   | ✅     | ✅     | ✅       | ✅        | ✅ full (list-as-read; `/add/agent` doubles as update) |
| Connection          | ✅     | (via list) | ✅ | ✅ | ✅ | (no single-row GET) | ✅ | 🟡 partial — no GET-by-id endpoint |
| Integration         | 🟡 xfail | -  | -    | -      | -      | -        | -         | 🟡 partial — requires a valid `template_key` on the install |
| MCP Server          | ✅     | ✅   | ✅   | ✅     | ✅     | ✅       | ✅        | ✅ full |
| User                | ✅     | 🟡 xfail | ✅ | 🟡 xfail | ✅   | ✅       | ✅        | 🟡 partial — BUG-DLT-001 auth gap on GET-by-id |
| Identity Provider   | 🟡 xfail | -  | -    | -      | -      | -        | -         | 🟡 partial — BUG-DLT-002 auth gap on POST |
| Knowledge Entry     | ⏭     | ⏭   | ⏭   | ⏭     | ⏭     | ⏭       | ⏭        | ⏭ Out of scope (multipart upload) |
| Custom Tool         | ⏭     | ⏭   | ⏭   | ⏭     | ⏭     | ⏭       | ⏭        | ⏭ Out of scope (`/save` returns HTML) |
| Solution            | ⏭     | ⏭   | ⏭   | ⏭     | ⏭     | ⏭       | ⏭        | ⏭ Out of scope (login_required, not API-key) |
| Document Type       | ⏭     | ⏭   | ⏭   | ⏭     | ⏭     | ⏭       | ⏭        | ⏭ Not a CRUD entity in this codebase |

## Known bugs (documented as xfails)

These tests run but report `XFAIL` so the suite stays green. Each is a
real bug to triage — search for the `BUG-DLT-NNN` token to find the
xfail call site.

* **BUG-DLT-001** (Severity MEDIUM) — `/get/user/<id>` uses
  `@admin_required(api=True)` while `/get/users` uses
  `@api_key_or_session_required(min_role=3)`. The API key auth works
  for the list endpoint but the GET-by-id redirects to `/login`.
  Asymmetric auth means an integration that lists then drills in
  works in the UI but not via API key.
  Repro:
  ```
  GET /get/users          -> 200 (list of users, JSON)
  GET /get/user/1         -> 302 -> /login (HTML)
  ```
  Both endpoints with the same `X-API-Key`.

* **BUG-DLT-002** (Severity MEDIUM) — `POST /api/identity/providers`
  uses `@admin_required(api=True)` and returns `404 HTML` (the auth
  wall) under the test API key. Whether this is intended (only
  cookie-session admins may configure identity providers) or a bug
  depends on policy; flagged because it's inconsistent with other
  `min_role=3` endpoints that do accept API keys.

* **BUG-DLT-003** (Severity HIGH) — `POST /api/integrations` with a
  built-in template (e.g. `custom_rest_api`) returns
  ```
  ('23000', "[23000] [Microsoft][ODBC SQL Server Driver][SQL Server]
   Violation of UNIQUE KEY constraint 'UQ_IntegrationTemplates_Key'.
   Cannot insert duplicate key in object 'dbo.IntegrationTemplates'.")
  ```
  The integration-create code path is INSERTing into the global
  template catalog (`IntegrationTemplates`) instead of (or in
  addition to) the per-tenant Integrations table. Repro: try to
  create two integrations using the same `template_key`. Severity
  HIGH because it blocks API-driven integration provisioning.

* **BUG-DLT-INTEGRATION-TEMPLATE** — `POST /api/integrations` requires
  a `template_key` resolvable to a real template on this install.
  Distinct from BUG-DLT-003: this is a "template missing", not a
  "duplicate key on insert".

## Files

* `entities.py` — single source of truth: `EntitySpec` dataclass per
  entity and the `ENTITIES` catalog. Adding a new entity = appending
  one dataclass instance (or, if its API shape is weird, adding a
  dedicated test module like `test_workflow_lifecycle.py`).
* `conftest.py` — `services_ready`, `api_session`, `unique_name`,
  `cleanup_tracker`, and the session-autouse `preclean_leftovers`
  fixture.
* `test_crud_lifecycle.py` — the parameterized 7-step lifecycle.
* `test_workflow_lifecycle.py` — workflow (custom JSON shape).
* `test_compliance_set_lifecycle.py` — nested-under-retailer.
* `pytest.ini` — local markers + stops pytest walking up to the
  strict-markers parent ini.

## Adding a new entity

For the common case (POST/GET-by-id/list/PUT/DELETE all clean JSON):

1. Open `entities.py`.
2. Add body builders for create and update at the top of the file.
3. Append an `EntitySpec(...)` to `ENTITIES` with the correct URL
   templates, the `id_keys` your create endpoint actually returns,
   and the `list_key` / `list_id_key` / `list_name_key` that match
   the list response shape.
4. If the create response wraps the id under a sub-key (e.g.
   `{"data": {"id": ...}}`), set `id_wrapper_keys=["data"]`.
5. If the list endpoint returns a JSON-encoded string (the legacy
   `jsonify(dataframe_to_json(df))` pattern), set
   `list_returns_json_string=True`.
6. If pre-clean should also sweep leftovers for this entity, add a
   tuple to the `cleanups` list in `conftest.py::preclean_leftovers`.
7. Run the suite. If a step is broken, document it as an xfail with
   `BUG-DLT-NNN` in the existing test code; do NOT mutate the spec
   to silently pass.

For an entity whose API doesn't fit the template (custom JSON body,
nested-under-parent, multipart upload, etc.) create a dedicated
`test_<entity>_lifecycle.py` modeled on `test_workflow_lifecycle.py`.
