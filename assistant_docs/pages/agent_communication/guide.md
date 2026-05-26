# Agent Communication Manager (`/agent_communication`)

A monitoring + lightweight messaging surface for agent-to-agent communication. Lets you send a message from one agent to another, build a sequenced workflow of agent-to-agent steps, and review the history of past communications.

> **This is not the same as `/chat`** (where a user chats with an agent). This page is for **agents talking to agents** — useful when you've built a multi-agent setup where one agent delegates or hands off work to another.

---

## Page Layout — Three Tabs

### 1. Direct Communication (default tab)
Left side:
- **Active Agents** — grid of agent cards showing currently available agents. Click a card to focus it.

Right side:
- **Send Message** form:
  - **From Agent** — pick the sender.
  - **To Agent** — pick the recipient.
  - **Message** — the body of the message.
  - **Context (JSON)** — optional structured context payload (default `{}`).
  - **Send Message** button.
- **Communication Result** — response/status appears below the form after sending.

### 2. Workflows
Build a multi-step sequence of agent-to-agent communications. Each step specifies sender, recipient, message, and ordering. Used to script chained delegations like "Agent A summarizes, then Agent B verifies, then Agent C drafts the email."

### 3. Communication History
A chronological log of past agent-to-agent messages with timestamps, participants, and outcomes. Use this for auditing or debugging multi-agent flows.

## Common Tasks

### "Send a one-off message between two agents"
Use **Direct Communication**. Pick From and To, type the message, optionally add JSON context, click Send. The result shows up in the result panel.

### "Set up a recurring multi-agent flow"
Use the **Workflows** tab to define an ordered sequence of agent-to-agent messages. Save the workflow, then trigger it as needed.

### "Why did Agent A respond to Agent B with X?"
Open the **Communication History** tab and find the entry. Each entry should show the inbound message, the context that was passed, and the outbound response.

## Related Pages

- **`/chat`** — user-to-agent chat. Not for agent-to-agent communication.
- **`/agent_dashboard`** — overall agent activity dashboard (metrics + task management).
- **`/workflow_tool`** — the visual workflow designer, which is the more powerful way to build multi-step agent flows when conditional branching, data passing, and approvals are involved. Prefer `/workflow_tool` for anything beyond simple message chains.

## What This Page Doesn't Do

- It's not where you configure an agent (tools, prompts, knowledge) — that's `/custom_agent_enhanced` and `/custom_data_agent`.
- It's not user-to-agent chat — that's `/chat`.
- It doesn't replace the workflow designer for complex automations — for branching, conditionals, integrations, approvals, use `/workflow_tool`.
