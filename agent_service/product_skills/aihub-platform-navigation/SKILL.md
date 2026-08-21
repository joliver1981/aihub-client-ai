---
name: aihub-platform-navigation
description: Use when a user asks where something lives in AI Hub, how to do
  something in the app, or which surface/tool fits their ask — the platform
  map and when to send people where.
---

# AI Hub platform map

You (The Agent) are the front door, but the classic app's pages remain the
right place for hands-on visual work. Paths below are on the main app
(same host, main port — the UI's Platform links and the Playbooks screen
already point there).

## The Agent's own surfaces (this UI)

- **Assistant** — conversation: explore data, build/dry-run/promote/schedule
  automations and code flows, store secrets, save skills.
- **My Work** — every human decision in one queue: workflow + automation
  approvals, outbound email drafts (body editable — what they approve is what
  sends), agent-raised questions/reviews/FYIs. Each item has a side-thread to
  ask you questions (read-only).
- **Views** — deterministic dashboards. Pinned SQL or pinned-automation
  tiles, refreshed with zero AI; user/group/tenant scopes (tenant needs an
  admin approval); `schedule_view_refresh` keeps shared caches fresh for
  non-developer viewers. When a user likes an analysis, offer to `save_view`
  it — full procedure in the views skill.
- **Playbooks** — inventory of workflows, code flows, and automations, with
  jump links to the Designer and Mission Control.
- **Email** — the user's personal agent address (`<prefix>-agent.<tenant>@…`).
  Mail sent there reaches the agent as a headless session run as that user;
  results land in My Work; drafted replies wait for approval. The screen has
  the prefix editor, enable toggle, and inbound activity log. Check a user's
  setup with `get_agent_email_status`.
- **Skills** — the procedural memory admin (product/tenant/group/user scopes).

## Classic app pages — when to send users there

- **Workflow Designer** `/workflow_tool` — visual drag-and-drop workflow
  editing. You cannot edit visual workflows; send builders here.
- **Mission Control** `/automations/` — automations dashboard: run feeds,
  checkpoints, versions, the Studio.
- **Documents** — `/document-manager` (browse/manage), `/document_processor`
  (ingest/upload), `/document-search` (AI search over ingested docs).
- **Data Explorer** `/data_explorer` — NL-to-SQL chat with saved dashboards;
  `/data_chat` is the lighter data chat; `/data_dictionary` documents tables.
- **Connections** `/connections` — add/edit database connections and
  credentials (admins).
- **My Connections** `/my-connections` — the USER'S own page for connecting
  and managing their personal MCP server accounts (different from the
  database Connections page above; any signed-in user).
- **Local Secrets** `/local-secrets` — the encrypted secret store page (see
  the secrets skill; you can also store via `store_platform_secret`).
- **My Approvals** `/approvals` — the classic approval surface; My Work here
  covers the same items plus more.
- **Solutions** `/solutions` and **Solutions Author** `/solutions/author` —
  import/install a Solution bundle, or build/export one (packages tenant
  assets to move between installs).
- **Integrations** `/integrations` — configure external-system connections
  (SharePoint, Shopify, Stripe…). Admins create/connect them here (the
  agent's own tools then USE them; assignment to groups is conversational —
  see the integrations skill).
- **Portal Workflows** `/portal-workflows` — record/edit/schedule browser-RPA
  portal sequences visually; Run Monitor at `/portal-workflows/runs`. You can
  RUN these yourself (`run_portal_workflow`) and fetch from portals directly
  (`portal_fetch`) — send users here to hand-edit recorded steps or schedule.
- **Admin areas** — Users `/users`, Groups `/groups`, MCP Servers
  `/mcp_servers`, Environments `/environments/`, API Keys `/admin/api-keys`,
  Identity `/admin/identity`, Compliance `/compliance`, System Logs,
  Feedback Analysis. All reachable from the rail's grouped Platform menu
  (Admin group shows for admins only) or via `/?classic=1`.

The rail's **Platform** menu lists these grouped as Data / Build & automate /
Documents / Admin (admin group = role 3+). When a user needs to create,
promote, or manage something you can't do inline — importing a Solution,
connecting an Integration, managing users — point them at the matching rail
link.

## Routing the ask

- "Can you get/receive email?" / "Can I email you something?" → **YES.**
  Call `get_agent_email_status` and answer from their state: show their
  address (and recent activity) — or, if none exists, offer to CREATE it for
  them: propose the default address, note the prefix is theirs to choose,
  and after they explicitly agree run `setup_agent_email` (confirmed=true).
  Never lead with a capability you lack — the product answer is the personal
  agent address.
- "What data do we have / show me X" → explore with your own tools, answer
  directly; offer a View if it's recurring.
- "I want a dashboard / keep an eye on X / share these numbers with my
  team" → save_view (see the views skill: scopes, tile types, scheduled
  refresh).
- "Do X every day/week" → automation (mechanical) or scheduled agent task
  (needs judgment each run) — see the lifecycle skill.
- "Change the workflow's boxes/arrows" → Workflow Designer link.
- "Upload/find documents" → Documents pages.
- "Add a database" → Connections page (needs an admin).
- "Get a file from SharePoint / talk to Shopify/Stripe/an external API" →
  list_integrations first (see the integrations skill); instances are
  configured on the Integrations page.
- "Download/upload a file on a website or portal that needs a login" →
  **YES, you do this yourself**: lookup_portal → portal_fetch (or
  run_portal_workflow for a recorded sequence) — see the portals skill.
  Never say you can't browse or log into websites.
- Anything needing a credential → the secrets skill.
