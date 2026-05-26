"""
Internal MCP endpoints hosted by the main AI Hub app.

These speak the MCP "streamable-http" wire protocol (JSON-RPC 2.0 over a
single POST endpoint). The MCP gateway treats them like any other remote
streamable-http MCP server: it POSTs initialize / tools/list / tools/call
messages here and reads back JSON responses.

Why in-process instead of a separate bundled exe? The tool implementations
already depend on:
  - oauth_manager.get_access_token  (DB-backed token cache)
  - CommonUtils / DB connectivity
  - encryption keys
…all of which live in the main app. Hosting the MCP endpoint here avoids
shipping a second PyInstaller bundle whose only job is to call back into
the same dependencies. The gateway stays provider-agnostic; the provider
intelligence lives where its deps already are.

Auth model: the endpoint is restricted to loopback (the gateway runs on
the same machine). The Authorization: Bearer header on each request is
passed through to Microsoft Graph — the gateway populates it via
mcp_agent_tools._get_auth_headers, which calls oauth_manager.get_access_token
to mint or refresh the user's Graph token.
"""
import json
import logging

from flask import Blueprint, request, jsonify

from builder_mcp.servers.graph_tools import TOOL_SCHEMAS, TOOL_HANDLERS

logger = logging.getLogger(__name__)

mcp_internal_bp = Blueprint('mcp_internal', __name__, url_prefix='/api/internal/mcp')

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "everiai-graph"
SERVER_VERSION = "1.0.0"

_LOOPBACK_ADDRS = {'127.0.0.1', '::1', 'localhost'}


def _loopback_only():
    """Reject anything not from the local machine. The MCP gateway always
    talks to us over 127.0.0.1, so anything else is suspicious."""
    if request.remote_addr not in _LOOPBACK_ADDRS:
        logger.warning(f"Rejected internal-MCP call from non-loopback {request.remote_addr}")
        return jsonify({'error': 'forbidden'}), 403
    return None


def _ok(req_id, result):
    return jsonify({"jsonrpc": "2.0", "id": req_id, "result": result})


def _err(req_id, code, message):
    return jsonify({"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": code, "message": message}})


def _bearer_from_headers() -> str:
    """Extract the Graph bearer token the gateway forwarded to us."""
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


@mcp_internal_bp.route('/graph', methods=['POST'])
def graph_mcp():
    """MCP streamable-http endpoint backing the EveriAI Graph server entry."""
    deny = _loopback_only()
    if deny is not None:
        return deny

    try:
        msg = request.get_json(force=True, silent=False) or {}
    except Exception as e:
        return jsonify({"jsonrpc": "2.0",
                        "error": {"code": -32700,
                                  "message": f"parse error: {e}"}}), 400

    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    token = _bearer_from_headers()

    def get_token():
        if not token:
            raise RuntimeError(
                "No bearer token forwarded by gateway. Has the OAuth flow "
                "been completed for this server? Click Authorize in the UI."
            )
        return token

    try:
        if method == "initialize":
            return _ok(req_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            })

        if method in ("notifications/initialized", "initialized"):
            return ('', 202)  # notification — no JSON-RPC response body

        if method == "tools/list":
            return _ok(req_id, {"tools": TOOL_SCHEMAS})

        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            handler = TOOL_HANDLERS.get(name)
            if not handler:
                return _err(req_id, -32601, f"Unknown tool: {name}")
            try:
                result = handler(args, get_token)
                return _ok(req_id, {
                    "content": [{"type": "text",
                                 "text": json.dumps(result, default=str, indent=2)}],
                    "isError": False,
                })
            except Exception as e:
                logger.error(f"Graph tool '{name}' failed: {e}", exc_info=True)
                return _ok(req_id, {
                    "content": [{"type": "text", "text": f"Error: {e}"}],
                    "isError": True,
                })

        if method == "shutdown":
            return _ok(req_id, {})

        if req_id is not None:
            return _err(req_id, -32601, f"Method not found: {method}")
        return ('', 202)

    except Exception as e:
        logger.error(f"Error handling internal MCP {method}: {e}", exc_info=True)
        if req_id is not None:
            return _err(req_id, -32603, str(e))
        return jsonify({}), 500
