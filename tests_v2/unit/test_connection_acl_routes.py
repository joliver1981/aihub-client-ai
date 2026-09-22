"""/get/connections + /api/discover/query/<id> — the connection ACL for
delegated callers (docs/handoff-the-agent-connection-acl.md §5 item 2,
2026-09-22).

THE CONTRACT (the doc-acl G1/G2 idiom, applied to connections):
  * X-AIHub-User ABSENT          -> unchanged: every row / any connection
                                    (browser sessions, CC, scheduler)
  * PRESENT, role >= floor (2)   -> unchanged, and the resolver is NOT called
  * PRESENT, role 1 with grants  -> listing scoped to the granted ids; a
                                    discover call on a granted id runs, on any
                                    other id answers 403 access:denied BEFORE
                                    any database work
  * PRESENT, role 1, no grants   -> empty listing; every discover call 403
  * PRESENT and invalid          -> 403, never "treat as missing"
  * CONNECTION_ACL_ENFORCE=false -> unchanged behaviour, resolver still runs
                                    (dry-run log line)

Route source is lifted straight out of app.py (app_route_harness); the
resolver is a recorder so no database is touched (connection_acl has its own
test). Force-add to git (gitignore hides test*.py).
"""
import json
import logging
import os
import sys
import types
from pathlib import Path

import pytest
from flask import Flask, jsonify, request

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

pytest.importorskip("jwt", reason="PyJWT not installed in this environment")
pd = pytest.importorskip("pandas")

import shared_auth as sa  # noqa: E402
from app_route_harness import load_app_symbols  # noqa: E402

pytestmark = pytest.mark.unit

_SECRET = "unit-test-secret-key-please-do-not-ship-0123456789"
# Dev-tenant shapes (2026-09-22): six connections; a regular user in the
# Analysts group reaches the five behind shared Data Assistants, PHARMA (168)
# has no data agent at all.
CONNECTIONS = [
    {"id": 5, "connection_name": "EDW (SQL Server)", "connection_string": "", "password": ""},
    {"id": 20, "connection_name": "ERPDB", "connection_string": "", "password": ""},
    {"id": 168, "connection_name": "PHARMA", "connection_string": "", "password": ""},
]
GRANTS = {13: [5, 20], 10: []}      # ru_alex-like seat / no-group seat


def _assertion(uid, role):
    return sa.sign_user_assertion(uid, "t1", role)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CC_JWT_SECRET", _SECRET)
    monkeypatch.delenv("CONNECTION_ACL_ENFORCE", raising=False)
    monkeypatch.delenv("CONNECTION_ACL_UNRESTRICTED_ROLE", raising=False)


@pytest.fixture
def resolver_calls(monkeypatch):
    """A fake `connection_acl` module: records every resolver call."""
    calls = []

    def accessible_connection_ids(user_id, user_role=None):
        calls.append((int(user_id), user_role))
        return list(GRANTS.get(int(user_id), []))

    def unrestricted_from_role():
        try:
            return int(os.getenv("CONNECTION_ACL_UNRESTRICTED_ROLE", "2") or 2)
        except (TypeError, ValueError):
            return 2

    fake = types.ModuleType("connection_acl")
    fake.accessible_connection_ids = accessible_connection_ids
    fake.unrestricted_from_role = unrestricted_from_role
    monkeypatch.setitem(sys.modules, "connection_acl", fake)
    return calls


@pytest.fixture
def platform(monkeypatch):
    """Stubs for everything the two routes touch besides the ACL."""
    state = {"conn_str_calls": []}

    secrets = types.ModuleType("connection_secrets")
    secrets.is_secret_reference = lambda p: False
    secrets.mask_connection_string_password = lambda s: s
    monkeypatch.setitem(sys.modules, "connection_secrets", secrets)

    local = types.ModuleType("local_secrets")
    local.get_secrets_manager = lambda: None
    monkeypatch.setitem(sys.modules, "local_secrets", local)

    gate = types.ModuleType("sql_gate")

    def gate_sql(sql, database_type=None, row_cap=None):
        return types.SimpleNamespace(ok=True, sql=sql, cap_applied=False, reason=None)
    gate.gate_sql = gate_sql
    monkeypatch.setitem(sys.modules, "sql_gate", gate)

    def get_database_connection_string(connection_id):
        state["conn_str_calls"].append(int(connection_id))
        return "Driver=fake", int(connection_id), "SQL Server"

    def execute_sql_query_v2(sql, conn_str):
        return pd.DataFrame([{"n": 1}, {"n": 2}]), None

    state["get_database_connection_string"] = get_database_connection_string
    state["execute_sql_query_v2"] = execute_sql_query_v2
    return state


