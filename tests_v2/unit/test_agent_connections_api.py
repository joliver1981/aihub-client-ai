"""Unit pack for The Agent's My Connections screen backend
(agent_service/connections_api.py + its two routes in main.py): the list the
screen shows, what it is told about The Agent's own rules (blocked servers,
allowed write tools), Disconnect, the on/off switch, and identity handling.

All HTTP seams are monkeypatched; nothing touches the network. Runs standalone
(aihub-agent python test_agent_connections_api.py) or under pytest; self-skips
in envs without the real claude_agent_sdk / fastapi.
"""
import asyncio
import os
import sys
from unittest import mock

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

try:
    from fastapi import HTTPException          # noqa: E402
    import connection_tools as C               # noqa: E402
    import connections_api as A                # noqa: E402
    # a stub claude_agent_sdk (some envs carry one) has no real @tool
    HAVE_SDK = hasattr(C.list_my_connections, "handler")
    _IMPORT_ERR = None if HAVE_SDK else "claude_agent_sdk is a stub here"
except ImportError as e:
    HAVE_SDK = False
    _IMPORT_ERR = e

if not HAVE_SDK:
    try:
        import pytest
        pytestmark = pytest.mark.skip(
            reason=f"needs the aihub-agent env (claude_agent_sdk, fastapi): {_IMPORT_ERR}")
    except ImportError:
        pass


def _run(coro):
    return asyncio.run(coro)


USER = {"user_id": 349, "role": 1, "username": "ru_alex", "name": "Alex Rivera",
        "tenant_id": 1}
ADMIN = dict(USER, user_id=13, role=3, username="admin")
CATALOG = {"status": "success", "user_id": 349, "count": 2, "connections": [
    {"server_id": 30, "name": "Acme Microsoft 365", "description": "Mail and calendar",
     "category": "Productivity", "icon": "fab fa-microsoft", "connected": True,
     "last_connected": "2026-09-03T12:57:47", "scope": "Mail.Read Calendars.Read",
     # a careless seam must not be able to leak these through the screen
     "server_url": "http://127.0.0.1:5001/api/internal/mcp/graph",
     "connection_config": "{\"secret\": \"x\"}"},
    {"server_id": 31, "name": "Google (TEST)", "description": None, "category": None,
     "icon": None, "connected": False, "last_connected": None, "scope": "gmail.readonly"},
]}
ENV = {"AGENT_MY_CONNECTIONS": "true", "AGENT_MY_CONNECTIONS_DENY": "",
       "AGENT_MY_CONNECTIONS_WRITE_TOOLS": "", "CC_JWT_SECRET": "unit-secret"}


def _raises(coro, status):
    try:
        _run(coro)
    except HTTPException as e:
        assert e.status_code == status, f"expected {status}, got {e.status_code}: {e.detail}"
        return str(e.detail)
    raise AssertionError(f"expected HTTP {status}, nothing was raised")


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

def test_list_is_the_users_catalog_as_them_without_connection_internals():
    seen = {}

    async def fake_get(path, user):
        seen["path"] = path
        seen["headers"] = C._headers(user)
        return CATALOG, 200

    with mock.patch.object(A, "_get", fake_get), mock.patch.dict(os.environ, ENV):
        out = _run(A.list_for(USER))
    assert seen["path"] == "/api/internal/my-connections"
    assert "X-AIHub-User" in seen["headers"], "the list is asked for AS the user"
    assert [c["server_id"] for c in out["connections"]] == [30, 31]
    first = out["connections"][0]
    assert first["connected"] is True and first["scope"] == "Mail.Read Calendars.Read"
    assert first["agent_blocked"] is False
    assert "server_url" not in first and "connection_config" not in first
    assert out["connections"][1]["connected"] is False
    assert out["write_tools"] == [] and out["can_publish"] is False
    # who the list is for, from the verified token: the Connect popup hands it
    # to AI Hub, which refuses when someone else is signed in there
    assert out["user_id"] == 349


def test_list_reports_the_agents_own_rules_and_who_can_publish():
    async def fake_get(path, user):
        return CATALOG, 200

    env = dict(ENV, AGENT_MY_CONNECTIONS_DENY="31",
               AGENT_MY_CONNECTIONS_WRITE_TOOLS="send_email, create_event")
    with mock.patch.object(A, "_get", fake_get), mock.patch.dict(os.environ, env):
        out = _run(A.list_for(ADMIN))
    by_id = {c["server_id"]: c for c in out["connections"]}
    # a blocked server is still SHOWN (other agents use it) but flagged
    assert by_id[31]["agent_blocked"] is True and by_id[30]["agent_blocked"] is False
    assert out["write_tools"] == ["create_event", "send_email"]
    assert out["can_publish"] is True


def test_list_with_nothing_published_is_an_empty_list_not_an_error():
    async def fake_get(path, user):
        return {"status": "success", "user_id": 349, "count": 0, "connections": []}, 200

    with mock.patch.object(A, "_get", fake_get), mock.patch.dict(os.environ, ENV):
        out = _run(A.list_for(USER))
    assert out["connections"] == []


