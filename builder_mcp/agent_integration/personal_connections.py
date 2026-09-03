"""
Personal connections (My Connections) — the per-user MCP bridge.

One module owns everything a caller needs to act THROUGH a user's own
personal MCP account (Microsoft 365 / Outlook via the in-app Graph server,
or any OAuth authorization_code server an admin has published):

  catalog_for_user(user_id)        -> the servers this user may connect, with
                                      connected/not state (what /my-connections
                                      shows, plus the connection fields)
  get_server_for_user(sid, uid)    -> one catalog entry or None
  list_user_tools(gw, entry, uid)  -> the server's tools, on THIS user's
                                      gateway connection
  call_user_tool(gw, entry, uid, tool, args, source)
                                   -> execute one tool as this user

Consumers: builder_mcp/routes/my_connections_routes.py (the user's own
page, session auth) and builder_mcp/routes/my_connections_internal_routes.py
(the service seam The Agent calls with a signed X-AIHub-User assertion).
GeneralAgent's Flow B (mcp_agent_tools.get_mcp_tools_for_agent) is the
other consumer of personal tokens; it shares the same per-user gateway
keying but binds tools at agent build time instead of per call.

Why not the admin routes (/api/mcp/servers/<id>/tools[/call])?
  Blocker A — they build the connection config WITHOUT a user, and
  oauth_manager.get_access_token raises for authorization_code grants
  without one; they physically cannot mint a user's token.
  Blocker B — the gateway used to cache ONE connection per server_id with
  the per-user bearer baked in, so user B's call could ride user A's
  token. Fixed 2026-09-03: the gateway keys connections by
  (server_id, user_id); everything here passes user_id, and refuses to
  proceed if the gateway does not confirm the per-user key (an older
  gateway would silently share — fail closed, never bleed).

Token freshness: a connection carries the bearer it was opened with. We
reopen this user's connection when it is older than
MY_CONNECTIONS_CONN_MAX_AGE seconds (default 60) — get_access_token then
hands back the cached token while valid (60 s leeway) or refreshes it —
and retry ONCE after an auth-shaped tool failure. Cheap on loopback
(initialize + tools/list), and it means a user's expired access token is
refreshed transparently instead of surfacing as a 401.

"Not authorized yet" is DATA, not an exception: every path returns
{"status": "needs_authorization", ...} with readable text pointing at
/my-connections, so a caller can steer the user instead of erroring.

Audit: every call writes the same [MCP_AUDIT] line MCPToolConverter writes
for GeneralAgent (logger "mcp.audit") — who, which server, which tool,
what outcome — with agent_id set to the caller's source (e.g. the_agent).
"""
import json
import logging
import os
import time
from typing import Optional

from CommonUtils import get_db_connection

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("mcp.audit")

MY_CONNECTIONS_PATH = "/my-connections"
CONNECTION_MAX_AGE_SECONDS = int(os.getenv("MY_CONNECTIONS_CONN_MAX_AGE", "60"))
REMOTE_TYPES = ("remote", "streamable-http", "sse")
INTERNAL_GRAPH_SUFFIX = "/api/internal/mcp/graph"

# Phrases the token/gateway layers use when the USER has not authorized (or
# the grant died). Kept in one place; MCPToolConverter matches the same set.
NEEDS_AUTH_PHRASES = (
    "must complete the oauth", "user must complete", "no refresh token",
    "no oauth token available", "no bearer token", "oauth refresh failed",
    "invalid_grant", "requires user_id for authorization_code",
)
# Phrases that mean the bearer on an OPEN connection went stale mid-flight.
STALE_AUTH_PHRASES = (
    "401", "invalidauthenticationtoken", "token is expired",
    "access token has expired", "unauthorized", "no bearer token",
    "lifetime validation failed",
)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

def _set_tenant(cursor):
    cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))


