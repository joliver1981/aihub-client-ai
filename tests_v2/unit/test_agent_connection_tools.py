"""Unit pack for The Agent's My Connections tools (agent_service/connection_tools.py,
2026-09-03): the write gate (default-closed, exact-match, annotation-declared
reads, headless policy), the server denylist, the discovery/format contract,
"not authorized yet" surfacing, identity handling and honest truncation.

All HTTP seams are monkeypatched; nothing touches the network. Runs standalone
(aihub-agent python test_agent_connection_tools.py) or under pytest; self-skips
in envs without claude_agent_sdk.
"""
import asyncio
import os
import sys
from unittest import mock

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

try:
    import connection_tools as C               # noqa: E402
    from platform_tools import CURRENT_USER    # noqa: E402
    HAVE_SDK = True
except ImportError as e:
    HAVE_SDK = False
    _IMPORT_ERR = e

if not HAVE_SDK:
    try:
        import pytest
        pytestmark = pytest.mark.skip(
            reason=f"needs the aihub-agent env (claude_agent_sdk): {_IMPORT_ERR}")
    except ImportError:
        pass


def _run(coro):
    return asyncio.run(coro)


def _txt(res):
    return res["content"][0]["text"]


def _as(uid=13, role=2, mode=None):
    ctx = {"user_id": uid, "role": role, "username": f"u{uid}", "name": f"User {uid}",
           "tenant_id": 1}
    if mode:
        ctx["mode"] = mode
    return CURRENT_USER.set(ctx)


READ = {"readOnlyHint": True}
WRITE = {"readOnlyHint": False}
GRAPH_TOOLS = [
    {"name": "get_my_profile", "description": "profile", "inputSchema": {"type": "object", "properties": {}},
     "annotations": READ},
    {"name": "list_recent_emails", "description": "recent mail",
     "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer"},
                                                       "folder": {"type": "string"}}},
     "annotations": READ},
    {"name": "send_email", "description": "send as the user",
     "inputSchema": {"type": "object", "properties": {"to": {"type": "string"}},
                     "required": ["to"]},
     "annotations": WRITE},
    {"name": "mystery_tool", "description": "no annotations at all",
     "inputSchema": {"type": "object", "properties": {}}},
]
CATALOG = {"status": "success", "user_id": 13, "count": 2, "connections": [
    {"server_id": 30, "name": "EveriAI Graph", "connected": True,
     "last_connected": "2026-09-03T12:57:47", "scope": "Mail.Read Mail.Send"},
    {"server_id": 31, "name": "Google (TEST)", "connected": False,
     "last_connected": None, "scope": "gmail.readonly"},
]}


# ---------------------------------------------------------------------------
# The shared guard
# ---------------------------------------------------------------------------

def test_write_gate_default_closed_exact_match_and_annotations():
    with mock.patch.dict(os.environ, {"AGENT_MY_CONNECTIONS_WRITE_TOOLS": ""}):
        assert C.write_tools_allowed() == set()
        ok, reason, is_write = C.tool_permission(GRAPH_TOOLS[1])       # read-only declared
        assert ok and not is_write and reason == ""
        ok, reason, is_write = C.tool_permission(GRAPH_TOOLS[2])       # send_email
        assert not ok and is_write and "DENIED" in reason and "send_email" in reason
        assert "own address" in reason.lower() or "own address" in reason
        ok, reason, _ = C.tool_permission(GRAPH_TOOLS[3])              # undeclared -> denied
        assert not ok and "mystery_tool" in reason
    with mock.patch.dict(os.environ, {"AGENT_MY_CONNECTIONS_WRITE_TOOLS": " send_email , other "}):
        assert C.write_tools_allowed() == {"send_email", "other"}
        ok, _, is_write = C.tool_permission(GRAPH_TOOLS[2])
        assert ok and is_write
        # exact match only: no case folding, no prefix/suffix matching
        assert not C.write_allowed("Send_Email")
        assert not C.write_allowed("send_email_v2")
        assert not C.write_allowed("send")
        ok, _, _ = C.tool_permission({"name": "send_email_v2", "annotations": WRITE})
        assert not ok


