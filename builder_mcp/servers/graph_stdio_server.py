"""
EveriAI Graph MCP Server (stdio)

A self-contained MCP server that exposes Microsoft Graph mail + calendar tools
to AI Hub agents. Bearer tokens are sourced through the in-repo OAuth manager,
which means every tool call gets a fresh, auto-refreshed token from the
MCPServerCredentials store. This is what lets us validate the OAuth pipeline
end-to-end through the MCP stack.

Launch (managed by the AI Hub MCP gateway as a Local server):
    <python.exe> -m builder_mcp.servers.graph_stdio_server

Env vars (auto-injected by the AI Hub gateway when this server is registered as Local):
    MCP_AIHUB_SERVER_ID  — the MCPServers row id whose OAuth credentials hold the
                           tenant_id / client_id / client_secret / refresh token.
                           Set automatically by mcp_agent_tools._build_connection_config.
    API_KEY              — tenant context for the DB lookup. Auto-injected if set
                           in the host process.

Exposed tools:
    get_my_profile
    list_recent_emails
    send_email
    list_upcoming_meetings
"""
import os
import sys
import json
import logging
import traceback
from datetime import datetime, timedelta, timezone

# ----------------------------------------------------------------------------
# Reserve stdout for JSON-RPC frames ONLY.
# Anything in our import chain (secure_config, app_config, etc.) that uses
# print() would otherwise corrupt the protocol. Dup fd 1 into a separate
# handle for our own writes, then redirect Python's sys.stdout to stderr.
# ----------------------------------------------------------------------------
try:
    _real_stdout_fd = os.dup(sys.stdout.fileno())
    _RPC_OUT = os.fdopen(_real_stdout_fd, 'w', encoding='utf-8', buffering=1)
except Exception:
    _RPC_OUT = sys.stdout  # fallback (e.g. when run interactively)
sys.stdout = sys.stderr

# Repo root on sys.path so we can import builder_mcp.* / CommonUtils / etc.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# File logging — important for diagnosing subprocess crashes (the gateway
# captures stderr but doesn't currently surface it).
_LOG_DIR = os.path.join(_REPO_ROOT, 'logs')
try:
    os.makedirs(_LOG_DIR, exist_ok=True)
    _LOG_FILE = os.path.join(_LOG_DIR, 'graph_mcp_server_log.txt')
except Exception:
    _LOG_FILE = None

_handlers = [logging.StreamHandler(stream=sys.stderr)]
if _LOG_FILE:
    try:
        _handlers.append(logging.FileHandler(_LOG_FILE, encoding='utf-8'))
    except Exception:
        pass

logging.basicConfig(
    level=os.environ.get('GRAPH_MCP_LOG_LEVEL', 'INFO'),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=_handlers,
    force=True,
)
logger = logging.getLogger("GraphMCPServer")

# Now safe to do the prints-happen-during-import imports.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_REPO_ROOT, '.env'))
except ImportError:
    logger.debug("python-dotenv not available; skipping .env load")
except Exception as e:
    logger.warning(f".env load failed: {e}")

try:
    from secure_config import load_secure_config
    load_secure_config()
except ImportError:
    logger.debug("secure_config not available; skipping")
except Exception as e:
    logger.warning(f"secure_config load failed: {e}")

import requests  # noqa: E402

# Shared tool definitions (also used by the in-process /api/internal/mcp/graph
# endpoint in production).
from builder_mcp.servers.graph_tools import TOOL_SCHEMAS, TOOL_HANDLERS  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "everiai-graph"
SERVER_VERSION = "1.0.0"
SERVER_ID_ENV = "MCP_AIHUB_SERVER_ID"


def _get_server_id() -> int:
    raw = os.environ.get(SERVER_ID_ENV)
    if not raw:
        raise RuntimeError(
            f"{SERVER_ID_ENV} env var is required. AI Hub normally injects it "
            f"automatically when launching a Local MCP server."
        )
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(f"{SERVER_ID_ENV} must be an integer, got {raw!r}")


def _get_token() -> str:
    """Source a Graph bearer token from the AIHub OAuth manager."""
    from builder_mcp.agent_integration.oauth_manager import get_access_token
    server_id = _get_server_id()
    token = get_access_token(server_id)
    if not token:
        raise RuntimeError(
            f"No OAuth token available for server_id={server_id}. "
            f"Configure OAuth credentials and click Authorize in the AI Hub MCP UI."
        )
    return token


# Wrap each shared handler so it receives our token-fetcher closure.
TOOL_CALLS = {name: (lambda h: lambda args: h(args, _get_token))(handler)
              for name, handler in TOOL_HANDLERS.items()}


# ============================================================================
# MCP JSON-RPC over stdio
# ============================================================================

def _send(msg: dict):
    _RPC_OUT.write(json.dumps(msg, separators=(",", ":")) + "\n")
    _RPC_OUT.flush()


def _ok(req_id, result):
    _send({"jsonrpc": "2.0", "id": req_id, "result": result})


def _err(req_id, code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    _send({"jsonrpc": "2.0", "id": req_id, "error": err})


def _handle(req: dict):
    method = req.get("method")
    req_id = req.get("id")
    params = req.get("params") or {}

    try:
        if method == "initialize":
            _ok(req_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            })
        elif method in ("notifications/initialized", "initialized"):
            return  # notification, no response
        elif method == "tools/list":
            _ok(req_id, {"tools": TOOL_SCHEMAS})
        elif method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            handler = TOOL_CALLS.get(name)
            if not handler:
                _err(req_id, -32601, f"Unknown tool: {name}")
                return
            try:
                result = handler(args)
                _ok(req_id, {
                    "content": [{"type": "text",
                                 "text": json.dumps(result, default=str, indent=2)}],
                    "isError": False,
                })
            except Exception as e:
                logger.error(f"Tool {name} failed: {e}", exc_info=True)
                _ok(req_id, {
                    "content": [{"type": "text", "text": f"Error: {e}"}],
                    "isError": True,
                })
        elif method == "shutdown":
            _ok(req_id, {})
        else:
            if req_id is not None:
                _err(req_id, -32601, f"Method not found: {method}")
    except Exception as e:
        logger.error(f"Error handling {method}: {e}", exc_info=True)
        if req_id is not None:
            _err(req_id, -32603, str(e))


def main():
    logger.info(f"EveriAI Graph MCP server starting "
                f"(server_id={os.environ.get(SERVER_ID_ENV)}, "
                f"python={sys.executable}, pid={os.getpid()})")
    # Use readline() rather than `for line in sys.stdin:` — the latter buffers
    # on Windows pipes and may never yield until EOF.
    while True:
        try:
            line = sys.stdin.readline()
        except Exception as e:
            logger.error(f"stdin read failed: {e}")
            break
        if not line:
            break  # EOF — parent closed stdin
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON-RPC line: {e}; line={line[:200]!r}")
            continue
        _handle(req)
    logger.info("EveriAI Graph MCP server stopping")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException as e:
        logger.critical(
            "EveriAI Graph MCP server crashed:\n" + traceback.format_exc()
        )
        sys.exit(1)
