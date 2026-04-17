"""
MCP Server Manager
Manages connections to multiple MCP servers concurrently.
Handles connection pooling, tool caching, and lifecycle management.
"""
import asyncio
import logging
import time
from typing import Dict, List, Optional, Any

from stdio_transport import StdioTransport
from sse_transport import SSETransport
from mcp_gateway_config import DEFAULT_CONNECT_TIMEOUT, DEFAULT_TOOL_CALL_TIMEOUT, TOOL_CACHE_TTL

logger = logging.getLogger("MCPGateway")


class MCPServerManager:
    """Manages connections to multiple MCP servers concurrently"""

    def __init__(self):
        self._connections: Dict[str, Dict[str, Any]] = {}
        self._tools_cache: Dict[str, Dict[str, Any]] = {}  # {server_id: {tools, timestamp}}
        self._locks: Dict[str, asyncio.Lock] = {}

    def _get_lock(self, server_id: str) -> asyncio.Lock:
        if server_id not in self._locks:
            self._locks[server_id] = asyncio.Lock()
        return self._locks[server_id]

    async def connect(self, server_id: str, config: dict) -> dict:
        """
        Connect to an MCP server.

        Args:
            server_id: Unique identifier for this server connection
            config: {
                type: 'local' | 'remote',
                command: str (for local),
                args: list (for local),
                env_vars: dict (for local),
                url: str (for remote),
                auth_headers: dict (for remote)
            }

        Returns:
            {status, tool_count, tools}
        """
        async with self._get_lock(server_id):
            try:
                # Disconnect existing connection if present
                if server_id in self._connections:
                    await self._cleanup_connection(server_id)

                server_type = config.get('type', 'local')
                transport = None

                if server_type == 'local':
                    transport = StdioTransport()
                    await transport.start(
                        command=config.get('command', ''),
                        args=config.get('args', []),
                        env_vars=config.get('env_vars', {}),
                        timeout=config.get('timeout', DEFAULT_CONNECT_TIMEOUT)
                    )
                elif server_type == 'remote':
                    transport = SSETransport()
                    await transport.connect(
                        url=config.get('url', ''),
                        auth_headers=config.get('auth_headers', {}),
                        timeout=config.get('timeout', DEFAULT_CONNECT_TIMEOUT)
                    )
                else:
                    return {"status": "error", "error": f"Unknown server type: {server_type}"}

                # Store connection
                self._connections[server_id] = {
                    'transport': transport,
                    'config': config,
                    'type': server_type,
                    'connected_at': time.time()
                }

                # Fetch and cache tools
                tools = await self._fetch_tools(server_id)

                logger.info(f"Server {server_id} connected: {len(tools)} tools available")
                return {
                    "status": "connected",
                    "tool_count": len(tools),
                    "tools": tools
                }

            except FileNotFoundError as e:
                logger.error(f"Command not found for server {server_id}: {e}")
                return {"status": "error", "error": f"Command not found: {config.get('command', '')}"}
            except TimeoutError as e:
                logger.error(f"Connection timeout for server {server_id}: {e}")
                return {"status": "error", "error": str(e)}
            except Exception as e:
                logger.error(f"Failed to connect server {server_id}: {e}", exc_info=True)
                await self._cleanup_connection(server_id)
                return {"status": "error", "error": str(e)}

    async def disconnect(self, server_id: str) -> dict:
        """Disconnect and cleanup a server connection"""
        async with self._get_lock(server_id):
            if server_id not in self._connections:
                return {"status": "not_connected"}

            await self._cleanup_connection(server_id)
            return {"status": "disconnected"}

    async def list_tools(self, server_id: str) -> list:
        """
        Get tools from a connected server. Uses cache if available and fresh.
        """
        if server_id not in self._connections:
            raise ConnectionError(f"Server {server_id} is not connected")

        # Check cache
        cache_entry = self._tools_cache.get(server_id)
        if cache_entry and (time.time() - cache_entry['timestamp']) < TOOL_CACHE_TTL:
            return cache_entry['tools']

        # Refresh from server
        async with self._get_lock(server_id):
            return await self._fetch_tools(server_id)

    async def call_tool(self, server_id: str, tool_name: str, arguments: dict,
                        timeout: int = DEFAULT_TOOL_CALL_TIMEOUT) -> dict:
        """
        Execute a tool on a connected server.

        Returns:
            {status: 'success', result: str} or {status: 'error', error: str}
        """
        if server_id not in self._connections:
            return {"status": "error", "error": f"Server {server_id} is not connected"}

        transport = self._connections[server_id]['transport']

        try:
            logger.debug(f"Calling tool '{tool_name}' on server {server_id}")

            response = await asyncio.wait_for(
                transport.send_request("tools/call", {
                    "name": tool_name,
                    "arguments": arguments
                }),
                timeout=timeout
            )

            if response.get("error"):
                error_msg = response.get("error_message", "Unknown error")
                logger.error(f"Tool call error on server {server_id}: {error_msg}")
                return {"status": "error", "error": error_msg}

            result = response.get("result", {})

            # Extract text content from MCP tool result
            content = result.get("content", [])
            if content:
                # Combine all text content
                text_parts = []
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "text":
                            text_parts.append(item.get("text", ""))
                        elif item.get("type") == "image":
                            text_parts.append(f"[Image: {item.get('mimeType', 'unknown')}]")
                        else:
                            text_parts.append(str(item))
                    else:
                        text_parts.append(str(item))
                result_text = "\n".join(text_parts)
            else:
                result_text = str(result)

            is_error = result.get("isError", False)
            if is_error:
                return {"status": "error", "error": result_text}

            return {"status": "success", "result": result_text}

        except asyncio.TimeoutError:
            logger.error(f"Tool call '{tool_name}' timed out on server {server_id}")
            return {"status": "error", "error": f"Tool call timed out after {timeout}s"}
        except Exception as e:
            logger.error(f"Error calling tool '{tool_name}' on server {server_id}: {e}")
            return {"status": "error", "error": str(e)}

    async def get_status(self, server_id: str) -> dict:
        """Get the connection status for a server"""
        if server_id not in self._connections:
            return {
                "status": "disconnected",
                "server_id": server_id
            }

        conn = self._connections[server_id]
        transport = conn['transport']

        return {
            "status": "connected" if transport.is_connected else "disconnected",
            "server_id": server_id,
            "type": conn['type'],
            "connected_at": conn.get('connected_at'),
            "tool_count": len(self._tools_cache.get(server_id, {}).get('tools', []))
        }

    def get_all_connections(self) -> dict:
        """Get status of all managed connections"""
        result = {}
        for server_id, conn in self._connections.items():
            transport = conn['transport']
            result[server_id] = {
                "status": "connected" if transport.is_connected else "disconnected",
                "type": conn['type'],
                "connected_at": conn.get('connected_at'),
                "tool_count": len(self._tools_cache.get(server_id, {}).get('tools', []))
            }
        return result

    async def test_connection(self, config: dict) -> dict:
        """
        Test a server configuration without persisting the connection.
        Connects, lists tools, then disconnects.
        """
        temp_id = f"_test_{int(time.time() * 1000)}"
        try:
            connect_result = await self.connect(temp_id, config)
            if connect_result.get("status") != "connected":
                return connect_result

            return {
                "status": "success",
                "tool_count": connect_result.get("tool_count", 0),
                "tools": connect_result.get("tools", [])
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}
        finally:
            # Always clean up the test connection
            try:
                await self.disconnect(temp_id)
            except Exception:
                pass

    async def _fetch_tools(self, server_id: str) -> list:
        """Fetch tools from a connected server and update cache"""
        if server_id not in self._connections:
            return []

        transport = self._connections[server_id]['transport']
        response = await transport.send_request("tools/list", {})

        if response.get("error"):
            logger.error(f"Failed to list tools from server {server_id}: {response.get('error_message')}")
            return []

        result = response.get("result", {})
        raw_tools = result.get("tools", [])

        tools = []
        for t in raw_tools:
            tools.append({
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "inputSchema": t.get("inputSchema", {})
            })

        # Update cache
        self._tools_cache[server_id] = {
            'tools': tools,
            'timestamp': time.time()
        }

        return tools

    async def _cleanup_connection(self, server_id: str):
        """Internal cleanup without lock"""
        if server_id in self._connections:
            try:
                transport = self._connections[server_id]['transport']
                await transport.close()
                logger.info(f"Server {server_id} disconnected and cleaned up")
            except Exception as e:
                logger.error(f"Error cleaning up server {server_id}: {e}")
            finally:
                del self._connections[server_id]
                self._tools_cache.pop(server_id, None)

    async def cleanup_all(self):
        """Clean up all connections (called on shutdown)"""
        logger.info("Cleaning up all MCP server connections...")
        server_ids = list(self._connections.keys())
        for server_id in server_ids:
            try:
                await self.disconnect(server_id)
            except Exception as e:
                logger.error(f"Error cleaning up server {server_id}: {e}")
