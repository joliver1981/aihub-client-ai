"""
The Agent's read-through layer (agent_service/readthrough.py) used to open the app
database directly with DATABASE_* environment variables. Those exist in the dev
tree's .env but on an install live only inside the frozen exes' baked
_build_config, so on every client the agent-builder tools, group visibility,
pending approvals and the user directory failed with "Login failed for user ''"
(pack-20 per-tool smoke against Latest7, 2026-09-03).

Since 2026-09-03 the service asks the main app's internal read-through endpoint
first and falls back to its old direct-SQL path only when that endpoint is not
usable. These tests pin the fallback contract, which is what keeps every
existing deployment behaving exactly as before:
  - 200 with status=success  -> the HTTP data is used, SQL is never touched
  - 404 (older main app) / 401 (key rejected) / unreachable -> the SQL thunk runs
  - 500 / non-success body   -> a real error surfaces (never a silent fallback)
  - AGENT_READTHROUGH_HTTP=false -> SQL, the rollback switch
"""
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
for p in (REPO, os.path.join(REPO, "agent_service")):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    import claude_agent_sdk  # noqa: F401  (the aihub-agent env)
except ImportError:
    # platform_tools registers its tools with the SDK at import; a minimal stub
    # lets the pure-Python helper under test import in the main env.
    import types
    _sdk = types.ModuleType("claude_agent_sdk")

    def _tool(*_a, **_k):
        return lambda fn: fn
    _sdk.tool = _tool
    _sdk.create_sdk_mcp_server = lambda *a, **k: None
    sys.modules["claude_agent_sdk"] = _sdk

import readthrough  # noqa: E402
import platform_tools  # noqa: E402


class _Resp:
    def __init__(self, status, body=None, text=""):
        self.status_code = status
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class _Client:
    """Stands in for httpx.Client: one canned response, or an exception."""
    resp = None
    raise_exc = None
    calls = []

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, json=None, headers=None):
        _Client.calls.append({"url": url, "json": json, "headers": headers})
        if _Client.raise_exc:
            raise _Client.raise_exc
        return _Client.resp


@pytest.fixture(autouse=True)
def _stub_http(monkeypatch):
    monkeypatch.setattr(readthrough.httpx, "Client", _Client)
    monkeypatch.delenv("AGENT_READTHROUGH_HTTP", raising=False)
    _Client.resp, _Client.raise_exc, _Client.calls = None, None, []
    yield


def _sql_thunk(marker="from-sql"):
    calls = []

    def thunk():
        calls.append(1)
        return marker
    thunk.calls = calls
    return thunk


def test_http_success_wins_and_sql_is_not_touched():
    _Client.resp = _Resp(200, {"status": "success", "data": [7, 8]})
    thunk = _sql_thunk()
    assert readthrough.fetch_or_sql("user_group_ids", thunk, user_id=13) == [7, 8]
    assert thunk.calls == []
    call = _Client.calls[0]
    assert call["url"].endswith("/api/internal/readthrough")
    assert call["json"] == {"op": "user_group_ids", "params": {"user_id": 13}}
    assert "X-Internal-API-Key" in call["headers"] and "X-API-Key" in call["headers"]


@pytest.mark.parametrize("status", [404, 401])
def test_missing_route_or_rejected_key_falls_back_to_sql(status):
    _Client.resp = _Resp(status, {"status": "error"})
    thunk = _sql_thunk()
    assert readthrough.fetch_or_sql("agents", thunk) == "from-sql"
    assert thunk.calls == [1]


def test_unreachable_main_app_falls_back_to_sql():
    _Client.raise_exc = ConnectionError("refused")
    thunk = _sql_thunk()
    assert readthrough.fetch_or_sql("groups", thunk) == "from-sql"
    assert thunk.calls == [1]


def test_server_failure_is_an_error_not_a_silent_fallback():
    _Client.resp = _Resp(500, {"status": "error", "message": "boom"})
    thunk = _sql_thunk()
    with pytest.raises(RuntimeError, match="boom"):
        readthrough.fetch_or_sql("agents", thunk)
    assert thunk.calls == []


def test_rollback_switch_forces_sql(monkeypatch):
    monkeypatch.setenv("AGENT_READTHROUGH_HTTP", "false")
    _Client.resp = _Resp(200, {"status": "success", "data": ["http"]})
    thunk = _sql_thunk()
    assert readthrough.fetch_or_sql("users", thunk) == "from-sql"
    assert _Client.calls == []


def test_user_group_ids_degrades_to_empty_when_both_paths_fail(monkeypatch):
    _Client.resp = _Resp(404)
    monkeypatch.setattr(readthrough, "_db", lambda: (_ for _ in ()).throw(RuntimeError("no creds")))
    assert readthrough.user_group_ids(13) == []


def test_filter_users_matches_the_old_sql_side_filter():
    rows = [{"id": 1, "name": "Ada Lovelace", "username": "ada", "email": "ada@x.io", "phone": ""},
            {"id": 2, "name": "Grace Hopper", "username": "grace", "email": "g@x.io", "phone": ""}]
    assert [u["id"] for u in platform_tools._filter_users(rows, "")] == [1, 2]
    assert [u["id"] for u in platform_tools._filter_users(rows, "grace")] == [2]
    assert [u["id"] for u in platform_tools._filter_users(rows, "ada@x.io")] == [1]
    assert [u["id"] for u in platform_tools._filter_users(rows, "lovelace ada")] == [1]
