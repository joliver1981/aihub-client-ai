"""
MCP Gateway Microservice
FastAPI service that manages MCP server connections with proper async handling.
"""
import sys
import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict, List, Optional, Any
import uvicorn
import asyncio
from mcp import StdioServerParameters, ClientSession
from mcp.client.stdio import stdio_client
import logging
from logging.handlers import WatchedFileHandler
from contextlib import AsyncExitStack

# Get the parent directory (the project root)
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(parent_dir)

# Only import CommonUtils if it exists
try:
    from CommonUtils import rotate_logs_on_startup, get_log_path
    _mcp_gw_log_default = get_log_path('mcp_gateway_log.txt')
except ImportError:
    print("Warning: CommonUtils not found, skipping log rotation")
    _mcp_gw_log_default = os.path.join(parent_dir, 'logs', 'mcp_gateway_log.txt')
    def rotate_logs_on_startup(path):
        pass

rotate_logs_on_startup(os.getenv('MCP_GATEWAY_LOG', _mcp_gw_log_default))

# Configure logging
logger = logging.getLogger("MCPGatewayLog")
log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
log_level = getattr(logging, log_level_name, logging.DEBUG)
logger.setLevel(log_level)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Create logs directory if it doesn't exist
log_dir = os.path.dirname(os.getenv('MCP_GATEWAY_LOG', _mcp_gw_log_default))
os.makedirs(log_dir, exist_ok=True)

handler = WatchedFileHandler(filename=os.getenv('MCP_GATEWAY_LOG', _mcp_gw_log_default), encoding='utf-8')
handler.setFormatter(formatter)
logger.addHandler(handler)

app = FastAPI(title="MCP Gateway", version="1.0.0")


# ============================================================================
# Models
# ============================================================================

class ConnectionConfig(BaseModel):
    command: str
    args: List[str]
    env_vars: Optional[Dict[str, str]] = {}


class ToolCallRequest(BaseModel):
    server_id: int
    tool_name: str
    arguments: Dict[str, Any]


# ============================================================================
# Connection Manager - FIXED with proper async handling
# ============================================================================

class MCPConnectionManager:
    def __init__(self):
        self.connections: Dict[int, Dict] = {}  # Store connection components
        self.tools_cache: Dict[int, List[Dict]] = {}
        self._locks: Dict[int, asyncio.Lock] = {}  # Add locks for thread safety
        
    def _get_lock(self, server_id: int) -> asyncio.Lock:
        """Get or create a lock for a server"""
        if server_id not in self._locks:
            self._locks[server_id] = asyncio.Lock()
        return self._locks[server_id]
        
    async def test_connection(self, config: ConnectionConfig) -> Dict:
        """Test a server configuration - create new context each time"""
        try:
            logger.info(f"Testing connection with command: {config.command} {config.args}")
            
            # Create server parameters
            server_params = StdioServerParameters(
                command=config.command,
                args=config.args,
                env=config.env_vars or {}
            )
            
            # Use async context manager properly
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    # Initialize session
                    await session.initialize()
                    
                    # Get tools
                    tools_result = await session.list_tools()
                    
                    tools = [
                        {
                            "name": tool.name,
                            "description": tool.description or "",
                            "input_schema": getattr(tool, 'inputSchema', {})
                        }
                        for tool in tools_result.tools
                    ]
                    
                    logger.info(f"Test successful: found {len(tools)} tools")
                    return {"status": "success", "tool_count": len(tools), "tools": tools}
                    
        except FileNotFoundError as e:
            logger.error(f"Command not found: {config.command}")
            return {"status": "failed", "error": f"Command not found: {config.command}"}
        except Exception as e:
            logger.error(f"Test connection error: {str(e)}", exc_info=True)
            return {"status": "failed", "error": str(e)}
    
    async def connect_server(self, server_id: int, config: ConnectionConfig) -> Dict:
        """Establish persistent connection with proper async management"""
        async with self._get_lock(server_id):
            try:
                logger.info(f"Connecting to server {server_id}")
                
                # Disconnect existing connection if present
                if server_id in self.connections:
                    await self.disconnect_server(server_id)
                
                # Create server parameters
                server_params = StdioServerParameters(
                    command=config.command,
                    args=config.args,
                    env=config.env_vars or {}
                )
                
                # Create an exit stack to manage the connection lifecycle
                exit_stack = AsyncExitStack()
                
                # Enter the stdio_client context
                stdio_ctx = stdio_client(server_params)
                read, write = await exit_stack.enter_async_context(stdio_ctx)
                
                # Create and enter the session context
                session = ClientSession(read, write)
                await exit_stack.enter_async_context(session)
                
                # Initialize the session
                await session.initialize()
                
                # Store connection components
                self.connections[server_id] = {
                    'session': session,
                    'exit_stack': exit_stack,
                    'config': config
                }
                
                # Get and cache tools
                tools_result = await session.list_tools()
                tools = [
                    {
                        "name": tool.name,
                        "description": tool.description or "",
                        "input_schema": getattr(tool, 'inputSchema', {})
                    }
                    for tool in tools_result.tools
                ]
                self.tools_cache[server_id] = tools
                
                logger.info(f"Connected to server {server_id}: {len(tools)} tools available")
                return {"status": "connected", "tools": tools}
                
            except Exception as e:
                logger.error(f"Connect error for server {server_id}: {str(e)}", exc_info=True)
                # Clean up on error
                if server_id in self.connections:
                    await self._cleanup_connection(server_id)
                raise HTTPException(status_code=500, detail=str(e))
    
    async def get_tools(self, server_id: int) -> List[Dict]:
        """Get cached tools"""
        if server_id not in self.tools_cache:
            raise HTTPException(status_code=404, detail=f"Server {server_id} not connected")
        return self.tools_cache[server_id]
    
    async def call_tool(self, server_id: int, tool_name: str, arguments: Dict) -> str:
        """Execute a tool with proper error handling"""
        async with self._get_lock(server_id):
            try:
                if server_id not in self.connections:
                    raise HTTPException(status_code=404, detail=f"Server {server_id} not connected")
                
                session = self.connections[server_id]['session']
                
                logger.debug(f"Calling tool {tool_name} on server {server_id} with args: {arguments}")
                result = await session.call_tool(tool_name, arguments)
                
                # Extract text content
                if result.content and len(result.content) > 0:
                    response_text = result.content[0].text
                    logger.debug(f"Tool {tool_name} returned: {response_text[:100]}...")
                    return response_text
                return ""
                
            except Exception as e:
                logger.error(f"Error calling tool {tool_name} on server {server_id}: {str(e)}")
                raise
    
    async def disconnect_server(self, server_id: int):
        """Close connection with proper cleanup"""
        async with self._get_lock(server_id):
            await self._cleanup_connection(server_id)
    
    async def _cleanup_connection(self, server_id: int):
        """Internal cleanup without lock"""
        if server_id in self.connections:
            try:
                logger.info(f"Disconnecting server {server_id}")
                exit_stack = self.connections[server_id].get('exit_stack')
                if exit_stack:
                    await exit_stack.aclose()
            except Exception as e:
                logger.error(f"Error during cleanup of server {server_id}: {e}")
            finally:
                del self.connections[server_id]
                if server_id in self.tools_cache:
                    del self.tools_cache[server_id]
                logger.info(f"Server {server_id} disconnected")
    
    async def cleanup_all(self):
        """Clean up all connections on shutdown"""
        logger.info("Cleaning up all connections...")
        server_ids = list(self.connections.keys())
        for server_id in server_ids:
            try:
                await self.disconnect_server(server_id)
            except Exception as e:
                logger.error(f"Error cleaning up server {server_id}: {e}")