def test_headless_reads_allowed_writes_need_extra_switch():
    tok = _as(mode="headless")
    try:
        with mock.patch.dict(os.environ, {"AGENT_MY_CONNECTIONS_WRITE_TOOLS": "send_email",
                                          "AGENT_MY_CONNECTIONS_HEADLESS_WRITES": ""}):
            ok, _, _ = C.tool_permission(GRAPH_TOOLS[1])
            assert ok, "reads must work in scheduled sessions (daily routines)"
            ok, reason, is_write = C.tool_permission(GRAPH_TOOLS[2])
            assert not ok and is_write and "scheduled" in reason
            assert "AGENT_MY_CONNECTIONS_HEADLESS_WRITES" in reason
        with mock.patch.dict(os.environ, {"AGENT_MY_CONNECTIONS_WRITE_TOOLS": "send_email",
                                          "AGENT_MY_CONNECTIONS_HEADLESS_WRITES": "true"}):
            ok, _, _ = C.tool_permission(GRAPH_TOOLS[2])
            assert ok
    finally:
        CURRENT_USER.reset(tok)


def test_split_tools_and_denylist_parsing():
    with mock.patch.dict(os.environ, {"AGENT_MY_CONNECTIONS_WRITE_TOOLS": "",
                                      "AGENT_MY_CONNECTIONS_DENY": "31, x, 5"}):
        usable, denied = C.split_tools(GRAPH_TOOLS)
        assert [t["name"] for t in usable] == ["get_my_profile", "list_recent_emails"]
        assert [t["name"] for t in denied] == ["send_email", "mystery_tool"]
        assert all(t["_reason"] for t in denied)
        assert C.denied_servers() == {31, 5}


# ---------------------------------------------------------------------------
# list_my_connections
# ---------------------------------------------------------------------------

def test_list_my_connections_formats_both_pools_and_sends_identity():
    seen = {}

    async def fake_get(path, user):
        seen["path"] = path
        seen["headers"] = C._headers(user)
        return CATALOG, 200

    tok = _as()
    try:
        with mock.patch.object(C, "_get", fake_get), \
             mock.patch.dict(os.environ, {"AGENT_MY_CONNECTIONS_DENY": "",
                                          "CC_JWT_SECRET": "unit-secret"}):
            out = _txt(_run(C.list_my_connections.handler({})))
        assert seen["path"] == "/api/internal/my-connections"
        assert "X-AIHub-User" in seen["headers"], "the seam needs the signed assertion"
        assert "1 of 2 connected" in out
        assert "id 30 — EveriAI Graph — CONNECTED" in out and "authorized 2026-09-03" in out
        assert "id 31 — Google (TEST) — NOT CONNECTED" in out and "/my-connections" in out
        assert "get_connection_tools" in out
    finally:
        CURRENT_USER.reset(tok)


def test_list_my_connections_honours_denylist_and_empty_catalog():
    async def fake_get(path, user):
        return CATALOG, 200

    async def fake_empty(path, user):
        return {"status": "success", "connections": []}, 200

    tok = _as()
    try:
        with mock.patch.object(C, "_get", fake_get), \
             mock.patch.dict(os.environ, {"AGENT_MY_CONNECTIONS_DENY": "30", "CC_JWT_SECRET": "s"}):
            out = _txt(_run(C.list_my_connections.handler({})))
            assert "EveriAI" not in out and "Google" in out
        with mock.patch.object(C, "_get", fake_empty), \
             mock.patch.dict(os.environ, {"CC_JWT_SECRET": "s"}):
            res = _run(C.list_my_connections.handler({}))
            out = _txt(res)
            assert "No personal connections are published" in out
            assert "agent mailbox" in out          # steers away from the wrong inbox
    finally:
        CURRENT_USER.reset(tok)


