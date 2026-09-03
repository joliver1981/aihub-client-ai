"""The My Connections bridge (builder_mcp/agent_integration/personal_connections.py)
and its internal seam (builder_mcp/routes/my_connections_internal_routes.py),
2026-09-03.

Pins the properties that make the bridge safe:
- every gateway call is scoped to the asserted user (Blocker B) and the
  bridge REFUSES a gateway that does not confirm the per-user key;
- "not authorized yet" is structured data pointing at /my-connections, never
  a 500, and never another user's data;
- a stale bearer triggers exactly one reconnect + retry;
- the seam takes identity ONLY from a signed X-AIHub-User assertion — no
  header -> 401, bad token -> 401, the service principal -> 401, a user_id
  query/body parameter is ignored;
- annotations for the in-app Graph server are overlaid when missing.

DB and gateway are faked; nothing touches the network.
"""
import json
import os
import sys
import types
from unittest import mock

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# personal_connections imports CommonUtils at module load (for the catalog's
# DB access); stub it so the module imports in a bare unit env.
if "CommonUtils" not in sys.modules:
    sys.modules["CommonUtils"] = types.SimpleNamespace(get_db_connection=lambda: None)

from builder_mcp.agent_integration import personal_connections as PC  # noqa: E402
import shared_auth  # noqa: E402


class FakeGateway:
    """Records every call with the user it was scoped to."""

    def __init__(self, scoped=True, tools=None, fail_calls_with=None):
        self.scoped = scoped
        self.tools = tools or [{"name": "list_recent_emails", "description": "d",
                                "inputSchema": {"type": "object"}}]
        self.calls = []
        self.connections = {}
        self.fail_calls_with = list(fail_calls_with or [])

    def get_server_status(self, server_id, user_id=None):
        self.calls.append(("status", server_id, user_id))
        key = f"{server_id}@u{user_id}" if user_id else str(server_id)
        c = self.connections.get(key)
        if not c:
            return {"status": "disconnected", "server_id": str(server_id), "connection_key": key}
        return {"status": "connected", "connection_key": key, "connected_at": c["at"]}

    def connect_server(self, server_id, config, user_id=None):
        self.calls.append(("connect", server_id, user_id, config.get("auth_headers")))
        key = f"{server_id}@u{user_id}" if user_id else str(server_id)
        self.connections[key] = {"at": __import__("time").time(), "config": config}
        out = {"status": "connected", "tool_count": len(self.tools), "tools": self.tools}
        if self.scoped:
            out["connection_key"] = key
        return out

    def disconnect_server(self, server_id, user_id=None):
        self.calls.append(("disconnect", server_id, user_id))
        return {"status": "disconnected"}

    def list_tools(self, server_id, user_id=None):
        self.calls.append(("list_tools", server_id, user_id))
        return self.tools

    def call_tool(self, server_id, tool_name, arguments, user_id=None):
        self.calls.append(("call", server_id, user_id, tool_name, arguments))
        if self.fail_calls_with:
            return {"status": "error", "error": self.fail_calls_with.pop(0)}
        return {"status": "success", "result": f"mail for u{user_id}"}


ENTRY = {"server_id": 30, "name": "EveriAI Graph", "connected": True,
         "server_type": "remote", "server_url": "http://127.0.0.1:5001/api/internal/mcp/graph",
         "auth_type": "oauth2", "connection_config": None}


@pytest.fixture
def token_ok(monkeypatch):
    tokens = {13: "tok-13", 77: "tok-77"}

    def fake_get_access_token(server_id, user_id=None):
        if user_id in tokens:
            return tokens[user_id]
        raise RuntimeError(f"No refresh token for user_id={user_id} on server_id={server_id} — "
                           "the user must complete the OAuth authorization flow (My Connections).")

    fake_oauth = types.SimpleNamespace(get_access_token=fake_get_access_token)
    monkeypatch.setitem(sys.modules, "builder_mcp.agent_integration.oauth_manager", fake_oauth)
    fake_tools = types.SimpleNamespace(
        _build_connection_config=lambda st, url, at, cc, sid, user_id=None: {
            "type": st, "url": url, "auth_headers": {}, "transport": None, "verify_ssl": True})
    monkeypatch.setitem(sys.modules, "builder_mcp.agent_integration.mcp_agent_tools", fake_tools)
    return tokens


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------

