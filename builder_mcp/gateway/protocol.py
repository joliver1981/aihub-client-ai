"""
MCP Protocol Handler
JSON-RPC 2.0 message formatting and parsing for MCP communication.
"""
import json
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("MCPGateway")


class MCPProtocol:
    """Handles MCP JSON-RPC 2.0 message formatting and parsing"""

    _request_counter = 0

    @classmethod
    def _next_id(cls) -> int:
        cls._request_counter += 1
        return cls._request_counter

    @staticmethod
    def create_request(method: str, params: dict = None, request_id: int = None) -> dict:
        """Create a JSON-RPC 2.0 request message"""
        if request_id is None:
            request_id = MCPProtocol._next_id()
        msg = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            msg["params"] = params
        return msg

    @staticmethod
    def create_notification(method: str, params: dict = None) -> dict:
        """Create a JSON-RPC 2.0 notification (no id, no response expected)"""
        msg = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            msg["params"] = params
        return msg

    @staticmethod
    def create_initialize_request(
        client_name: str = "AIHub",
        client_version: str = "1.0.0",
        protocol_version: str = "2024-11-05"
    ) -> dict:
        """Create the MCP initialize handshake request"""
        return MCPProtocol.create_request("initialize", {
            "protocolVersion": protocol_version,
            "capabilities": {},
            "clientInfo": {
                "name": client_name,
                "version": client_version
            }
        })

    @staticmethod
    def create_initialized_notification() -> dict:
        """Create the notifications/initialized message sent after init response"""
        return MCPProtocol.create_notification("notifications/initialized")

    @staticmethod
    def create_list_tools_request() -> dict:
        """Create a tools/list request"""
        return MCPProtocol.create_request("tools/list", {})

    @staticmethod
    def create_call_tool_request(tool_name: str, arguments: dict) -> dict:
        """Create a tools/call request"""
        return MCPProtocol.create_request("tools/call", {
            "name": tool_name,
            "arguments": arguments
        })

    @staticmethod
    def parse_response(data: dict) -> dict:
        """
        Parse a JSON-RPC 2.0 response.

        Returns:
            dict with either 'result' or 'error' key
        """
        if "error" in data:
            error = data["error"]
            return {
                "error": True,
                "error_code": error.get("code", -1),
                "error_message": error.get("message", "Unknown error"),
                "error_data": error.get("data")
            }

        if "result" in data:
            return {
                "error": False,
                "result": data["result"]
            }

        # Unexpected format
        return {
            "error": True,
            "error_code": -1,
            "error_message": "Invalid JSON-RPC response: missing 'result' and 'error'",
            "raw": data
        }

    @staticmethod
    def encode_message(msg: dict) -> bytes:
        """Encode a JSON-RPC message for stdio transport (newline-delimited JSON)"""
        return (json.dumps(msg) + "\n").encode("utf-8")

    @staticmethod
    def decode_message(line: str) -> Optional[dict]:
        """Decode a single line of JSON-RPC from stdio transport"""
        line = line.strip()
        if not line:
            return None
        try:
            return json.loads(line)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to decode JSON-RPC message: {e}")
            return None
