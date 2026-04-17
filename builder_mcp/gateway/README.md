# MCP Gateway Service

Standalone FastAPI microservice that manages MCP server connections and translates REST requests into MCP protocol (JSON-RPC 2.0) messages.

## Quick Start

```batch
setup.bat          # Create venv and install deps
start_gateway.bat  # Start the service on port 5071
```

## REST API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| POST | `/api/mcp/connect` | Connect to MCP server |
| POST | `/api/mcp/disconnect` | Disconnect |
| GET | `/api/mcp/servers/{id}/status` | Connection status |
| GET | `/api/mcp/servers/{id}/tools` | List tools (cached) |
| POST | `/api/mcp/servers/{id}/tools/call` | Execute a tool |
| POST | `/api/mcp/test` | Test config (ephemeral) |
| GET | `/api/mcp/connections` | All active connections |

## NSSM Service Registration

```batch
nssm install AIHub_MCP_Gateway "C:\path\to\venv\Scripts\python.exe" "C:\path\to\app_mcp_gateway.py"
nssm set AIHub_MCP_Gateway AppDirectory "C:\path\to\gateway"
nssm start AIHub_MCP_Gateway
```
