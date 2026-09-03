"""
MCP Server Manager
Manages connections to multiple MCP servers concurrently.
Handles connection pooling, tool caching, and lifecycle management.

Connection identity (2026-09-03, cross-user token bleed fix):
A connection is keyed by (server_id, user_id), not server_id alone. For
per-user (delegated OAuth) servers the caller's bearer token is baked into
the transport at connect time, so a single shared connection per server_id
meant user B's tool call could ride on user A's token whenever their agent
builds interleaved. Callers that pass user_id get their own connection;
callers that don't (admin routes, shared/service-account servers) keep the
legacy server_id-only key, so nothing changes for them.
"""
import asyncio
import logging
import time
from typing import Dict, List, Optional, Any

from stdio_transport import StdioTransport
from sse_transport import SSETransport
from streamable_http_transport import StreamableHTTPTransport
from mcp_gateway_config import DEFAULT_CONNECT_TIMEOUT, DEFAULT_TOOL_CALL_TIMEOUT, TOOL_CACHE_TTL

logger = logging.getLogger("MCPGateway")


def connection_key(server_id: str, user_id: Optional[str] = None) -> str:
    """The unit of connection identity: server alone, or server + user.

    None / '' / 0 / '0' mean "no user dimension" (the service principal, or a
    caller that predates per-user keying) and map to the legacy key.
    """
    sid = str(server_id)
    if user_id is None or str(user_id).strip() in ('', '0', 'None'):
        return sid
    return f"{sid}@u{str(user_id).strip()}"


def split_key(key: str) -> tuple:
    """Inverse of connection_key: (server_id, user_id_or_None)."""
    if '@u' in key:
        sid, uid = key.split('@u', 1)
        return sid, uid
    return key, None


