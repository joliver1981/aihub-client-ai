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


def get_mcp_tools_for_agent(agent_id: int, user_id: int = None) -> List:
    """
    Get all MCP tools available to a specific agent for a given user.

    1. Query AgentMCPServers to find admin-assigned, enabled servers
    2. For each server, get config from MCPServers table
    3. Connect via gateway client (passing the user's bearer token via
       auth_headers — sourced from per-user tokens for authorization_code
       grants, or the shared service-account token for client_credentials)
    4. List tools from that server
    5. Convert to LangChain tools using MCPToolConverter
    6. Return combined list

    Args:
        agent_id: The agent whose assigned servers should be loaded.
        user_id: The id of the calling user (current_user.id). Required to
            source per-user OAuth tokens for authorization_code servers. May
            be None for server-side / background invocations that only use
            client_credentials servers.

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

        # Query assigned + personal servers from database
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        # 1) Admin-assigned servers (current behavior — shared servers, e.g.
        #    Microsoft Learn or any client_credentials service account).
        cursor.execute("""
            SELECT ms.server_id, ms.server_name, ms.server_type,
                   ms.server_url, ms.auth_type, ms.connection_config
            FROM MCPServers ms
            INNER JOIN AgentMCPServers ams ON ms.server_id = ams.server_id
            WHERE ams.agent_id = ? AND ms.enabled = 1 AND ams.enabled = 1
        """, agent_id)
        servers = list(cursor.fetchall())
        seen_ids = {row[0] for row in servers}

        # 2) Personal user connections (Flow B). If the agent allows personal
        #    connections (column default = 1), include every OAuth server the
        #    calling user has personally authorized. This is what lets any
        #    agent — RetailOps, your data agent, anything — silently pick up
        #    "the user's email" without per-agent admin assignment.
        if user_id:
            try:
                cursor.execute("""
                    SELECT allow_personal_connections FROM Agents WHERE id = ?
                """, agent_id)
                flag_row = cursor.fetchone()
                allow_personal = bool(flag_row[0]) if flag_row else True
            except Exception:
                allow_personal = True  # Pre-migration safety: column may not exist yet

            if allow_personal:
                cursor.execute("""
                    SELECT DISTINCT ms.server_id, ms.server_name, ms.server_type,
                           ms.server_url, ms.auth_type, ms.connection_config
                    FROM MCPServers ms
                    INNER JOIN MCPUserTokens mut ON mut.server_id = ms.server_id
                    WHERE mut.user_id = ?
                      AND ms.enabled = 1
                      AND ms.auth_type = 'oauth2'
                """, user_id)
                for row in cursor.fetchall():
                    if row[0] not in seen_ids:
                        servers.append(row)
                        seen_ids.add(row[0])

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
                    connection_config, server_id, user_id=user_id,
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

                # Convert to LangChain tools (user_id + agent_id captured for audit log)
                converter = MCPToolConverter(
                    gateway, server_id, server_name,
                    user_id=user_id, agent_id=agent_id,
                )
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


def get_mcp_system_prompt_addition(agent_id: int, user_id: int = None) -> str:
    """
    Generate system prompt text describing available MCP tools.

    Two pools are reported:
      - Admin-assigned servers (shared) — always shown
      - Personal connections (per-user) — shown if user is connected
      - Personal connections the user has NOT yet authorized are also surfaced
        as actionable suggestions (so the agent can tell the user to go to My
        Connections instead of silently failing).

    Returns text to append to the agent's system prompt, or empty string.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        # Admin-assigned (shared) servers
        cursor.execute("""
            SELECT ms.server_name, ms.description
            FROM MCPServers ms
            INNER JOIN AgentMCPServers ams ON ms.server_id = ams.server_id
            WHERE ams.agent_id = ? AND ms.enabled = 1 AND ams.enabled = 1
        """, agent_id)
        shared = cursor.fetchall()

        personal_connected = []
        personal_unconnected = []

        if user_id:
            # Does this agent opt in to personal connections?
            try:
                cursor.execute("SELECT allow_personal_connections FROM Agents WHERE id = ?", agent_id)
                row = cursor.fetchone()
                allow_personal = bool(row[0]) if row else True
            except Exception:
                allow_personal = True

            if allow_personal:
                # Personal servers in this tenant that the user has authorized
                cursor.execute("""
                    SELECT ms.server_name, ms.description
                    FROM MCPServers ms
                    INNER JOIN MCPUserTokens mut ON mut.server_id = ms.server_id
                    WHERE mut.user_id = ? AND ms.enabled = 1 AND ms.auth_type = 'oauth2'
                    GROUP BY ms.server_name, ms.description
                """, user_id)
                personal_connected = cursor.fetchall()

                # OAuth servers the user could connect to but hasn't yet
                cursor.execute("""
                    SELECT ms.server_name, ms.description
                    FROM MCPServers ms
                    WHERE ms.enabled = 1 AND ms.auth_type = 'oauth2'
                      AND ms.server_id NOT IN (
                          SELECT server_id FROM MCPUserTokens WHERE user_id = ?
                      )
                """, user_id)
                personal_unconnected = cursor.fetchall()

        cursor.close()
        conn.close()

        if not (shared or personal_connected or personal_unconnected):
            return ""

        # IMPORTANT: do NOT include the server-level `description` field in the
        # prompt. Server descriptions are admin-authored setup notes (often with
        # placeholders like "{tenant_id}", install instructions, etc.) and the
        # LLM treats them as guidance to itself. Tool-level descriptions (from
        # the MCP server's tools/list response) already tell the model what
        # each tool does — that's the right signal. Just list server NAMES
        # here for orientation.
        def _name_lines(rows):
            return "\n".join(f"- {n}" for n, _d in rows)

        sections = []

        if shared:
            sections.append("Admin-assigned external tool servers:\n" + _name_lines(shared))

        if personal_connected:
            sections.append(
                "The current user has personally connected:\n" + _name_lines(personal_connected) +
                "\nWhen the user asks for something these services provide, call the "
                "corresponding tool. Do not ask the user for tokens, tenant ids, account "
                "context, or workspace selection — the credentials are already attached "
                "automatically when the tool runs."
            )

        if personal_unconnected:
            sections.append(
                "Personal services the user could connect but has NOT yet authorized:\n" +
                _name_lines(personal_unconnected) +
                "\nIf the user asks for something one of these would provide, tell them: "
                "\"You'd need to connect that service first — go to My Connections in the "
                "left sidebar and click Connect.\" Do not attempt to use these services until "
                "the user has connected them."
            )

        body = "\n\n".join(sections)
        return f"""

## External MCP Tools

{body}

Tool names are prefixed with the server name (e.g., servername_toolname).
"""

    except Exception as e:
        logger.warning(f"Error building MCP system prompt for agent {agent_id}: {e}")
        return ""


