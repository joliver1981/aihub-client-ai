"""
MCP Streamable HTTP Transport
Implements the modern MCP "Streamable HTTP" transport (spec rev 2025-03-26 / 2025-06-18).

Protocol summary:
  - Single HTTP endpoint serves both client->server (POST) and optional server->client streaming.
  - Client POSTs a JSON-RPC message with:
        Accept: application/json, text/event-stream
  - Server replies either:
        a) Content-Type: application/json     — one synchronous JSON-RPC response
        b) Content-Type: text/event-stream    — SSE stream containing the response (and any
                                                interleaved notifications) until the stream
                                                closes for that request.
  - A session is established when the server returns an `Mcp-Session-Id` header on the
    initialize response. The client MUST echo that header on every subsequent request.
  - Notifications (no `id`) are accepted via 202 with empty body.

Used by Microsoft Learn MCP (https://learn.microsoft.com/api/mcp) and other modern
hosted MCP servers. Falls back gracefully — if the server returns 405/404 the caller
can try the older SSE transport.
"""
import asyncio
import json
import logging
import uuid
from typing import Dict, Optional, Any

from protocol import MCPProtocol
from mcp_gateway_config import DEFAULT_CONNECT_TIMEOUT, MCP_CLIENT_NAME, MCP_CLIENT_VERSION, MCP_PROTOCOL_VERSION

logger = logging.getLogger("MCPGateway")

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False


class StreamableHTTPTransport:
    """MCP Streamable HTTP transport client."""

    def __init__(self):
        self._client: Optional[Any] = None
        self._endpoint: Optional[str] = None
        self._auth_headers: Dict[str, str] = {}
        self._session_id: Optional[str] = None
        self._connected = False
        self._server_capabilities: Dict[str, Any] = {}

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self, url: str, auth_headers: Dict[str, str] = None,
                      timeout: int = DEFAULT_CONNECT_TIMEOUT, verify_ssl: bool = True):
        if not HTTPX_AVAILABLE:
            raise ImportError("httpx is required for streamable-http transport. pip install httpx")

        self._endpoint = url
        self._auth_headers = auth_headers or {}

        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, read=120.0),
            verify=verify_ssl,
        )

        logger.info(f"Connecting to streamable-http MCP server: {self._endpoint}")
        await self._perform_initialize(timeout)

    async def _perform_initialize(self, timeout: int):
        init_request = MCPProtocol.create_initialize_request(
            client_name=MCP_CLIENT_NAME,
            client_version=MCP_CLIENT_VERSION,
            protocol_version=MCP_PROTOCOL_VERSION,
        )

        response_msg, session_id = await self._post_and_read(init_request, timeout=timeout,
                                                             capture_session=True)
        if session_id:
            self._session_id = session_id
            logger.info(f"streamable-http session established: {session_id}")

        parsed = MCPProtocol.parse_response(response_msg)
        if parsed.get("error"):
            raise ConnectionError(f"MCP initialize failed: {parsed.get('error_message')}")
        self._server_capabilities = parsed.get("result", {}).get("capabilities", {})

        notif = MCPProtocol.create_initialized_notification()
        try:
            await self._post_notification(notif)
        except Exception as e:
            logger.debug(f"initialized notification post failed (continuing): {e}")

        self._connected = True
        logger.info(f"streamable-http MCP server initialized: {self._endpoint}")

    async def send_request(self, method: str, params: dict = None, timeout: int = 60) -> dict:
        if not self.is_connected:
            raise ConnectionError("Not connected to streamable-http MCP server")

        request = MCPProtocol.create_request(method, params)
        try:
            response_msg, _ = await asyncio.wait_for(
                self._post_and_read(request, timeout=timeout),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            raise TimeoutError(f"streamable-http MCP request '{method}' timed out after {timeout}s")

        return MCPProtocol.parse_response(response_msg)

    async def _post_and_read(self, message: dict, timeout: int = 60,
                             capture_session: bool = False):
        """POST a JSON-RPC message, then read the response, which may be either
        a single JSON body or an SSE stream. Returns (response_dict, session_id_or_None)."""
        headers = self._build_headers()
        request_id = message.get("id")

        # Use stream so we can handle both JSON and SSE responses uniformly.
        async with self._client.stream(
            "POST",
            self._endpoint,
            json=message,
            headers=headers,
            timeout=timeout,
        ) as resp:
            session_id = resp.headers.get("Mcp-Session-Id") if capture_session else None

            if resp.status_code in (401, 403):
                # Read body for better error messages
                body = await resp.aread()
                raise PermissionError(
                    f"streamable-http auth failed (HTTP {resp.status_code}): {body[:300]!r}"
                )
            if resp.status_code == 404:
                raise ConnectionError("streamable-http endpoint not found (HTTP 404)")
            if resp.status_code == 405:
                raise ConnectionError("streamable-http POST not supported here (HTTP 405)")
            resp.raise_for_status()

            content_type = (resp.headers.get("content-type") or "").lower()

            if "text/event-stream" in content_type:
                response_msg = await self._read_sse_until_response(resp, request_id, timeout)
                return response_msg, session_id

            # Plain JSON body
            body_bytes = await resp.aread()
            if not body_bytes:
                return {"jsonrpc": "2.0", "id": request_id, "result": {}}, session_id
            try:
                data = json.loads(body_bytes)
            except json.JSONDecodeError as e:
                raise ConnectionError(f"Invalid JSON from MCP server: {e}; body={body_bytes[:300]!r}")
            return data, session_id

    async def _read_sse_until_response(self, resp, request_id, timeout: int) -> dict:
        """Consume an SSE stream until we see the response matching request_id."""
        buffer = ""
        event_data_lines = []
        async for chunk in resp.aiter_text():
            buffer += chunk
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.rstrip("\r")
                if line == "":
                    if event_data_lines:
                        data_str = "\n".join(event_data_lines)
                        event_data_lines = []
                        try:
                            msg = json.loads(data_str)
                        except json.JSONDecodeError:
                            logger.debug(f"Skipping non-JSON SSE data: {data_str[:120]}")
                            continue
                        msg_id = msg.get("id")
                        if msg_id is not None and msg_id == request_id:
                            return msg
                        # ignore notifications / unrelated responses; keep reading
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("data:"):
                    event_data_lines.append(line[5:].lstrip())
        raise ConnectionError(f"SSE stream ended before response for request id={request_id}")

    async def _post_notification(self, notif: dict):
        headers = self._build_headers()
        resp = await self._client.post(self._endpoint, json=notif, headers=headers)
        # 202 Accepted is normal for notifications
        if resp.status_code >= 400 and resp.status_code not in (202,):
            logger.debug(f"notification returned HTTP {resp.status_code}")

    def _build_headers(self) -> dict:
        h = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        }
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        h.update(self._auth_headers or {})
        return h

    async def close(self):
        self._connected = False
        if self._client:
            try:
                if self._session_id:
                    # Best-effort: tell server to dispose the session
                    try:
                        await self._client.delete(
                            self._endpoint,
                            headers=self._build_headers(),
                            timeout=5.0,
                        )
                    except Exception:
                        pass
                await self._client.aclose()
            except Exception as e:
                logger.debug(f"streamable-http close error: {e}")
            finally:
                self._client = None
        logger.info(f"Disconnected from streamable-http MCP server: {self._endpoint}")
