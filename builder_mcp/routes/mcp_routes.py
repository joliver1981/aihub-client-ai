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
from flask import Blueprint, request, jsonify, session, redirect, url_for, g
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


# Credential keys the edit form never re-displays. A blank value for one of
# these on save means "keep what is stored" — see update_server.
_SECRET_CREDENTIAL_KEYS = frozenset({'oauth_client_secret', 'client_secret', 'password', 'token', 'key'})


def _apply_available_to_users(cursor, server_id, data):
    """WI-4: persist the "Available to users on My Connections" switch when the
    payload carries it AND migration 020 has been applied. Silent no-op
    otherwise (the column-missing fallback is "visible", see
    mcp_server_visibility)."""
    from builder_mcp.agent_integration.mcp_server_visibility import (
        has_available_to_users_column, coerce_flag,
    )
    if not isinstance(data, dict) or 'available_to_users' not in data:
        return
    flag = coerce_flag(data.get('available_to_users'))
    if flag is None or not has_available_to_users_column(cursor):
        return
    cursor.execute("UPDATE MCPServers SET available_to_users = ? WHERE server_id = ?",
                   (flag, server_id))


def _read_available_to_users(cursor, server_id):
    """(effective_flag, column_present). Missing column → (True, False): the
    server IS visible today, and the UI shows why the switch is inert."""
    from builder_mcp.agent_integration.mcp_server_visibility import has_available_to_users_column
    try:
        if not has_available_to_users_column(cursor):
            return True, False
        cursor.execute("SELECT available_to_users FROM MCPServers WHERE server_id = ?", server_id)
        row = cursor.fetchone()
        return (bool(row[0]) if row else True), True
    except Exception as e:
        logger.warning(f"available_to_users read failed for server {server_id}: {e}")
        return True, False


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

        # WI-4: effective "Available to users" flag per server (fallback visible).
        try:
            from builder_mcp.agent_integration.mcp_server_visibility import has_available_to_users_column
            column_present = has_available_to_users_column(cursor)
            flags = {}
            if column_present:
                cursor.execute("SELECT server_id, available_to_users FROM MCPServers")
                flags = {r[0]: bool(r[1]) for r in cursor.fetchall()}
        except Exception as e:
            logger.warning(f"available_to_users listing read failed: {e}")
            column_present, flags = False, {}
        for server in servers:
            server['visibility_column_present'] = column_present
            server['available_to_users'] = flags.get(server['server_id'], True) if column_present else True

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
            _apply_available_to_users(cursor, server_id, data)

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
                    'oauth_redirect_uri',
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
                # Lets the edit form say whether a secret is on file — the exact
                # thing whose absence produced AADSTS7000218 at the provider.
                server['has_client_secret'] = 'oauth_client_secret' in keys

        server['available_to_users'], server['visibility_column_present'] = \
            _read_available_to_users(cursor, server_id)

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

            _apply_available_to_users(cursor, server_id, data)

            # Update credentials. Per-user OAuth runtime tokens live in
            # MCPUserTokens (separate table) so a config edit here never
            # touches them — users don't need to re-authorize on edit.
            #
            # Keep-on-blank for SECRET keys: the edit form never re-displays a
            # secret and sends '' for "leave as is". Until 2026-09 the DELETE
            # below dropped every stored row and the insert loop skipped
            # blanks — so ANY edit of an OAuth server (rename, scope, the new
            # publish switch) silently wiped its client secret, and bearer /
            # basic / API-key secrets likewise. Non-secret keys keep the
            # replace-all semantics (blank = cleared, e.g. the redirect override).
            auth_config = data.get('auth_config')
            if auth_config is not None:
                keep_keys = [k for k, v in auth_config.items()
                             if (v is None or v == '') and k in _SECRET_CREDENTIAL_KEYS]
                if keep_keys:
                    placeholders = ','.join('?' for _ in keep_keys)
                    cursor.execute(f"""
                        DELETE FROM MCPServerCredentials
                        WHERE server_id = ? AND credential_key NOT IN ({placeholders})
                    """, server_id, *keep_keys)
                else:
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
            'description': ("Outlook email and calendar for each signed-in user (their own mailbox). "
                            "Setup: replace {tenant_id} in both endpoint URLs with your Entra tenant GUID; "
                            "register the Redirect URI shown below under the app registration's Web platform; "
                            "paste the app's Client ID and a Client Secret; Save; then switch on "
                            "\"Available to users on My Connections\". Users connect themselves from My Connections."),
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
#
# Redirect model (docs/my-connections-oauth-broker-handoff.md):
#
#   REGISTERED redirect URI — what the provider is told, what the customer's IT
#       registers in the app registration (Entra: the Web platform), and what
#       the token exchange MUST repeat verbatim. A stable HTTPS endpoint on the
#       cloud API (the "broker"). Resolution: per-server credential key
#       `oauth_redirect_uri` → OAUTH_REDIRECT_BASE_URL → AI_HUB_API_URL → hard
#       default, each joined with /api/mcp/oauth/callback.
#
#   RETURN ADDRESS — this install's own callback on the origin the user is
#       browsing. It travels inside the signed `state`; the broker verifies the
#       HMAC (tenant API key) and 302s the browser back here. Host-derivation is
#       exactly right for the return address: it guarantees the user lands on
#       the origin whose session cookie holds the PKCE verifier.
#
#   SELF-BROKER — if this callback is reached on a different origin than the
#       return address (the localhost pin used in testing, or a per-server
#       override pointing at an on-prem TLS host), it performs the same signed
#       bounce itself. No cloud round trip is needed in that configuration.

