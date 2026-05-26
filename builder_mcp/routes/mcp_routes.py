"""
MCP Server Management Routes
Flask Blueprint providing REST API for MCP server CRUD and gateway actions.

CRUD operations (list, create, update, delete) hit the DATABASE directly.
Action operations (test, list tools, call tool) proxy to the GATEWAY service.
OAuth actions (authorize, callback) live here too — refresh/exchange runs server-side.
"""
import os
import json
import secrets
import hashlib
import base64
import logging
from urllib.parse import urlparse
from flask import Blueprint, request, jsonify, session, redirect, url_for
from flask_login import login_required, current_user
from flask_cors import cross_origin
from role_decorators import api_key_or_session_required
from CommonUtils import get_db_connection

logger = logging.getLogger(__name__)

mcp_bp = Blueprint('mcp', __name__, url_prefix='/api/mcp')


# ============================================================================
# Helper: get encryption key
# ============================================================================

def _get_encryption_key():
    """Get encryption key for credential storage"""
    try:
        from encrypt import ENCRYPTION_KEY
        return os.environ.get('MCP_ENCRYPTION_KEY', ENCRYPTION_KEY)
    except ImportError:
        return os.environ.get('MCP_ENCRYPTION_KEY', 'default_key')


def _get_gateway_client():
    """Get a lazy-initialized MCPGatewayClient instance"""
    from builder_mcp.client.mcp_gateway_client import MCPGatewayClient
    return MCPGatewayClient()


def _graph_stdio_script_path() -> str:
    """Absolute path to the in-repo Graph stdio MCP server.

    Used as a directory-entry default. Avoids the `-m` invocation which would
    require the launching Python to already have the repo root on sys.path.
    """
    # __file__ is .../builder_mcp/routes/mcp_routes.py — three dirname()s lands
    # at the repo root.
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    return os.path.join(repo_root, 'builder_mcp', 'servers', 'graph_stdio_server.py')


def _internal_graph_url() -> str:
    """URL the MCP gateway calls back to reach our in-process Graph MCP endpoint.

    Always loopback to the main app's HOST_PORT.
    """
    port = os.getenv('HOST_PORT', '5001')
    return f"http://127.0.0.1:{port}/api/internal/mcp/graph"


# ============================================================================
# Server CRUD — hit DATABASE directly
# ============================================================================