# Create global manager instance
mcp_manager = MCPConnectionManager()


# ============================================================================
# Endpoints
# ============================================================================

@app.get("/api/mcp/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "active_connections": len(mcp_manager.connections)
    }


@app.post("/api/mcp/servers/test")
async def test_server(config: ConnectionConfig):
    """Test a server configuration"""
    try:
        result = await mcp_manager.test_connection(config)
        return result
    except Exception as e:
        logger.error(f"Test server endpoint error: {e}")
        return {"status": "failed", "error": str(e)}


@app.post("/api/mcp/servers/{server_id}/connect")
async def connect_server(server_id: int, config: ConnectionConfig):
    """Connect to an MCP server"""
    try:
        result = await mcp_manager.connect_server(server_id, config)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Connect server endpoint error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/mcp/servers/{server_id}/tools")
async def get_server_tools(server_id: int):
    """Get available tools from a connected server"""
    try:
        tools = await mcp_manager.get_tools(server_id)
        return {"server_id": server_id, "tools": tools}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get tools endpoint error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/mcp/tools/call")
async def call_tool(request: ToolCallRequest):
    """Call a tool on an MCP server"""
    try:
        result = await mcp_manager.call_tool(
            server_id=request.server_id,
            tool_name=request.tool_name,
            arguments=request.arguments
        )
        return {"status": "success", "result": result}
    except HTTPException as e:
        return {"status": "error", "error": e.detail}
    except Exception as e:
        logger.error(f"Call tool endpoint error: {e}")
        return {"status": "error", "error": str(e)}


@app.delete("/api/mcp/servers/{server_id}/disconnect")
async def disconnect_server(server_id: int):
    """Disconnect from an MCP server"""
    try:
        await mcp_manager.disconnect_server(server_id)
        return {"status": "disconnected"}
    except Exception as e:
        logger.error(f"Disconnect server endpoint error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up all connections on shutdown"""
    await mcp_manager.cleanup_all()


if __name__ == "__main__":
    import sys
    
    # Get port from command line or use default
    port = 5061
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass
    
    print(f"Starting MCP Gateway on port {port}")
    print("Logs will be written to: " + os.getenv('MCP_GATEWAY_LOG', os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')), 'logs', 'mcp_gateway_log.txt')))
    
    uvicorn.run(
        "mcp_gateway_service:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="info"
    )