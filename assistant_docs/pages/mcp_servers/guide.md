# MCP Server Management (/mcp_servers)

The MCP Server Management page is where administrators register external systems that AI agents can call into using the Model Context Protocol (MCP). Once registered, an MCP server's tools become available to any agent the administrator grants access to.

## What MCP Is, in Plain Terms

MCP is a standard way for AI agents to talk to outside systems — a CRM, an ERP, a database, a SaaS app, or a local script. Each MCP server exposes a set of *tools* (functions). When you connect a server here, the tools it provides can be wired into your agents on the agent's configuration page.

Think of this page as the "control panel of external capabilities" for your agents.

## Page Layout

### Header
- **Gateway Status** — a banner at the top showing whether the MCP Gateway service is reachable. The gateway is the local component that brokers all MCP traffic; if it is down, no server connection will work.

### Action Bar
- **Add Server** — open the modal to register a new server (Remote or Local).
- **Server Directory** — browse a built-in catalog of common MCP servers with one-click installation.
- **Test All** — connect to every configured server and refresh their status.

### Statistics Cards
- **Total Servers** — how many MCP servers are configured.
- **Last Test OK** — how many passed their most recent connection test.
- **Available Tools** — total tool count across all servers.
- **Agents Using** — number of agents that have at least one MCP tool wired in.

### Configured Servers Table
Each row is a registered server:
- **Type** — Remote (HTTP endpoint) or Local (subprocess).
- **Name / Description** — friendly name and what it does.
- **Endpoint** — URL for Remote, command/path for Local.
- **Category** — grouping label (CRM, ERP, Database, etc.).
- **Status** — last connection result.
- **Tools** — how many tools this server exposes.
- **Agents** — how many agents currently use it.
- **Actions** — edit, test, or remove the server.

Filter the table with the *Type* dropdown and the *Search* box in the card header.

## Common Tasks

### Add a remote MCP server (the typical case)
1. Click **Add Server**.
2. On the **Remote** tab, fill in:
   - *Server Name* — anything memorable.
   - *MCP Endpoint URL* — the URL the server provider gave you.
   - *Category* — pick a label so it's easy to find later.
   - *Description* — what this server does.
3. Open the **Authentication** card and pick the right mode:
   - **None** — public or open server.
   - **Bearer Token** — paste the token in *Bearer Token*.
   - **API Key** — paste the key and the header name it goes in.
   - **Basic Auth** — username + password.
   - **Custom Headers** — supply key/value pairs.
4. Save. The system will test the connection and pull the list of tools.

### Add a local MCP server
1. Click **Add Server**, switch to the **Local** tab.
2. Provide the command and arguments that launch the server process on this machine.
3. Save. The gateway will start the process and connect to it.

### Install a server from the directory
1. Click **Server Directory**.
2. Browse or search the catalog.
3. Click **Install** on the one you want — fill in any required credentials.

### Test connections
- One server: use the row's *Test* action.
- All servers: click **Test All** at the top.

### Remove a server
- Use the row's *Delete* action. Any agents that referenced its tools will lose access to those tools but will otherwise continue to work.

## Troubleshooting

- **Gateway Status banner says "Checking…" forever** — the MCP Gateway service is not responding. On the host machine it runs on port 5071. Restart the MCP Gateway service (or its Windows service via NSSM) and refresh.
- **Server shows red status** — open *Edit* on the row and verify the URL and credentials. For Remote servers, confirm the endpoint is reachable from this host. For Local servers, confirm the command runs by hand.
- **Tools count is zero on a server you just added** — the connection succeeded but the server returned no tools. Check the server's documentation; some servers require additional configuration before they expose tools.
- **An agent isn't using the tools you expected** — connecting a server here makes its tools *available*. The agent must be edited and the specific tools enabled on its configuration page.

## What This Page Is NOT

- It is **not** where you build agents — go to Custom Agents.
- It is **not** where you wire MCP tools to a specific agent — that happens on the agent's edit page, where MCP tools appear alongside built-in and custom tools.
- It is **not** a server marketplace — the Server Directory is a curated catalog of known servers; you are still connecting to those servers' real endpoints.