def test_tools_refuse_without_a_real_user():
    tok = CURRENT_USER.set({"user_id": 0, "role": 2, "username": "agent-service"})
    try:
        res = _run(C.list_my_connections.handler({}))
        assert res.get("is_error") and "No signed-in user" in _txt(res)
        res = _run(C.use_my_connection.handler({"server_id": 30, "tool_name": "x"}))
        assert res.get("is_error")
    finally:
        CURRENT_USER.reset(tok)


def test_list_surfaces_identity_refusal_and_outage_honestly():
    async def fake_401(path, user):
        return {"status": "error", "code": "no_identity", "message": "bad assertion"}, 401

    async def fake_down(path, user):
        return {"message": "main app unreachable: boom"}, 0

    tok = _as()
    try:
        with mock.patch.object(C, "_get", fake_401), mock.patch.dict(os.environ, {"CC_JWT_SECRET": "s"}):
            res = _run(C.list_my_connections.handler({}))
            assert res.get("is_error") and "identity" in _txt(res) and "Nothing was read" in _txt(res)
        with mock.patch.object(C, "_get", fake_down), mock.patch.dict(os.environ, {"CC_JWT_SECRET": "s"}):
            res = _run(C.list_my_connections.handler({}))
            assert res.get("is_error") and "unreachable" in _txt(res)
    finally:
        CURRENT_USER.reset(tok)


# ---------------------------------------------------------------------------
# get_connection_tools
# ---------------------------------------------------------------------------

def _tools_ok(path, user):
    assert path == "/api/internal/my-connections/30/tools"
    return {"status": "success", "server_id": 30, "name": "EveriAI Graph",
            "tools": GRAPH_TOOLS, "tool_count": len(GRAPH_TOOLS)}, 200


def test_get_connection_tools_hides_denied_and_steers():
    async def fake_get(path, user):
        return _tools_ok(path, user)

    tok = _as()
    try:
        with mock.patch.object(C, "_get", fake_get), \
             mock.patch.dict(os.environ, {"AGENT_MY_CONNECTIONS_WRITE_TOOLS": "",
                                          "AGENT_MY_CONNECTIONS_DENY": "", "CC_JWT_SECRET": "s"}):
            out = _txt(_run(C.get_connection_tools.handler({"server_id": 30})))
        assert "- get_my_profile [read]" in out
        assert "- list_recent_emails [read]" in out and "limit:integer" in out
        assert "- send_email" not in out.split("NOT available")[0]   # hidden from the usable list
        assert "NOT available here (2): send_email, mystery_tool" in out
        assert "own address" in out                                   # steer to the agent mailbox
    finally:
        CURRENT_USER.reset(tok)


def test_get_connection_tools_shows_allowed_write_as_write():
    async def fake_get(path, user):
        return _tools_ok(path, user)

    tok = _as()
    try:
        with mock.patch.object(C, "_get", fake_get), \
             mock.patch.dict(os.environ, {"AGENT_MY_CONNECTIONS_WRITE_TOOLS": "send_email",
                                          "CC_JWT_SECRET": "s"}):
            out = _txt(_run(C.get_connection_tools.handler({"server_id": 30})))
        assert "- send_email [WRITE]" in out and "to*:string" in out
        assert "NOT available here (1): mystery_tool" in out
    finally:
        CURRENT_USER.reset(tok)


def test_get_connection_tools_not_authorized_not_found_denied():
    async def fake_needs_auth(path, user):
        return {"status": "needs_authorization", "connected": False,
                "message": "This user has not authorized 'EveriAI Graph' yet ... /my-connections"}, 200

    async def fake_404(path, user):
        return {"status": "error", "code": "not_found", "message": "nope"}, 404

    tok = _as()
    try:
        with mock.patch.object(C, "_get", fake_needs_auth), mock.patch.dict(os.environ, {"CC_JWT_SECRET": "s"}):
            res = _run(C.get_connection_tools.handler({"server_id": 30}))
            assert not res.get("is_error") and "/my-connections" in _txt(res)
        with mock.patch.object(C, "_get", fake_404), mock.patch.dict(os.environ, {"CC_JWT_SECRET": "s"}):
            res = _run(C.get_connection_tools.handler({"server_id": 99}))
            assert res.get("is_error") and "list_my_connections" in _txt(res)
        with mock.patch.dict(os.environ, {"AGENT_MY_CONNECTIONS_DENY": "30", "CC_JWT_SECRET": "s"}):
            res = _run(C.get_connection_tools.handler({"server_id": 30}))
            assert res.get("is_error") and "AGENT_MY_CONNECTIONS_DENY" in _txt(res)
        res = _run(C.get_connection_tools.handler({"server_id": "abc"}))
        assert res.get("is_error")
    finally:
        CURRENT_USER.reset(tok)


