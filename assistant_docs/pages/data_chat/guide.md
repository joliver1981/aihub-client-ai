# Data Assistant Chat (/data_chat)

The Data Assistant Chat page is a conversational interface for asking questions about your data in plain English. You pick a *data agent* that has access to specific databases or datasets, then chat with it. The agent translates your question into a query, runs it, and returns answers as tables, charts, and explanations.

## How It Differs From the Regular Agent Chat (/chat)

- **/chat** talks to general-purpose AI agents that may use any tools the administrator wired into them.
- **/data_chat** talks to *data agents* specifically — agents whose primary job is answering questions over structured data sources. They have data-aware tools and prompts tuned for analytical questions.

If you want to ask "what were our top 10 customers last quarter?", this is the right page. If you want to draft an email, summarize a document, or run a multi-step workflow, use /chat or the appropriate specialized page.

## Page Layout

### Header
- **Theme toggle** — switch between light and dark mode.
- **Reset** — clear the current conversation and start a new one with the same data agent.

### Left Sidebar

**Agent Selection**
- *Select an agent* dropdown — choose which data agent to talk to. Required.
- *Agent objective* — read-only summary of what this agent is meant for and which datasets it has access to.

**AI Cautiousness** (only shown if the caution system is enabled for this tenant)
- **Low** — the agent makes more assumptions; fastest but may guess wrong.
- **Medium** (default) — balanced; the agent confirms when ambiguous.
- **High** — the agent verifies more before answering; slower.
- **Very High** — maximum verification; the agent asks clarifying questions and double-checks results.

Pick a higher level when accuracy matters more than speed (compliance reports, financial figures); pick lower when you're exploring.

### Main Chat Area
- **Welcome state** — shown before your first message. Suggests common starter prompts.
- **Message bubbles** — your messages on the right, agent on the left. Agent replies can contain rich content: tables, charts, formatted summaries, SQL or Python snippets.
- **Conversation length banner** — appears in red at the top of the chat when the conversation has gotten too long for the model to handle reliably. When you see it, hit Reset.
- **Input area** — type your question; press Enter to send (Shift+Enter for a newline).
- **"Want me to explain?" link** — shown after some answers. Click it to get a plain-English explanation of how the agent arrived at the answer (which tables it queried, what filter it applied, what it assumed).

### Right Detail Panel
- Slides in when the agent returns structured content (a chart point, a table row) that you can click into for more detail.

## Common Tasks

### Ask your first data question
1. Pick a data agent from the dropdown.
2. Read the *Objective* so you know what data this agent can see.
3. Type a natural-language question — e.g., "Show me the top 10 customers by revenue last quarter."
4. Press Enter.

### Understand how the agent got an answer
- Click **Want me to explain?** under the agent's response. The agent will describe its reasoning, the query/queries it ran, and any assumptions.

### Adjust precision vs. speed
- Use the *AI Cautiousness* dropdown. Higher caution = the agent will ask clarifying questions instead of guessing.

### Switch to a different dataset
- Pick a different agent from the dropdown. Each data agent has its own data sources.
- Note: switching agents starts the conversation over; the new agent doesn't see the previous one's chat.

### When the conversation gets too long
- A red banner appears at the top of the chat. Click **Reset** in the header to clear and continue.

## Common Question Patterns

The data assistant works best with questions that:
- **Reference real entities** — "customers", "orders", "products" — match these to the dataset.
- **Specify a time window** — "last quarter", "this year", "since January".
- **Specify aggregation** — "top 10", "by month", "average per region".
- **Ask one thing at a time** — chained questions in a single message work but follow-ups are often clearer.

Examples:
- *"What are the top 10 customers by revenue last quarter?"*
- *"Compare Q1 and Q2 sales by region."*
- *"Which products had the biggest YoY decline?"*
- *"Plot monthly orders for the last 12 months."*

Less ideal:
- *"How is the business doing?"* — too vague; the agent will likely ask for clarification.
- *"Why are sales down?"* — the agent can show the numbers; explaining *why* usually needs context it doesn't have.

## Troubleshooting

- **Agent says it can't see a table or column you expect** — the agent is scoped to whatever its configuration grants. Switch to a different agent that has access, or have an administrator update this agent's data sources (Custom Data Agents page).
- **Wrong number returned** — click *Want me to explain?* to see the query the agent ran. Common causes: it filtered on a column you didn't mean, or it interpreted a time window differently.
- **Conversation feels stuck or repetitive** — Reset and rephrase. Sometimes a fresh start with a clearer question works better than back-and-forth clarification.
- **Error modal pops up** — read the message; common causes are timeout (large query), permission denied (the agent's connection lacks access), or a transient connection drop.

## What This Page Is NOT

- It is **not** a SQL editor — you don't write the query; the agent does. If you want to see the raw query, ask the agent to show it, or click *Want me to explain?*.
- It is **not** the place to build dashboards — use the Data Explorer page for that.
- It is **not** where data agents are configured — use Custom Data Agents to define which datasets an agent has access to.
