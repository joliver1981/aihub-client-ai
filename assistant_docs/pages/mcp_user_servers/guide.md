# MCP Server Connections (`/mcp_user_servers`)

A user-facing surface for adding and managing MCP (Model Context Protocol) server connections so AI agents can reach enterprise systems, cloud services, and SaaS platforms.

> **Three MCP-related pages — don't conflate them:**
> - **`/mcp_servers`** — the administrative MCP server registry. Configure servers, OAuth grant types, scopes, enabled state, tenant-wide service-account integrations.
> - **`/my-connections`** — the per-user OAuth sign-in page for personal accounts (Microsoft 365, Google, etc.).
> - **`/mcp_user_servers`** (this page) — user-side management of MCP server connections: add a connection, view status, test all.

---

## Page Layout

### Header / Info banner
The top of the page explains what MCP servers are and the kinds of systems they connect to:
- **Enterprise systems** — ERP, CRM, HR systems with MCP endpoints.
- **Cloud services** — AWS, Azure, Google Cloud MCP APIs.
- **SaaS platforms** — Salesforce, Slack, GitHub, etc.

The banner is dismissible.

### Top action row
- **Add Connection** — open the dialog to register a new MCP server connection (URL, auth, capabilities).
- **Server Directory** — browse a curated list of known MCP servers and add by template.
- **Import Config** — load an existing MCP server configuration from a file.
- **MCP Registry** (external link) — opens [modelcontextprotocol.io/registry](https://modelcontextprotocol.io/registry) in a new tab for community-maintained servers.
- **Test All** — health-check every configured connection at once.

### Server statistics row
Counter cards for Total Connections, Active, and others depending on deployment.

### Server list
The body of the page lists each configured connection with status (active / failed / disconnected), capabilities exposed, and per-row management actions.

## Common Tasks

### "Connect a new MCP server"
Click **Add Connection**. Provide the server's URL, auth credentials, and any required capabilities. Save, then click **Test All** (or the row-level test) to confirm reachability.

### "Browse available MCP servers and pick one"
Click **Server Directory** — shows curated entries you can add with one click and a credentials prompt.

### "Check if my MCP servers are still working"
Click **Test All** — every configured connection runs a health check. Failed connections show as such in the list; click into one to see the error.

### "Import an MCP config from another environment"
Click **Import Config** and provide the config file. The connections are registered against the current user/tenant.

## Related Pages
- **`/mcp_servers`** — admin-side registry where servers are formally defined (OAuth grant types, scopes, enabled flag, tenant-wide service accounts).
- **`/my-connections`** — personal OAuth sign-in for per-user services (Microsoft 365, Google Workspace, etc.).
- **`/custom_agent_enhanced`** — once a server is connected here, its tools can be wired into a specific agent on the agent builder page.

## What This Page Doesn't Do

- It doesn't sign you into a specific OAuth service for personal use — that's `/my-connections`.
- It doesn't wire MCP tools into a specific agent — that's the agent builder.
- It doesn't expose tools globally to every agent automatically; an admin still controls which agents can use which servers.
