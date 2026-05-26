# Agent Chat (/chat)

The Agent Chat page is the primary conversational interface in AI Hub for talking with a configured AI agent. It replaces the older Assistants page for end-user conversations.

## Purpose

This is where you:
- Have a back-and-forth conversation with a selected agent
- Ask questions, request analysis, or have the agent execute tasks via its tools
- Attach documents to give the agent additional context for the conversation
- Resume earlier conversations from history
- Use MCP server tools the agent has access to (when available)

## Page Layout

### Header (top bar)
- **Agent name** — the currently-selected agent. Shows "Agent Chat" before selection.
- **Conversation history button (clock icon)** — opens a sidebar listing past conversations, with pinned items, recent items, and a search box.
- **Theme toggle (moon/sun icon)** — switches between light and dark mode. Preference persists.
- **Reset conversation (sync icon)** — clears the current conversation and starts a new one with the same agent.

### Left Sidebar

**Agent Selection**
- *Select Agent* dropdown — choose which agent to chat with. Required before sending messages.
- *Objective* — read-only summary of the selected agent's purpose, shown after selection.

**Quick Actions** (appears once an agent is selected)
- *Edit* — opens the agent's configuration page.
- *Email* — opens email settings for agents that handle email.
- *Inbox* — opens the agent's email inbox; a badge shows unread count.
- *Email status indicator* — shows whether email is configured for the agent.

**Document Context** (collapsible, hidden until relevant)
- Drop files into the upload zone or click to browse.
- Supported types: PDF, Word (.docx/.doc), Excel (.xlsx/.xls), text, CSV, images (JPG/PNG).
- Files are queued, then uploaded with the Upload button.
- Uploaded documents become context the agent can reference for the rest of the conversation.

**MCP Servers** (collapsible, hidden unless the agent has MCP tools)
- Lists the MCP servers and their tools available to the selected agent.

### Main Chat Area
- **Welcome state** — shown before the first message. Suggests example prompts.
- **Message bubbles** — user messages on the right, agent messages on the left. Agent messages can contain rich content (formatted text, code blocks, tables, charts).
- **Input area** — the text box at the bottom. Press Enter to send, Shift+Enter for a new line. The paperclip icon attaches files when document context is enabled.

### Detail Panel (right side, slides in)
- Opens when the agent emits structured content the user can drill into (e.g., a row from a table, a chart point). The panel header label is "Detail".

## Common Tasks

### Start a new conversation
1. Pick an agent from the **Select Agent** dropdown.
2. Read the *Objective* to confirm this is the right agent for what you need.
3. Type your message and press Enter.

### Continue a past conversation
1. Click the clock icon to open **History**.
2. Find the conversation by scrolling or using the search box.
3. Click it to load — messages reappear in the main area.

### Add documents the agent can read
1. Expand **Document Context** in the left sidebar.
2. Drop files onto the upload zone, or click to browse.
3. Review the queue, then click **Upload**.
4. After upload completes, the agent will reference those documents in its replies.

### Reset and start over with the same agent
- Click the sync icon in the header. This clears the current chat but keeps the same agent selected.

### Switch agents
- Pick a different agent from the dropdown. The current conversation is left intact in history — it does not transfer to the new agent.

## Troubleshooting

- **Send button doesn't respond** — confirm an agent is selected; the dropdown must have a value.
- **Agent says it can't access documents** — confirm the upload completed (look for the document in the *Uploaded Documents* list under Document Context, not just the queue).
- **History is empty** — history is per-user and per-agent; switching agents shows that agent's history.
- **Theme keeps reverting** — the preference is stored in browser localStorage; a different browser or private window will start in dark mode.
- **Email status says "Email not configured"** — open *Email* under Quick Actions and complete the setup before using email-triggered features.

## What This Page Is NOT

- It is **not** the place to build or edit agents — use the agent's *Edit* quick action, or the Custom Agents page.
- It is **not** a workflow editor — use the Workflow Tool for visual node-based automation.
- It is **not** the Command Center — the floating assistant in the corner is a separate help/orientation assistant; the main chat area talks to the agent you selected.