# ---------------------------------------------------------------------------
# use_my_connection
# ---------------------------------------------------------------------------

def test_use_my_connection_read_path_truncates_and_labels():
    posted = {}

    async def fake_get(path, user):
        return _tools_ok(path, user)

    async def fake_post(path, body, user, timeout=120.0):
        posted["path"], posted["body"] = path, body
        return {"status": "success", "server_id": 30, "tool_name": "list_recent_emails",
                "result": "X" * 3000}, 200

    tok = _as()
    try:
        with mock.patch.object(C, "_get", fake_get), mock.patch.object(C, "_post", fake_post), \
             mock.patch.dict(os.environ, {"AGENT_MY_CONNECTIONS_WRITE_TOOLS": "", "CC_JWT_SECRET": "s"}):
            res = _run(C.use_my_connection.handler(
                {"server_id": 30, "tool_name": "list_recent_emails",
                 "arguments_json": '{"limit": 3}'}))
        out = _txt(res)
        assert not res.get("is_error")
        assert posted["path"] == "/api/internal/my-connections/30/call"
        assert posted["body"]["tool_name"] == "list_recent_emails"
        assert posted["body"]["arguments"] == {"limit": 3}
        assert posted["body"]["context"]["source"] == "the_agent"
        assert posted["body"]["context"]["user_id"] == 13
        assert out.startswith("Read from the user's personal account 'EveriAI Graph'")
        assert "truncated — 3000 chars total" in out and out.count("X") == C.MAX_RESULT_CHARS
    finally:
        CURRENT_USER.reset(tok)


def test_use_my_connection_refuses_denied_write_without_calling_seam():
    calls = []

    async def fake_get(path, user):
        return _tools_ok(path, user)

    async def fake_post(path, body, user, timeout=120.0):
        calls.append(body)
        return {"status": "success", "result": "SENT"}, 200

    tok = _as()
    try:
        with mock.patch.object(C, "_get", fake_get), mock.patch.object(C, "_post", fake_post), \
             mock.patch.dict(os.environ, {"AGENT_MY_CONNECTIONS_WRITE_TOOLS": "", "CC_JWT_SECRET": "s"}):
            res = _run(C.use_my_connection.handler(
                {"server_id": 30, "tool_name": "send_email",
                 "arguments_json": '{"to": "a@b.c", "subject": "s", "body": "b"}'}))
            assert res.get("is_error") and "DENIED" in _txt(res)
            assert "send_email / draft_email_reply" in _txt(res)
            assert calls == [], "a denied write must never reach the seam"
            # undeclared tool: also denied, fail closed
            res = _run(C.use_my_connection.handler({"server_id": 30, "tool_name": "mystery_tool"}))
            assert res.get("is_error") and calls == []
            # unknown name: honest list of what exists
            res = _run(C.use_my_connection.handler({"server_id": 30, "tool_name": "nope"}))
            assert res.get("is_error") and "no tool named 'nope'" in _txt(res) and calls == []
    finally:
        CURRENT_USER.reset(tok)