DEFAULT_OAUTH_REDIRECT_BASE_URL = 'https://ai-hub-api.azurewebsites.net'
OAUTH_CALLBACK_PATH = '/api/mcp/oauth/callback'
OAUTH_VERIFY_PATH = '/api/mcp/oauth/verify'
OAUTH_STATE_TTL_SECONDS = 600
_OAUTH_SESSION_PREFIX = 'mcp_oauth_state_'
_OAUTH_SESSION_MAX_PENDING = 3
_TENANT_ID_CACHE_TTL = 300
_tenant_id_cache = {'id': 0, 'at': 0.0}


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == '':
        return default
    return raw.strip().lower() in ('1', 'true', 'yes', 'on')


def _oauth_return_address() -> str:
    """This install's callback on the origin the browser is using (see above)."""
    return url_for('mcp.oauth_callback', _external=True)


def _oauth_redirect_uri() -> str:
    """Demoted (2026-09): this is the RETURN ADDRESS, not the registered redirect
    URI. Kept under its old name so nothing that imports it breaks."""
    return _oauth_return_address()


def _oauth_registered_redirect_uri(server_id=None, cfg=None):
    """(uri, source) — the redirect URI the provider sees and IT registers.

    source ∈ {'server', 'env', 'ai_hub_api_url', 'default'}. A base URL that
    already ends with the callback path is accepted as-is.
    """
    if cfg is None and server_id is not None:
        try:
            from builder_mcp.agent_integration.oauth_manager import _load_server_config
            cfg = _load_server_config(server_id)
        except Exception as e:
            logger.warning(f"Could not load OAuth config for server {server_id}: {e}")
            cfg = {}
    override = ((cfg or {}).get('oauth_redirect_uri') or '').strip()
    if override:
        return override, 'server'
    for env_name, source in (('OAUTH_REDIRECT_BASE_URL', 'env'),
                             ('AI_HUB_API_URL', 'ai_hub_api_url')):
        base = (os.getenv(env_name) or '').strip().rstrip('/')
        if base:
            if base.endswith(OAUTH_CALLBACK_PATH):
                return base, source
            return base + OAUTH_CALLBACK_PATH, source
    return DEFAULT_OAUTH_REDIRECT_BASE_URL + OAUTH_CALLBACK_PATH, 'default'