def catalog_for_user(user_id: int) -> list:
    """The personal-connection catalog for one user.

    Same filter as the My Connections page: auth_type='oauth2' AND enabled=1
    AND (published to users when migration 020 is present) AND the grant is
    authorization_code (delegated, per-user). Every entry carries
    connected/last_connected for THIS user, plus the connection fields the
    bridge needs (server_type, server_url, auth_type, connection_config).
    Servers the user has NOT authorized are returned too (connected=False)
    so a caller can say "go connect it" instead of silently lacking a tool.
    """
    from builder_mcp.agent_integration.oauth_manager import (
        _load_server_config, has_user_token,
    )
    from builder_mcp.agent_integration.mcp_server_visibility import (
        has_available_to_users_column,
    )
    uid = int(user_id)
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        _set_tenant(cursor)
        published_clause = (" AND available_to_users = 1"
                            if has_available_to_users_column(cursor) else "")
        cursor.execute(f"""
            SELECT server_id, server_name, description, category, icon,
                   server_type, server_url, auth_type, connection_config
            FROM MCPServers
            WHERE auth_type = 'oauth2' AND enabled = 1{published_clause}
            ORDER BY server_name
        """)
        rows = cursor.fetchall()
        result = []
        for (sid, name, desc, cat, icon, server_type, server_url,
             auth_type, connection_config) in rows:
            cfg = _load_server_config(sid)
            if (cfg.get('oauth_grant_type') or '').lower() != 'authorization_code':
                continue
            connected = has_user_token(sid, uid)
            last_connected = None
            if connected:
                cursor.execute("""
                    SELECT MAX(updated_date) FROM MCPUserTokens
                    WHERE server_id = ? AND user_id = ?
                """, sid, uid)
                r = cursor.fetchone()
                if r and r[0]:
                    try:
                        last_connected = r[0].isoformat()
                    except Exception:
                        last_connected = str(r[0])
            result.append({
                'server_id': int(sid),
                'name': name,
                'description': desc,
                'category': cat,
                'icon': icon,
                'connected': bool(connected),
                'last_connected': last_connected,
                'scope': cfg.get('oauth_scope', ''),
                # bridge-internal fields (stripped by public_view)
                'server_type': server_type,
                'server_url': server_url,
                'auth_type': auth_type,
                'connection_config': connection_config,
            })
        cursor.close()
        return result
    finally:
        try:
            conn.close()
        except Exception:
            pass


PUBLIC_FIELDS = ('server_id', 'name', 'description', 'category', 'icon',
                 'connected', 'last_connected', 'scope')


def public_view(entry: dict) -> dict:
    """The page/API shape — no connection internals."""
    return {k: entry.get(k) for k in PUBLIC_FIELDS}


def get_server_for_user(server_id: int, user_id: int) -> Optional[dict]:
    sid = int(server_id)
    for entry in catalog_for_user(user_id):
        if int(entry['server_id']) == sid:
            return entry
    return None


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------

def needs_authorization(entry: dict, detail: str = "") -> dict:
    name = (entry or {}).get('name') or f"server {(entry or {}).get('server_id')}"
    msg = (f"This user has not authorized '{name}' yet, or the authorization "
           f"has expired. They must connect it themselves at {MY_CONNECTIONS_PATH} "
           f"(My Connections in the rail), then try again. Nothing was read or sent.")
    if detail:
        msg += f" Detail: {str(detail)[:300]}"
    return {"status": "needs_authorization", "code": "needs_authorization",
            "connected": False, "server_id": (entry or {}).get('server_id'),
            "message": msg}


def _error(code: str, message: str, **extra) -> dict:
    out = {"status": "error", "code": code, "message": message}
    out.update(extra)
    return out


def looks_like_needs_auth(text: str) -> bool:
    low = (text or "").lower()
    return any(p in low for p in NEEDS_AUTH_PHRASES)


