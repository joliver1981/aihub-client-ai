"""
MCP Gateway Configuration
"""
import os
import sys

# Service settings
# Bind to loopback by default — the gateway is consumed by the local main app
# over HTTP. Set MCP_GATEWAY_HOST=0.0.0.0 only behind a firewall/reverse proxy.
MCP_GATEWAY_HOST = os.getenv('MCP_GATEWAY_HOST', '127.0.0.1')
MCP_GATEWAY_PORT = int(os.getenv('MCP_GATEWAY_PORT', '5071'))


def _find_app_root():
    """Resolve the AIHub installation root, where the shared logs/ folder lives.

    Three-step fallback:
      1. APP_ROOT env var if explicitly set.
      2. PyInstaller frozen mode — walk up from sys.executable. Each service exe
         lives at <AIHub>/<service>/<service>.exe, so grandparent = AIHub root.
         This must work even before .env is loaded.
      3. Dev mode — fall back to this file's directory.
    """
    explicit = os.getenv('APP_ROOT')
    if explicit:
        return os.path.abspath(explicit)
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.abspath(__file__))


_APP_ROOT = _find_app_root()
MCP_GATEWAY_LOG = os.getenv('MCP_GATEWAY_LOG', os.path.join(_APP_ROOT, 'logs', 'mcp_gateway_log.txt'))
LOG_LEVEL = os.getenv('LOG_LEVEL', 'DEBUG')

# Connection defaults
DEFAULT_CONNECT_TIMEOUT = int(os.getenv('MCP_CONNECT_TIMEOUT', '30'))
DEFAULT_TOOL_CALL_TIMEOUT = int(os.getenv('MCP_TOOL_CALL_TIMEOUT', '60'))
DEFAULT_MAX_RETRIES = int(os.getenv('MCP_MAX_RETRIES', '3'))

# Tool cache TTL in seconds (how long to cache tool lists)
TOOL_CACHE_TTL = int(os.getenv('MCP_TOOL_CACHE_TTL', '300'))

# MCP Protocol version
MCP_PROTOCOL_VERSION = "2024-11-05"
MCP_CLIENT_NAME = "AIHub"
MCP_CLIENT_VERSION = "1.0.0"
