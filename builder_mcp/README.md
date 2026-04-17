# AI Hub MCP Integration

Complete MCP (Model Context Protocol) integration for AI Hub, enabling AI agents to use external tools from MCP servers.

## Architecture

```
builder_mcp/
├── gateway/                      # STANDALONE SERVICE (own Python env, port 5071)
│   ├── app_mcp_gateway.py        # FastAPI entry point
│   ├── server_manager.py         # Server lifecycle management
│   ├── stdio_transport.py        # Local subprocess MCP servers
│   ├── sse_transport.py          # Remote HTTP/SSE MCP servers
│   ├── protocol.py               # JSON-RPC 2.0 message handling
│   ├── config.py                 # Gateway configuration
│   ├── requirements.txt          # Gateway-specific dependencies
│   ├── setup.bat                 # Windows setup script
│   ├── start_gateway.bat         # Start script
│   └── tests/
│       ├── sample_mcp_server.py  # Test MCP server
│       └── test_gateway.py       # Gateway tests
│
├── client/                       # MAIN APP — HTTP client
│   ├── mcp_gateway_client.py     # REST client for gateway
│   └── tool_converter.py         # MCP tools → LangChain tools
│
├── agent_integration/            # MAIN APP — Agent wiring
│   └── mcp_agent_tools.py        # Tool loader + system prompt
│
├── routes/                       # MAIN APP — Flask Blueprint
│   └── mcp_routes.py             # /api/mcp/* endpoints
│
├── templates/                    # MAIN APP — UI
│   └── mcp_servers.html          # Server management page
│
└── tests/
    └── test_integration.py       # End-to-end tests
```

## Setup

### 1. Gateway Service

```batch
cd builder_mcp\gateway
setup.bat
```

This creates a virtual environment and installs dependencies (FastAPI, uvicorn, httpx).

**Start the gateway:**
```batch
start_gateway.bat
```

**Or register as a Windows service:**
```batch
nssm install AIHub_MCP_Gateway "C:\path\to\gateway\venv\Scripts\python.exe" "C:\path\to\gateway\app_mcp_gateway.py"
nssm set AIHub_MCP_Gateway AppDirectory "C:\path\to\gateway"
nssm set AIHub_MCP_Gateway AppEnvironmentExtra "MCP_GATEWAY_PORT=5071" "MCP_GATEWAY_LOG=C:\path\to\logs\mcp_gateway_log.txt"
nssm start AIHub_MCP_Gateway
```

### 2. Main Application Integration

#### Register the Blueprint (app.py)

Add to your `app.py`:

```python
# MCP Blueprint
from builder_mcp.routes.mcp_routes import mcp_bp
app.register_blueprint(mcp_bp)

# MCP Server Management Page
@app.route('/mcp_servers')
@developer_required()
def mcp_servers_page():
    return render_template('mcp_servers.html',
                          template_folder='builder_mcp/templates')
```

Note: Since the template is in `builder_mcp/templates/`, you may need to add this folder to Flask's template search path:
```python
app = Flask(__name__, template_folder='templates')
app.jinja_loader = jinja2.ChoiceLoader([
    app.jinja_loader,
    jinja2.FileSystemLoader('builder_mcp/templates')
])
```

Or copy `mcp_servers.html` to the main `templates/` directory.

#### Wire into GeneralAgent.py

Add after the `# EMAIL INBOX TOOLS` section (around line 2149), before `# Bind the tools`:

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

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST_PORT` | `5001` | Base port (gateway = base + 70) |
| `MCP_GATEWAY_PORT` | `5071` | Gateway service port (override) |
| `MCP_GATEWAY_LOG` | `./logs/mcp_gateway_log.txt` | Gateway log file |
| `MCP_CONNECT_TIMEOUT` | `30` | Connection timeout (seconds) |
| `MCP_TOOL_CALL_TIMEOUT` | `60` | Tool call timeout (seconds) |
| `MCP_TOOL_CACHE_TTL` | `300` | Tool list cache TTL (seconds) |

## Database Tables (Pre-existing)

- `MCPServers` — Server configurations (local and remote)
- `MCPServerCredentials` — Encrypted auth credentials
- `AgentMCPServers` — Agent-to-server assignments
- `UserMCPServers` — User-level server configs

## Data Flow

```
Agent receives user message
  → LLM selects MCP tool (e.g., filesystem_read_file)
    → LangChain StructuredTool callable executes
      → MCPGatewayClient.call_tool() sends HTTP POST
        → MCP Gateway receives REST request
          → ServerManager routes to connected server
            → StdioTransport sends JSON-RPC via stdin
              → MCP Server executes tool, returns result
            ← Result flows back through all layers
          ← Gateway returns HTTP response
        ← Client extracts result text
      ← Tool returns result string to LangChain
    ← LLM generates response using tool result
  ← Agent returns response to user
```

## API Endpoints (Blueprint: /api/mcp)

### Server CRUD (Database)
- `GET /api/mcp/servers` — List all servers
- `POST /api/mcp/servers` — Create server
- `GET /api/mcp/servers/<id>` — Get server details
- `PUT /api/mcp/servers/<id>` — Update server
- `DELETE /api/mcp/servers/<id>` — Delete server

### Server Actions (via Gateway)
- `POST /api/mcp/servers/<id>/test` — Test connection
- `GET /api/mcp/servers/<id>/tools` — List tools
- `POST /api/mcp/servers/<id>/tools/call` — Execute a tool

### Agent Assignments (Database)
- `GET /api/mcp/servers/<id>/agents` — Get assigned agents
- `POST /api/mcp/servers/<id>/agents` — Update assignments

### Other
- `GET /api/mcp/directory` — Known server templates
- `GET /api/mcp/gateway/health` — Gateway health check

## Testing

### Gateway tests (run in gateway venv):
```batch
cd builder_mcp\gateway
venv\Scripts\activate
pip install pytest pytest-asyncio
python -m pytest tests/ -v
```

### Integration tests (run in main app env):
```batch
python -m pytest builder_mcp/tests/test_integration.py -v
```

### Manual test with sample server:
```batch
REM Start gateway
cd builder_mcp\gateway
start_gateway.bat

REM In another terminal, test the sample server
curl -X POST http://localhost:5071/api/mcp/test -H "Content-Type: application/json" -d "{\"type\":\"local\",\"command\":\"python\",\"args\":[\"tests/sample_mcp_server.py\"]}"
```
