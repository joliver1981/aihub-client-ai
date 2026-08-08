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
- **Views** — deterministic dashboards. Pinned SQL, refreshed with zero AI.
  When a user likes an analysis, offer to `save_view` it.
- **Playbooks** — inventory of workflows, code flows, and automations, with
  jump links to the Designer and Mission Control.
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
- **Local Secrets** `/local-secrets` — the encrypted secret store page (see
  the secrets skill; you can also store via `store_platform_secret`).
- **My Approvals** `/approvals` — the classic approval surface; My Work here
  covers the same items plus more.
- **Scheduler, users, settings** — classic admin area; direct admins to the
  classic app (`/?classic=1` from the home page when Agent Mode is on).

## Routing the ask

- "What data do we have / show me X" → explore with your own tools, answer
  directly; offer a View if it's recurring.
- "Do X every day/week" → automation (mechanical) or scheduled agent task
  (needs judgment each run) — see the lifecycle skill.
- "Change the workflow's boxes/arrows" → Workflow Designer link.
- "Upload/find documents" → Documents pages.
- "Add a database" → Connections page (needs an admin).
- Anything needing a credential → the secrets skill.
