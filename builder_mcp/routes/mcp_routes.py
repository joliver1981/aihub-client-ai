"""
MCP Server Management Routes
Flask Blueprint providing REST API for MCP server CRUD and gateway actions.

CRUD operations (list, create, update, delete) hit the DATABASE directly.
Action operations (test, list tools, call tool) proxy to the GATEWAY service.
"""
import os
import json
import logging
from flask import Blueprint, request, jsonify, session
from flask_login import login_required
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

            # For local servers, parse connection_config for convenience
            if server['server_type'] == 'local' and server['connection_config']:
                try:
                    config = json.loads(server['connection_config'])
                    server['command'] = config.get('command')
                    server['args'] = config.get('args', [])
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

        if server_type == 'remote':
            auth_config = data.get('auth_config', {})
            cursor.execute("""
                INSERT INTO MCPServers (
                    server_name, server_type, server_url, auth_type,
                    description, category, icon, enabled, created_by, created_date,
                    request_timeout, max_retries, verify_ssl
                )
                OUTPUT INSERTED.server_id
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, getutcdate(), ?, ?, ?)
            """, (
                data.get('server_name'),
                'remote',
                data.get('server_url'),
                data.get('auth_type', 'none'),
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

            # Store auth credentials encrypted
            if auth_config:
                encryption_key = _get_encryption_key()
                for key, value in auth_config.items():
                    cursor.execute("""
                        INSERT INTO MCPServerCredentials (server_id, credential_key, credential_value)
                        VALUES (?, ?, ENCRYPTBYPASSPHRASE(?, ?))
                    """, (server_id, key, encryption_key, value))
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

        # Parse local config
        if server['server_type'] == 'local' and server['connection_config']:
            try:
                config = json.loads(server['connection_config'])
                server['command'] = config.get('command')
                server['args'] = config.get('args', [])
                server['env_vars'] = config.get('env_vars', {})
            except (json.JSONDecodeError, TypeError):
                pass

        # Get credential keys (not values) for remote servers
        if server['server_type'] == 'remote':
            cursor.execute("""
                SELECT credential_key
                FROM MCPServerCredentials
                WHERE server_id = ?
            """, server_id)
            server['credential_keys'] = [r[0] for r in cursor.fetchall()]

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

        if server_type == 'remote':
            cursor.execute("""
                UPDATE MCPServers
                SET server_name = ?, server_type = ?, server_url = ?, auth_type = ?,
                    description = ?, category = ?, icon = ?,
                    request_timeout = ?, max_retries = ?, verify_ssl = ?
                WHERE server_id = ?
            """, (
                data.get('server_name'),
                'remote',
                data.get('server_url'),
                data.get('auth_type', 'none'),
                data.get('description', ''),
                data.get('category', ''),
                data.get('icon', ''),
                data.get('request_timeout', 30),
                data.get('max_retries', 3),
                data.get('verify_ssl', True),
                server_id
            ))

            # Update credentials
            auth_config = data.get('auth_config')
            if auth_config is not None:
                cursor.execute("DELETE FROM MCPServerCredentials WHERE server_id = ?", server_id)
                encryption_key = _get_encryption_key()
                for key, value in auth_config.items():
                    cursor.execute("""
                        INSERT INTO MCPServerCredentials (server_id, credential_key, credential_value)
                        VALUES (?, ?, ENCRYPTBYPASSPHRASE(?, ?))
                    """, (server_id, key, encryption_key, value))
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
            SELECT ams.agent_id, ams.enabled, ams.assigned_date, ams.assigned_by
            FROM AgentMCPServers ams
            WHERE ams.server_id = ?
        """, server_id)

        agents = []
        for row in cursor.fetchall():
            agents.append({
                'agent_id': row[0],
                'enabled': row[1],
                'assigned_date': row[2].isoformat() if row[2] else None,
                'assigned_by': row[3]
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
                INSERT INTO AgentMCPServers (agent_id, server_id, enabled, assigned_date, assigned_by)
                VALUES (?, ?, 1, getutcdate(), ?)
            """, (agent_id, server_id, session.get('user_email', 'unknown')))

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
            'name': 'Salesforce CRM',
            'category': 'CRM',
            'url_template': 'https://{instance}.salesforce.com/services/mcp/v1',
            'auth_type': 'oauth2',
            'description': 'Customer relationship management',
            'provider': 'Salesforce'
        },
        {
            'name': 'SAP ERP',
            'category': 'ERP',
            'url_template': 'https://{hostname}/sap/opu/mcp/v1',
            'auth_type': 'oauth2',
            'description': 'Enterprise resource planning',
            'provider': 'SAP'
        },
        {
            'name': 'Microsoft Azure',
            'category': 'Cloud',
            'url_template': 'https://management.azure.com/mcp/v1',
            'auth_type': 'oauth2',
            'description': 'Azure cloud services',
            'provider': 'Microsoft'
        },
        {
            'name': 'GitHub',
            'category': 'Development',
            'url_template': 'https://api.github.com/mcp/v1',
            'auth_type': 'bearer',
            'description': 'Code repository and collaboration',
            'provider': 'GitHub'
        },
        {
            'name': 'Slack',
            'category': 'Communication',
            'url_template': 'https://slack.com/api/mcp/v1',
            'auth_type': 'bearer',
            'description': 'Team messaging and collaboration',
            'provider': 'Slack'
        },
    ]
    return jsonify(directory)


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
