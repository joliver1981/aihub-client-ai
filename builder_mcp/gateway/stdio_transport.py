"""
MCP Stdio Transport
Manages local MCP server subprocesses using stdio (stdin/stdout) transport.
Handles the JSON-RPC 2.0 message exchange over process pipes.
"""
import asyncio
import json
import logging
import os
import sys
import time
from typing import Dict, List, Optional, Any

from protocol import MCPProtocol
from mcp_gateway_config import DEFAULT_CONNECT_TIMEOUT, MCP_CLIENT_NAME, MCP_CLIENT_VERSION, MCP_PROTOCOL_VERSION

logger = logging.getLogger("MCPGateway")


class StdioTransport:
    """Manages a local MCP server subprocess using stdio transport"""

    def __init__(self):
        self.process: Optional[asyncio.subprocess.Process] = None
        self.command: Optional[str] = None
        self.args: List[str] = []
        self.env_vars: Dict[str, str] = {}
        self._read_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._pending_responses: Dict[int, asyncio.Future] = {}
        self._reader_task: Optional[asyncio.Task] = None
        self._connected = False
        self._server_capabilities = {}

    @property
    def is_connected(self) -> bool:
        return self._connected and self.process is not None and self.process.returncode is None

    async def start(self, command: str, args: List[str], env_vars: Dict[str, str] = None,
                    timeout: int = DEFAULT_CONNECT_TIMEOUT):
        """Start the subprocess and perform MCP initialization handshake"""
        self.command = command
        self.args = args
        self.env_vars = env_vars or {}

        # Build environment: inherit current env + add custom vars
        env = os.environ.copy()
        env.update(self.env_vars)

        # On Windows, npx might need to be called as npx.cmd
        actual_command = command
        if sys.platform == 'win32' and command in ('npx', 'node', 'python', 'python3', 'uv', 'uvx'):
            # Check if we need the .cmd extension
            cmd_variant = command + '.cmd'
            # Try the .cmd variant first on Windows for npm/npx
            if command in ('npx', 'node'):
                actual_command = cmd_variant

        logger.info(f"Starting MCP server subprocess: {actual_command} {' '.join(args)}")

        try:
            self.process = await asyncio.create_subprocess_exec(
                actual_command, *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError:
            # If .cmd variant failed, try original command
            if actual_command != command:
                logger.debug(f"Command {actual_command} not found, trying {command}")
                self.process = await asyncio.create_subprocess_exec(
                    command, *args,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
            else:
                raise

        # Start background reader for stdout
        self._reader_task = asyncio.create_task(self._read_loop())

        # Perform MCP initialize handshake
        init_request = MCPProtocol.create_initialize_request(
            client_name=MCP_CLIENT_NAME,
            client_version=MCP_CLIENT_VERSION,
            protocol_version=MCP_PROTOCOL_VERSION
        )

        try:
            init_response = await asyncio.wait_for(
                self.send_request_raw(init_request),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            await self.close()
            raise TimeoutError(f"MCP initialization timed out after {timeout}s")

        parsed = MCPProtocol.parse_response(init_response)
        if parsed.get("error"):
            await self.close()
            raise ConnectionError(f"MCP initialization failed: {parsed.get('error_message')}")

        self._server_capabilities = parsed.get("result", {}).get("capabilities", {})

        # Send initialized notification
        notification = MCPProtocol.create_initialized_notification()
        await self._write_message(notification)

        self._connected = True
        logger.info(f"MCP server initialized successfully: {command} {' '.join(args)}")

    async def send_request(self, method: str, params: dict = None, timeout: int = 60) -> dict:
        """Send a JSON-RPC request and wait for the response"""
        if not self.is_connected:
            raise ConnectionError("Not connected to MCP server")

        request = MCPProtocol.create_request(method, params)

        try:
            response = await asyncio.wait_for(
                self.send_request_raw(request),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            raise TimeoutError(f"MCP request '{method}' timed out after {timeout}s")

        return MCPProtocol.parse_response(response)

    async def send_request_raw(self, request: dict) -> dict:
        """Send a raw JSON-RPC request and wait for matching response by id"""
        request_id = request.get("id")
        if request_id is None:
            raise ValueError("Request must have an 'id' field")

        # Create a future for this request
        future = asyncio.get_event_loop().create_future()
        self._pending_responses[request_id] = future

        # Send the message
        await self._write_message(request)

        try:
            # Wait for the matching response
            response = await future
            return response
        finally:
            self._pending_responses.pop(request_id, None)

    async def _write_message(self, msg: dict):
        """Write a JSON-RPC message to the subprocess stdin"""
        if self.process is None or self.process.stdin is None:
            raise ConnectionError("Process not running")

        async with self._write_lock:
            data = MCPProtocol.encode_message(msg)
            self.process.stdin.write(data)
            await self.process.stdin.drain()
            logger.debug(f"Sent: {json.dumps(msg)[:200]}")

    async def _read_loop(self):
        """Background task that reads stdout and dispatches responses"""
        try:
            while self.process and self.process.stdout:
                line = await self.process.stdout.readline()
                if not line:
                    break

                decoded = line.decode("utf-8", errors="replace").strip()
                if not decoded:
                    continue

                msg = MCPProtocol.decode_message(decoded)
                if msg is None:
                    continue

                logger.debug(f"Received: {decoded[:200]}")

                # Check if this is a response to a pending request
                msg_id = msg.get("id")
                if msg_id is not None and msg_id in self._pending_responses:
                    future = self._pending_responses[msg_id]
                    if not future.done():
                        future.set_result(msg)
                elif "method" in msg and "id" not in msg:
                    # This is a notification from the server — log it
                    logger.debug(f"Server notification: {msg.get('method')}")
                else:
                    logger.debug(f"Unhandled message: {decoded[:100]}")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in stdio read loop: {e}")
            # Set error on all pending futures
            for future in self._pending_responses.values():
                if not future.done():
                    future.set_exception(ConnectionError(f"Read loop error: {e}"))

        self._connected = False
        logger.info("Stdio read loop ended")

    async def close(self):
        """Terminate the subprocess gracefully"""
        self._connected = False

        # Cancel reader task
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

        # Cancel pending requests
        for future in self._pending_responses.values():
            if not future.done():
                future.set_exception(ConnectionError("Transport closed"))
        self._pending_responses.clear()

        # Terminate process
        if self.process and self.process.returncode is None:
            try:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    self.process.kill()
                    await self.process.wait()
                logger.info("MCP server subprocess terminated")
            except ProcessLookupError:
                pass
            except Exception as e:
                logger.warning(f"Error terminating subprocess: {e}")

        self.process = None