def _resolve_tenant_id() -> int:
    """The cloud-side tenant id the broker uses to find our API key.

    1. Local DB: SESSION_CONTEXT after sp_setTenantContext — seeded from the
       cloud registration at install time; no network.
    2. Cloud: agent_email_routes.get_numeric_tenant_id() (cached 5 min) when
       the local row is missing/stale (the on-prem "tenant context NULL" case).
    A wrong id fails CLOSED at the broker (signature mismatch) — the admin
    self-test at /api/mcp/oauth/broker_check makes that visible before any
    user clicks Connect.
    """
    import time as _time
    now = _time.time()
    if _tenant_id_cache['id'] and now - _tenant_id_cache['at'] < _TENANT_ID_CACHE_TTL:
        return _tenant_id_cache['id']
    tenant_id = 0
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            cursor.execute("SELECT CAST(SESSION_CONTEXT(N'TenantId') AS INT)")
            row = cursor.fetchone()
            tenant_id = int(row[0]) if row and row[0] is not None else 0
            cursor.close()
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"Local tenant id lookup failed: {e}")
    if not tenant_id:
        try:
            from agent_email_routes import get_numeric_tenant_id
            tenant_id = int(get_numeric_tenant_id() or 0)
        except Exception as e:
            logger.warning(f"Cloud tenant id lookup failed: {e}")
    if tenant_id:
        _tenant_id_cache['id'] = tenant_id
        _tenant_id_cache['at'] = now
    return tenant_id


def _oauth_page(title: str, message: str, status: int = 400,
                close_hint: bool = True, auto_close: bool = False):
    """Plain, escaped HTML for the popup/tab the flow runs in.

    Never reflects request data unescaped — the previous callback did, via
    <pre>{error}</pre>, which was a reflected XSS on ?error=.
    """
    from markupsafe import escape
    body = (
        "<html><head><meta charset='utf-8'><title>{t}</title></head>"
        "<body style='font-family:sans-serif;padding:2rem;max-width:40rem;'>"
        "<h3>{t}</h3><p style='white-space:pre-wrap;'>{m}</p>{c}{s}</body></html>"
    ).format(
        t=escape(title), m=escape(message),
        c="<p style='color:#666;'>You can close this window.</p>" if close_hint else '',
        s="<script>setTimeout(function(){window.close();},1500);</script>" if auto_close else '',
    )
    return body, status, {'Content-Type': 'text/html; charset=utf-8'}


def _oauth_refusal(message: str, status: int):
    """Browser flows get an HTML page; API-key callers get JSON."""
    if getattr(g, 'auth_method', None) == 'api_key':
        return jsonify({'status': 'error', 'error': message}), status
    return _oauth_page('Connection not started', message, status)


def _session_role() -> int:
    try:
        if current_user.is_authenticated:
            return int(getattr(current_user, 'role', 0) or 0)
    except Exception:
        pass
    return 0


def _oauth_session_put(nonce: str, ctx: dict):
    """Store the pending flow under its nonce; prune stale/excess entries so
    abandoned popups cannot bloat the (client-side, ~4 KB) session cookie."""
    import time as _time
    now = int(_time.time())
    pending = []
    for key in list(session.keys()):
        if not key.startswith(_OAUTH_SESSION_PREFIX):
            continue
        entry = session.get(key)
        created = int(entry.get('created', 0) or 0) if isinstance(entry, dict) else 0
        if now - created > OAUTH_STATE_TTL_SECONDS:
            session.pop(key, None)
        else:
            pending.append((created, key))
    pending.sort()
    while len(pending) >= _OAUTH_SESSION_MAX_PENDING:
        _, oldest = pending.pop(0)
        session.pop(oldest, None)
    ctx = dict(ctx)
    ctx['created'] = now
    session[_OAUTH_SESSION_PREFIX + nonce] = ctx