@pytest.fixture
def client(platform, resolver_calls):
    ns = {
        "request": request, "jsonify": jsonify, "os": os,
        "logger": logging.getLogger("t"),
        "cfg": types.SimpleNamespace(DISCOVER_QUERY_ROW_CAP=50,
                                     DISCOVER_QUERY_MAX_COLS=30,
                                     DISCOVER_QUERY_MAX_CELL=200),
        "Get_Connection": lambda: pd.DataFrame(CONNECTIONS),
        "dataframe_to_json": lambda df: df.to_json(orient="records"),
        "get_database_connection_string": platform["get_database_connection_string"],
        "execute_sql_query_v2": platform["execute_sql_query_v2"],
    }
    load_app_symbols(["_InvalidUserAssertion", "_caller_identity",
                      "_connection_acl_enforced", "_caller_connection_scope",
                      "_connection_denied_response", "_connection_access_refusal",
                      "get_connections", "discover_query_api"], ns)
    app = Flask("t")
    app.add_url_rule("/get/connections", "get_connections", ns["get_connections"])
    app.add_url_rule("/api/discover/query/<int:connection_id>", "discover_query",
                     ns["discover_query_api"], methods=["POST"])
    return app.test_client()


def _ids(resp):
    assert resp.status_code == 200, resp.data
    rows = json.loads(resp.get_json())
    return sorted(int(r["id"]) for r in rows)


def _query(client, cid, headers=None):
    return client.post(f"/api/discover/query/{cid}", json={"sql": "select 1"},
                       headers=headers or {})


# ------------------------------------------------------------- /get/connections

def test_absent_assertion_is_unchanged(client, resolver_calls):
    assert _ids(client.get("/get/connections")) == [5, 20, 168]
    assert resolver_calls == []


def test_role1_listing_is_scoped_to_granted_connections(client, resolver_calls):
    h = {"X-AIHub-User": _assertion(13, 1)}
    assert _ids(client.get("/get/connections", headers=h)) == [5, 20]
    assert resolver_calls == [(13, 1)]


def test_role1_without_grants_gets_an_empty_list_not_everything(client):
    h = {"X-AIHub-User": _assertion(10, 1)}
    assert _ids(client.get("/get/connections", headers=h)) == []


def test_role2_is_unrestricted_and_the_resolver_is_not_called(client, resolver_calls):
    h = {"X-AIHub-User": _assertion(141, 2)}
    assert _ids(client.get("/get/connections", headers=h)) == [5, 20, 168]
    assert resolver_calls == []


def test_forged_assertion_is_a_hard_403(client):
    r = client.get("/get/connections", headers={"X-AIHub-User": "not.a.jwt"})
    assert r.status_code == 403


def test_kill_switch_restores_the_old_behaviour(client, resolver_calls, monkeypatch):
    monkeypatch.setenv("CONNECTION_ACL_ENFORCE", "false")
    h = {"X-AIHub-User": _assertion(10, 1)}
    assert _ids(client.get("/get/connections", headers=h)) == [5, 20, 168]
    assert resolver_calls == [(10, 1)]      # dry run still resolves (log line)


def test_floor_override_restricts_developers_too(client, monkeypatch):
    monkeypatch.setenv("CONNECTION_ACL_UNRESTRICTED_ROLE", "3")
    h = {"X-AIHub-User": _assertion(13, 2)}
    assert _ids(client.get("/get/connections", headers=h)) == [5, 20]
    h = {"X-AIHub-User": _assertion(1, 3)}
    assert _ids(client.get("/get/connections", headers=h)) == [5, 20, 168]


# ------------------------------------------------------ /api/discover/query/<id>

def test_query_absent_assertion_runs_on_any_connection(client, platform):
    r = _query(client, 168)
    assert r.status_code == 200 and r.get_json()["success"] is True
    assert r.get_json()["row_count"] == 2
    assert platform["conn_str_calls"] == [168]


def test_query_role1_granted_connection_runs(client, platform):
    r = _query(client, 20, {"X-AIHub-User": _assertion(13, 1)})
    assert r.status_code == 200 and r.get_json()["success"] is True
    assert platform["conn_str_calls"] == [20]


def test_query_role1_other_connection_is_denied_before_any_db_work(client, platform):
    r = _query(client, 168, {"X-AIHub-User": _assertion(13, 1)})
    assert r.status_code == 403
    body = r.get_json()
    assert body["success"] is False and body["access"] == "denied"
    # honest wording: an access restriction, not "does not exist"
    assert "not shared with your account" in body["error"]
    assert "access restriction" in body["error"]
    assert "Groups page" in body["error"]
    assert platform["conn_str_calls"] == []


def test_query_role1_without_grants_is_denied_everywhere(client, platform):
    for cid in (5, 20, 168):
        r = _query(client, cid, {"X-AIHub-User": _assertion(10, 1)})
        assert r.status_code == 403 and r.get_json()["access"] == "denied"
    assert platform["conn_str_calls"] == []


def test_query_role2_is_unrestricted(client, platform, resolver_calls):
    r = _query(client, 168, {"X-AIHub-User": _assertion(141, 2)})
    assert r.status_code == 200 and r.get_json()["success"] is True
    assert resolver_calls == []


def test_query_forged_assertion_is_403_before_any_db_work(client, platform):
    r = _query(client, 20, {"X-AIHub-User": "garbage"})
    assert r.status_code == 403
    assert platform["conn_str_calls"] == []


def test_query_kill_switch_restores_the_old_behaviour(client, platform, monkeypatch):
    monkeypatch.setenv("CONNECTION_ACL_ENFORCE", "0")
    r = _query(client, 168, {"X-AIHub-User": _assertion(10, 1)})
    assert r.status_code == 200 and r.get_json()["success"] is True
