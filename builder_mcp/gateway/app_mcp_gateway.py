"""
MCP Gateway Service
Standalone FastAPI application for handling MCP server communication.
Designed to run as a separate process with its own Python environment.

Exposes REST endpoints that the main application calls via HTTP.
Translates REST requests into MCP protocol messages (JSON-RPC 2.0).
"""
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Add parent directories to path for CommonUtils access
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, parent_dir)
gateway_dir = os.path.dirname(__file__)
sys.path.insert(0, gateway_dir)

# Load API_KEY from Windows Registry and credentials from encrypted store
try:
    from secure_config import load_secure_config
    load_secure_config()
except ImportError:
    pass

import logging
from logging.handlers import WatchedFileHandler
from datetime import datetime
from typing import Dict, List, Optional, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio

# Try to import log rotation from CommonUtils
try:
    from CommonUtils import rotate_logs_on_startup
except ImportError:
    def rotate_logs_on_startup(path):
        pass

from mcp_gateway_config import MCP_GATEWAY_PORT, MCP_GATEWAY_LOG, LOG_LEVEL

# ============================================================================
# Logging Setup
# ============================================================================

log_dir = os.path.dirname(MCP_GATEWAY_LOG)
if log_dir:
    os.makedirs(log_dir, exist_ok=True)

rotate_logs_on_startup(MCP_GATEWAY_LOG)

logger = logging.getLogger("MCPGateway")
log_level = getattr(logging, LOG_LEVEL, logging.DEBUG)
logger.setLevel(log_level)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler = WatchedFileHandler(filename=MCP_GATEWAY_LOG, encoding='utf-8')
handler.setFormatter(formatter)
logger.addHandler(handler)

# Also log to console — reconfigure stdout for UTF-8 on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='backslashreplace')
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# ============================================================================
# FastAPI App
# ============================================================================

app = FastAPI(title="MCP Gateway", version="1.0.0", description="MCP Server Communication Gateway")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================================
# Request/Response Models
# ============================================================================

class ConnectRequest(BaseModel):
    server_id: str
    type: str = "local"                          # 'local' or 'remote'
    command: Optional[str] = None                 # For local servers
    args: Optional[List[str]] = None              # For local servers
    env_vars: Optional[Dict[str, str]] = None     # For local servers
    url: Optional[str] = None                     # For remote servers
    auth_headers: Optional[Dict[str, str]] = None # For remote servers
    timeout: Optional[int] = 30


class DisconnectRequest(BaseModel):
    server_id: str


class TestRequest(BaseModel):
    type: str = "local"
    command: Optional[str] = None
    args: Optional[List[str]] = None
    env_vars: Optional[Dict[str, str]] = None
    url: Optional[str] = None
    auth_headers: Optional[Dict[str, str]] = None
    timeout: Optional[int] = 30


class ToolCallRequest(BaseModel):
    tool_name: str
    arguments: Dict[str, Any] = {}


# ============================================================================
# Server Manager Instance
# ============================================================================

from server_manager import MCPServerManager

mcp_manager = MCPServerManager()


# ============================================================================
# Endpoints
# ============================================================================

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    connections = mcp_manager.get_all_connections()
    active_count = sum(1 for c in connections.values() if c.get('status') == 'connected')
    return {
        "status": "ok",
        "message": "MCP Gateway is operational",
        "service": "mcp-gateway",
        "timestamp": datetime.utcnow().isoformat(),
        "active_connections": active_count,
        "total_connections": len(connections)
    }


@app.post("/api/mcp/connect")
async def connect_server(req: ConnectRequest):
    """Connect to an MCP server"""
    config = {
        "type": req.type,
        "timeout": req.timeout or 30
    }
    if req.type == "local":
        config["command"] = req.command
        config["args"] = req.args or []
        config["env_vars"] = req.env_vars or {}
    elif req.type == "remote":
        config["url"] = req.url
        config["auth_headers"] = req.auth_headers or {}

    result = await mcp_manager.connect(req.server_id, config)

    if result.get("status") == "error":
        logger.error(f"Failed to connect server {req.server_id}: {result.get('error')}")

    return result


@app.post("/api/mcp/disconnect")
async def disconnect_server(req: DisconnectRequest):
    """Disconnect from a server"""
    result = await mcp_manager.disconnect(req.server_id)
    return result


@app.get("/api/mcp/servers/{server_id}/status")
async def get_server_status(server_id: str):
    """Get connection status for a server"""
    return await mcp_manager.get_status(server_id)


@app.get("/api/mcp/servers/{server_id}/tools")
async def list_server_tools(server_id: str):
    """List available tools from a connected server"""
    try:
        tools = await mcp_manager.list_tools(server_id)
        return {"server_id": server_id, "tools": tools, "tool_count": len(tools)}
    except ConnectionError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error listing tools for server {server_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/mcp/servers/{server_id}/tools/call")
async def call_server_tool(server_id: str, req: ToolCallRequest):
    """Execute a tool on a connected server"""
    result = await mcp_manager.call_tool(
        server_id=server_id,
        tool_name=req.tool_name,
        arguments=req.arguments
    )
    return result


@app.post("/api/mcp/test")
async def test_server(req: TestRequest):
    """Test a server configuration (connect, list tools, disconnect)"""
    config = {
        "type": req.type,
        "timeout": req.timeout or 30
    }
    if req.type == "local":
        config["command"] = req.command
        config["args"] = req.args or []
        config["env_vars"] = req.env_vars or {}
    elif req.type == "remote":
        config["url"] = req.url
        config["auth_headers"] = req.auth_headers or {}

    result = await mcp_manager.test_connection(config)
    return result


@app.get("/api/mcp/connections")
async def list_connections():
    """List all active gateway connections"""
    return mcp_manager.get_all_connections()


# ============================================================================
# Lifecycle Events
# ============================================================================

@app.on_event("shutdown")
async def shutdown_event():
    """Clean up all connections on shutdown"""
    logger.info("Gateway shutting down — cleaning up connections")
    await mcp_manager.cleanup_all()


# ============================================================================
# Entry Point
# ============================================================================

if __name__ == '__main__':
    import uvicorn

    port = int(os.getenv('MCP_GATEWAY_PORT', str(MCP_GATEWAY_PORT)))

    # On Windows, ensure ProactorEventLoop is used for subprocess support
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    logger.info(f"Starting MCP Gateway on port {port}")
    print(f"Starting MCP Gateway on port {port}")
    print(f"Logs: {MCP_GATEWAY_LOG}")

    # Detect if running as a PyInstaller-frozen executable.
    # In frozen mode we MUST pass the app object directly (not a
    # "module:attr" string) so uvicorn doesn't try to re-import the
    # module — which would re-execute the .exe and cause an infinite
    # fork-bomb.  reload is already False but we enforce it explicitly.
    is_frozen = getattr(sys, 'frozen', False)

    if is_frozen:
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=port,
            log_level="info"
        )
    else:
        uvicorn.run(
            "app_mcp_gateway:app",
            host="0.0.0.0",
            port=port,
            reload=False,
            log_level="info"
        )
