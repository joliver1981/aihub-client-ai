"""
Internal (service-to-service) seam for My Connections — lets a platform
service act through a user's OWN personal MCP account (Outlook / Microsoft
365 via the in-app Graph server, or any published authorization_code
server), AS that user.

Consumer today: The Agent (agent_service/connection_tools.py). Sibling of
the /api/internal/integrations* seam in app.py.

  GET  /api/internal/my-connections
       -> {status, user_id, connections:[{server_id, name, description,
           category, icon, connected, last_connected, scope}]}
          Servers the user has NOT authorized are included (connected:false)
          so the caller can steer them to /my-connections.
  GET  /api/internal/my-connections/<server_id>/tools
       -> {status:'success', server_id, name, tools:[{name, description,
           inputSchema[, annotations]}]}
        | {status:'needs_authorization', connected:false, message}
  POST /api/internal/my-connections/<server_id>/call
       body {tool_name, arguments:{...} | "<json>", context:{source}}
       -> {status:'success', result} | {status:'needs_authorization', ...}
        | {status:'error', code, message}

Auth — TWO things, both required:
  1. the machine-bound internal service key (X-Internal-API-Key / X-API-Key),
     like every /api/internal/* route;
  2. WHO the call is for: a signed X-AIHub-User assertion
     (shared_auth.sign_user_assertion, aud=aihub-internal, ~5 min), the same
     assertion The Agent already mints for the document ACL and
     api_agent_chat already verifies. The user id is NEVER taken from a
     query/body parameter — a service-key holder cannot simply name a user.
     The service principal (sub 0) is not a user and is refused.

Outcomes are structured data, not exceptions: "not authorized yet" is a 200
with status=needs_authorization (see personal_connections.py), so the
caller can tell the user where to connect instead of surfacing a 500.
Only malformed requests (400), missing/invalid identity (401) and an
unknown/unpublished server (404) use error codes.
"""
import logging

from flask import Blueprint, request, jsonify

from role_decorators import internal_api_key_required

logger = logging.getLogger(__name__)

my_connections_internal_bp = Blueprint(
    'my_connections_internal', __name__, url_prefix='/api/internal/my-connections')


def asserted_user(headers) -> tuple:
    """(user_id, role, error) — the user a signed X-AIHub-User assertion
    names, or (None, None, "<reason>")."""
    token = (headers.get('X-AIHub-User') or '').strip()
    if not token:
        return None, None, "no X-AIHub-User assertion — this seam acts AS a user and needs one"
    import shared_auth
    claims, err = shared_auth.verify_token(token, shared_auth.AUD_INTERNAL)
    if err or not claims:
        return None, None, f"invalid X-AIHub-User assertion: {err}"
    uid = shared_auth.claim_user_id(claims)
    if not isinstance(uid, int) or uid <= 0:
        return None, None, "the assertion does not name a real user (service principal)"
    return uid, claims.get('role'), None


def _identity_or_401():
    uid, role, err = asserted_user(request.headers)
    if err:
        logger.warning(f"[my-connections internal] refused: {err}")
        return None, (jsonify({'status': 'error', 'code': 'no_identity',
                               'message': err}), 401)
    return uid, None


def _gateway():
    from builder_mcp.client.mcp_gateway_client import MCPGatewayClient
    return MCPGatewayClient()


@my_connections_internal_bp.route('', methods=['GET'], strict_slashes=False)
@my_connections_internal_bp.route('/', methods=['GET'], strict_slashes=False)
@internal_api_key_required()
def internal_list_my_connections():
    uid, refusal = _identity_or_401()
    if refusal:
        return refusal
    try:
        from builder_mcp.agent_integration.personal_connections import (
            catalog_for_user, public_view,
        )
        entries = [public_view(e) for e in catalog_for_user(uid)]
        return jsonify({'status': 'success', 'user_id': uid,
                        'connections': entries, 'count': len(entries)})
    except Exception as e:
        logger.error(f"[my-connections internal] list failed for user {uid}: {e}",
                     exc_info=True)
        return jsonify({'status': 'error', 'code': 'server', 'message': str(e)}), 500


@my_connections_internal_bp.route('/<int:server_id>/tools', methods=['GET'])
@internal_api_key_required()
def internal_my_connection_tools(server_id):
    uid, refusal = _identity_or_401()
    if refusal:
        return refusal
    try:
        from builder_mcp.agent_integration.personal_connections import (
            get_server_for_user, list_user_tools, needs_authorization,
        )
        entry = get_server_for_user(server_id, uid)
        if not entry:
            return jsonify({'status': 'error', 'code': 'not_found',
                            'message': f"No personal connection {server_id} is "
                                       "published to this user."}), 404
        if not entry.get('connected'):
            return jsonify(needs_authorization(entry))
        return jsonify(list_user_tools(_gateway(), entry, uid))
    except Exception as e:
        logger.error(f"[my-connections internal] tools failed server={server_id} "
                     f"user={uid}: {e}", exc_info=True)
        return jsonify({'status': 'error', 'code': 'server', 'message': str(e)}), 500


@my_connections_internal_bp.route('/<int:server_id>/call', methods=['POST'])
@internal_api_key_required()
def internal_my_connection_call(server_id):
    uid, refusal = _identity_or_401()
    if refusal:
        return refusal
    try:
        from builder_mcp.agent_integration.personal_connections import (
            get_server_for_user, call_user_tool, needs_authorization,
            coerce_arguments,
        )
        body = request.get_json(silent=True) or {}
        tool_name = str(body.get('tool_name') or '').strip()
        if not tool_name:
            return jsonify({'status': 'error', 'code': 'bad_request',
                            'message': 'tool_name is required'}), 400
        args, arg_err = coerce_arguments(body.get('arguments'))
        if arg_err:
            return jsonify({'status': 'error', 'code': 'bad_request',
                            'message': arg_err}), 400
        source = str((body.get('context') or {}).get('source') or 'internal')[:40]
        entry = get_server_for_user(server_id, uid)
        if not entry:
            return jsonify({'status': 'error', 'code': 'not_found',
                            'message': f"No personal connection {server_id} is "
                                       "published to this user."}), 404
        if not entry.get('connected'):
            return jsonify(needs_authorization(entry))
        return jsonify(call_user_tool(_gateway(), entry, uid, tool_name, args, source))
    except Exception as e:
        logger.error(f"[my-connections internal] call failed server={server_id} "
                     f"user={uid}: {e}", exc_info=True)
        return jsonify({'status': 'error', 'code': 'server', 'message': str(e)}), 500