def test_list_failures_are_reported_never_shown_as_an_empty_list():
    async def unreachable(path, user):
        return {"message": "main app unreachable: boom"}, 0

    async def refused(path, user):
        return {"status": "error", "code": "no_identity", "message": "bad assertion"}, 401

    async def odd(path, user):
        return {"status": "error", "message": "db down"}, 200

    with mock.patch.dict(os.environ, ENV):
        with mock.patch.object(A, "_get", unreachable):
            assert "unreachable" in _raises(A.list_for(USER), 502)
        with mock.patch.object(A, "_get", refused):
            assert "identity" in _raises(A.list_for(USER), 502)
        with mock.patch.object(A, "_get", odd):
            _raises(A.list_for(USER), 502)


# ---------------------------------------------------------------------------
# switch + identity
# ---------------------------------------------------------------------------

def test_switch_off_means_no_screen_and_no_calls():
    called = []

    async def fake(*a, **k):
        called.append(a)
        return CATALOG, 200

    with mock.patch.object(A, "_get", fake), mock.patch.object(A, "_post", fake), \
         mock.patch.dict(os.environ, dict(ENV, AGENT_MY_CONNECTIONS="false")):
        assert A.enabled() is False
        _raises(A.list_for(USER), 404)
        _raises(A.disconnect(USER, 30), 404)
    assert called == [], "nothing may reach the platform while the switch is off"
    with mock.patch.dict(os.environ, ENV):
        assert A.enabled() is True
    env = dict(ENV)
    env.pop("AGENT_MY_CONNECTIONS")
    with mock.patch.dict(os.environ, env):
        os.environ.pop("AGENT_MY_CONNECTIONS", None)
        assert A.enabled() is True, "on by default, like the chat tools"


def test_no_real_user_is_refused_before_any_call():
    called = []

    async def fake(*a, **k):
        called.append(a)
        return CATALOG, 200

    with mock.patch.object(A, "_get", fake), mock.patch.object(A, "_post", fake), \
         mock.patch.dict(os.environ, ENV):
        for nobody in ({"user_id": 0, "role": 3}, {"user_id": None}, {"user_id": "x"}, {}):
            _raises(A.list_for(nobody), 403)
            _raises(A.disconnect(nobody, 30), 403)
    assert called == []


# ---------------------------------------------------------------------------
# disconnect
# ---------------------------------------------------------------------------

def test_disconnect_posts_as_the_user_and_reports_success():
    seen = {}

    async def fake_post(path, body, user, timeout=120.0):
        seen.update(path=path, body=body, headers=C._headers(user))
        return {"status": "success", "server_id": 30, "connected": False}, 200

    with mock.patch.object(A, "_post", fake_post), mock.patch.dict(os.environ, ENV):
        out = _run(A.disconnect(USER, "30"))
    assert seen["path"] == "/api/internal/my-connections/30/disconnect"
    assert "user_id" not in seen["body"], "who it is for travels in the signed assertion only"
    assert "X-AIHub-User" in seen["headers"]
    assert out == {"status": "success", "server_id": 30, "connected": False}


def test_disconnect_failures_are_never_reported_as_done():
    async def old_main_app(path, body, user, timeout=120.0):
        return {"message": "<!doctype html><title>404 Not Found</title>"}, 404

    async def failed(path, body, user, timeout=120.0):
        return {"status": "error", "code": "server", "message": "db down"}, 500

    async def unreachable(path, body, user, timeout=120.0):
        return {"message": "main app unreachable: boom"}, 0

    with mock.patch.dict(os.environ, ENV):
        with mock.patch.object(A, "_post", old_main_app):
            detail = _raises(A.disconnect(USER, 30), 502)
            assert "classic app" in detail and "<!doctype" not in detail
        with mock.patch.object(A, "_post", failed):
            assert "db down" in _raises(A.disconnect(USER, 30), 502)
        with mock.patch.object(A, "_post", unreachable):
            assert "unreachable" in _raises(A.disconnect(USER, 30), 502)


# ---------------------------------------------------------------------------
# where the assistant sends people
# ---------------------------------------------------------------------------

def test_the_assistant_points_at_its_own_screen_and_the_page_handles_that_link():
    assert "#connections" in C.CONNECT_LINK and "left rail" in C.CONNECT_WHERE
    text = C._needs_auth_text("This user has not authorized 'X' yet. Detail: expired")
    assert "expired" in text and C.CONNECT_LINK in text
    assert C.CONNECT_LINK in C._needs_auth_text("")
    page = open(os.path.join(APP_ROOT, "agent_service", "static", "index.html"),
                encoding="utf-8").read()
    # the rail entry, the screen, and the handler for the link the tools hand out
    assert 'id="nav-connections"' in page and 'id="view-connections"' in page
    assert '!== "connections"' in page and "hashchange" in page
    brain = open(os.path.join(APP_ROOT, "agent_service", "brain.py"), encoding="utf-8").read()
    assert C.CONNECT_LINK in brain
    assert "(/my-connections)" not in brain, "the classic page is not where The Agent sends people"


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
