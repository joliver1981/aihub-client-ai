# Compliance / Integrations / MCP — Smoke Test Plan

Quick visual checks for the three remaining major surface areas. These are not deep regression tests — they confirm the pages render, the basic CRUD works, and nothing obviously broken bubbles up.

---

## How to run

For each section, walk through the steps and check off each box. Anything failing → file a ticket with screenshots.

---

## Compliance — `S1: Compliance Module`

1. Navigate to **Compliance** in the main nav.
2. Confirm the retailer list page loads with no console errors.
3. Click **"Create Retailer"** (or equivalent).
4. Enter:
   - Name: `HUMAN-TEST-Retailer-Acme`
   - Region: any value
5. Save and confirm the retailer appears in the list.
6. Open the new retailer's detail page; confirm fields show what you entered.
7. Navigate to **Compliance → Schemas**.
8. Confirm schemas list loads with at least one schema visible.
9. Delete the test retailer.

| Step | Expected | Pass/Fail | Notes |
|---|---|---|---|
| Page loads | No console errors |   |   |
| Create form opens | Form fields visible |   |   |
| Save persists | New row appears |   |   |
| Detail page | Saved fields shown |   |   |
| Schemas list loads | ≥1 schema |   |   |
| Delete | Row disappears |   |   |

---

## Integrations — `S2: Integrations Module`

1. Navigate to **Integrations**.
2. Confirm the page lists existing integrations or shows the "create your first" empty state.
3. Click **"Add Integration"** and pick the **Azure Blob Storage** template (any safe template will do).
4. Fill in dummy non-secret fields:
   - Display name: `HUMAN-TEST-Int-Blob`
   - Container: `test-container`
5. Save without supplying secrets.
6. Confirm the integration appears in the list with a "Not configured" or "Draft" status.
7. Delete it.

| Step | Expected | Pass/Fail | Notes |
|---|---|---|---|
| List page loads |   |   |   |
| Template picker opens |   |   |   |
| Save persists as draft |   |   |   |
| Status correct |   |   |   |
| Delete |   |   |   |

---

## MCP Servers — `S3: MCP Module`

1. Navigate to **MCP Servers (admin)**.
2. Confirm the page loads. If there are no servers configured, that's fine — the empty state should render.
3. Click **"Add MCP Server"**.
4. Fill in:
   - Name: `HUMAN-TEST-MCP-Echo`
   - Transport: pick the simplest available (e.g. stdio with `echo`)
   - Command: leave defaults or use `npx -y @modelcontextprotocol/server-everything` if your environment supports it
5. Save.
6. If a "Test connection" button exists, click it and observe the result (it may fail due to env — the goal is the button works, not that the server connects).
7. Delete the test MCP server.
8. Navigate to **MCP User Servers** as a non-admin user (if RBAC allows you to test this). Confirm the admin server is **not** visible by default — confirms tenant scoping.

| Step | Expected | Pass/Fail | Notes |
|---|---|---|---|
| Admin MCP list loads |   |   |   |
| Add form opens |   |   |   |
| Save persists |   |   |   |
| Test-connection runs (may fail) |   |   |   |
| Delete |   |   |   |
| User MCP scoping correct |   |   |   |

---

## Console / network hygiene (overall)

Open the browser DevTools console while doing the above. Note any:

- Red console errors (other than expected third-party noise like favicon 404s).
- 5xx responses in the Network panel.
- Mixed-content warnings.

Report any cluster of errors with a timestamp and the page URL.

---

## Scoring

| Section | Pass / Partial / Fail | Notes |
|---|---|---|
| Compliance |   |   |
| Integrations |   |   |
| MCP |   |   |
| Console hygiene |   |   |

**Pass criteria:** all four sections "Pass" or "Partial with cosmetic notes". Any "Fail" should be escalated.