def _build_connection_config(server_type, server_url, auth_type,
                             connection_config_json, server_id,
                             user_id: int = None) -> dict:
    """Build a connection config dict for the gateway from database fields.

    user_id is forwarded to _get_auth_headers so per-user OAuth tokens are
    looked up correctly for authorization_code grants.
    """
    if server_type == 'local':
        # Parse the stored JSON config
        config = {}
        if connection_config_json:
            try:
                config = json.loads(connection_config_json)
            except (json.JSONDecodeError, TypeError):
                pass
        env_vars = dict(config.get("env_vars", {}) or {})
        # Auto-inject identifiers so an in-repo MCP server can locate its own
        # OAuth credentials from MCPServerCredentials. User-set env_vars win.
        env_vars.setdefault("MCP_AIHUB_SERVER_ID", str(server_id))
        if os.getenv('API_KEY'):
            env_vars.setdefault("API_KEY", os.getenv('API_KEY'))
        return {
            "type": "local",
            "command": config.get("command", ""),
            "args": config.get("args", []),
            "env_vars": env_vars,
        }
    elif server_type in ('remote', 'streamable-http', 'sse'):
        # connection_config (JSON) may carry an explicit transport hint + verify_ssl
        transport_hint = None
        verify_ssl = True
        if connection_config_json:
            try:
                extra = json.loads(connection_config_json)
                transport_hint = extra.get('transport')
                if 'verify_ssl' in extra:
                    verify_ssl = bool(extra.get('verify_ssl'))
            except (json.JSONDecodeError, TypeError):
                pass

        auth_headers = _get_auth_headers(server_id, auth_type, user_id=user_id)
        return {
            "type": server_type,
            "url": server_url or "",
            "auth_headers": auth_headers,
            "transport": transport_hint,
            "verify_ssl": verify_ssl,
        }
    else:
        return {"type": server_type}


def _get_auth_headers(server_id: int, auth_type: str, user_id: int = None) -> dict:
    """Fetch and build auth headers from MCPServerCredentials table.

    For auth_type='oauth2', user_id is required for authorization_code grants
    (per-user tokens). For client_credentials it's ignored. Other auth types
    (bearer/apikey/basic/custom) are server-shared and ignore user_id.
    """
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
            # Custom headers stored directly — skip OAuth-managed keys
            for key, value in credentials.items():
                if key.startswith('oauth_'):
                    continue
                headers[key] = value
        elif auth_type == 'oauth2':
            try:
                from builder_mcp.agent_integration.oauth_manager import get_access_token
                token = get_access_token(server_id, user_id=user_id)
                if token:
                    headers['Authorization'] = f'Bearer {token}'
                else:
                    logger.warning(
                        f"OAuth2 configured for MCP server {server_id} but no token available "
                        f"for user_id={user_id} — user may need to authorize in My Connections"
                    )
            except Exception as oauth_err:
                logger.warning(
                    f"OAuth2 token acquisition failed for server {server_id} "
                    f"user_id={user_id}: {oauth_err}"
                )

        return headers

    except Exception as e:
        logger.warning(f"Error fetching auth headers for server {server_id}: {e}")
        return {}