@mcp_bp.route('/servers', methods=['GET'])
@api_key_or_session_required(min_role=2)
@cross_origin()
def list_servers():
    """List all MCP servers for the current tenant"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        cursor.execute("""
            SELECT
                ms.server_id,
                ms.server_name,
                ms.server_type,
                ms.server_url,
                ms.auth_type,
                ms.connection_config,
                ms.description,
                ms.category,
                ms.icon,
                ms.enabled,
                ms.created_by,
                ms.created_date,
                ms.last_tested_date,
                ms.last_test_status,
                ms.tool_count,
                ms.request_timeout,
                ms.max_retries,
                ms.verify_ssl,
                (SELECT COUNT(*) FROM AgentMCPServers ams
                 WHERE ams.server_id = ms.server_id AND ams.enabled = 1) as agent_count
            FROM MCPServers ms
            ORDER BY ms.server_type DESC, ms.server_name
        """)

        servers = []
        for row in cursor.fetchall():
            server = {
                'server_id': row[0],
                'server_name': row[1],
                'server_type': row[2],
                'server_url': row[3],
                'auth_type': row[4],
                'connection_config': row[5],
                'description': row[6],
                'category': row[7],
                'icon': row[8],
                'enabled': row[9],
                'created_by': row[10],
                'created_date': row[11].isoformat() if row[11] else None,
                'last_tested_date': row[12].isoformat() if row[12] else None,
                'last_test_status': row[13],
                'tool_count': row[14],
                'request_timeout': row[15],
                'max_retries': row[16],
                'verify_ssl': row[17],
                'agent_count': row[18]
            }

            # Parse connection_config for convenience
            if server['connection_config']:
                try:
                    config = json.loads(server['connection_config'])
                    if server['server_type'] == 'local':
                        server['command'] = config.get('command')
                        server['args'] = config.get('args', [])
                    else:
                        server['transport'] = config.get('transport')
                except (json.JSONDecodeError, TypeError):
                    pass

            servers.append(server)

        cursor.close()
        conn.close()
        return jsonify(servers)

    except Exception as e:
        logger.error(f"Error listing MCP servers: {e}")
        return jsonify({'error': str(e)}), 500


@mcp_bp.route('/servers', methods=['POST'])
@api_key_or_session_required(min_role=2)
@cross_origin()
def create_server():
    """Create a new MCP server configuration"""
    try:
        data = request.json
        server_type = data.get('server_type', 'local')

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        if server_type in ('remote', 'streamable-http', 'sse'):
            auth_config = data.get('auth_config', {}) or {}
            # DB has a CHECK constraint allowing only 'local' or 'remote' — store the
            # actual transport choice in connection_config JSON instead.
            transport = data.get('transport')
            if not transport and server_type in ('streamable-http', 'sse'):
                transport = server_type
            connection_config_json = json.dumps({
                'transport': transport,
                'verify_ssl': data.get('verify_ssl', True),
            })
            cursor.execute("""
                INSERT INTO MCPServers (
                    server_name, server_type, server_url, auth_type, connection_config,
                    description, category, icon, enabled, created_by, created_date,
                    request_timeout, max_retries, verify_ssl
                )
                OUTPUT INSERTED.server_id
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, getutcdate(), ?, ?, ?)
            """, (
                data.get('server_name'),
                'remote',
                data.get('server_url'),
                data.get('auth_type', 'none'),
                connection_config_json,
                data.get('description', ''),
                data.get('category', ''),
                data.get('icon', ''),
                1,
                session.get('user_email', 'unknown'),
                data.get('request_timeout', 30),
                data.get('max_retries', 3),
                data.get('verify_ssl', True)
            ))

            server_id = cursor.fetchone()[0]

            # Store auth credentials encrypted. Strip empty values to avoid clobbering.
            if auth_config:
                encryption_key = _get_encryption_key()
                for key, value in auth_config.items():
                    if value is None or value == '':
                        continue
                    cursor.execute("""
                        INSERT INTO MCPServerCredentials (server_id, credential_key, credential_value)
                        VALUES (?, ?, ENCRYPTBYPASSPHRASE(?, CAST(? AS NVARCHAR(MAX))))
                    """, (server_id, key, encryption_key, str(value)))
        else:
            # Local server
            connection_config = {
                'command': data.get('command'),
                'args': data.get('args', []),
                'env_vars': data.get('env_vars', {})
            }
            cursor.execute("""
                INSERT INTO MCPServers (
                    server_name, server_type, connection_config,
                    description, category, icon, enabled, created_by, created_date
                )
                OUTPUT INSERTED.server_id
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, getutcdate())
            """, (
                data.get('server_name'),
                'local',
                json.dumps(connection_config),
                data.get('description', ''),
                data.get('category', ''),
                data.get('icon', ''),
                1,
                session.get('user_email', 'unknown')
            ))

            server_id = cursor.fetchone()[0]

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            'status': 'success',
            'server_id': server_id,
            'message': 'MCP server created successfully'
        })

    except Exception as e:
        logger.error(f"Error creating MCP server: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@mcp_bp.route('/servers/<int:server_id>', methods=['GET'])
@api_key_or_session_required(min_role=2)
@cross_origin()
def get_server(server_id):
    """Get a single MCP server configuration"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        cursor.execute("""
            SELECT server_id, server_name, server_type, server_url, auth_type,
                   connection_config, description, category, icon, enabled,
                   created_by, created_date, last_tested_date, last_test_status,
                   tool_count, request_timeout, max_retries, verify_ssl
            FROM MCPServers
            WHERE server_id = ?
        """, server_id)

        row = cursor.fetchone()
        if not row:
            cursor.close()
            conn.close()
            return jsonify({'error': 'Server not found'}), 404

        server = {
            'server_id': row[0],
            'server_name': row[1],
            'server_type': row[2],
            'server_url': row[3],
            'auth_type': row[4],
            'connection_config': row[5],
            'description': row[6],
            'category': row[7],
            'icon': row[8],
            'enabled': row[9],
            'created_by': row[10],
            'created_date': row[11].isoformat() if row[11] else None,
            'last_tested_date': row[12].isoformat() if row[12] else None,
            'last_test_status': row[13],
            'tool_count': row[14],
            'request_timeout': row[15],
            'max_retries': row[16],
            'verify_ssl': row[17]
        }

        # Parse connection config
        if server['connection_config']:
            try:
                config = json.loads(server['connection_config'])
                if server['server_type'] == 'local':
                    server['command'] = config.get('command')
                    server['args'] = config.get('args', [])
                    server['env_vars'] = config.get('env_vars', {})
                else:
                    server['transport'] = config.get('transport')
            except (json.JSONDecodeError, TypeError):
                pass

        # Get credential keys (not values) for remote servers, plus OAuth readiness.
        # For OAuth servers, also decrypt and return the non-secret config fields
        # (endpoints, scope, client_id, grant type, audience) so the edit form
        # can repopulate them. Secret fields (client_secret, access/refresh token)
        # are never returned to the browser.
        if server['server_type'] in ('remote', 'streamable-http', 'sse'):
            cursor.execute("""
                SELECT credential_key
                FROM MCPServerCredentials
                WHERE server_id = ?
            """, server_id)
            keys = [r[0] for r in cursor.fetchall()]
            server['credential_keys'] = keys
            if server.get('auth_type') == 'oauth2':
                # Per-user authorization now lives in MCPUserTokens. Report
                # whether the CURRENT user has authorized (used by the edit
                # modal to label the Authorize button) and how many users
                # have authorized in total (for the admin overview).
                from builder_mcp.agent_integration.oauth_manager import has_user_token
                this_user_authorized = False
                if current_user.is_authenticated:
                    try:
                        this_user_authorized = has_user_token(server_id, int(current_user.id))
                    except Exception:
                        pass
                server['oauth_authorized'] = this_user_authorized
                # How many distinct users have a token row?
                try:
                    cursor.execute("""
                        SELECT COUNT(DISTINCT user_id) FROM MCPUserTokens
                        WHERE server_id = ? AND user_id <> 0
                    """, server_id)
                    server['oauth_user_count'] = int(cursor.fetchone()[0] or 0)
                except Exception:
                    server['oauth_user_count'] = 0
                encryption_key = _get_encryption_key()
                non_secret_keys = (
                    'oauth_grant_type', 'oauth_token_endpoint', 'oauth_auth_endpoint',
                    'oauth_scope', 'oauth_client_id', 'oauth_audience',
                )
                placeholders = ','.join('?' for _ in non_secret_keys)
                cursor.execute(f"""
                    SELECT credential_key,
                           CONVERT(NVARCHAR(MAX), DECRYPTBYPASSPHRASE(?, credential_value)) as v
                    FROM MCPServerCredentials
                    WHERE server_id = ? AND credential_key IN ({placeholders})
                """, encryption_key, server_id, *non_secret_keys)
                oauth_cfg = {}
                for row in cursor.fetchall():
                    if row[1] is not None:
                        oauth_cfg[row[0]] = row[1]
                server['oauth_config'] = oauth_cfg

        cursor.close()
        conn.close()
        return jsonify(server)

    except Exception as e:
        logger.error(f"Error getting MCP server {server_id}: {e}")
        return jsonify({'error': str(e)}), 500


@mcp_bp.route('/servers/<int:server_id>', methods=['PUT'])
@api_key_or_session_required(min_role=2)
@cross_origin()
def update_server(server_id):
    """Update an existing MCP server configuration"""
    try:
        data = request.json
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        # Verify server exists
        cursor.execute("SELECT server_id, server_type FROM MCPServers WHERE server_id = ?", server_id)
        row = cursor.fetchone()
        if not row:
            cursor.close()
            conn.close()
            return jsonify({'error': 'Server not found'}), 404

        server_type = data.get('server_type', row[1])

        if server_type in ('remote', 'streamable-http', 'sse'):
            transport = data.get('transport')
            if not transport and server_type in ('streamable-http', 'sse'):
                transport = server_type
            connection_config_json = json.dumps({
                'transport': transport,
                'verify_ssl': data.get('verify_ssl', True),
            })
            cursor.execute("""
                UPDATE MCPServers
                SET server_name = ?, server_type = ?, server_url = ?, auth_type = ?,
                    connection_config = ?,
                    description = ?, category = ?, icon = ?,
                    request_timeout = ?, max_retries = ?, verify_ssl = ?
                WHERE server_id = ?
            """, (
                data.get('server_name'),
                'remote',
                data.get('server_url'),
                data.get('auth_type', 'none'),
                connection_config_json,
                data.get('description', ''),
                data.get('category', ''),
                data.get('icon', ''),
                data.get('request_timeout', 30),
                data.get('max_retries', 3),
                data.get('verify_ssl', True),
                server_id
            ))

            # Update credentials. Per-user OAuth runtime tokens live in
            # MCPUserTokens (separate table) so a config edit here never
            # touches them — users don't need to re-authorize on edit.
            auth_config = data.get('auth_config')
            if auth_config is not None:
                cursor.execute("""
                    DELETE FROM MCPServerCredentials
                    WHERE server_id = ?
                """, server_id)
                encryption_key = _get_encryption_key()
                for key, value in auth_config.items():
                    if value is None or value == '':
                        continue
                    cursor.execute("""
                        INSERT INTO MCPServerCredentials (server_id, credential_key, credential_value)
                        VALUES (?, ?, ENCRYPTBYPASSPHRASE(?, CAST(? AS NVARCHAR(MAX))))
                    """, (server_id, key, encryption_key, str(value)))
        else:
            connection_config = {
                'command': data.get('command'),
                'args': data.get('args', []),
                'env_vars': data.get('env_vars', {})
            }
            cursor.execute("""
                UPDATE MCPServers
                SET server_name = ?, server_type = ?, connection_config = ?,
                    description = ?, category = ?, icon = ?
                WHERE server_id = ?
            """, (
                data.get('server_name'),
                'local',
                json.dumps(connection_config),
                data.get('description', ''),
                data.get('category', ''),
                data.get('icon', ''),
                server_id
            ))

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({'status': 'success', 'message': 'MCP server updated'})

    except Exception as e:
        logger.error(f"Error updating MCP server {server_id}: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@mcp_bp.route('/servers/<int:server_id>', methods=['DELETE'])
@api_key_or_session_required(min_role=2)
@cross_origin()
def delete_server(server_id):
    """Delete an MCP server configuration"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        # Verify server exists
        cursor.execute("SELECT server_id FROM MCPServers WHERE server_id = ?", server_id)
        if not cursor.fetchone():
            cursor.close()
            conn.close()
            return jsonify({'error': 'Server not found'}), 404

        # Delete related records first
        cursor.execute("DELETE FROM AgentMCPServers WHERE server_id = ?", server_id)
        cursor.execute("DELETE FROM MCPServerCredentials WHERE server_id = ?", server_id)
        cursor.execute("DELETE FROM MCPServers WHERE server_id = ?", server_id)

        conn.commit()
        cursor.close()
        conn.close()

        # Try to disconnect from gateway
        try:
            gateway = _get_gateway_client()
            gateway.disconnect_server(server_id)
        except Exception:
            pass  # Gateway disconnect is best-effort

        return jsonify({'status': 'success', 'message': 'MCP server deleted'})

    except Exception as e:
        logger.error(f"Error deleting MCP server {server_id}: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ============================================================================
# Server Actions — proxy to GATEWAY service
# ============================================================================

@mcp_bp.route('/test', methods=['POST'])
@api_key_or_session_required(min_role=2)
@cross_origin()
def test_config():
    """Test a server configuration directly (before saving).
    Accepts config in request body, proxies to gateway.
    """
    try:
        data = request.json
        gateway = _get_gateway_client()
        result = gateway.test_server(data)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error testing MCP config: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500


@mcp_bp.route('/servers/<int:server_id>/test', methods=['POST'])
@api_key_or_session_required(min_role=2)
@cross_origin()
def test_server(server_id):
    """Test an MCP server connection via the gateway"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        cursor.execute("""
            SELECT server_type, server_url, auth_type, connection_config
            FROM MCPServers WHERE server_id = ?
        """, server_id)
        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if not row:
            return jsonify({'error': 'Server not found'}), 404

        server_type, server_url, auth_type, connection_config = row

        # Build test config
        from builder_mcp.agent_integration.mcp_agent_tools import _build_connection_config
        config = _build_connection_config(server_type, server_url, auth_type,
                                          connection_config, server_id)

        gateway = _get_gateway_client()
        result = gateway.test_server(config)

        # Update test status in database
        _update_test_status(server_id, result)

        return jsonify(result)

    except Exception as e:
        logger.error(f"Error testing MCP server {server_id}: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500


@mcp_bp.route('/servers/<int:server_id>/tools', methods=['GET'])
@api_key_or_session_required(min_role=2)
@cross_origin()
def get_server_tools(server_id):
    """List tools from a connected MCP server via the gateway"""
    try:
        gateway = _get_gateway_client()

        # First check if server is connected; if not, connect it
        status = gateway.get_server_status(server_id)
        if status.get('status') != 'connected':
            # Get config from DB and connect
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            cursor.execute("""
                SELECT server_type, server_url, auth_type, connection_config
                FROM MCPServers WHERE server_id = ?
            """, server_id)
            row = cursor.fetchone()
            cursor.close()
            conn.close()

            if not row:
                return jsonify({'error': 'Server not found'}), 404

            server_type, server_url, auth_type, connection_config = row
            from builder_mcp.agent_integration.mcp_agent_tools import _build_connection_config
            config = _build_connection_config(server_type, server_url, auth_type,
                                              connection_config, server_id)
            connect_result = gateway.connect_server(server_id, config)
            if connect_result.get('status') == 'error':
                return jsonify(connect_result), 500

        tools = gateway.list_tools(server_id)
        return jsonify({'server_id': server_id, 'tools': tools, 'tool_count': len(tools)})

    except Exception as e:
        logger.error(f"Error listing tools for server {server_id}: {e}")
        return jsonify({'error': str(e)}), 500


@mcp_bp.route('/servers/<int:server_id>/tools/call', methods=['POST'])
@api_key_or_session_required(min_role=2)
@cross_origin()
def call_server_tool(server_id):
    """Call a tool on a connected MCP server (for UI testing)"""
    try:
        data = request.json
        tool_name = data.get('tool_name')
        arguments = data.get('arguments', {})

        if not tool_name:
            return jsonify({'error': 'tool_name is required'}), 400

        gateway = _get_gateway_client()
        result = gateway.call_tool(server_id, tool_name, arguments)
        return jsonify(result)

    except Exception as e:
        logger.error(f"Error calling tool on server {server_id}: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500


# ============================================================================
# Agent Assignments — hit DATABASE directly
# ============================================================================

@mcp_bp.route('/servers/<int:server_id>/agents', methods=['GET'])
@api_key_or_session_required(min_role=2)
@cross_origin()
def get_server_agents(server_id):
    """Get agents assigned to an MCP server"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        cursor.execute("""
            SELECT ams.agent_id, ams.enabled, ams.added_date
            FROM AgentMCPServers ams
            WHERE ams.server_id = ?
        """, server_id)

        agents = []
        for row in cursor.fetchall():
            agents.append({
                'agent_id': row[0],
                'enabled': row[1],
                'added_date': row[2].isoformat() if row[2] else None,
            })

        cursor.close()
        conn.close()
        return jsonify(agents)

    except Exception as e:
        logger.error(f"Error getting agents for server {server_id}: {e}")
        return jsonify({'error': str(e)}), 500


@mcp_bp.route('/servers/<int:server_id>/agents', methods=['POST'])
@api_key_or_session_required(min_role=2)
@cross_origin()
def update_server_agents(server_id):
    """Update agent assignments for an MCP server"""
    try:
        data = request.json
        agent_ids = data.get('agent_ids', [])

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        # Verify server exists
        cursor.execute("SELECT server_id FROM MCPServers WHERE server_id = ?", server_id)
        if not cursor.fetchone():
            cursor.close()
            conn.close()
            return jsonify({'error': 'Server not found'}), 404

        # Remove existing assignments
        cursor.execute("DELETE FROM AgentMCPServers WHERE server_id = ?", server_id)

        # Add new assignments
        for agent_id in agent_ids:
            cursor.execute("""
                INSERT INTO AgentMCPServers (agent_id, server_id, enabled)
                VALUES (?, ?, 1)
            """, (agent_id, server_id))

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            'status': 'success',
            'message': f'Updated assignments: {len(agent_ids)} agents'
        })

    except Exception as e:
        logger.error(f"Error updating agents for server {server_id}: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ============================================================================
# Server Directory
# ============================================================================

@mcp_bp.route('/directory', methods=['GET'])
@api_key_or_session_required(min_role=2)
@cross_origin()
def get_server_directory():
    """Get directory of known MCP server templates"""
    directory = [
        {
            'name': 'Microsoft Learn',
            'category': 'Development',
            'server_type': 'streamable-http',
            'transport': 'streamable-http',
            'url_template': 'https://learn.microsoft.com/api/mcp',
            'auth_type': 'none',
            'description': 'Search Microsoft Learn documentation, code samples and reference content (no auth required).',
            'provider': 'Microsoft'
        },
        {
            'name': 'Microsoft 365',
            'category': 'Productivity',
            'server_type': 'streamable-http',
            'transport': 'streamable-http',
            'url_template': _internal_graph_url(),
            'auth_type': 'oauth2',
            'oauth_defaults': {
                'oauth_grant_type': 'authorization_code',
                'oauth_token_endpoint': 'https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token',
                'oauth_auth_endpoint': 'https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize',
                'oauth_scope': 'User.Read Mail.Read Mail.Send Calendars.Read offline_access',
            },
            'description': "Outlook email and calendar access for the signed-in user. Setup: replace the tenant id placeholder in both endpoint URLs with your Entra tenant GUID, paste your Azure app's Client ID and Client Secret, Save, then click Authorize.",
            'provider': 'Microsoft'
        },
        {
            'name': 'Microsoft Graph (OAuth)',
            'category': 'Productivity',
            'server_type': 'streamable-http',
            'transport': 'streamable-http',
            'url_template': 'https://graph.microsoft.com/mcp',
            'auth_type': 'oauth2',
            'oauth_defaults': {
                'oauth_grant_type': 'client_credentials',
                'oauth_token_endpoint': 'https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token',
                'oauth_auth_endpoint': 'https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize',
                'oauth_scope': 'https://graph.microsoft.com/.default'
            },
            'description': 'Microsoft 365 / Graph API via OAuth 2.0. Replace {tenant_id} and add your app registration client_id and client_secret.',
            'provider': 'Microsoft'
        },
        {
            'name': 'Salesforce CRM',
            'category': 'CRM',
            'server_type': 'remote',
            'url_template': 'https://{instance}.salesforce.com/services/mcp/v1',
            'auth_type': 'oauth2',
            'description': 'Customer relationship management',
            'provider': 'Salesforce'
        },
        {
            'name': 'GitHub',
            'category': 'Development',
            'server_type': 'remote',
            'url_template': 'https://api.github.com/mcp/v1',
            'auth_type': 'bearer',
            'description': 'Code repository and collaboration',
            'provider': 'GitHub'
        },
        {
            'name': 'Slack',
            'category': 'Communication',
            'server_type': 'remote',
            'url_template': 'https://slack.com/api/mcp/v1',
            'auth_type': 'bearer',
            'description': 'Team messaging and collaboration',
            'provider': 'Slack'
        },
    ]
    return jsonify(directory)


# ============================================================================
# OAuth 2.0 — authorize / callback
# ============================================================================

def _oauth_redirect_uri() -> str:
    """Build the redirect URI used by the authorization code flow."""
    return url_for('mcp.oauth_callback', _external=True)


@mcp_bp.route('/oauth/redirect_uri', methods=['GET'])
@api_key_or_session_required(min_role=2)
@cross_origin()
def oauth_redirect_uri():
    """Return the OAuth redirect URI for the user to register with the IdP."""
    return jsonify({'redirect_uri': _oauth_redirect_uri()})


@mcp_bp.route('/oauth/authorize/<int:server_id>', methods=['GET'])
@api_key_or_session_required(min_role=2)
def oauth_authorize(server_id):
    """Start the authorization-code flow for an MCP server. Redirects user to the IdP.

    For client_credentials grant types this endpoint just forces a token fetch
    and returns a JSON status, since no user interaction is needed.
    """
    try:
        from builder_mcp.agent_integration.oauth_manager import (
            build_authorize_url, get_access_token, _load_server_config,
        )

        cfg = _load_server_config(server_id)
        grant_type = (cfg.get('oauth_grant_type') or '').lower()

        if grant_type == 'client_credentials':
            # No user-interaction needed; just force a token fetch for the
            # service-account pseudo-user. Admin-only operation.
            try:
                token = get_access_token(server_id, user_id=None)
                return jsonify({'status': 'success', 'has_token': bool(token)})
            except Exception as e:
                return jsonify({'status': 'error', 'error': str(e)}), 400

        if grant_type != 'authorization_code':
            return jsonify({'status': 'error',
                            'error': f"server not configured for OAuth (grant_type={grant_type!r})"}), 400

        # authorization_code → per-user. Capture the currently-logged-in user
        # so the callback knows whose tokens to store.
        if not current_user.is_authenticated:
            return jsonify({'status': 'error',
                            'error': 'You must be logged in to authorize a personal connection'}), 401

        # PKCE
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).decode().rstrip('=')

        state = secrets.token_urlsafe(32)
        session_key = f'mcp_oauth_state_{state}'
        session[session_key] = {
            'server_id': server_id,
            'user_id': int(current_user.id),
            'code_verifier': verifier,
        }

        redirect_uri = _oauth_redirect_uri()
        url = build_authorize_url(server_id, redirect_uri, state, code_challenge=challenge)
        return redirect(url)

    except Exception as e:
        logger.error(f"Error starting OAuth authorize for server {server_id}: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500


@mcp_bp.route('/oauth/callback', methods=['GET'])
def oauth_callback():
    """OAuth 2.0 authorization-code redirect handler.

    Note: no role decorator — the IdP redirects the browser here and Flask's session
    cookie carries the original user. We do enforce state-token match below.
    """
    try:
        from builder_mcp.agent_integration.oauth_manager import exchange_authorization_code

        error = request.args.get('error')
        if error:
            return f"<h3>OAuth error</h3><pre>{error}: {request.args.get('error_description', '')}</pre>", 400

        code = request.args.get('code')
        state = request.args.get('state')
        if not code or not state:
            return "<h3>OAuth callback missing code or state</h3>", 400

        session_key = f'mcp_oauth_state_{state}'
        ctx = session.pop(session_key, None)
        if not ctx:
            return "<h3>OAuth state mismatch — re-initiate the authorization flow</h3>", 400

        server_id = ctx['server_id']
        user_id = ctx.get('user_id')
        verifier = ctx.get('code_verifier')
        if not user_id:
            return "<h3>OAuth callback missing user context — re-initiate the flow</h3>", 400

        token = exchange_authorization_code(
            server_id=server_id,
            user_id=user_id,
            code=code,
            redirect_uri=_oauth_redirect_uri(),
            code_verifier=verifier,
        )

        if token:
            return (
                "<html><body style='font-family:sans-serif;padding:2rem;'>"
                "<h3>&#10004; MCP server authorized</h3>"
                "<p>You can close this window and return to the MCP Servers page.</p>"
                "<script>setTimeout(function(){window.close();},1500);</script>"
                "</body></html>"
            )
        return "<h3>Token exchange returned no access_token</h3>", 500

    except Exception as e:
        logger.error(f"OAuth callback error: {e}")
        return f"<h3>OAuth callback error</h3><pre>{e}</pre>", 500


# ============================================================================
# Gateway Health
# ============================================================================

@mcp_bp.route('/gateway/health', methods=['GET'])
@api_key_or_session_required(min_role=2)
@cross_origin()
def gateway_health():
    """Check MCP Gateway service health"""
    try:
        gateway = _get_gateway_client()
        is_healthy = gateway.health_check()
        return jsonify({
            'status': 'ok' if is_healthy else 'unavailable',
            'gateway_url': gateway.base_url
        })
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500


# ============================================================================
# Helper Functions
# ============================================================================

def _update_test_status(server_id: int, result: dict):
    """Update the test status in the MCPServers table"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        status = result.get('status', 'unknown')
        tool_count = result.get('tool_count', 0) if status == 'success' else 0

        cursor.execute("""
            UPDATE MCPServers
            SET last_tested_date = getutcdate(),
                last_test_status = ?,
                tool_count = ?
            WHERE server_id = ?
        """, (status, tool_count, server_id))

        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.warning(f"Failed to update test status for server {server_id}: {e}")
