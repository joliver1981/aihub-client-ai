"""WI-4 — "Available to users" enforcement and its column-missing fallback.

  * mcp_server_visibility: the helpers fall back to VISIBLE when migration 020
    has not been applied (or anything errors), and read the flag when it has.
  * my_connections listing: the SQL filters on available_to_users only when the
    column exists.
  * mcp_routes.update_server: blank secret keeps the stored secret; blank
    non-secret keys are cleared (replace-all semantics preserved).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.api


# ---------------------------------------------------------------------------
# helpers module
# ---------------------------------------------------------------------------

@pytest.fixture
def vis():
    import builder_mcp.agent_integration.mcp_server_visibility as v
    v.reset_cache()
    yield v
    v.reset_cache()


def _cursor(column_count=None, flag_row=None, fail=False):
    cur = MagicMock()
    results = {}

    def execute(sql, *params):
        if fail:
            raise RuntimeError("db down")
        if "INFORMATION_SCHEMA.COLUMNS" in sql:
            results["row"] = (column_count,)
        elif "SELECT available_to_users" in sql:
            results["row"] = flag_row
        else:
            results["row"] = None

    cur.execute.side_effect = execute
    cur.fetchone.side_effect = lambda: results.get("row")
    return cur


def test_column_missing_means_visible(vis):
    cur = _cursor(column_count=0)
    assert vis.has_available_to_users_column(cur) is False
    assert vis.server_available_to_users(30, cursor=cur) is True


def test_column_present_reads_the_flag(vis):
    assert vis.server_available_to_users(30, cursor=_cursor(1, (1,))) is True
    vis.reset_cache()
    assert vis.server_available_to_users(30, cursor=_cursor(1, (0,))) is False
    vis.reset_cache()
    assert vis.server_available_to_users(30, cursor=_cursor(1, None)) is True, "no row → leave it to the 404 path"


def test_db_error_means_visible_and_column_missing(vis):
    cur = _cursor(fail=True)
    assert vis.has_available_to_users_column(cur) is False
    assert vis.server_available_to_users(30, cursor=cur) is True


def test_column_check_is_cached(vis):
    cur = _cursor(column_count=1)
    assert vis.has_available_to_users_column(cur) is True
    assert vis.has_available_to_users_column(_cursor(column_count=0)) is True, "cached for 5 min"
    assert vis.has_available_to_users_column(_cursor(column_count=0), force=True) is False


@pytest.mark.parametrize("value, expected", [
    (True, 1), (False, 0), (1, 1), (0, 0), ("true", 1), ("off", 0), ("", 0), (None, None), ("maybe", None),
])
def test_coerce_flag(vis, value, expected):
    assert vis.coerce_flag(value) == expected


# ---------------------------------------------------------------------------
# listing route
# ---------------------------------------------------------------------------

class FakeUser(SimpleNamespace):
    is_authenticated = True
    is_active = True
    is_anonymous = False

    def get_id(self):
        return str(self.id)


@pytest.fixture
def listing_app(monkeypatch):
    from flask import Flask
    from flask_login import LoginManager
    import builder_mcp.routes.my_connections_routes as mc
    import builder_mcp.agent_integration.oauth_manager as om
    import builder_mcp.agent_integration.mcp_server_visibility as vis

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test"
    lm = LoginManager()
    lm.init_app(app)
    lm.request_loader(lambda req: FakeUser(id=7, role=1))
    app.register_blueprint(mc.my_connections_bp)

    executed = []
    cur = MagicMock()

    def execute(sql, *params):
        executed.append(sql)
    cur.execute.side_effect = execute
    cur.fetchall.return_value = [(30, "Microsoft 365", "mail", "Productivity", "fab fa-microsoft")]
    conn = MagicMock()
    conn.cursor.return_value = cur
    monkeypatch.setattr(mc, "get_db_connection", lambda: conn)
    monkeypatch.setattr(om, "_load_server_config",
                        lambda sid: {"oauth_grant_type": "authorization_code", "oauth_scope": "Mail.Read"})
    monkeypatch.setattr(om, "has_user_token", lambda sid, uid: False)
    state = SimpleNamespace(app=app, client=app.test_client(), executed=executed, vis=vis)
    return state


def _select_sql(executed):
    return next(s for s in executed if "FROM MCPServers" in s)


def test_listing_filters_on_the_flag_when_the_column_exists(listing_app, monkeypatch):
    monkeypatch.setattr(listing_app.vis, "has_available_to_users_column", lambda cursor=None, force=False: True)
    r = listing_app.client.get("/api/my-connections/servers")
    assert r.status_code == 200 and r.get_json()[0]["server_id"] == 30
    assert "available_to_users = 1" in _select_sql(listing_app.executed)


def test_listing_falls_back_to_visible_without_the_column(listing_app, monkeypatch):
    monkeypatch.setattr(listing_app.vis, "has_available_to_users_column", lambda cursor=None, force=False: False)
    r = listing_app.client.get("/api/my-connections/servers")
    assert r.status_code == 200 and len(r.get_json()) == 1
    assert "available_to_users" not in _select_sql(listing_app.executed)


# ---------------------------------------------------------------------------
# update_server: blank secret keeps the stored value
# ---------------------------------------------------------------------------

def _flat(params):
    """pyodbc accepts both execute(sql, a, b) and execute(sql, (a, b)); normalise."""
    if len(params) == 1 and isinstance(params[0], (tuple, list)):
        return tuple(params[0])
    return tuple(params)


@pytest.fixture
def admin_routes(monkeypatch):
    import importlib
    from functools import wraps
    import role_decorators as rd

    def stub(permissions=None, min_role=None):
        def decorator(f):
            @wraps(f)
            def wrapper(*a, **kw):
                from flask import g
                g.auth_method = "session"
                return f(*a, **kw)
            return wrapper
        return decorator

    orig = rd.api_key_or_session_required
    rd.api_key_or_session_required = stub
    import builder_mcp.routes.mcp_routes as m
    m = importlib.reload(m)
    yield m
    rd.api_key_or_session_required = orig
    importlib.reload(m)


def test_update_keeps_secret_on_blank_and_clears_blank_non_secrets(admin_routes, monkeypatch):
    from flask import Flask
    import builder_mcp.agent_integration.mcp_server_visibility as vis
    m = admin_routes
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test"
    app.register_blueprint(m.mcp_bp)

    executed = []
    cur = MagicMock()
    cur.execute.side_effect = lambda sql, *p: executed.append((" ".join(sql.split()), _flat(p)))
    cur.fetchone.return_value = (30, "remote")
    conn = MagicMock()
    conn.cursor.return_value = cur
    monkeypatch.setattr(m, "get_db_connection", lambda: conn)
    monkeypatch.setattr(m, "_get_encryption_key", lambda: "enc")
    monkeypatch.setattr(vis, "has_available_to_users_column", lambda cursor=None, force=False: True)

    payload = {
        "server_type": "remote", "server_name": "M365", "server_url": "http://127.0.0.1:5001/x",
        "auth_type": "oauth2", "available_to_users": True,
        "auth_config": {
            "oauth_grant_type": "authorization_code", "oauth_client_id": "cid",
            "oauth_client_secret": "",          # blank → KEEP the stored secret
            "oauth_redirect_uri": "",           # blank → cleared (non-secret)
            "oauth_scope": "Mail.Read",
        },
    }
    r = app.test_client().put("/api/mcp/servers/30", json=payload)
    assert r.status_code == 200, r.data

    deletes = [(s, p) for s, p in executed if s.startswith("DELETE FROM MCPServerCredentials")]
    assert len(deletes) == 1
    sql, params = deletes[0]
    assert "credential_key NOT IN (?)" in sql and params == (30, "oauth_client_secret")

    inserted = sorted(p[1] for s, p in executed if s.startswith("INSERT INTO MCPServerCredentials"))
    # (the test client serialises JSON with sorted keys, so compare as sets)
    assert inserted == ["oauth_client_id", "oauth_grant_type", "oauth_scope"], \
        "blank keys are not re-inserted; the kept secret survives the DELETE"

    flag_updates = [p for s, p in executed if s.startswith("UPDATE MCPServers SET available_to_users")]
    assert flag_updates == [(1, 30)]


def test_update_without_blank_secret_replaces_everything(admin_routes, monkeypatch):
    from flask import Flask
    import builder_mcp.agent_integration.mcp_server_visibility as vis
    m = admin_routes
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test"
    app.register_blueprint(m.mcp_bp)
    executed = []
    cur = MagicMock()
    cur.execute.side_effect = lambda sql, *p: executed.append((" ".join(sql.split()), _flat(p)))
    cur.fetchone.return_value = (30, "remote")
    conn = MagicMock()
    conn.cursor.return_value = cur
    monkeypatch.setattr(m, "get_db_connection", lambda: conn)
    monkeypatch.setattr(m, "_get_encryption_key", lambda: "enc")
    monkeypatch.setattr(vis, "has_available_to_users_column", lambda cursor=None, force=False: False)

    payload = {"server_type": "remote", "server_name": "M365", "server_url": "u", "auth_type": "oauth2",
               "available_to_users": False,
               "auth_config": {"oauth_client_secret": "new-secret", "oauth_client_id": "cid"}}
    assert app.test_client().put("/api/mcp/servers/30", json=payload).status_code == 200
    deletes = [(s, p) for s, p in executed if s.startswith("DELETE FROM MCPServerCredentials")]
    assert deletes == [("DELETE FROM MCPServerCredentials WHERE server_id = ?", (30,))]
    assert not [s for s, p in executed if s.startswith("UPDATE MCPServers SET available_to_users")], \
        "column absent → the switch is a no-op, never an error"