def looks_like_stale_auth(text: str) -> bool:
    low = (text or "").lower()
    return any(p in low for p in STALE_AUTH_PHRASES)


def _user_key(server_id, user_id) -> str:
    return f"{int(server_id)}@u{int(user_id)}"


# ---------------------------------------------------------------------------
# Tool annotations
# ---------------------------------------------------------------------------

def annotate_known_tools(server_url: str, tools: list) -> list:
    """Overlay MCP annotations for servers whose schemas live in THIS app
    (the in-process Graph server), for tools that arrived without them —
    e.g. through a gateway build that did not forward annotations. Tools
    from other servers are returned untouched (their own declaration, or
    none, stands)."""
    if not (server_url or "").rstrip('/').endswith(INTERNAL_GRAPH_SUFFIX):
        return tools
    try:
        from builder_mcp.servers.graph_tools import TOOL_SCHEMAS
    except Exception:
        return tools
    known = {t.get("name"): t.get("annotations") for t in TOOL_SCHEMAS
             if isinstance(t.get("annotations"), dict)}
    out = []
    for t in tools or []:
        t = dict(t)
        if not isinstance(t.get("annotations"), dict) and t.get("name") in known:
            t["annotations"] = dict(known[t["name"]])
        out.append(t)
    return out


# ---------------------------------------------------------------------------
# Gateway connection, scoped to one user
# ---------------------------------------------------------------------------

def ensure_user_connection(gateway, entry: dict, user_id: int,
                           force: bool = False) -> tuple:
    """Make sure THIS user's connection to the server is open and fresh.

    Returns (True, None) or (False, outcome_dict). Never raises.
    """
    sid = int(entry['server_id'])
    uid = int(user_id)
    key = _user_key(sid, uid)
    if (entry.get('server_type') or 'remote') not in REMOTE_TYPES:
        return False, _error("unsupported",
                             "Personal connections must be remote MCP servers "
                             f"(server {sid} is type '{entry.get('server_type')}').")
    if not force:
        try:
            st = gateway.get_server_status(sid, user_id=uid) or {}
        except Exception as e:
            st = {}
            logger.debug(f"[my-connections] status check failed for {key}: {e}")
        age = time.time() - float(st.get('connected_at') or 0)
        if (st.get('status') == 'connected' and st.get('connection_key') == key
                and age < CONNECTION_MAX_AGE_SECONDS):
            return True, None

    # A valid token for THIS user (cached while valid, refreshed when not;
    # raises when the user never authorized or the grant died).
    try:
        from builder_mcp.agent_integration.oauth_manager import get_access_token
        token = get_access_token(sid, user_id=uid)
    except Exception as e:
        logger.info(f"[my-connections] token unavailable for {key}: {e}")
        return False, needs_authorization(entry, str(e))
    if not token:
        return False, needs_authorization(entry, "no token on file")

    from builder_mcp.agent_integration.mcp_agent_tools import _build_connection_config
    # auth_type 'none' -> transport hints + verify_ssl only; the bearer is
    # set explicitly below so the token we just validated is the one used.
    config = _build_connection_config(entry.get('server_type'), entry.get('server_url'),
                                      'none', entry.get('connection_config'), sid)
    config['auth_headers'] = {'Authorization': f'Bearer {token}'}
    try:
        res = gateway.connect_server(sid, config, user_id=uid) or {}
    except Exception as e:
        return False, _error("gateway", f"MCP gateway unreachable: {e}")
    if res.get('status') != 'connected':
        err = str(res.get('error') or 'unknown error')
        if looks_like_needs_auth(err):
            return False, needs_authorization(entry, err)
        return False, _error("gateway", f"Could not connect '{entry.get('name')}': {err}")
    if res.get('connection_key') != key:
        # The gateway did not confirm a per-user connection — an older build
        # keys by server_id alone and would let this user's token serve
        # everyone. Refuse rather than bleed.
        try:
            gateway.disconnect_server(sid, user_id=uid)
        except Exception:
            pass
        return False, _error(
            "gateway_unscoped",
            "The MCP gateway did not confirm a per-user connection (it needs "
            "the 2026-09-03 build that keys connections by server AND user). "
            "Refusing to proceed so one user's token can never serve another — "
            "restart/upgrade the MCP gateway service.")
    return True, None


