"""
MCP Gateway Configuration
"""
import os

# Service settings
MCP_GATEWAY_PORT = int(os.getenv('MCP_GATEWAY_PORT', '5071'))
_APP_ROOT = os.path.abspath(os.getenv('APP_ROOT', os.path.dirname(os.path.abspath(__file__))))
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
