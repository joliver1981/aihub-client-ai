# Data Explorer (/data_explorer)

Data Explorer is a conversational data-analysis workspace. You pick a data source, ask questions in plain English, and the assistant returns tables and charts. Promising results can be pinned into a dashboard, arranged, and saved for reuse.

## What Makes This Page Different

Data Explorer combines three things that usually live in separate tools:

1. **Conversational querying** — like /data_chat, you ask questions in natural language.
2. **Visualization** — answers come back as interactive tables and charts, not just text.
3. **Dashboarding** — you can pin charts onto a dashboard grid, arrange them, save the dashboard, and come back to it later.

If you just want to ask a one-off question and read the answer, /data_chat is lighter. If you want to *build a thing* — a recurring dashboard, a saved view, a chart you'll show in a meeting — Data Explorer is the right tool.

## Page Layout

### Left Sidebar

**Data Source**
- Dropdown of available data agents. Each agent has its own connections and access scope.
- Below the dropdown, the agent's *objective* shows what this agent can see and answer.

**Dashboards**
- *New* (+ button) — start a fresh empty dashboard.
- *Saved dashboards list* — click any saved dashboard to open it in the slide-out panel.

**Settings** (bottom of sidebar)
- *Theme toggle* — light/dark.
- *New Session* — clears the conversation and resets state (does not delete saved dashboards).

### Main Area

**Welcome State** (before your first question)
- Suggestion chips for common starter prompts — click one to send it.

**Conversation**
- The chat scrolls up from the input bar. User messages on one side, assistant on the other.
- Assistant replies are typically **rich**: a short explanation plus a table or chart you can interact with.
- A **status indicator** ("Thinking…", "Querying…") appears above the input while the assistant works.

**Input Bar** (fixed at bottom)
- Type your question; press Enter or click the paper-plane button.

### Dashboard Panel (slides in from the side)

Opens when you select a saved dashboard, click *+ New*, or pin a chart.

- **Title** — click to rename.
- **Refresh** — re-run all queries on this dashboard with current data.
- **Save** — opens the Save Dashboard modal to name and persist it.
- **Grid** — pinned charts arranged with [Gridstack](https://gridstackjs.com/). Drag tiles to rearrange; resize from corners.

### Detail Panel (slides in from the right)
- Opens when you drill into a chart point or table row for more context.

## Common Tasks

### Run your first analysis
1. Pick a data source from the sidebar.
2. Read the *objective* to confirm this agent can see what you need.
3. Type a question, e.g. "Show me total sales by month."
4. Press Enter. A chart or table appears in the conversation.

### Build a dashboard
1. After getting a chart you like, pin it to the dashboard (look for a pin/add-to-dashboard action on the chart).
2. Ask the next question; pin that result too.
3. Open the **Dashboard** panel from the sidebar; the pinned tiles are arranged in a grid.
4. Drag/resize tiles to taste.
5. Click **Save**. Give the dashboard a name. It will appear under *Saved dashboards* in the sidebar.

### Re-open a saved dashboard
- Click its name in the *Saved dashboards* list. The dashboard panel slides in and all queries re-run against current data.

### Refresh a dashboard with the latest data
- Open the dashboard panel and click **Refresh**. Every tile re-queries.

### Rename or replace a dashboard
- Open the dashboard, click the title to rename, then click **Save** (it overwrites by name).

### Reset the conversation
- Click **New Session** in the sidebar. Saved dashboards remain; only the live conversation is cleared.

## Tips That Make Data Explorer Faster

- **Be specific about time windows.** "Last quarter" works; "recently" usually doesn't.
- **Specify the chart type if you care.** "Show me a line chart of monthly orders" beats "show me monthly orders" if you want a line specifically.
- **Iterate.** Get a rough chart, then ask "now break that down by region" to refine.
- **Pin generously, prune later.** Dashboards are easy to save and easy to delete.

## Troubleshooting

- **Status indicator spins forever** — the underlying data agent is taking too long or got stuck. Open **New Session** and retry with a narrower question.
- **Chart looks wrong / empty** — open the detail panel for any data point to inspect, or ask the assistant "what query did you run?" to verify the agent interpreted the question correctly.
- **A saved dashboard's tile shows an error** — the data the tile expects no longer exists (column renamed, table dropped, permissions changed). Open the tile and re-ask its question against the current data.
- **Tile won't drag/resize** — the dashboard panel needs focus; click an empty area of the grid first.
- **"Loading agents…" never resolves** — the data agent registry endpoint isn't responding. Reload the page; if it persists, check that the data service is up.

## What This Page Is NOT

- It is **not** a SQL editor — the agent writes the queries. If you want to see them, ask the agent.
- It is **not** the place to define data sources — that's done in Custom Data Agents and Connections.
- It is **not** a production reporting tool — it's exploratory. For pixel-perfect scheduled reports, use a workflow that exports to Excel.
