"""
My Connections — per-user view of personal MCP integrations.

Surfaces only `auth_type='oauth2'` servers whose grant_type is
`authorization_code` (delegated, per-user) AND that an admin has published
(`MCPServers.available_to_users`, migration 020 — absent column = visible).
Service-account servers (`client_credentials`) and non-OAuth servers stay on
the admin MCP Servers page; they aren't user-facing.

The catalog itself lives in
builder_mcp/agent_integration/personal_connections.py (catalog_for_user),
shared with the internal seam The Agent uses — one filter, two surfaces.

Endpoints:
  GET  /my-connections                                 — HTML page
  GET  /my-connections/connect/<server_id>             — start Connect (popup from The Agent)
  GET  /api/my-connections/servers                     — list + per-user state
  POST /api/my-connections/<server_id>/disconnect      — revoke current user's tokens
"""
import logging
from flask import Blueprint, render_template, jsonify, redirect, request
from flask_login import login_required, current_user

logger = logging.getLogger(__name__)
my_connections_bp = Blueprint('my_connections', __name__)


@my_connections_bp.route('/my-connections')
@login_required
def my_connections_page():
    return render_template('my_connections.html')


@my_connections_bp.route('/my-connections/connect/<int:server_id>')
@login_required
def connect_my_connection(server_id):
    """Start Connect for one server from OUTSIDE this page — The Agent's My
    Connections screen opens this address in a popup. It only forwards to the
    same authorize route the Connect button here uses, which makes every
    decision (published to users, signed in, settings complete). The reason it
    exists: this is a page address, so a browser whose AI Hub sign-in has
    lapsed is taken to the sign-in form and brought back here afterwards,
    where the /api/ address would answer with a bare "Authentication
    required".

    ?as=<user id> is the caller saying who it believes is connecting. The
    account is always attached to whoever is signed in to AI Hub in this
    browser — never to the id in the address — so when the two differ (The
    Agent still open as one person, AI Hub since signed in as another) the
    flow stops here instead of quietly attaching a mailbox to the wrong
    person. It can only ever stop a flow; it grants nothing."""
    expected = (request.args.get('as') or '').strip()
    if expected:
        try:
            same = int(expected) == int(current_user.id)
        except (TypeError, ValueError):
            same = False
        if not same:
            logger.warning(f"my-connections connect refused: session user "
                           f"{current_user.id} != expected {expected!r} (server {server_id})")
            from builder_mcp.routes.mcp_routes import _oauth_page
            who = getattr(current_user, 'username', None) or 'another user'
            return _oauth_page(
                'Connection not started',
                f"AI Hub is signed in as {who} in this browser, but the Connect "
                "button was pressed by a different user. Nothing was connected.\n\n"
                "Sign out of AI Hub, sign in as yourself, and press Connect again.",
                409)
    return redirect(f'/api/mcp/oauth/authorize/{int(server_id)}')


@my_connections_bp.route('/api/my-connections/servers', methods=['GET'])
@login_required
def list_my_connections():
    """List MCP servers the current user can personally connect to, with state."""
    try:
        from builder_mcp.agent_integration.personal_connections import (
            catalog_for_user, public_view,
        )
        user_id = int(current_user.id)
        return jsonify([public_view(e) for e in catalog_for_user(user_id)])
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
        # Drop this user's live gateway connection too, so a revoked token
        # cannot keep serving an already-open transport.
        try:
            from builder_mcp.client.mcp_gateway_client import MCPGatewayClient
            MCPGatewayClient().disconnect_server(server_id, user_id=int(current_user.id))
        except Exception as ge:
            logger.debug(f"gateway disconnect after revoke skipped: {ge}")
        return jsonify({'status': 'success'})
    except Exception as e:
        logger.error(f"Error disconnecting server {server_id}: {e}", exc_info=True)
        return jsonify({'status': 'error', 'error': str(e)}), 500