def test_call_is_scoped_to_the_user_and_uses_that_users_token(token_ok):
    gw = FakeGateway()
    res = PC.call_user_tool(gw, ENTRY, 13, "list_recent_emails", {"limit": 3}, source="the_agent")
    assert res["status"] == "success" and res["result"] == "mail for u13"
    connect = [c for c in gw.calls if c[0] == "connect"][0]
    assert connect[2] == 13 and connect[3] == {"Authorization": "Bearer tok-13"}
    call = [c for c in gw.calls if c[0] == "call"][0]
    assert call[2] == 13 and call[3] == "list_recent_emails" and call[4] == {"limit": 3}


def test_two_users_never_share_a_connection(token_ok):
    gw = FakeGateway()
    a = PC.call_user_tool(gw, ENTRY, 13, "list_recent_emails", {})
    b = PC.call_user_tool(gw, ENTRY, 77, "list_recent_emails", {})
    assert a["result"] == "mail for u13" and b["result"] == "mail for u77"
    assert set(gw.connections) == {"30@u13", "30@u77"}
    assert gw.connections["30@u13"]["config"]["auth_headers"]["Authorization"] == "Bearer tok-13"
    assert gw.connections["30@u77"]["config"]["auth_headers"]["Authorization"] == "Bearer tok-77"


def test_unauthorized_user_gets_needs_authorization_not_mail(token_ok):
    gw = FakeGateway()
    res = PC.call_user_tool(gw, ENTRY, 999, "list_recent_emails", {})
    assert res["status"] == "needs_authorization" and res["connected"] is False
    assert "/my-connections" in res["message"] and "Nothing was read" in res["message"]
    assert not [c for c in gw.calls if c[0] in ("connect", "call")], "no gateway traffic without a token"
    res = PC.list_user_tools(gw, ENTRY, 999)
    assert res["status"] == "needs_authorization"


def test_fresh_connection_is_reused_then_reopened_when_old(token_ok, monkeypatch):
    gw = FakeGateway()
    PC.call_user_tool(gw, ENTRY, 13, "list_recent_emails", {})
    PC.call_user_tool(gw, ENTRY, 13, "list_recent_emails", {})
    assert len([c for c in gw.calls if c[0] == "connect"]) == 1, "fresh connection reused"
    gw.connections["30@u13"]["at"] -= PC.CONNECTION_MAX_AGE_SECONDS + 5
    PC.call_user_tool(gw, ENTRY, 13, "list_recent_emails", {})
    assert len([c for c in gw.calls if c[0] == "connect"]) == 2, "old connection reopened"


def test_stale_bearer_reconnects_once_and_retries(token_ok):
    gw = FakeGateway(fail_calls_with=["HTTP 401 InvalidAuthenticationToken: token is expired"])
    res = PC.call_user_tool(gw, ENTRY, 13, "list_recent_emails", {})
    assert res["status"] == "success"
    assert len([c for c in gw.calls if c[0] == "connect"]) == 2
    assert len([c for c in gw.calls if c[0] == "call"]) == 2
    # a persistent auth failure ends as needs_authorization / error, not a loop
    gw = FakeGateway(fail_calls_with=["No bearer token forwarded by gateway", "No bearer token forwarded by gateway"])
    res = PC.call_user_tool(gw, ENTRY, 13, "list_recent_emails", {})
    assert res["status"] == "needs_authorization"
    assert len([c for c in gw.calls if c[0] == "call"]) == 2


def test_tool_error_is_surfaced_verbatim(token_ok):
    gw = FakeGateway(fail_calls_with=["Graph returned 403 Forbidden for this scope"])
    res = PC.call_user_tool(gw, ENTRY, 13, "list_recent_emails", {})
    assert res["status"] == "error" and res["code"] == "tool_error"
    assert "403 Forbidden" in res["message"]


