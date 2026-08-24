# Handoff spec — surface Portal Workflows in The Agent's Playbooks

**Owner service:** `agent_service` (The Agent, port 5111)
**Type:** additive feature, low risk
**Status:** ready to implement (research done; no code written yet)

## Problem / goal

The Agent can already list and run **portal workflows** (recorded browser/RPA
sequences that log into a web portal and download/upload files) via the
`list_portal_workflows` / `run_portal_workflow` tools, and there's a dedicated
**Portal Workflows** page at `/portal-workflows`. But the Playbooks rail in The
Agent's UI does **not** show them — it only lists regular workflows, code
flows, and automations. A user who has saved portal workflows sees them nowhere
in Playbooks.

Goal: surface portal workflows in Playbooks as their **own distinct type**
(not merged into "workflow"), with a filter chip and a deep link to the Portal
Workflows page. Keep them clearly separate from regular workflows — the portals
skill/prompt deliberately states "portal workflows are NOT the platform's
regular workflows/playbooks" — this change only makes them *discoverable*, it
does not merge the two systems.

## Current behavior (what to change)

`GET /api/playbooks` in [`agent_service/main.py`](agent_service/main.py:817)
aggregates exactly three kinds and returns `{playbooks: [...], errors: [...]}`:

1. `workflow` and `code_flow` — from `_get("/get/workflows")` (main-app REST).
2. `automation` — from `_post("/automations/api/internal/manage", {action:"list", ...})`.

Portal workflows live in a **separate per-user store**
(`data/portal_workflows.json`) and are never queried here, so they never appear.

## Required change

### 1. Backend — `GET /api/playbooks` (`agent_service/main.py`)

Add a fourth source: the caller's portal workflows. Read them directly from the
shared store (already importable in this service — `agent_config.py` puts
`APP_ROOT` on `sys.path`, and `portal_tools.py` already imports from
`command_center.tools`):

```python
try:
    from command_center.tools import portal_workflows as _pwf
    for w in _pwf.list_workflows(int(user.get("user_id") or 0)):
        out.append({
            "kind": "portal_workflow",
            "id": w.get("slug"),                       # portal workflows are keyed by slug
            "name": w.get("name"),
            "description": w.get("goal") or w.get("start_url") or "",
        })
except Exception as e:
    errors.append(f"portal workflows: {e}")
```

`list_workflows(user_id)` ([`command_center/tools/portal_workflows.py:162`](command_center/tools/portal_workflows.py:162))
returns per workflow: `{slug, name, portal_slug, start_url, goal, step_count,
step_types, uploads, success_count, last_run_status}` — **never any secret**.
Use `slug` as the `id` (there is no numeric id) and `goal` as the description.

**Visibility/auth:** the store is per-user keyed by `user_id`; `list_workflows`
already scopes to the caller. No extra ACL needed. Gate consistently with the
portal tools if desired — they check `AGENT_PORTAL_TOOLS` /
`BROWSER_USE_ENABLED` and Developer role — but for a read-only list it's
acceptable to just return the user's own rows (an empty list when they have
none). Do **not** add a hard failure if the store is missing; append to
`errors` and continue, matching the existing pattern.

### 2. UI — the Playbooks rail (`agent_service/static/index.html`)

The rail already renders arbitrary kinds generically (`appendPbItem`), so this
is small:

- **Chip style** — add a key to `PB_STYLE`
  ([index.html:1559](agent_service/static/index.html:1559)):
  ```js
  const PB_STYLE = { workflow: "t-review", code_flow: "t-provide_input",
                     automation: "t-approve_deny", portal_workflow: "t-edit_and_return" };
  ```
  (pick any existing `t-*` class not already used for another kind; the chip
  label is auto-derived as `kind.replace("_"," ")` → "portal workflow".)

- **Filter chip** — add to `PB_FILTERS`
  ([index.html:1562](agent_service/static/index.html:1562)):
  ```js
  ["portal_workflow", "Portal workflows"]
  ```

- **Deep link on click** — in `appendPbItem`
  ([index.html:1567](agent_service/static/index.html:1567)) the open handler
  currently sends `automation` → `/automations/` and everything else →
  `/workflow_tool?load_workflow_id=<id>`. Add a branch so
  `portal_workflow` opens the Portal Workflows page with that workflow loaded:
  ```js
  const url = p.kind === "automation"      ? platUrl("/automations/")
            : p.kind === "portal_workflow" ? platUrl(`/portal-workflows?load=${encodeURIComponent(p.id)}`)
            : platUrl(`/workflow_tool?load_workflow_id=${encodeURIComponent(p.id)}`);
  window.open(url, "_blank", "noopener");
  ```
  The Portal Workflows page **supports `?load=<slug>`** (the co-browse "Save as
  workflow" flow already deep-links with `?load=`; see
  [`templates/portal_workflows.html`](templates/portal_workflows.html) `loadWorkflow`).
  Links already open in a new tab (2026-08-23 fix), so no target handling
  needed.

- Remember the mandatory cache-bust: bump the `?v=` on the index.html script
  include if this project versions it (check the current pattern before saving).

## Testing

- **Backend:** with a user who has ≥1 saved portal workflow, `GET /api/playbooks`
  returns entries with `kind:"portal_workflow"`, a real `name`/`slug`, and no
  secret fields. With none, the array simply omits them and `errors` stays
  clean. Seed via the `save_portal`/record flow or by writing a test row to the
  store (see `tests_v2/unit/test_agent_portal_tools.py` for store setup).
- **UI (headless-Chromium smoke, aihub2.1 env):** follow the pattern in
  [`test_human/20_The_Agent/ui_smoke_links.py`](test_human/20_The_Agent/ui_smoke_links.py)
  — stub `/api/playbooks` to include a `portal_workflow` row, open the
  Playbooks view, assert the chip renders, the "Portal workflows" filter shows
  it, and clicking opens `…/portal-workflows?load=<slug>` in a NEW tab.
- Consider adding a **pack-20 check** (e.g. PT-14) that hits the live
  `/api/playbooks` and asserts a seeded portal workflow appears with the right
  kind.

## Files to touch

| File | Change |
|---|---|
| `agent_service/main.py` (`/api/playbooks`, ~line 817) | add the `portal_workflow` source |
| `agent_service/static/index.html` (~1559, ~1562, ~1567) | chip style, filter chip, deep link |
| `test_human/20_The_Agent/ui_smoke_links.py` and/or `runner.py` | coverage |

## Out of scope / notes

- Do **not** merge portal workflows into the `workflow` kind — keep the
  distinct `portal_workflow` tag (the two are different subsystems).
- No change to the portal tools, the store, or the Portal Workflows page.
- The service serves `index.html` from disk (FileResponse) → UI change is live
  on reload; the backend change needs a targeted 5111 restart (kill the PID
  owning 5111 → run `agent_service/start_agent_service_dev.bat` → verify
  `/health`).
- Playwright lives only in the `aihub2.1` conda env on the dev box.