@mcp_bp.route('/oauth/redirect_uri', methods=['GET'])
@api_key_or_session_required(min_role=2)
@cross_origin()
def oauth_redirect_uri():
    """What IT registers with the IdP (the broker), plus where the broker sends
    the browser back. ?server_id= honours a per-server override."""
    server_id = request.args.get('server_id', type=int)
    uri, source = _oauth_registered_redirect_uri(server_id=server_id)
    return jsonify({
        'redirect_uri': uri,
        'source': source,
        'return_address': _oauth_return_address(),
        'tenant_id': _resolve_tenant_id(),
        'platform_note': 'Register it under the Web platform (confidential client), '
                         'not "Mobile and desktop".',
    })


@mcp_bp.route('/oauth/broker_check', methods=['GET'])
@api_key_or_session_required(min_role=2)
@cross_origin()
def oauth_broker_check():
    """Admin self-test: sign a state exactly as Connect would and ask the broker
    to verify it (no redirect). Proves tenant id + API key + return address
    agree end to end BEFORE IT registers anything or a user clicks Connect.
    Never sends or logs a code; the state is a short-lived signed blob."""
    import requests as _requests
    from builder_mcp.agent_integration.oauth_state import sign_state, StateError

    server_id = request.args.get('server_id', type=int)
    uri, source = _oauth_registered_redirect_uri(server_id=server_id)
    result = {'ok': False, 'redirect_uri': uri, 'source': source,
              'return_address': _oauth_return_address()}
    if not uri.endswith(OAUTH_CALLBACK_PATH):
        result['reason'] = 'custom redirect URI — no verify endpoint to ask'
        return jsonify(result)
    verify_url = uri[:-len(OAUTH_CALLBACK_PATH)] + OAUTH_VERIFY_PATH
    result['verify_url'] = verify_url
    tenant_id = _resolve_tenant_id()
    result['tenant_id'] = tenant_id
    if not tenant_id:
        result['reason'] = "could not determine this installation's tenant id (API_KEY / AI_HUB_API_URL)"
        return jsonify(result)
    try:
        state, _ = sign_state(os.getenv('API_KEY', ''), tenant_id,
                              result['return_address'], ttl_seconds=120)
    except (StateError, ValueError) as e:
        result['reason'] = f'could not sign a state: {e}'
        return jsonify(result)
    try:
        resp = _requests.post(verify_url, json={'state': state}, timeout=15,
                              headers={'Accept': 'application/json', 'Connection': 'close'})
        is_json = (resp.headers.get('Content-Type') or '').startswith('application/json')
        data = resp.json() if is_json else {}
        result['http_status'] = resp.status_code
        result['ok'] = bool(resp.status_code == 200 and data.get('ok'))
        result['reason'] = data.get('reason') or ('verified' if result['ok'] else f'HTTP {resp.status_code}')
        if data.get('tenant_id') is not None:
            result['broker_tenant_id'] = data.get('tenant_id')
    except Exception as e:
        result['reason'] = f'broker unreachable: {e}'
    return jsonify(result)


@mcp_bp.route('/oauth/verify', methods=['POST'])
def oauth_verify():
    """Self-broker twin of the cloud verify endpoint (used by broker_check when
    the registered URI points at this install). Verifies with OUR key; never
    echoes the state. Reveals nothing beyond ok/reason."""
    from builder_mcp.agent_integration.oauth_state import (
        verify_state_with_key, StateError, origin_of,
    )
    data = request.get_json(silent=True) or {}
    state = data.get('state')
    if not isinstance(state, str):
        return jsonify({'ok': False, 'reason': 'missing state'}), 400
    try:
        payload = verify_state_with_key(state, os.getenv('API_KEY', ''))
    except StateError as e:
        return jsonify({'ok': False, 'reason': e.reason})
    return jsonify({'ok': True, 'tenant_id': payload['t'],
                    'return_origin': origin_of(payload['r'])})