def test_unscoped_gateway_is_refused(token_ok):
    gw = FakeGateway(scoped=False)
    res = PC.call_user_tool(gw, ENTRY, 13, "list_recent_emails", {})
    assert res["status"] == "error" and res["code"] == "gateway_unscoped"
    assert not [c for c in gw.calls if c[0] == "call"], "never call on an unscoped connection"
    assert [c for c in gw.calls if c[0] == "disconnect"], "and tear the shared one down"


def test_local_servers_are_not_bridged(token_ok):
    gw = FakeGateway()
    res = PC.call_user_tool(gw, dict(ENTRY, server_type="local"), 13, "x", {})
    assert res["status"] == "error" and res["code"] == "unsupported"


def test_annotations_overlay_only_for_the_in_app_graph_server():
    tools = [{"name": "send_email", "description": "d", "inputSchema": {}},
             {"name": "list_recent_emails", "description": "d", "inputSchema": {},
              "annotations": {"readOnlyHint": False}},   # a declared value is never overridden
             {"name": "unknown_tool", "description": "d", "inputSchema": {}}]
    out = PC.annotate_known_tools("http://127.0.0.1:5001/api/internal/mcp/graph", tools)
    by = {t["name"]: t for t in out}
    assert by["send_email"]["annotations"]["readOnlyHint"] is False
    assert by["list_recent_emails"]["annotations"] == {"readOnlyHint": False}
    assert "annotations" not in by["unknown_tool"]
    # foreign server: untouched
    out = PC.annotate_known_tools("https://mcp.example/api", tools)
    assert "annotations" not in out[0]


def test_graph_schemas_declare_reads_and_the_write():
    from builder_mcp.servers.graph_tools import TOOL_SCHEMAS
    ann = {t["name"]: t.get("annotations") for t in TOOL_SCHEMAS}
    assert ann["get_my_profile"]["readOnlyHint"] is True
    assert ann["list_recent_emails"]["readOnlyHint"] is True
    assert ann["list_upcoming_meetings"]["readOnlyHint"] is True
    assert ann["send_email"]["readOnlyHint"] is False
    assert all("handler" not in t for t in TOOL_SCHEMAS)


def test_public_view_strips_connection_internals():
    v = PC.public_view(ENTRY)
    assert set(v) == set(PC.PUBLIC_FIELDS)
    assert "server_url" not in v and "connection_config" not in v


def test_coerce_arguments():
    assert PC.coerce_arguments(None) == ({}, None)
    assert PC.coerce_arguments({"a": 1}) == ({"a": 1}, None)
    assert PC.coerce_arguments('{"a": 1}') == ({"a": 1}, None)
    assert PC.coerce_arguments("[1]")[1]
    assert PC.coerce_arguments("nope")[1]


# ---------------------------------------------------------------------------
# Seam (Flask blueprint) — identity handling
# ---------------------------------------------------------------------------

@pytest.fixture
def seam_app(monkeypatch):
    monkeypatch.setenv("CC_JWT_SECRET", "seam-unit-secret")
    try:
        from flask import Flask
        import role_decorators
        from builder_mcp.routes import my_connections_internal_routes as R
    except Exception as e:  # pragma: no cover - env without flask
        pytest.skip(f"flask / role_decorators unavailable: {e}")
    monkeypatch.setattr(role_decorators, "_validate_internal_api_key",
                        lambda key: {"valid": key == "internal-key", "source": "internal"})
    app = Flask("seam-test")
    app.register_blueprint(R.my_connections_internal_bp)
    return app, R


def _assert_for(uid, role=2):
    return shared_auth.sign_user_assertion(uid, 1, role)


