"""
MCP Agent Tools
Provides MCP server tools to AI Hub agents.
Called during GeneralAgent initialization to load MCP tools.

Follows the same pattern as integration_agent_tools.py and agent_email_tools.py.
"""
import os
import json
import logging
from typing import List

from CommonUtils import get_db_connection

logger = logging.getLogger(__name__)


def get_mcp_tools_for_agent(agent_id: int) -> List:
    """
    Get all MCP tools available to a specific agent.

    1. Query AgentMCPServers to find assigned & enabled servers
    2. For each server, get config from MCPServers table
    3. Connect via gateway client (if not already connected)
    4. List tools from that server
    5. Convert to LangChain tools using MCPToolConverter
    6. Return combined list

    Returns empty list if no servers assigned or gateway unavailable.
    Never raises — logs errors and returns empty list.
    """
    try:
        from builder_mcp.client.mcp_gateway_client import MCPGatewayClient
        from builder_mcp.client.tool_converter import MCPToolConverter

        # Check gateway health first to fail fast
        gateway = MCPGatewayClient()
        if not gateway.health_check():
            logger.debug("MCP Gateway is not available — skipping MCP tools")
            return []

        # Query assigned servers from database
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        cursor.execute("""
            SELECT ms.server_id, ms.server_name, ms.server_type,
                   ms.server_url, ms.auth_type, ms.connection_config
            FROM MCPServers ms
            INNER JOIN AgentMCPServers ams ON ms.server_id = ams.server_id
            WHERE ams.agent_id = ? AND ms.enabled = 1 AND ams.enabled = 1
        """, agent_id)

        servers = cursor.fetchall()
        cursor.close()
        conn.close()

        if not servers:
            return []

        all_tools = []

        for row in servers:
            server_id, server_name, server_type, server_url, auth_type, connection_config = row

            try:
                # Build connection config for the gateway
                config = _build_connection_config(
                    server_type, server_url, auth_type,
                    connection_config, server_id
                )

                # Connect to the server via gateway
                connect_result = gateway.connect_server(server_id, config)

                if connect_result.get('status') != 'connected':
                    logger.warning(
                        f"Failed to connect MCP server '{server_name}' (id={server_id}): "
                        f"{connect_result.get('error', 'unknown error')}"
                    )
                    continue

                # Get tools (may come from connect response or separate list call)
                tools = connect_result.get('tools', [])
                if not tools:
                    tools = gateway.list_tools(server_id)

                # Convert to LangChain tools
                converter = MCPToolConverter(gateway, server_id, server_name)
                langchain_tools = converter.convert_all_tools(tools)
                all_tools.extend(langchain_tools)

                logger.info(
                    f"Loaded {len(langchain_tools)} MCP tools from '{server_name}' "
                    f"for agent {agent_id}"
                )

            except Exception as e:
                logger.warning(f"Error loading MCP server '{server_name}' for agent {agent_id}: {e}")
                continue

        return all_tools

    except ImportError as e:
        logger.debug(f"MCP module not available: {e}")
        return []
    except Exception as e:
        logger.warning(f"Failed to load MCP tools for agent {agent_id}: {e}")
        return []


def get_mcp_system_prompt_addition(agent_id: int) -> str:
    """
    Generate system prompt text describing available MCP tools.

    Returns text to append to the agent's system prompt, or empty string
    if no MCP servers are assigned.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        cursor.execute("""
            SELECT ms.server_name, ms.description
            FROM MCPServers ms
            INNER JOIN AgentMCPServers ams ON ms.server_id = ams.server_id
            WHERE ams.agent_id = ? AND ms.enabled = 1 AND ams.enabled = 1
        """, agent_id)

        servers = cursor.fetchall()
        cursor.close()
        conn.close()

        if not servers:
            return ""

        server_lines = []
        for name, description in servers:
            desc_str = f" — {description}" if description else ""
            server_lines.append(f"- {name}{desc_str}")

        server_list = "\n".join(server_lines)

        return f"""

## External MCP Tools

You have access to external tools from the following MCP servers:
{server_list}

These tools extend your capabilities to interact with external systems.
Use them when the user's request involves operations provided by these servers.
Tool names are prefixed with the server name (e.g., servername_toolname).
"""

    except Exception as e:
        logger.warning(f"Error building MCP system prompt for agent {agent_id}: {e}")
        return ""


def _build_connection_config(server_type, server_url, auth_type,
                             connection_config_json, server_id) -> dict:
    """Build a connection config dict for the gateway from database fields"""
    if server_type == 'local':
        # Parse the stored JSON config
        config = {}
        if connection_config_json:
            try:
                config = json.loads(connection_config_json)
            except (json.JSONDecodeError, TypeError):
                pass
        return {
            "type": "local",
            "command": config.get("command", ""),
            "args": config.get("args", []),
            "env_vars": config.get("env_vars", {})
        }
    elif server_type == 'remote':
        # Build auth headers from credentials
        auth_headers = _get_auth_headers(server_id, auth_type)
        return {
            "type": "remote",
            "url": server_url or "",
            "auth_headers": auth_headers
        }
    else:
        return {"type": server_type}


def _get_auth_headers(server_id: int, auth_type: str) -> dict:
    """Fetch and build auth headers from MCPServerCredentials table"""
    if not auth_type or auth_type == 'none':
        return {}

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        # Get encryption key
        try:
            from encrypt import ENCRYPTION_KEY
            encryption_key = os.environ.get('MCP_ENCRYPTION_KEY', ENCRYPTION_KEY)
        except ImportError:
            encryption_key = os.environ.get('MCP_ENCRYPTION_KEY', 'default_key')

        cursor.execute("""
            SELECT credential_key,
                   CONVERT(NVARCHAR(MAX), DECRYPTBYPASSPHRASE(?, credential_value)) as credential_value
            FROM MCPServerCredentials
            WHERE server_id = ?
        """, encryption_key, server_id)

        credentials = {}
        for row in cursor.fetchall():
            if row[1]:  # Only if decryption succeeded
                credentials[row[0]] = row[1]

        cursor.close()
        conn.close()

        # Build headers based on auth type
        headers = {}
        if auth_type == 'bearer':
            token = credentials.get('token', '')
            if token:
                headers['Authorization'] = f'Bearer {token}'
        elif auth_type == 'apikey':
            header_name = credentials.get('header', 'X-API-Key')
            key = credentials.get('key', '')
            if key:
                headers[header_name] = key
        elif auth_type == 'basic':
            import base64
            username = credentials.get('username', '')
            password = credentials.get('password', '')
            if username:
                encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
                headers['Authorization'] = f'Basic {encoded}'
        elif auth_type == 'custom':
            # Custom headers stored directly
            for key, value in credentials.items():
                headers[key] = value

        return headers

    except Exception as e:
        logger.warning(f"Error fetching auth headers for server {server_id}: {e}")
        return {}