@mcp_bp.route('/oauth/authorize/<int:server_id>', methods=['GET'])
@api_key_or_session_required()
def oauth_authorize(server_id):
    """Start the OAuth flow for an MCP server.

    authorization_code (per-user — My Connections): ANY signed-in user may start
    it for a server that is published to users (available_to_users); Developers
    and Admins (role ≥ 2) may also start it for an unpublished server so they
    can test before publishing. Tokens bind to current_user.id, so a user can
    only ever authorize themselves.

    client_credentials (service account, tenant-wide): role ≥ 2 as before — it
    forces a shared token fetch, genuinely an admin action. API-key callers are
    trusted (internal services).
    """
    try:
        from builder_mcp.agent_integration.oauth_manager import (
            build_authorize_url, get_access_token, _load_server_config,
        )
        from builder_mcp.agent_integration.oauth_state import sign_state, StateError
        from builder_mcp.agent_integration.mcp_server_visibility import server_available_to_users

        cfg = _load_server_config(server_id)
        grant_type = (cfg.get('oauth_grant_type') or '').lower()
        role = _session_role()
        via_api_key = getattr(g, 'auth_method', None) == 'api_key'

        if grant_type == 'client_credentials':
            if not via_api_key and role < 2:
                return jsonify({'status': 'error',
                                'error': 'Developer access required to authorize a '
                                         'service-account connection'}), 403
            try:
                token = get_access_token(server_id, user_id=None)
                return jsonify({'status': 'success', 'has_token': bool(token)})
            except Exception as e:
                return jsonify({'status': 'error', 'error': str(e)}), 400

        if grant_type != 'authorization_code':
            return _oauth_refusal(
                f"This server is not configured for a per-user OAuth flow "
                f"(grant_type={grant_type or 'none'!r}).", 400)

        if not current_user.is_authenticated:
            return _oauth_refusal('You must be logged in to connect a personal account.', 401)

        # WI-4 enforcement: an unpublished server is admin/developer-only, even by direct URL.
        if role < 2 and not server_available_to_users(server_id):
            return _oauth_refusal(
                "This connection isn't available to users yet. An administrator has to "
                "switch on \"Available to users on My Connections\" for it first.", 403)

        # Pre-flight: the confidential-client model needs a secret. Fail here, naming
        # the fix, instead of bouncing the user to the provider to fail there.
        if _env_flag('OAUTH_REQUIRE_CLIENT_SECRET', True) and not cfg.get('oauth_client_secret'):
            return _oauth_refusal(
                'This server has no client secret configured; an administrator must add one '
                'on the MCP Servers page (edit the server → Client Secret → Save).', 409)
        if not cfg.get('oauth_auth_endpoint') or not cfg.get('oauth_client_id'):
            return _oauth_refusal(
                'This server is missing its Authorization Endpoint or Client ID; an '
                'administrator must complete the OAuth settings on the MCP Servers page.', 409)

        tenant_id = _resolve_tenant_id()
        if not tenant_id:
            return _oauth_refusal("Could not determine this installation's tenant id "
                                  "(check API_KEY and AI_HUB_API_URL).", 500)

        # PKCE
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).decode().rstrip('=')

        return_address = _oauth_return_address()
        try:
            state, nonce = sign_state(os.getenv('API_KEY', ''), tenant_id, return_address,
                                      ttl_seconds=OAUTH_STATE_TTL_SECONDS)
        except (StateError, ValueError) as e:
            return _oauth_refusal(
                f'Could not build the OAuth state for return address {return_address}: {e}', 500)

        _oauth_session_put(nonce, {
            'server_id': server_id,
            'user_id': int(current_user.id),
            'code_verifier': verifier,
        })

        registered_uri, source = _oauth_registered_redirect_uri(cfg=cfg)
        url = build_authorize_url(server_id, registered_uri, state, code_challenge=challenge)
        logger.info(f"OAuth authorize: server={server_id} user={current_user.id} tenant={tenant_id} "
                    f"redirect_uri={registered_uri} ({source}) return={return_address}")
        return redirect(url)

    except Exception as e:
        logger.error(f"Error starting OAuth authorize for server {server_id}: {e}", exc_info=True)
        return _oauth_refusal(f'Could not start the authorization flow: {e}', 500)


