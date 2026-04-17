# AI Hub MCP Integration -- User Guide

## Table of Contents

1. [Overview](#1-overview)
2. [Prerequisites](#2-prerequisites)
3. [Installation and Setup](#3-installation-and-setup)
4. [Configuration](#4-configuration)
5. [Using the Management UI](#5-using-the-management-ui)
6. [How It Works](#6-how-it-works)
7. [Adding Custom MCP Servers](#7-adding-custom-mcp-servers)
8. [Troubleshooting](#8-troubleshooting)
9. [API Reference](#9-api-reference)
10. [Security Notes](#10-security-notes)

---

## 1. Overview

### What is MCP?

The **Model Context Protocol (MCP)** is an open standard that allows AI models to interact with external tools and data sources through a unified interface. MCP servers expose discrete "tools" -- functions that an AI agent can discover and invoke at runtime -- using the JSON-RPC 2.0 message format. This means an AI agent can read files, query databases, call APIs, or interact with virtually any system, as long as an MCP server exposes the capability.

### What This Integration Provides

The AI Hub MCP integration connects AI Hub agents to any MCP-compliant server, whether it runs locally as a subprocess or remotely over HTTP. The integration consists of six components:

| Component | Location | Role |
|---|---|---|
| **MCP Gateway Service** | `builder_mcp/gateway/` | Standalone FastAPI microservice (port 5071) that manages connections to MCP servers and translates REST to JSON-RPC 2.0 |
| **Client Library** | `builder_mcp/client/mcp_gateway_client.py` | HTTP client (`MCPGatewayClient`) used by the main application to communicate with the gateway |
| **Tool Converter** | `builder_mcp/client/tool_converter.py` | Converts MCP tool definitions into LangChain `StructuredTool` objects for agent use |
| **Agent Integration** | `builder_mcp/agent_integration/mcp_agent_tools.py` | Loads MCP tools into AI agents during initialization and builds system prompt additions |
| **Flask Routes** | `builder_mcp/routes/mcp_routes.py` | Blueprint at `/api/mcp/*` providing REST endpoints for server CRUD and gateway actions |
| **Management UI** | `builder_mcp/templates/mcp_servers.html` | Bootstrap 4 / jQuery page for managing MCP server configurations |

### Project Structure

```
builder_mcp/
├── gateway/                      # STANDALONE SERVICE (own Python env, port 5071)
│   ├── app_mcp_gateway.py        # FastAPI entry point
│   ├── server_manager.py         # Server lifecycle management
│   ├── stdio_transport.py        # Local subprocess MCP servers (stdin/stdout)
│   ├── sse_transport.py          # Remote HTTP/SSE MCP servers
│   ├── protocol.py               # JSON-RPC 2.0 message handling
│   ├── config.py                 # Gateway configuration
│   ├── requirements.txt          # Gateway-specific dependencies
│   ├── setup.bat                 # Windows setup script
│   ├── start_gateway.bat         # Start script
│   └── tests/
│       ├── sample_mcp_server.py  # Test MCP server (3 sample tools)
│       └── test_gateway.py       # Gateway unit tests
│
├── client/                       # MAIN APP -- HTTP client
│   ├── mcp_gateway_client.py     # REST client for gateway
│   └── tool_converter.py         # MCP tools -> LangChain StructuredTool
│
├── agent_integration/            # MAIN APP -- Agent wiring
│   └── mcp_agent_tools.py        # Tool loader + system prompt builder
│
├── routes/                       # MAIN APP -- Flask Blueprint
│   └── mcp_routes.py             # /api/mcp/* endpoints
│
├── templates/                    # MAIN APP -- UI
│   └── mcp_servers.html          # Server management page
│
└── tests/
    └── test_integration.py       # End-to-end tests
```

---

## 2. Prerequisites

### System Requirements

- **Operating System**: Windows Server 2016+ or Windows 10+ (the gateway uses `WindowsProactorEventLoopPolicy` for subprocess support)
- **Python**: 3.8 or higher
- **Database**: Microsoft SQL Server with the AI Hub schema already deployed
- **Network**: The gateway binds to `0.0.0.0` on its configured port (default 5071); ensure this port is available

### Database Tables

The following tables must exist before the integration can be used. They are part of the standard AI Hub database schema and use row-level security via `tenant.sp_setTenantContext`:

| Table | Purpose |
|---|---|
| `MCPServers` | Stores server configurations (name, type, URL, auth type, connection config, category, icon, timeouts, test status) |
| `MCPServerCredentials` | Stores encrypted authentication credentials using `ENCRYPTBYPASSPHRASE` / `DECRYPTBYPASSPHRASE` |
| `AgentMCPServers` | Maps which agents have access to which MCP servers (many-to-many, with `enabled` flag) |
| `UserMCPServers` | User-level server configurations |

### Software Dependencies

**Gateway service** (installed in its own virtual environment):

| Package | Minimum Version | Purpose |
|---|---|---|
| `fastapi` | 0.100.0 | Async web framework |
| `uvicorn` | 0.23.0 | ASGI server |
| `httpx` | 0.24.0 | Async HTTP client for remote SSE servers |
| `pydantic` | 2.0.0 | Request/response validation |
| `python-dotenv` | any | Environment variable loading |
| `requests` | any | Sync HTTP (utility) |

**Main application** (no new dependencies beyond what AI Hub already uses):

- `requests` (HTTP calls to gateway)
- `langchain` (for `StructuredTool`)
- `pydantic` (for dynamic model generation)

---

## 3. Installation and Setup

### 3.1 Gateway Service Setup

The gateway runs as its own process with its own Python virtual environment, isolated from the main AI Hub application.

**Step 1: Run the setup script**

```batch
cd builder_mcp\gateway
setup.bat
```

This script:
1. Verifies Python is installed and on the PATH
2. Creates a virtual environment at `builder_mcp\gateway\venv\`
3. Installs all dependencies from `requirements.txt`

**Step 2: Start the gateway (development)**

```batch
cd builder_mcp\gateway
start_gateway.bat
```

The start script activates the virtual environment and runs the FastAPI application. By default it listens on port 5071.

You should see output similar to:

```
Starting MCP Gateway on port 5071
Logs: ./logs/mcp_gateway_log.txt
INFO:     Uvicorn running on http://0.0.0.0:5071
```

**Step 3: Verify the gateway is running**

```batch
curl http://localhost:5071/health
```

Expected response:

```json
{
  "status": "ok",
  "message": "MCP Gateway is operational",
  "service": "mcp-gateway",
  "timestamp": "2025-01-15T10:30:00.000000",
  "active_connections": 0,
  "total_connections": 0
}
```

### 3.2 Main Application Integration

#### Register the Flask Blueprint

Add the following to your `app.py` file:

```python
# MCP Blueprint
from builder_mcp.routes.mcp_routes import mcp_bp
app.register_blueprint(mcp_bp)
```

#### Configure the Template Loader

Since the MCP template lives in `builder_mcp/templates/` rather than the main `templates/` directory, you need to tell Flask where to find it. Add the MCP templates folder to the Jinja loader:

```python
import jinja2

app = Flask(__name__, template_folder='templates')
app.jinja_loader = jinja2.ChoiceLoader([
    app.jinja_loader,
    jinja2.FileSystemLoader('builder_mcp/templates')
])
```

Alternatively, copy `mcp_servers.html` to the main `templates/` directory.

#### Add the Management Page Route

```python
@app.route('/mcp_servers')
@developer_required()
def mcp_servers_page():
    return render_template('mcp_servers.html',
                          template_folder='builder_mcp/templates')
```

### 3.3 Agent Wiring

To make MCP tools available to AI agents, add the following block to `GeneralAgent.py` after the email tools section and before the tool binding step:

```python
#########################
# MCP TOOLS
#########################
try:
    from builder_mcp.agent_integration.mcp_agent_tools import (
        get_mcp_tools_for_agent,
        get_mcp_system_prompt_addition
    )
    mcp_tools = get_mcp_tools_for_agent(agent_id)
    if mcp_tools:
        self.tools.extend(mcp_tools)
        mcp_prompt = get_mcp_system_prompt_addition(agent_id)
        if mcp_prompt:
            self.SYSTEM += mcp_prompt
        print(f'MCP tools added: {len(mcp_tools)}')
except ImportError:
    logger.debug("MCP module not available")
except Exception as e:
    logger.warning(f"Failed to load MCP tools for agent {agent_id}: {e}")
```

This code:
1. Checks gateway health (fails fast if the gateway is not running)
2. Queries the database for MCP servers assigned to this agent
3. Connects each server through the gateway
4. Converts MCP tool definitions into LangChain `StructuredTool` objects
5. Appends tool descriptions to the agent system prompt
6. Never raises exceptions -- returns an empty list on any failure

### 3.4 NSSM Service Registration (Production)

For production deployments, register the gateway as a Windows service using [NSSM](https://nssm.cc/) (Non-Sucking Service Manager):

```batch
nssm install AIHub_MCP_Gateway "C:\path\to\builder_mcp\gateway\venv\Scripts\python.exe" "C:\path\to\builder_mcp\gateway\app_mcp_gateway.py"
nssm set AIHub_MCP_Gateway AppDirectory "C:\path\to\builder_mcp\gateway"
nssm set AIHub_MCP_Gateway AppEnvironmentExtra "MCP_GATEWAY_PORT=5071" "MCP_GATEWAY_LOG=C:\path\to\logs\mcp_gateway_log.txt"
nssm start AIHub_MCP_Gateway
```

Replace `C:\path\to\` with the actual installation path. The `AppDirectory` setting ensures the gateway can resolve relative paths for its log files.

To manage the service after installation:

```batch
nssm status AIHub_MCP_Gateway
nssm restart AIHub_MCP_Gateway
nssm stop AIHub_MCP_Gateway
nssm remove AIHub_MCP_Gateway confirm
```

---

## 4. Configuration

### Environment Variables

All configuration is driven by environment variables. Set these in your `.env` file, system environment, or NSSM service configuration.

| Variable | Default | Description |
|---|---|---|
| `HOST_PORT` | `5001` | Base port for all AI Hub services. The MCP gateway port is calculated as `HOST_PORT + 70`. |
| `MCP_GATEWAY_PORT` | `5071` | Explicit override for the gateway port. Takes precedence over the `HOST_PORT + 70` calculation. |
| `MCP_GATEWAY_URL` | (calculated) | Full base URL of the gateway (e.g., `http://localhost:5071`). If not set, the client calculates it from `HOST_PORT`. |
| `MCP_GATEWAY_LOG` | `./logs/mcp_gateway_log.txt` | Path to the gateway log file. The directory is created automatically if it does not exist. |
| `LOG_LEVEL` | `DEBUG` | Gateway log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `MCP_CONNECT_TIMEOUT` | `30` | Maximum time in seconds to wait for an MCP server connection and initialization handshake. |
| `MCP_TOOL_CALL_TIMEOUT` | `60` | Maximum time in seconds to wait for an individual tool execution to complete. |
| `MCP_TOOL_CACHE_TTL` | `300` | Time in seconds to cache the tool list for a connected server before re-fetching. |
| `MCP_MAX_RETRIES` | `3` | Maximum retry attempts for failed gateway operations. |
| `MCP_ENCRYPTION_KEY` | (from `encrypt.py`) | Passphrase used with SQL Server `ENCRYPTBYPASSPHRASE` / `DECRYPTBYPASSPHRASE` for credential storage. |

### Port Scheme

AI Hub follows a convention where each microservice runs at a fixed offset from the base port:

| Service | Offset | Default Port |
|---|---|---|
| Main App | +0 | 5001 |
| Documentation | +10 | 5011 |
| Scheduler | +20 | 5021 |
| Vector | +30 | 5031 |
| Agent | +40 | 5041 |
| Knowledge | +50 | 5051 |
| Executor | +60 | 5061 |
| **MCP Gateway** | **+70** | **5071** |

---

## 5. Using the Management UI

### 5.1 Accessing the Page

Navigate to `/mcp_servers` in your browser (requires developer-level access). The page displays:

- **Gateway status banner** -- Green when the gateway service is reachable, red when it is not.
- **Statistics cards** -- Total servers, servers with passing test status, total available tools, and agents using MCP servers.
- **Server table** -- Lists all configured MCP servers with type, name, endpoint, category, status, tool count, agent count, and action buttons.

### 5.2 Adding a Local MCP Server

Local servers run as subprocesses managed by the gateway. Use this for MCP servers installed on the same machine.

1. Click **Add Server**.
2. Select the **Local** tab.
3. Fill in:
   - **Server Name**: A descriptive name (e.g., "File System").
   - **Description**: Optional description of the server capabilities.
   - **Command**: The executable to run (e.g., `python`, `npx`, `node`, `uvx`). On Windows, the gateway automatically tries `.cmd` variants for `npx` and `node`.
   - **Arguments**: One argument per line. For example:
     ```
     -m
     my_mcp_server
     ```
   - **Environment Variables**: Click "Add Variable" to provide key-value pairs passed to the subprocess.
4. Click **Test Connection** to verify the configuration. The gateway starts the subprocess, performs the MCP handshake, lists tools, then tears down the connection.
5. Click **Save** to persist the configuration to the database.

### 5.3 Adding a Remote MCP Server

Remote servers communicate over HTTP/SSE. Use this for cloud-hosted MCP endpoints.

1. Click **Add Server**.
2. Select the **Remote** tab (active by default).
3. Fill in:
   - **Server Name**: A descriptive name (e.g., "Production CRM").
   - **MCP Endpoint URL**: The base URL of the remote MCP server (e.g., `https://api.example.com/mcp/v1`). The gateway appends `/sse` to open the SSE stream and falls back to plain HTTP POST if SSE is not available.
   - **Category**: Optional category for organization.
   - **Description**: Optional description.
4. Configure **Authentication** (see below).
5. Optionally expand **Advanced Settings** to set custom timeout, max retries, and SSL verification.
6. Click **Test Connection** to verify.
7. Click **Save**.

#### Authentication Types

| Type | Fields | Header Generated |
|---|---|---|
| **None** | (none) | (none) |
| **Bearer Token** | Token | `Authorization: Bearer <token>` |
| **API Key** | Header Name, Key Value | `<Header-Name>: <key>` (default header: `X-API-Key`) |
| **Basic Auth** | Username, Password | `Authorization: Basic <base64(user:pass)>` |
| **Custom Headers** | Arbitrary key-value pairs | Each pair becomes a separate HTTP header |

All credentials are stored encrypted in the `MCPServerCredentials` table using SQL Server `ENCRYPTBYPASSPHRASE`.

### 5.4 Testing Server Connections

- **Test a single server**: Click the plug icon in the server row's Actions column. The gateway connects, runs the MCP handshake, lists tools, and disconnects. Results are stored in the database (`last_tested_date`, `last_test_status`, `tool_count`).
- **Test all servers**: Click the **Test All** button in the toolbar. Tests run sequentially with a 500ms delay between servers.

### 5.5 Browsing Available Tools

Click the wrench icon in the server row's Actions column. The UI connects the server (if not already connected), fetches the tool list, and displays each tool with its:

- **Name**: The MCP tool identifier.
- **Description**: What the tool does.
- **Parameters**: Expandable JSON Schema showing input parameters.

### 5.6 Assigning Servers to Agents

1. Click the users icon in the server row's Actions column.
2. The modal shows all available agents with checkboxes.
3. Check the agents that should have access to this server's tools.
4. Click **Save**.

Agent assignments are stored in the `AgentMCPServers` table. When an agent initializes, `get_mcp_tools_for_agent()` queries this table to determine which servers to connect and which tools to load.

### 5.7 Using the Server Directory

Click **Server Directory** in the toolbar. The directory shows pre-configured templates for known MCP server providers (Salesforce, SAP, Azure, GitHub, Slack). Clicking an entry pre-fills the Add Server form with the template values.

### 5.8 Filtering and Searching

Use the **Type** dropdown and **Search** field above the server table to filter the list:

- **Type filter**: Show only Remote or Local servers.
- **Search**: Free-text search across all visible columns.

---

## 6. How It Works

### End-to-End Data Flow

The following diagram shows the complete path from a user message to an MCP tool execution and back:

```
User sends message to AI Agent
  |
  v
Agent receives message, LLM decides to use an MCP tool
  |  (e.g., the LLM picks "filesystem_read_file")
  v
LangChain StructuredTool callable executes
  |  (closure created by MCPToolConverter)
  v
MCPGatewayClient.call_tool(server_id, tool_name, arguments)
  |  HTTP POST to http://localhost:5071/api/mcp/servers/{id}/tools/call
  v
MCP Gateway (FastAPI) receives REST request
  |
  v
MCPServerManager routes request to the correct transport
  |
  +--[Local Server]----> StdioTransport
  |                      Writes JSON-RPC 2.0 message to subprocess stdin
  |                      Reads response from subprocess stdout
  |
  +--[Remote Server]---> SSETransport
                         POSTs JSON-RPC 2.0 message to HTTP endpoint
                         Reads response via SSE stream (or HTTP body)
  |
  v
MCP Server executes the tool and returns a JSON-RPC 2.0 response
  |
  v (response flows back through all layers)
  |
Gateway extracts text content from MCP response
  |  Combines content items of type "text"
  |  Handles "image" content with placeholder text
  v
MCPGatewayClient receives HTTP response
  |
  v
StructuredTool returns result string to LangChain
  |
  v
LLM generates a response incorporating the tool result
  |
  v
Agent returns response to user
```

### MCP Protocol Handshake

When a server connection is established, the gateway performs the standard MCP initialization:

1. **Client sends `initialize` request** -- includes protocol version (`2024-11-05`), client info (`AIHub 1.0.0`), and empty capabilities.
2. **Server responds** with its capabilities, server info, and supported protocol version.
3. **Client sends `notifications/initialized`** notification -- signals the handshake is complete.
4. **Client requests `tools/list`** -- the server responds with all available tool definitions.

### Tool Conversion

The `MCPToolConverter` converts each MCP tool definition into a LangChain `StructuredTool`:

1. **Tool name**: Prefixed with the sanitized server name to avoid collisions (e.g., `filesystem_read_file`).
2. **Input schema**: The MCP `inputSchema` (JSON Schema) is converted to a dynamic Pydantic model using `create_model()`. JSON Schema types map to Python types (`string` -> `str`, `number` -> `float`, `integer` -> `int`, `boolean` -> `bool`, `array` -> `list`, `object` -> `dict`).
3. **Callable**: A closure that calls `MCPGatewayClient.call_tool()` with the server ID and original tool name.

### Tool Caching

The gateway caches the tool list for each connected server for `MCP_TOOL_CACHE_TTL` seconds (default 300). Subsequent `list_tools` calls return cached results until the TTL expires, reducing overhead during frequent agent initializations.

---

## 7. Adding Custom MCP Servers

### 7.1 Local Stdio Servers

Local MCP servers run as child processes of the gateway. Communication happens over stdin/stdout using newline-delimited JSON-RPC 2.0 messages.

#### Configuration Fields

| Field | Description | Example |
|---|---|---|
| `command` | The executable to launch | `python`, `npx`, `node`, `uvx` |
| `args` | List of command-line arguments | `["-m", "my_server"]`, `["@modelcontextprotocol/server-filesystem", "C:\\data"]` |
| `env_vars` | Environment variables passed to the subprocess | `{"DATA_DIR": "C:\\data", "API_KEY": "sk-..."}` |

#### Example: Filesystem Server

Using the official MCP filesystem server via npx:

| Setting | Value |
|---|---|
| **Server Name** | File System |
| **Command** | `npx` |
| **Arguments** | `-y` (line 1), `@modelcontextprotocol/server-filesystem` (line 2), `C:\allowed\directory` (line 3) |

#### Example: Custom Python Server

| Setting | Value |
|---|---|
| **Server Name** | My Custom Tools |
| **Command** | `python` |
| **Arguments** | `-m` (line 1), `my_mcp_server` (line 2) |
| **Env Vars** | `DATABASE_URL` = `mssql+pyodbc://...` |

#### Example: Using the Sample Test Server

The integration includes a sample MCP server for testing at `builder_mcp/gateway/tests/sample_mcp_server.py`. It provides three tools: `echo`, `add_numbers`, and `get_current_time`.

| Setting | Value |
|---|---|
| **Server Name** | Sample Test Server |
| **Command** | `python` |
| **Arguments** | `tests/sample_mcp_server.py` |

Test via command line:

```batch
curl -X POST http://localhost:5071/api/mcp/test ^
  -H "Content-Type: application/json" ^
  -d "{\"type\":\"local\",\"command\":\"python\",\"args\":[\"tests/sample_mcp_server.py\"]}"
```

#### Windows Command Notes

On Windows, the gateway automatically handles `.cmd` variants for common tools:
- `npx` is tried as `npx.cmd` first
- `node` is tried as `node.cmd` first
- If the `.cmd` variant fails, the original command is attempted as a fallback

### 7.2 Remote SSE/HTTP Servers

Remote MCP servers are hosted externally and communicate over HTTP. The gateway supports two transport modes:

1. **SSE mode (standard)**: The gateway opens a `GET` request to `{url}/sse`. The server sends an `endpoint` event containing a POST URL. All subsequent messages are sent as `POST` requests and responses arrive over the SSE stream.

2. **HTTP fallback**: If the SSE connection fails, the gateway falls back to plain HTTP POST/response on the base URL.

#### Configuration Fields

| Field | Description | Example |
|---|---|---|
| `url` | Base URL of the MCP server | `https://api.example.com/mcp/v1` |
| `auth_headers` | HTTP headers for authentication | `{"Authorization": "Bearer sk-..."}` |

#### Example: GitHub MCP Server (Remote)

| Setting | Value |
|---|---|
| **Server Name** | GitHub |
| **Endpoint URL** | `https://api.github.com/mcp/v1` |
| **Auth Type** | Bearer Token |
| **Token** | `ghp_your_personal_access_token` |
| **Category** | Development |

#### Example: Custom API with API Key

| Setting | Value |
|---|---|
| **Server Name** | Internal Analytics |
| **Endpoint URL** | `https://analytics.internal.company.com/mcp` |
| **Auth Type** | API Key |
| **Header Name** | `X-API-Key` |
| **API Key** | `your-api-key-value` |

---

## 8. Troubleshooting

### 8.1 Gateway Not Starting

| Symptom | Cause | Solution |
|---|---|---|
| `ModuleNotFoundError: No module named 'fastapi'` | Virtual environment not set up or not activated | Run `setup.bat` from the `builder_mcp\gateway\` directory |
| `Address already in use` / `[WinError 10048]` | Port 5071 is in use by another process | Change the port via `MCP_GATEWAY_PORT` environment variable, or stop the conflicting process |
| `ERROR: Python is not installed or not in PATH` | Python not found | Install Python 3.8+ and ensure it is on the system PATH |
| Gateway starts but exits immediately | Check the log file at `MCP_GATEWAY_LOG` path | Review `logs/mcp_gateway_log.txt` for startup errors |

### 8.2 Gateway Health Check Fails

| Symptom | Cause | Solution |
|---|---|---|
| UI shows "MCP Gateway is not reachable" | Gateway service is not running | Start the gateway with `start_gateway.bat` or check the NSSM service status |
| `Could not connect to MCP Gateway at ...` | Wrong port or host configuration | Verify `HOST_PORT` and `MCP_GATEWAY_PORT` environment variables. The client computes the URL as `http://{HOST}:{HOST_PORT + 70}` |
| Connection refused from agent code | Firewall blocking the port | Ensure port 5071 (or your configured port) allows local connections |

### 8.3 Server Connection Failures

| Error Message | Cause | Solution |
|---|---|---|
| `Command not found: npx` | The command is not installed or not on PATH | Install Node.js (for npx) or ensure the command is available in the gateway process PATH |
| `MCP initialization timed out after 30s` | The server subprocess started but did not complete the MCP handshake in time | Increase `MCP_CONNECT_TIMEOUT`. Check that the server actually implements MCP stdio protocol on stdin/stdout (not stderr). |
| `MCP initialization failed: ...` | The server returned an error during the handshake | Check the server implementation. Ensure it responds to the `initialize` JSON-RPC method correctly. |
| `httpx is required for remote SSE transport` | The `httpx` package is not installed in the gateway venv | Run `pip install httpx` in the gateway virtual environment |
| `Timed out waiting for SSE endpoint event` | Remote server did not send the SSE `endpoint` event | Verify the remote URL is correct and the server supports the MCP SSE transport. Check that the `/sse` endpoint is accessible. |
| `Server {id} is not connected` | Attempting to list tools or call a tool on a disconnected server | Connect the server first. The UI and agent integration handle this automatically, but direct API calls need an explicit connect step. |

### 8.4 Tool Execution Errors

| Error Message | Cause | Solution |
|---|---|---|
| `Tool call timed out after 60s` | The tool execution exceeded the timeout | Increase `MCP_TOOL_CALL_TIMEOUT` for long-running tools. Some operations (large file reads, complex queries) may need more time. |
| `Error executing tool_name: ...` | The MCP server returned an error for the tool call | Review the error details. Check the MCP server logs for the underlying cause. |
| `Error calling MCP tool ...: Could not connect to MCP Gateway` | The gateway became unreachable during execution | Verify the gateway is still running. Check for network issues or service crashes. |

### 8.5 Agent Integration Issues

| Symptom | Cause | Solution |
|---|---|---|
| No MCP tools appear in the agent | Gateway not running, no servers assigned, or servers disabled | Check: (1) gateway health, (2) server assignments in the UI, (3) both the server and assignment have `enabled = 1` |
| `MCP Gateway is not available -- skipping MCP tools` (in logs) | The agent's health check to the gateway failed | Start the gateway service. This message is normal when the gateway is intentionally not deployed. |
| Tool names conflict with existing agent tools | Two MCP servers expose tools with the same name | Tool names are prefixed with the sanitized server name (e.g., `servername_toolname`), so conflicts should be rare. If they occur, rename the server. |
| `Failed to create pydantic model` warnings | Unusual JSON Schema in the tool definition | The converter falls back to a generic string input model. The tool will still work but may not provide ideal parameter descriptions to the LLM. |

### 8.6 Checking Logs

**Gateway logs**: Located at the path specified by `MCP_GATEWAY_LOG` (default: `./logs/mcp_gateway_log.txt` relative to the gateway directory). Logs include connection events, tool calls, errors, and protocol-level debug messages.

**Main application logs**: MCP-related messages are logged under the `builder_mcp.*` loggers at various levels:
- `DEBUG`: Gateway availability checks, module import status
- `INFO`: Successful tool loads, server connections
- `WARNING`: Non-fatal errors (server connection failures, individual tool conversion failures)

---

## 9. API Reference

All endpoints are served by the Flask Blueprint at `/api/mcp`. Actions that read/write server configurations hit the database directly. Actions that test connections or execute tools proxy to the MCP Gateway service.

### Server CRUD (Database)

#### List All Servers

```
GET /api/mcp/servers
```

Returns an array of server objects including `server_id`, `server_name`, `server_type`, `server_url`, `auth_type`, `connection_config`, `description`, `category`, `enabled`, `last_test_status`, `tool_count`, and `agent_count`.

#### Create Server

```
POST /api/mcp/servers
Content-Type: application/json
```

**Request body (remote server):**

```json
{
  "server_type": "remote",
  "server_name": "My API Server",
  "server_url": "https://api.example.com/mcp/v1",
  "auth_type": "bearer",
  "auth_config": {
    "token": "your-bearer-token"
  },
  "description": "Example remote server",
  "category": "Custom",
  "request_timeout": 30,
  "max_retries": 3,
  "verify_ssl": true
}
```

**Request body (local server):**

```json
{
  "server_type": "local",
  "server_name": "File System",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:\\data"],
  "env_vars": {},
  "description": "Local filesystem access"
}
```

**Response:**

```json
{
  "status": "success",
  "server_id": 42,
  "message": "MCP server created successfully"
}
```

#### Get Server Details

```
GET /api/mcp/servers/<server_id>
```

Returns the full server configuration. For local servers, includes parsed `command`, `args`, and `env_vars`. For remote servers, includes `credential_keys` (names only, not values).

#### Update Server

```
PUT /api/mcp/servers/<server_id>
Content-Type: application/json
```

Same body format as Create. For remote servers, passing `auth_config` replaces all existing credentials.

#### Delete Server

```
DELETE /api/mcp/servers/<server_id>
```

Deletes the server, its credentials, and all agent assignments. Also sends a best-effort disconnect to the gateway.

### Server Actions (via Gateway)

#### Test Configuration (Before Saving)

```
POST /api/mcp/test
Content-Type: application/json
```

**Request body:**

```json
{
  "type": "local",
  "command": "python",
  "args": ["tests/sample_mcp_server.py"],
  "timeout": 30
}
```

**Response (success):**

```json
{
  "status": "success",
  "tool_count": 3,
  "tools": [
    {
      "name": "echo",
      "description": "Echo back the provided message",
      "inputSchema": { "type": "object", "properties": { "message": { "type": "string" } }, "required": ["message"] }
    }
  ]
}
```

#### Test Saved Server

```
POST /api/mcp/servers/<server_id>/test
```

Reads config from the database, proxies to the gateway, and updates `last_tested_date`, `last_test_status`, and `tool_count` in the database.

#### List Server Tools

```
GET /api/mcp/servers/<server_id>/tools
```

Connects the server if not already connected, then returns the tool list.

**Response:**

```json
{
  "server_id": 42,
  "tools": [ ... ],
  "tool_count": 5
}
```

#### Call a Tool

```
POST /api/mcp/servers/<server_id>/tools/call
Content-Type: application/json
```

**Request body:**

```json
{
  "tool_name": "echo",
  "arguments": {
    "message": "Hello from AI Hub"
  }
}
```

**Response:**

```json
{
  "status": "success",
  "result": "Echo: Hello from AI Hub"
}
```

### Agent Assignments (Database)

#### Get Server Agents

```
GET /api/mcp/servers/<server_id>/agents
```

Returns an array of `{ agent_id, enabled, assigned_date, assigned_by }`.

#### Update Server Agents

```
POST /api/mcp/servers/<server_id>/agents
Content-Type: application/json
```

**Request body:**

```json
{
  "agent_ids": [1, 5, 12]
}
```

Replaces all existing assignments for this server with the provided agent IDs.

### Other Endpoints

#### Server Directory

```
GET /api/mcp/directory
```

Returns a static list of known MCP server templates with `name`, `category`, `url_template`, `auth_type`, `description`, and `provider`.

#### Gateway Health

```
GET /api/mcp/gateway/health
```

**Response:**

```json
{
  "status": "ok",
  "gateway_url": "http://localhost:5071"
}
```

### Gateway Direct Endpoints

These endpoints are on the gateway service itself (port 5071), not the Flask blueprint. They are used internally by the `MCPGatewayClient`.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Gateway health check |
| `POST` | `/api/mcp/connect` | Connect to an MCP server |
| `POST` | `/api/mcp/disconnect` | Disconnect from a server |
| `GET` | `/api/mcp/servers/{server_id}/status` | Get connection status |
| `GET` | `/api/mcp/servers/{server_id}/tools` | List tools from a connected server |
| `POST` | `/api/mcp/servers/{server_id}/tools/call` | Execute a tool |
| `POST` | `/api/mcp/test` | Test a server configuration (connect, list, disconnect) |
| `GET` | `/api/mcp/connections` | List all active gateway connections |

---

## 10. Security Notes

### Credential Encryption

Authentication credentials (bearer tokens, API keys, passwords, custom headers) are stored encrypted in the `MCPServerCredentials` table using SQL Server's `ENCRYPTBYPASSPHRASE` function. Decryption is performed at runtime using `DECRYPTBYPASSPHRASE` when the agent or UI needs to connect to a server.

The encryption passphrase is sourced from:
1. The `MCP_ENCRYPTION_KEY` environment variable (if set), or
2. The `ENCRYPTION_KEY` constant in `encrypt.py` (fallback)

Credential values are never returned to the UI. The `GET /api/mcp/servers/<id>` endpoint returns only `credential_keys` (the names of stored credentials, such as `token` or `header`), never the actual values.

### Tenant Isolation

All database queries execute `EXEC tenant.sp_setTenantContext ?` with the current `API_KEY` before any data access. This enables row-level security (RLS) in SQL Server, ensuring that each tenant can only see and manage their own MCP server configurations, credentials, and agent assignments.

### Authentication Types

| Type | Storage | Transmission |
|---|---|---|
| **None** | No credentials stored | No auth headers sent |
| **Bearer** | `token` encrypted in `MCPServerCredentials` | `Authorization: Bearer <token>` header sent to remote server |
| **API Key** | `header` (name) and `key` (value) encrypted | Custom header sent (e.g., `X-API-Key: <value>`) |
| **Basic** | `username` and `password` encrypted | `Authorization: Basic <base64>` header sent |
| **Custom** | Arbitrary key-value pairs encrypted | Each pair sent as an HTTP header |

### Gateway Security Considerations

- The gateway binds to `0.0.0.0` and accepts connections from any origin (CORS `allow_origins=["*"]`). In production, restrict this using a reverse proxy or firewall rules to allow only the main application to reach the gateway.
- The gateway does not perform its own authentication. It relies on being accessible only from the local network or the same host.
- Local MCP server subprocesses inherit the gateway process environment. Avoid placing highly sensitive variables in the gateway's environment unless they are needed by MCP servers.
- The gateway logs protocol-level details at `DEBUG` level, which may include tool arguments and results. Set `LOG_LEVEL=INFO` or higher in production to reduce exposure.

### Local Server Isolation

Local MCP servers run as child processes of the gateway. They:
- Inherit the gateway's environment plus any server-specific `env_vars`
- Have filesystem access equivalent to the gateway process user
- Can be terminated by the gateway at any time (on disconnect or shutdown)

Restrict the gateway service account's filesystem and network access to limit the blast radius of any misbehaving MCP server.

### Network Security

- All communication between the main application and the gateway happens over HTTP on the local network. Use HTTPS and a reverse proxy in production if the gateway is on a different host.
- Remote MCP server connections support SSL verification (configurable per server via the `verify_ssl` setting). Do not disable SSL verification in production.
- The `MCPGatewayClient` forces `Connection: close` headers and creates fresh HTTP connections for each request to avoid connection pooling issues.