class MCPServerManager:
    """Manages connections to multiple MCP servers concurrently"""

    def __init__(self):
        self._connections: Dict[str, Dict[str, Any]] = {}
        self._tools_cache: Dict[str, Dict[str, Any]] = {}  # {key: {tools, timestamp}}
        self._locks: Dict[str, asyncio.Lock] = {}

    def _get_lock(self, key: str) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def connect(self, server_id: str, config: dict,
                      user_id: Optional[str] = None) -> dict:
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
            user_id: optional — scopes the connection to one user (per-user
                bearer tokens). Different users never share a connection.

        Returns:
            {status, tool_count, tools}
        """
        key = connection_key(server_id, user_id)
        async with self._get_lock(key):
            try:
                # Disconnect existing connection if present
                if key in self._connections:
                    await self._cleanup_connection(key)

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
                elif server_type in ('remote', 'streamable-http', 'sse'):
                    transport = await self._connect_remote(server_type, config)
                else:
                    return {"status": "error", "error": f"Unknown server type: {server_type}"}

                # Store connection
                self._connections[key] = {
                    'transport': transport,
                    'config': config,
                    'type': server_type,
                    'server_id': str(server_id),
                    'user_id': (None if key == str(server_id) else str(user_id)),
                    'connected_at': time.time()
                }

                # Fetch and cache tools
                tools = await self._fetch_tools(key)

                logger.info(f"Server {key} connected: {len(tools)} tools available")
                return {
                    "status": "connected",
                    "tool_count": len(tools),
                    "tools": tools,
                    "connection_key": key,
                }

            except FileNotFoundError as e:
                logger.error(f"Command not found for server {key}: {e}")
                return {"status": "error", "error": f"Command not found: {config.get('command', '')}"}
            except TimeoutError as e:
                logger.error(f"Connection timeout for server {key}: {e}")
                return {"status": "error", "error": str(e)}
            except Exception as e:
                logger.error(f"Failed to connect server {key}: {e}", exc_info=True)
                await self._cleanup_connection(key)
                return {"status": "error", "error": str(e)}

    async def disconnect(self, server_id: str, user_id: Optional[str] = None) -> dict:
        """Disconnect and cleanup a server connection"""
        key = connection_key(server_id, user_id)
        async with self._get_lock(key):
            if key not in self._connections:
                return {"status": "not_connected"}

            await self._cleanup_connection(key)
            return {"status": "disconnected"}

    async def list_tools(self, server_id: str, user_id: Optional[str] = None) -> list:
        """
        Get tools from a connected server. Uses cache if available and fresh.
        """
        key = connection_key(server_id, user_id)
        if key not in self._connections:
            raise ConnectionError(f"Server {key} is not connected")

        # Check cache
        cache_entry = self._tools_cache.get(key)
        if cache_entry and (time.time() - cache_entry['timestamp']) < TOOL_CACHE_TTL:
            return cache_entry['tools']

        # Refresh from server
        async with self._get_lock(key):
            return await self._fetch_tools(key)

    async def call_tool(self, server_id: str, tool_name: str, arguments: dict,
                        timeout: int = DEFAULT_TOOL_CALL_TIMEOUT,
                        user_id: Optional[str] = None) -> dict:
        """
        Execute a tool on a connected server.

        Returns:
            {status: 'success', result: str} or {status: 'error', error: str}
        """
        key = connection_key(server_id, user_id)
        if key not in self._connections:
            return {"status": "error", "error": f"Server {key} is not connected"}

        transport = self._connections[key]['transport']

        try:
            logger.debug(f"Calling tool '{tool_name}' on server {key}")

            response = await asyncio.wait_for(
                transport.send_request("tools/call", {
                    "name": tool_name,
                    "arguments": arguments
                }),
                timeout=timeout
            )

            if response.get("error"):
                error_msg = response.get("error_message", "Unknown error")
                logger.error(f"Tool call error on server {key}: {error_msg}")
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
            logger.error(f"Tool call '{tool_name}' timed out on server {key}")
            return {"status": "error", "error": f"Tool call timed out after {timeout}s"}
        except Exception as e:
            logger.error(f"Error calling tool '{tool_name}' on server {key}: {e}")
            return {"status": "error", "error": str(e)}

    async def get_status(self, server_id: str, user_id: Optional[str] = None) -> dict:
        """Get the connection status for a server"""
        key = connection_key(server_id, user_id)
        if key not in self._connections:
            return {
                "status": "disconnected",
                "server_id": str(server_id),
                "connection_key": key,
            }

        conn = self._connections[key]
        transport = conn['transport']

        return {
            "status": "connected" if transport.is_connected else "disconnected",
            "server_id": str(server_id),
            "user_id": conn.get('user_id'),
            "connection_key": key,
            "type": conn['type'],
            "connected_at": conn.get('connected_at'),
            "tool_count": len(self._tools_cache.get(key, {}).get('tools', []))
        }

    def get_all_connections(self) -> dict:
        """Get status of all managed connections (keyed by connection key)"""
        result = {}
        for key, conn in self._connections.items():
            transport = conn['transport']
            result[key] = {
                "status": "connected" if transport.is_connected else "disconnected",
                "type": conn['type'],
                "server_id": conn.get('server_id', split_key(key)[0]),
                "user_id": conn.get('user_id'),
                "connected_at": conn.get('connected_at'),
                "tool_count": len(self._tools_cache.get(key, {}).get('tools', []))
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

    async def _connect_remote(self, server_type: str, config: dict):
        """Select a remote transport based on server_type or explicit `transport` hint.

        - server_type='streamable-http' or transport='streamable-http': use Streamable HTTP only
        - server_type='sse' or transport='sse': use SSE only
        - server_type='remote' (legacy default): auto — try Streamable HTTP first
          (modern spec, used by most hosted MCPs incl. Microsoft Learn), fall back to SSE.
        """
        url = config.get('url', '')
        auth_headers = config.get('auth_headers', {})
        verify_ssl = config.get('verify_ssl', True)
        timeout = config.get('timeout', DEFAULT_CONNECT_TIMEOUT)
        transport_hint = (config.get('transport') or '').lower()
        if transport_hint:
            effective = transport_hint
        elif server_type == 'streamable-http':
            effective = 'streamable-http'
        elif server_type == 'sse':
            effective = 'sse'
        else:
            effective = 'auto'

        if effective == 'streamable-http':
            t = StreamableHTTPTransport()
            await t.connect(url=url, auth_headers=auth_headers, timeout=timeout, verify_ssl=verify_ssl)
            return t

        if effective == 'sse':
            t = SSETransport()
            await t.connect(url=url, auth_headers=auth_headers, timeout=timeout)
            return t

        # auto: streamable-http -> sse fallback
        try:
            t = StreamableHTTPTransport()
            await t.connect(url=url, auth_headers=auth_headers, timeout=timeout, verify_ssl=verify_ssl)
            return t
        except Exception as e:
            logger.info(f"streamable-http connect failed ({e}); falling back to SSE")
            try:
                await t.close()
            except Exception:
                pass
            t = SSETransport()
            await t.connect(url=url, auth_headers=auth_headers, timeout=timeout)
            return t

    async def _fetch_tools(self, key: str) -> list:
        """Fetch tools from a connected server and update cache"""
        if key not in self._connections:
            return []

        transport = self._connections[key]['transport']
        response = await transport.send_request("tools/list", {})

        if response.get("error"):
            logger.error(f"Failed to list tools from server {key}: {response.get('error_message')}")
            return []

        result = response.get("result", {})
        raw_tools = result.get("tools", [])

        tools = []
        for t in raw_tools:
            entry = {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "inputSchema": t.get("inputSchema", {})
            }
            # MCP tool annotations (readOnlyHint / destructiveHint / ...) are
            # a server's own declaration about a tool; carry them through so
            # callers can gate reads vs writes without guessing from names.
            if isinstance(t.get("annotations"), dict):
                entry["annotations"] = t["annotations"]
            tools.append(entry)

        # Update cache
        self._tools_cache[key] = {
            'tools': tools,
            'timestamp': time.time()
        }

        return tools

    async def _cleanup_connection(self, key: str):
        """Internal cleanup without lock"""
        if key in self._connections:
            try:
                transport = self._connections[key]['transport']
                await transport.close()
                logger.info(f"Server {key} disconnected and cleaned up")
            except Exception as e:
                logger.error(f"Error cleaning up server {key}: {e}")
            finally:
                del self._connections[key]
                self._tools_cache.pop(key, None)

    async def cleanup_all(self):
        """Clean up all connections (called on shutdown)"""
        logger.info("Cleaning up all MCP server connections...")
        keys = list(self._connections.keys())
        for key in keys:
            try:
                sid, uid = split_key(key)
                await self.disconnect(sid, uid)
            except Exception as e:
                logger.error(f"Error cleaning up server {key}: {e}")