@mcp_bp.route('/oauth/callback', methods=['GET'])
def oauth_callback():
    """OAuth 2.0 authorization-code redirect handler — reached via the broker's
    302, or directly when the registered URI is this install.

    No role decorator: the provider/broker redirects the browser here and the
    session cookie carries the user. The controls are the signed state (HMAC
    over this install's API key) and the per-nonce session entry holding the
    PKCE verifier; a replayed or foreign response matches neither.
    """
    try:
        from builder_mcp.agent_integration.oauth_manager import exchange_authorization_code
        from builder_mcp.agent_integration.oauth_state import (
            verify_state_with_key, StateError, origin_of, append_query,
        )

        error = request.args.get('error')
        if error:
            desc = request.args.get('error_description', '')
            logger.warning(f"OAuth callback: provider error {error!r}: {desc[:200]!r}")
            return _oauth_page('Authorization failed', f"{error}: {desc}".strip(': '), 400)

        code = request.args.get('code')
        state = request.args.get('state')
        if not code or not state:
            return _oauth_page('OAuth callback incomplete',
                               'The response from the provider is missing "code" or "state". '
                               'Re-initiate the flow from My Connections.', 400)

        try:
            payload = verify_state_with_key(state, os.getenv('API_KEY', ''))
        except StateError as e:
            logger.warning(f"OAuth callback: state refused ({e.reason})")
            return _oauth_page('OAuth state invalid',
                               'The authorization response could not be verified. '
                               'Re-initiate the flow from My Connections.', 400)

        # Self-broker: landed on a different origin than the one the flow started on.
        here, there = origin_of(request.host_url), origin_of(payload['r'])
        if here != there:
            logger.info(f"OAuth callback: bouncing {here} -> {there}")
            return redirect(append_query(payload['r'], {'code': code, 'state': state}), code=302)

        ctx = session.pop(_OAUTH_SESSION_PREFIX + payload['n'], None)
        if not ctx:
            return _oauth_page('OAuth state mismatch',
                               'This browser has no pending authorization for that response — it '
                               'may have expired, or the flow was started on a different address. '
                               'Re-initiate the flow from My Connections.', 400)

        server_id = ctx['server_id']
        user_id = ctx.get('user_id')
        verifier = ctx.get('code_verifier')
        if not user_id:
            return _oauth_page('OAuth callback missing user context',
                               'Re-initiate the flow from My Connections.', 400)

        # The token exchange must repeat the REGISTERED redirect URI exactly —
        # not the return address. (The most likely bug in this whole change.)
        registered_uri, source = _oauth_registered_redirect_uri(server_id=server_id)
        token = exchange_authorization_code(
            server_id=server_id,
            user_id=user_id,
            code=code,
            redirect_uri=registered_uri,
            code_verifier=verifier,
        )

        if token:
            logger.info(f"OAuth callback: tokens stored server={server_id} user={user_id} "
                        f"(redirect_uri source={source})")
            return _oauth_page('✔ Connected',
                               'Your account is now connected. You can close this window and '
                               'return to My Connections.', 200, close_hint=False, auto_close=True)
        return _oauth_page('Token exchange returned no access token',
                           'The provider accepted the authorization but returned no access token. '
                           "Ask an administrator to check the server's OAuth settings.", 500)

    except Exception as e:
        logger.error(f"OAuth callback error: {e}", exc_info=True)
        return _oauth_page('OAuth callback error', str(e), 500)


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
