"""
My Connections — per-user view of personal MCP integrations.

Surfaces only `auth_type='oauth2'` servers whose grant_type is
`authorization_code` (delegated, per-user). Service-account servers
(`client_credentials`) and non-OAuth servers stay on the admin MCP Servers
page; they aren't user-facing.

Endpoints:
  GET  /my-connections                                 — HTML page
  GET  /api/my-connections/servers                     — list + per-user state
  POST /api/my-connections/<server_id>/disconnect      — revoke current user's tokens
"""
import os
import logging
from flask import Blueprint, render_template, jsonify
from flask_login import login_required, current_user

from CommonUtils import get_db_connection

logger = logging.getLogger(__name__)
my_connections_bp = Blueprint('my_connections', __name__)


@my_connections_bp.route('/my-connections')
@login_required
def my_connections_page():
    return render_template('my_connections.html')


@my_connections_bp.route('/api/my-connections/servers', methods=['GET'])
@login_required
def list_my_connections():
    """List MCP servers the current user can personally connect to, with state."""
    try:
        from builder_mcp.agent_integration.oauth_manager import (
            _load_server_config, has_user_token,
        )

        user_id = int(current_user.id)

        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

            # All OAuth servers in this tenant — we filter to authorization_code
            # below since grant_type lives in the encrypted credentials table.
            cursor.execute("""
                SELECT server_id, server_name, description, category, icon
                FROM MCPServers
                WHERE auth_type = 'oauth2' AND enabled = 1
                ORDER BY server_name
            """)
            rows = cursor.fetchall()

            result = []
            for sid, name, desc, cat, icon in rows:
                cfg = _load_server_config(sid)
                grant_type = (cfg.get('oauth_grant_type') or '').lower()
                if grant_type != 'authorization_code':
                    continue

                connected = has_user_token(sid, user_id)
                last_connected = None
                if connected:
                    cursor.execute("""
                        SELECT MAX(updated_date) FROM MCPUserTokens
                        WHERE server_id = ? AND user_id = ?
                    """, sid, user_id)
                    r = cursor.fetchone()
                    if r and r[0]:
                        try:
                            last_connected = r[0].isoformat()
                        except Exception:
                            last_connected = str(r[0])

                result.append({
                    'server_id': sid,
                    'name': name,
                    'description': desc,
                    'category': cat,
                    'icon': icon,
                    'connected': connected,
                    'last_connected': last_connected,
                    'scope': cfg.get('oauth_scope', ''),
                })

            cursor.close()
            return jsonify(result)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    except Exception as e:
        logger.error(f"Error listing my connections: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@my_connections_bp.route('/api/my-connections/<int:server_id>/disconnect', methods=['POST'])
@login_required
def disconnect_my_connection(server_id):
    """Revoke the current user's tokens for this server."""
    try:
        from builder_mcp.agent_integration.oauth_manager import revoke_user_token
        revoke_user_token(server_id, int(current_user.id))
        return jsonify({'status': 'success'})
    except Exception as e:
        logger.error(f"Error disconnecting server {server_id}: {e}", exc_info=True)
        return jsonify({'status': 'error', 'error': str(e)}), 500
