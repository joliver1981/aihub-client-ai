# 01 — All Pages Open Successfully  (requested item #8)

**Goal:** every page in the app loads and renders — no `500`, no blank white page, no "Internal
Server Error", no broken template. This is the **gate**: if a core page won't open, stop and fix
before running the rest.

**How to run:** log in as `admin`, then walk the sidebar top to bottom, opening each page. For an
agent driver, `navigate` to each URL below and `read_page` to confirm the page's heading/content
rendered. Base = `http://localhost:5001` unless noted.

**Pass for each page:** HTTP 200 **and** the page's own content is visible (heading, form, or panel).
A page that loads but shows a red error banner or an empty shell = ❌.

> Tip (agent): a fast first pass is to `navigate` and check the response isn't a 500 and the page
> `<title>`/main heading matches. Then eyeball the ones that matter for later sections.

---

## A. Core "Work" pages (every user)

| # | Page | URL / how | Loads when you see |
|---|------|-----------|--------------------|
| REG-01-A1 | Login | `/login` | username/password form |
| REG-01-A2 | Dashboard | `/` (or `/dashboard`) | agent cards / dashboard tiles |
| REG-01-A3 | Agent Chat | `/chat` | chat composer + agent picker |
| REG-01-A4 | Data Assistant Chat | `/data_assistants` | data-assistant chat UI |
| REG-01-A5 | Data Explorer | `/data_explorer` *(new tab)* | "Data Explorer" welcome + "Ask a question…" box |
| REG-01-A6 | Agent Jobs | `/jobs` | jobs list / "New Job" |
| REG-01-A7 | My Approvals | `/approvals` | approvals queue (may be empty — still renders) |
| REG-01-A8 | Command Center | `/command-center` → `http://localhost:5091` *(new tab)* | CC chat interface |
| REG-01-A9 | Portal Workflows | `/portal-workflows` | portal workflow builder/list |

## B. "Build" pages (Developer / role ≥ 2)

| # | Page | URL / how | Loads when you see |
|---|------|-----------|--------------------|
| REG-01-B1 | Agent Builder | `/custom_agent_enhanced` | agent config form |
| REG-01-B2 | Data Assistant Builder | `/custom_data_agent` | data-assistant config form |
| REG-01-B3 | Database Connections | `/connections` | connections table + "Add" |
| REG-01-B4 | Data Dictionary | `/data_dictionary` | dictionary / discovery UI |
| REG-01-B5 | Local Secrets | `/local-secrets` | secrets list + "Add secret" |
| REG-01-B6 | Custom Tools | `/custom` | tool builder |
| REG-01-B7 | Document Processor | `/document_processor` | processor / new-job UI |
| REG-01-B8 | Document Search | `/document-search` | search box + results pane |
| REG-01-B9 | Document Manager | `/document-manager` | document list |
| REG-01-B10 | Workflow Designer | `/workflow_tool` *(new tab)* | canvas + node palette |
| REG-01-B11 | Workflow Monitor | `/monitoring` *(new tab)* | run monitor dashboard |
| REG-01-B12 | Builder | `/builder` *(new tab)* | builder chat/canvas |
| REG-01-B13 | MCP Servers | `/mcp_servers` | MCP server list |
| REG-01-B14 | Integrations | `/integrations` | integrations catalog |
| REG-01-B15 | Retailer Compliance | `/compliance` | compliance dashboard |
| REG-01-B16 | Agent Environments | `/environments` *(if enabled)* | environments list |

## C. "Admin" pages (role 3 / admin)

| # | Page | URL / how | Loads when you see |
|---|------|-----------|--------------------|
| REG-01-C1 | Users | `/users` | user table |
| REG-01-C2 | Groups | `/groups` | groups table |
| REG-01-C3 | System Logs | `/system_logs` | log viewer |
| REG-01-C4 | Tier & Usage | `/admin/tier` *(new tab)* | usage dashboard |
| REG-01-C5 | API Keys (BYOK) | sidebar → Admin → System Management → **API Keys** | key config form |
| REG-01-C6 | Identity & Security | sidebar → Admin → **Identity & Security** | identity settings |
| REG-01-C7 | Email Processing | `/email-processing/history` *(if Enterprise)* | processing history |
| REG-01-C8 | Feedback Analysis | sidebar → Admin → **Feedback Analysis** | feedback dashboard |

## D. Solutions (role ≥ 2, if enabled)

| # | Page | URL | Loads when you see |
|---|------|-----|--------------------|
| REG-01-D1 | Solutions | `/solutions` | solutions gallery |
| REG-01-D2 | Solutions Author | `/solutions/author` | authoring UI |

---

## Notes

- Some pages are **feature-flag gated** — if a nav item isn't in the sidebar for your install, mark
  the check **N/A** (flag off) rather than ❌. The releasable set is "everything that appears in the
  nav opens."
- Pages that **open in a new tab** (Command Center, Data Explorer, Workflow Designer/Monitor, Builder,
  Tier) are the usual regression suspects — confirm the new tab actually renders, not just that a tab
  opened.
- Console-error sanity (agent driver): after loading a core page, `read_console_messages` with
  `onlyErrors:true` — a page that renders but throws a wall of JS errors is worth a ⚠️ note.

## Scorecard

| Check | ✅/⚠️/❌/N-A | Evidence (title seen / error) |
|---|---|---|
| A1 Login | | |
| A2 Dashboard | | |
| A3 Agent Chat | | |
| A4 Data Assistant Chat | | |
| A5 Data Explorer | | |
| A6 Agent Jobs | | |
| A7 My Approvals | | |
| A8 Command Center | | |
| A9 Portal Workflows | | |
| B1 Agent Builder | | |
| B2 Data Assistant Builder | | |
| B3 Connections | | |
| B4 Data Dictionary | | |
| B5 Local Secrets | | |
| B6 Custom Tools | | |
| B7 Document Processor | | |
| B8 Document Search | | |
| B9 Document Manager | | |
| B10 Workflow Designer | | |
| B11 Workflow Monitor | | |
| B12 Builder | | |
| B13 MCP Servers | | |
| B14 Integrations | | |
| B15 Compliance | | |
| B16 Environments | | |
| C1 Users | | |
| C2 Groups | | |
| C3 System Logs | | |
| C4 Tier & Usage | | |
| C5 API Keys | | |
| C6 Identity & Security | | |
| C7 Email Processing | | |
| C8 Feedback Analysis | | |
| D1 Solutions | | |
| D2 Solutions Author | | |

**Gate:** any core page (A2, A3, A5, A8, A9, B3, B10) that 500s or shows a blank/error shell is a
**release blocker** — stop and file it before continuing.
