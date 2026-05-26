# AI Hub — Cross-Page Concepts

This file is the orientation map for the assistant. It explains concepts that span pages, so the assistant can answer "what is X?" questions without the user being on the X page. **Keep answers brief**; detail belongs in the per-page guide for the feature.

## What AI Hub Is

AI Hub is an enterprise platform for building, running, and monitoring AI agents and the workflows that use them. Users configure agents (often without code), give them access to data and tools, chat with them, and stitch them into automated workflows.

## Core Concepts

### Agents
An agent is a configured AI worker: a language model plus a defined role, set of tools, and (optionally) attached knowledge. Three flavors appear in the UI:
- **Custom agents** — general-purpose; you pick tools and prompts.
- **Data agents** — tuned for answering questions over structured data sources.
- **Compliance agents** — data agents with a knowledge base of compliance documents.

### Tools
A tool is a capability an agent can invoke (a function call). Tools come from three places:
- **Built-in** — search, math, web fetch, etc., shipped with AI Hub.
- **Custom tools** — defined per-tenant in the Custom Tool page.
- **MCP tools** — exposed by external systems via MCP servers and made available to selected agents.

### Knowledge
Knowledge is the document corpus an agent can search when answering. Files (PDF, Word, text, etc.) are uploaded, chunked, embedded, and indexed into a per-agent vector store. Agents look up relevant chunks at conversation time.

### Workflows
A workflow is a visual, node-based automation: triggers, agent steps, data steps, conditions, and approvals chained together. Built in the **Workflow Designer**; watched in the **Workflow Monitor**.

### MCP (Model Context Protocol)
MCP is the standard way AI Hub connects agents to external systems (CRMs, ERPs, databases, SaaS apps). An MCP server publishes a set of tools; once registered on the MCP Servers page, those tools can be wired into agents alongside built-in and custom ones.

### Connections
Connections store the credentials and config needed to reach external systems (databases, APIs, mail servers). Tools and data agents reference connections by name rather than embedding secrets.

### Solutions
A solution is a portable bundle (a `.zip`) that packages agents, workflows, tools, knowledge, and config together. Solutions are installed from the Solutions Gallery or uploaded directly; they let working capabilities be shared across environments or customers without manual rebuild.

### Retailer Compliance
A vertical feature for teams that need to track vendor/retailer compliance documents: per-retailer document sets, versioned uploads, automatic extraction of requirements, and version-to-version comparison.

### Schedules
Workflows and certain jobs (document refresh, scheduled chats) can run on a recurring schedule. Schedules are managed alongside the thing they trigger and observable from the Workflow Monitor's Schedules tab.

### Environments
A single AI Hub installation can serve multiple environments (often per-customer or per-team). Agents, workflows, knowledge, and connections are scoped to the active environment; switching environments changes what the user sees everywhere.

### Roles
Each user has a role (end user, builder/developer, administrator). Some pages and actions are gated by role; the assistant should respect what the current page allows rather than suggest actions the user can't perform.

## Where Things Live (Navigation Map)

| If the user wants to… | Send them to |
|---|---|
| Chat with an agent | `/chat` (the primary chat interface) |
| Ask questions about data conversationally | `/data_chat` or `/data_explorer` |
| Build a chart or dashboard from data | `/data_explorer` |
| Create or edit a custom agent | `/custom_agent_enhanced` (Custom Agents) |
| Create or edit a data agent | `/custom_data_agent` |
| Build a workflow | `/workflow_tool` (Workflow Designer) |
| See workflow runs, approvals, schedules | `/workflow_monitor` |
| Manage documents / knowledge files | `/document_manager` |
| Connect external systems via MCP | `/mcp_servers` |
| Connect your personal accounts (Microsoft 365, Google, etc.) | `/my-connections` |
| Browse / connect SaaS integrations (Shopify, QuickBooks, etc.) | `/integrations` |
| Track retailer compliance documents | `/compliance_management` |
| Install a pre-built capability | `/solutions` (the Solutions Gallery) |
| Package tenant content as a reusable solution | `/solutions/author` |
| Manage users / groups | `/users`, `/groups` |
| Manage credentials and shared connections | `/connections` |

> Note: The older `/assistants` page is a legacy chat surface. New users should use `/chat` instead.

## Guidance for the Assistant

- Prefer to answer using what is on the current page first; reach for cross-page concepts only when the question requires it.
- Don't recite this glossary unprompted — use it to ground specific questions.
- If a user asks how to do something that lives on a different page, name that page and what to look for, rather than trying to walk them through it from here.
- If a concept above does not match what the user is describing, say so plainly instead of speculating.
