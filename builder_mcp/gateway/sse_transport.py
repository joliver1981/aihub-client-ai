"""
MCP SSE Transport
Manages connection to remote MCP servers via HTTP/SSE (Server-Sent Events).
"""
import asyncio
import json
import logging
import uuid
from typing import Dict, List, Optional, Any

from protocol import MCPProtocol
from mcp_gateway_config import DEFAULT_CONNECT_TIMEOUT, MCP_CLIENT_NAME, MCP_CLIENT_VERSION, MCP_PROTOCOL_VERSION

logger = logging.getLogger("MCPGateway")

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    logger.warning("httpx not installed — remote SSE transport will not be available")


class SSETransport:
    """Manages connection to a remote MCP server via HTTP/SSE"""

    def __init__(self):
        self._client: Optional[Any] = None  # httpx.AsyncClient
        self._base_url: Optional[str] = None
        self._auth_headers: Dict[str, str] = {}
        self._connected = False
        self._server_capabilities = {}
        self._message_endpoint: Optional[str] = None  # POST endpoint for sending messages
        self._sse_task: Optional[asyncio.Task] = None
        self._pending_responses: Dict[int, asyncio.Future] = {}

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self, url: str, auth_headers: Dict[str, str] = None,
                      timeout: int = DEFAULT_CONNECT_TIMEOUT):
        """
        Connect to a remote MCP server via SSE.

        The MCP SSE protocol works as follows:
        1. Client opens GET request to the SSE endpoint
        2. Server sends an 'endpoint' event with the URL to POST messages to
        3. Client sends initialize via POST to that endpoint
        4. Server responds via SSE stream
        """
        if not HTTPX_AVAILABLE:
            raise ImportError("httpx is required for remote SSE transport. Install with: pip install httpx")

        self._base_url = url.rstrip('/')
        self._auth_headers = auth_headers or {}

        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, read=120.0),
            headers={
                'Content-Type': 'application/json',
                **self._auth_headers
            },
            verify=True
        )

        logger.info(f"Connecting to remote MCP server: {self._base_url}")

        try:
            # Try to connect via SSE endpoint
            await self._connect_sse(timeout)
        except Exception:
            # Fallback: try simple HTTP POST/response pattern
            logger.info("SSE connection failed, trying HTTP POST fallback")
            await self._connect_http_fallback(timeout)

    async def _connect_sse(self, timeout: int):
        """Connect using SSE transport (standard MCP remote protocol)"""
        sse_url = self._base_url + '/sse'

        # Open SSE connection to get the message endpoint
        endpoint_future = asyncio.get_event_loop().create_future()

        async def sse_reader():
            try:
                async with self._client.stream('GET', sse_url) as response:
                    response.raise_for_status()
                    buffer = ""
                    event_type = None

                    async for chunk in response.aiter_text():
                        buffer += chunk
                        while '\n' in buffer:
                            line, buffer = buffer.split('\n', 1)
                            line = line.strip()

                            if line.startswith('event:'):
                                event_type = line[6:].strip()
                            elif line.startswith('data:'):
                                data = line[5:].strip()

                                if event_type == 'endpoint':
                                    if not endpoint_future.done():
                                        endpoint_future.set_result(data)
                                elif event_type == 'message':
                                    try:
                                        msg = json.loads(data)
                                        msg_id = msg.get("id")
                                        if msg_id is not None and msg_id in self._pending_responses:
                                            future = self._pending_responses[msg_id]
                                            if not future.done():
                                                future.set_result(msg)
                                    except json.JSONDecodeError:
                                        logger.warning(f"Invalid JSON in SSE message: {data[:100]}")

                                event_type = None
                            elif line == '':
                                event_type = None
            except asyncio.CancelledError:
                pass
            except Exception as e:
                if not endpoint_future.done():
                    endpoint_future.set_exception(e)
                logger.error(f"SSE reader error: {e}")

        self._sse_task = asyncio.create_task(sse_reader())

        # Wait for the endpoint event
        try:
            endpoint = await asyncio.wait_for(endpoint_future, timeout=timeout)
            # The endpoint might be relative or absolute
            if endpoint.startswith('http'):
                self._message_endpoint = endpoint
            else:
                self._message_endpoint = self._base_url + endpoint
            logger.info(f"SSE message endpoint: {self._message_endpoint}")
        except asyncio.TimeoutError:
            if self._sse_task:
                self._sse_task.cancel()
            raise TimeoutError("Timed out waiting for SSE endpoint event")

        # Perform MCP initialize handshake
        await self._perform_initialize(timeout)

    async def _connect_http_fallback(self, timeout: int):
        """Fallback: use simple HTTP POST for both sending and receiving"""
        self._message_endpoint = self._base_url
        await self._perform_initialize(timeout)

    async def _perform_initialize(self, timeout: int):
        """Perform the MCP initialize handshake"""
        init_request = MCPProtocol.create_initialize_request(
            client_name=MCP_CLIENT_NAME,
            client_version=MCP_CLIENT_VERSION,
            protocol_version=MCP_PROTOCOL_VERSION
        )

        init_response = await asyncio.wait_for(
            self._send_and_receive(init_request),
            timeout=timeout
        )

        parsed = MCPProtocol.parse_response(init_response)
        if parsed.get("error"):
            raise ConnectionError(f"MCP initialization failed: {parsed.get('error_message')}")

        self._server_capabilities = parsed.get("result", {}).get("capabilities", {})

        # Send initialized notification
        notification = MCPProtocol.create_initialized_notification()
        await self._send_notification(notification)

        self._connected = True
        logger.info(f"Remote MCP server initialized: {self._base_url}")

    async def send_request(self, method: str, params: dict = None, timeout: int = 60) -> dict:
        """Send a JSON-RPC request and wait for the response"""
        if not self.is_connected:
            raise ConnectionError("Not connected to remote MCP server")

        request = MCPProtocol.create_request(method, params)

        try:
            response = await asyncio.wait_for(
                self._send_and_receive(request),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            raise TimeoutError(f"Remote MCP request '{method}' timed out after {timeout}s")

        return MCPProtocol.parse_response(response)

    async def _send_and_receive(self, request: dict) -> dict:
        """Send request and get response, either via SSE or HTTP POST"""
        if self._sse_task and not self._sse_task.done():
            # SSE mode: POST the request, response comes via SSE stream
            request_id = request.get("id")
            future = asyncio.get_event_loop().create_future()
            self._pending_responses[request_id] = future

            try:
                response = await self._client.post(
                    self._message_endpoint,
                    json=request
                )
                response.raise_for_status()
                # Wait for the SSE response
                result = await future
                return result
            finally:
                self._pending_responses.pop(request_id, None)
        else:
            # HTTP fallback: POST and get response directly
            response = await self._client.post(
                self._message_endpoint,
                json=request
            )
            response.raise_for_status()
            return response.json()

    async def _send_notification(self, notification: dict):
        """Send a notification (no response expected)"""
        if self._message_endpoint:
            try:
                await self._client.post(
                    self._message_endpoint,
                    json=notification
                )
            except Exception as e:
                logger.debug(f"Failed to send notification: {e}")

    async def close(self):
        """Close the SSE connection"""
        self._connected = False

        # Cancel SSE reader
        if self._sse_task and not self._sse_task.done():
            self._sse_task.cancel()
            try:
                await self._sse_task
            except asyncio.CancelledError:
                pass

        # Cancel pending requests
        for future in self._pending_responses.values():
            if not future.done():
                future.set_exception(ConnectionError("Transport closed"))
        self._pending_responses.clear()

        # Close HTTP client
        if self._client:
            await self._client.aclose()
            self._client = None

        logger.info(f"Disconnected from remote MCP server: {self._base_url}")