def test_seam_refuses_missing_bad_and_service_principal_identity(seam_app, monkeypatch):
    app, R = seam_app
    c = app.test_client()
    hdr = {"X-Internal-API-Key": "internal-key"}
    r = c.get("/api/internal/my-connections", headers=hdr)
    assert r.status_code == 401 and r.get_json()["code"] == "no_identity"
    r = c.get("/api/internal/my-connections", headers=dict(hdr, **{"X-AIHub-User": "garbage"}))
    assert r.status_code == 401
    r = c.get("/api/internal/my-connections", headers=dict(hdr, **{"X-AIHub-User": _assert_for(0)}))
    assert r.status_code == 401 and "service principal" in r.get_json()["message"]
    # wrong audience (a CC session token) is not an internal assertion
    cc = shared_auth.sign_cc_token({"user_id": 13, "role": 2, "tenant_id": 1})
    r = c.get("/api/internal/my-connections", headers=dict(hdr, **{"X-AIHub-User": cc}))
    assert r.status_code == 401
    # no service key at all -> 401 before identity is even looked at
    r = c.get("/api/internal/my-connections", headers={"X-AIHub-User": _assert_for(13)})
    assert r.status_code == 401


def test_seam_lists_for_the_asserted_user_and_ignores_user_id_params(seam_app, monkeypatch):
    app, R = seam_app
    seen = {}

    def fake_catalog(uid):
        seen["uid"] = uid
        return [dict(ENTRY), {"server_id": 31, "name": "Google", "connected": False,
                              "last_connected": None, "scope": "", "description": None,
                              "category": None, "icon": None, "server_type": "remote",
                              "server_url": "x", "auth_type": "oauth2", "connection_config": None}]

    monkeypatch.setattr(PC, "catalog_for_user", fake_catalog)
    c = app.test_client()
    hdr = {"X-Internal-API-Key": "internal-key", "X-AIHub-User": _assert_for(13)}
    r = c.get("/api/internal/my-connections?user_id=999", headers=hdr)
    assert r.status_code == 200
    body = r.get_json()
    assert seen["uid"] == 13, "identity comes from the assertion, never the query string"
    assert body["user_id"] == 13 and body["count"] == 2
    assert [x["server_id"] for x in body["connections"]] == [30, 31]
    assert "server_url" not in body["connections"][0]


def test_seam_tools_and_call_route_to_the_asserted_user(seam_app, monkeypatch, token_ok):
    app, R = seam_app
    gw = FakeGateway(tools=[{"name": "list_recent_emails", "description": "d", "inputSchema": {}}])
    monkeypatch.setattr(PC, "catalog_for_user", lambda uid: [dict(ENTRY, connected=(uid == 13))])
    monkeypatch.setattr(R, "_gateway", lambda: gw)
    c = app.test_client()
    hdr13 = {"X-Internal-API-Key": "internal-key", "X-AIHub-User": _assert_for(13)}
    hdr77 = {"X-Internal-API-Key": "internal-key", "X-AIHub-User": _assert_for(77)}

    r = c.get("/api/internal/my-connections/30/tools", headers=hdr13)
    assert r.status_code == 200 and r.get_json()["status"] == "success"
    tools = r.get_json()["tools"]
    assert tools[0]["annotations"]["readOnlyHint"] is True    # overlaid from graph_tools

    r = c.post("/api/internal/my-connections/30/call", headers=hdr13,
               json={"tool_name": "list_recent_emails", "arguments": {"limit": 2},
                     "context": {"source": "the_agent"}, "user_id": 77})
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "success" and body["result"] == "mail for u13"
    call = [x for x in gw.calls if x[0] == "call"][-1]
    assert call[2] == 13, "a body user_id must not redirect the call"

    # user 77 has no token in the catalog -> structured needs_authorization, no gateway traffic
    before = len(gw.calls)
    r = c.post("/api/internal/my-connections/30/call", headers=hdr77,
               json={"tool_name": "list_recent_emails"})
    assert r.status_code == 200 and r.get_json()["status"] == "needs_authorization"
    assert len(gw.calls) == before

    # malformed requests
    r = c.post("/api/internal/my-connections/30/call", headers=hdr13, json={})
    assert r.status_code == 400
    r = c.post("/api/internal/my-connections/30/call", headers=hdr13,
               json={"tool_name": "x", "arguments": "[1]"})
    assert r.status_code == 400
    r = c.get("/api/internal/my-connections/31/tools", headers=hdr13)
    assert r.status_code == 404