def test_use_my_connection_allowed_write_is_labelled_write():
    async def fake_get(path, user):
        return _tools_ok(path, user)

    async def fake_post(path, body, user, timeout=120.0):
        return {"status": "success", "result": '{"status": "sent"}'}, 200

    tok = _as()
    try:
        with mock.patch.object(C, "_get", fake_get), mock.patch.object(C, "_post", fake_post), \
             mock.patch.dict(os.environ, {"AGENT_MY_CONNECTIONS_WRITE_TOOLS": "send_email",
                                          "CC_JWT_SECRET": "s"}):
            res = _run(C.use_my_connection.handler(
                {"server_id": 30, "tool_name": "send_email",
                 "arguments_json": '{"to": "a@b.c", "subject": "s", "body": "b"}'}))
        assert not res.get("is_error")
        assert _txt(res).startswith("WRITE done through the user's personal account")
    finally:
        CURRENT_USER.reset(tok)


def test_use_my_connection_headless_write_refused_read_allowed():
    calls = []

    async def fake_get(path, user):
        return _tools_ok(path, user)

    async def fake_post(path, body, user, timeout=120.0):
        calls.append(body["tool_name"])
        return {"status": "success", "result": "ok"}, 200

    tok = _as(mode="headless")
    try:
        with mock.patch.object(C, "_get", fake_get), mock.patch.object(C, "_post", fake_post), \
             mock.patch.dict(os.environ, {"AGENT_MY_CONNECTIONS_WRITE_TOOLS": "send_email",
                                          "AGENT_MY_CONNECTIONS_HEADLESS_WRITES": "", "CC_JWT_SECRET": "s"}):
            res = _run(C.use_my_connection.handler(
                {"server_id": 30, "tool_name": "send_email", "arguments_json": '{"to": "x"}'}))
            assert res.get("is_error") and "scheduled" in _txt(res) and calls == []
            res = _run(C.use_my_connection.handler({"server_id": 30, "tool_name": "list_recent_emails"}))
            assert not res.get("is_error") and calls == ["list_recent_emails"]
    finally:
        CURRENT_USER.reset(tok)


def test_use_my_connection_surfaces_needs_auth_and_tool_errors():
    async def fake_get(path, user):
        return _tools_ok(path, user)

    async def fake_needs(path, body, user, timeout=120.0):
        return {"status": "needs_authorization", "message": "re-connect at /my-connections"}, 200

    async def fake_err(path, body, user, timeout=120.0):
        return {"status": "error", "code": "tool_error", "message": "Graph said 403"}, 200

    tok = _as()
    try:
        with mock.patch.object(C, "_get", fake_get), mock.patch.object(C, "_post", fake_needs), \
             mock.patch.dict(os.environ, {"CC_JWT_SECRET": "s"}):
            res = _run(C.use_my_connection.handler({"server_id": 30, "tool_name": "get_my_profile"}))
            assert not res.get("is_error") and "/my-connections" in _txt(res)
        with mock.patch.object(C, "_get", fake_get), mock.patch.object(C, "_post", fake_err), \
             mock.patch.dict(os.environ, {"CC_JWT_SECRET": "s"}):
            res = _run(C.use_my_connection.handler({"server_id": 30, "tool_name": "get_my_profile"}))
            assert res.get("is_error") and "Graph said 403" in _txt(res) and "do not claim" in _txt(res)
        res = _run(C.use_my_connection.handler({"server_id": 30, "tool_name": "get_my_profile",
                                                "arguments_json": "not json"}))
        assert res.get("is_error") and "JSON" in _txt(res)
    finally:
        CURRENT_USER.reset(tok)


def test_headers_never_mint_for_service_principal():
    with mock.patch.dict(os.environ, {"CC_JWT_SECRET": "s"}):
        assert "X-AIHub-User" not in C._headers({"user_id": 0})
        assert "X-AIHub-User" not in C._headers({"user_id": None})
        assert "X-AIHub-User" in C._headers({"user_id": 13, "role": 2, "tenant_id": 1})


if __name__ == "__main__":
    if not HAVE_SDK:
        print(f"SKIP-ALL: {_IMPORT_ERR}")
        sys.exit(0)
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in fns:
        try:
            f()
            print(f"PASS  {n}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {n}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {n}: {e!r}")
    sys.exit(1 if failed else 0)