def _audit(user_id, source, server_id, tool, status, started):
    audit_logger.info(
        f"[MCP_AUDIT] user_id={user_id} agent_id={source} server_id={server_id} "
        f"tool={tool} status={status} elapsed_ms={int((time.time() - started) * 1000)}")


def list_user_tools(gateway, entry: dict, user_id: int) -> dict:
    """{status:'success', server_id, name, tools:[{name, description,
    inputSchema[, annotations]}]} or a needs_authorization / error outcome."""
    ok, outcome = ensure_user_connection(gateway, entry, user_id)
    if not ok:
        return outcome
    sid = int(entry['server_id'])
    try:
        tools = gateway.list_tools(sid, user_id=int(user_id)) or []
    except Exception as e:
        return _error("gateway", f"Could not list tools: {e}")
    tools = annotate_known_tools(entry.get('server_url') or '', tools)
    return {"status": "success", "server_id": sid, "name": entry.get('name'),
            "tools": tools, "tool_count": len(tools)}


def call_user_tool(gateway, entry: dict, user_id: int, tool_name: str,
                   arguments: dict, source: str = "internal") -> dict:
    """Execute one tool on THIS user's connection.

    {status:'success', result:<text>} | needs_authorization | error.
    """
    sid = int(entry['server_id'])
    uid = int(user_id)
    started = time.time()
    ok, outcome = ensure_user_connection(gateway, entry, uid)
    if not ok:
        _audit(uid, source, sid, tool_name, outcome.get('status', 'error'), started)
        return outcome
    args = arguments if isinstance(arguments, dict) else {}
    try:
        res = gateway.call_tool(sid, tool_name, args, user_id=uid) or {}
    except Exception as e:
        _audit(uid, source, sid, tool_name, "exception", started)
        return _error("gateway", f"Tool call failed at the gateway: {e}")
    if res.get('status') != 'success':
        err = str(res.get('error') or res.get('message') or 'unknown error')
        if looks_like_stale_auth(err) or looks_like_needs_auth(err):
            # The bearer on the open connection went stale (or the gateway
            # restarted): reopen with a fresh token and retry exactly once.
            ok, outcome = ensure_user_connection(gateway, entry, uid, force=True)
            if not ok:
                _audit(uid, source, sid, tool_name, outcome.get('status', 'error'), started)
                return outcome
            try:
                res = gateway.call_tool(sid, tool_name, args, user_id=uid) or {}
            except Exception as e:
                _audit(uid, source, sid, tool_name, "exception", started)
                return _error("gateway", f"Tool call failed at the gateway: {e}")
            err = str(res.get('error') or res.get('message') or 'unknown error')
        if res.get('status') != 'success':
            if looks_like_needs_auth(err):
                _audit(uid, source, sid, tool_name, "needs_auth", started)
                return needs_authorization(entry, err)
            _audit(uid, source, sid, tool_name, "error", started)
            return _error("tool_error", err, server_id=sid, tool_name=tool_name)
    _audit(uid, source, sid, tool_name, "success", started)
    return {"status": "success", "server_id": sid, "tool_name": tool_name,
            "result": res.get('result', '')}


def coerce_arguments(raw) -> tuple:
    """(dict, error_text) — accepts a dict or a JSON object string."""
    if raw is None or raw == "":
        return {}, None
    if isinstance(raw, dict):
        return raw, None
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except Exception:
            return None, "arguments must be a JSON object"
        if isinstance(parsed, dict):
            return parsed, None
    return None, "arguments must be a JSON object"
