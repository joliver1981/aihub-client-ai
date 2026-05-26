# Agent-Environment Assignments (`/environments/assignments`)

The page where you map agents to **execution environments**. An "environment" here is a configured runtime context (Python bundle, container, cloud configuration, model deployment target) that an agent will run in. This is a developer/admin page — it requires a developer role and the Agent Environments feature flag.

> **Prerequisite:** Environments must be defined first (under the Environments management area, typically `/environments/`). This page is purely for **assigning** existing agents to existing environments — not for creating either.

> **Two URLs reach this same page:** `/environments/assignments` (primary) and `/assignments/manage`. The page name extracted from the URL differs, so per-page documentation lookups may behave differently between the two paths. Prefer `/environments/assignments`.

---

## Page Layout

- **Compact header** — "Agent-Environment Assignments" with a description of the page's purpose.
- **Assignment management UI** — the page lists agents (or environments) and lets you change which environment each agent is currently assigned to. Exact controls vary by deployment (table view, card view, drag-and-drop, dropdown per row).
- **Permission gating** — only roles 2 and above (developer / admin) can use this page. Users below that role see a permission-denied flash and are redirected.

## What an Assignment Does

When an agent is assigned to an environment:
- Its runtime executes against that environment's configuration (e.g., a specific Python venv, cloud region, or model deployment).
- Tool execution, model calls, and any sandboxed code run within that environment's constraints.
- Switching an agent's environment changes its behavior immediately for new conversations — running conversations may continue under the prior environment until they end.

## Common Tasks

### "Move an agent to a different environment"
Find the agent in the assignment table, change its environment via the dropdown / picker, and save. The change applies to new runs.

### "I don't see this page"
- Confirm Agent Environments are enabled for your tenant (the page redirects with a warning otherwise).
- Confirm your role is developer (2) or admin (3). End users (role 1) cannot access this page.

### "I need to create a new environment first"
This page only manages assignments — you can't create environments here. Go to the Environments management area (`/environments/` or wherever the editor lives in your deployment), define the environment, then return here to assign agents to it.

## Related Pages

- **`/environments/`** — environment definitions (create / edit environments themselves).
- **`/custom_agent_enhanced`** — agent configuration (tools, prompts, knowledge). Per-agent environment may also be set there, depending on deployment.
- **`/agent_dashboard`** — see currently active agents and tasks.

## What This Page Doesn't Do

- It doesn't create or edit environment definitions.
- It doesn't create or configure agents.
- It doesn't show per-agent run history — for that, use `/agent_dashboard` or the agent-specific monitoring views.
