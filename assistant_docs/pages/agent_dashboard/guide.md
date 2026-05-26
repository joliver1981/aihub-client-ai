# Agent Management Dashboard (`/agent_dashboard`)

A monitoring and lightweight management surface for AI agents. Shows aggregate metrics, the list of agents with their status, a recent-activity feed, and a task table. Auto-refreshes every 30 seconds.

> **This is a dashboard, not a builder.** To create or deeply configure a Custom Agent (tools, prompts, knowledge), use `/custom_agent_enhanced`. To create a Data Agent, use `/custom_data_agent`. This page lets you see what's running, kick off quick tasks, and create a lightweight agent shell.

---

## Page Sections

### Header
- **Refresh** — manually re-pull all data. The page also auto-refreshes every 30s.
- **New Agent** — opens a modal to create a lightweight agent shell with just a name, description, and type (General / Data / Workflow). For full configuration, edit the agent on the appropriate builder page afterward.

### Metric Cards (top row)
Four counters across the top:
- **Total Agents** — every agent visible to the current user/tenant.
- **Active Agents** — agents currently enabled.
- **Active Tasks** — tasks in flight.
- **Failed Tasks** — tasks that ended in failure (cumulative within the dashboard's window).

### Active Agents list (left, 2/3 width)
- Each agent shows as a card with its status indicator (active / inactive / busy color stripe), name, description, and tool list.
- **Filter dropdown:** All / Active Only / Inactive Only / Busy Only.
- Each card has quick-action buttons for common operations (status varies — read what's on the card before describing actions to the user).

### Recent Activity feed (right, 1/3 width)
Reverse-chronological list of recent agent events (task started, completed, errored). Color-coded:
- Green = success
- Red = error
- Yellow = warning

### Task Management table (bottom)
- Full-width table of recent tasks: Task ID, Agent, Description, Status, Created, Actions.
- **Create Task** button opens a modal to dispatch a new task to a chosen agent with a description and priority (low / medium / high / urgent).

---

## Common Tasks

### "How do I create a new agent here?"
Click **New Agent**. Fill in name, description, and pick the type (General / Data / Workflow). This creates a lightweight shell. For real configuration (tools, prompts, knowledge, model), open the agent on its proper builder page: `/custom_agent_enhanced` for general agents, `/custom_data_agent` for data agents.

### "How do I send a quick task to an agent?"
Scroll to the **Task Management** section, click **Create Task**, pick an agent from the dropdown, write the task description, and set priority. The task appears in the table and shows up in the activity feed as it runs.

### "An agent isn't doing anything"
Check its status indicator on the agent card (color stripe on the left). Inactive agents need to be enabled before they can take tasks — that's done from the agent's edit page on the appropriate builder.

### "Why are my failed tasks high?"
Click into individual tasks in the Task Management table to see error details. For deep debugging of an agent's tool calls and reasoning, the `/chat` page with that agent and tool-call visibility turned on is usually more informative than the dashboard.

---

## What This Page Doesn't Do

- It doesn't let you configure tools, prompts, knowledge, or models on an agent — that's the builder pages.
- It doesn't show workflow runs — that's `/workflow_monitor`.
- It doesn't show chat conversations — those live on `/chat`.
- It doesn't manage MCP server connections — that's `/mcp_servers`.

## Related Pages
- **Build/edit agents:** `/custom_agent_enhanced` (general), `/custom_data_agent` (data).
- **Chat with an agent:** `/chat`.
- **Workflow runs and schedules:** `/workflow_monitor`.
- **MCP servers powering tools:** `/mcp_servers`.
