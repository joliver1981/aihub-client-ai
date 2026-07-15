"""
Code Flows internal_manage dispatch (codeflows/api.py) — the CC-facing
service-to-service authoring endpoint. Flask test client with the manager
backed by the in-memory stub, so the dispatch + auth/role gates are exercised
without a DB or a login session.
"""
from __future__ import annotations

import sys

import pytest
from flask import Flask

import codeflows.api as cf_api
from tests_v2.unit.test_code_flows_manager import _MemManager

pytestmark = pytest.mark.unit

DEV = {"user_id": 7, "role": 2, "username": "dev"}


@pytest.fixture
def client(monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(cf_api.code_flows_bp)
    mem = _MemManager()
    monkeypatch.setattr(cf_api, "_manager", mem)
    monkeypatch.setattr(cf_api, "_service_key_ok", lambda k: k == "good-key")
    monkeypatch.setattr(cf_api.cfg, "AUTOMATIONS_ENABLED", True, raising=False)
    c = app.test_client()
    c._mem = mem
    return c


def _manage(client, action, payload=None, uc=DEV, key="good-key"):
    return client.post("/codeflows/api/internal/manage",
                       json={"action": action, "user_context": uc, "payload": payload or {}},
                       headers={"X-API-Key": key})


# ------------------------------------------------------------------- auth

def test_bad_service_key_401(client):
    r = _manage(client, "list", key="nope")
    assert r.status_code == 401


def test_role_below_developer_403(client):
    r = _manage(client, "list", uc={"user_id": 5, "role": 1, "username": "u"})
    assert r.status_code == 403


def test_anonymous_user_id_403(client):
    r = _manage(client, "list", uc={"user_id": 0, "role": 2, "username": "u"})
    assert r.status_code == 403


def test_feature_disabled_403(client, monkeypatch):
    monkeypatch.setattr(cf_api.cfg, "AUTOMATIONS_ENABLED", False, raising=False)
    r = _manage(client, "list")
    assert r.status_code == 403


# ----------------------------------------------------------- authoring flow

def test_full_authoring_flow_through_dispatch(client):
    # create
    r = _manage(client, "create", {"name": "recon", "description": "reconcile"})
    assert r.status_code == 201, r.get_json()

    # add two steps (name travels in every authoring payload)
    r1 = _manage(client, "add_step", {"name": "recon", "step_name": "pull", "code": "print(1)",
                                      "connections": ["ERPDB"]})
    assert r1.status_code == 201, r1.get_json()
    s1 = r1.get_json()["step_id"]
    r2 = _manage(client, "add_step", {"name": "recon", "step_name": "push", "code": "print(2)"})
    assert r2.status_code == 201, r2.get_json()
    s2 = r2.get_json()["step_id"]

    # wire s1 -> s2 on pass
    rw = _manage(client, "wire", {"name": "recon", "from_step": s1, "to_step": s2, "on": "pass"})
    assert rw.status_code == 200, rw.get_json()

    # get returns both definition + compiled nodes
    rg = _manage(client, "get", {"name": "recon"})
    cf = rg.get_json()["code_flow"]
    assert len(cf["definition"]["steps"]) == 2
    assert len(cf["nodes"]) == 2 and cf["nodes"][0]["type"] == "Code Step"

    # list shows it
    rl = _manage(client, "list")
    assert any(x["name"] == "recon" for x in rl.get_json()["code_flows"])


def test_dispatch_passes_name_in_payload(client):
    # every authoring action keys off payload['name']; verify create then act on
    # a DIFFERENT flow doesn't cross-contaminate
    _manage(client, "create", {"name": "a"})
    _manage(client, "create", {"name": "b"})
    _manage(client, "add_step", {"name": "a", "step_name": "s", "code": "print('a')"})
    ra = _manage(client, "get", {"name": "a"})
    rb = _manage(client, "get", {"name": "b"})
    assert len(ra.get_json()["code_flow"]["definition"]["steps"]) == 1
    assert len(rb.get_json()["code_flow"]["definition"]["steps"]) == 0


def test_add_step_rejects_credentials_through_dispatch(client):
    _manage(client, "create", {"name": "leaky"})
    r = _manage(client, "add_step", {"name": "leaky", "step_name": "x",
                                     "code": 'access_token = "ghp_0123456789abcdef0123456789abcdef"'})
    assert r.status_code == 400 and "credential" in r.get_json()["error"].lower()


def test_update_step_code_through_dispatch(client):
    _manage(client, "create", {"name": "flow"})
    sid = _manage(client, "add_step", {"name": "flow", "step_name": "s",
                                       "code": "print('v1')"}).get_json()["step_id"]
    r = _manage(client, "update_step_code", {"name": "flow", "step_id": sid,
                                             "code": "print('v2')"})
    assert r.status_code == 200
    cf = _manage(client, "get", {"name": "flow"}).get_json()["code_flow"]
    assert "v2" in cf["nodes"][0]["config"]["code"]


def test_dry_run_through_dispatch(client, monkeypatch):
    from automations.runner import AutomationRunner
    import automations.runner as runner_mod

    class _CfgStub:
        AUTOMATIONS_ENV_CRED_INJECTION = False
    monkeypatch.setattr(runner_mod, "_load_cfg", lambda: _CfgStub)

    def _live_runner(*a, **k):
        r = AutomationRunner.__new__(AutomationRunner)
        r.manager = None
        r.tenant_id = "cftest"
        r.connection_string = "stub"
        r._resolve_python = lambda env_id: sys.executable
        r._resolve_connection = lambda n: None
        r._resolve_secret = lambda n: None
        return r
    monkeypatch.setattr(runner_mod, "AutomationRunner", _live_runner)

    _manage(client, "create", {"name": "chain"})
    _manage(client, "add_step", {"name": "chain", "step_name": "s",
                                 "code": "print('ran')\n"})
    r = _manage(client, "dry_run", {"name": "chain"})
    body = r.get_json()
    assert r.status_code == 200 and body["status"] == "success"
    assert body["steps"][0]["status"] == "success"


def test_delete_through_dispatch(client):
    _manage(client, "create", {"name": "gone"})
    r = _manage(client, "delete", {"name": "gone"})
    assert r.status_code == 200
    assert _manage(client, "get", {"name": "gone"}).status_code == 404


def test_unknown_action_400(client):
    r = _manage(client, "frobnicate")
    assert r.status_code == 400 and "unknown action" in r.get_json()["error"]


def test_schedule_dispatch_uses_workflow_job_type(client, monkeypatch):
    # stub the DB-bound schedule writer; assert the dispatch hands it the flow's
    # workflow id (the 'workflow' job type's TargetId)
    seen = {}

    def _fake_schedule(name, workflow_id, schedule_data, variables, user_id, username):
        seen.update(name=name, workflow_id=workflow_id, schedule=schedule_data)
        return {"scheduled_job_id": 1, "workflow_id": workflow_id}, 201
    monkeypatch.setattr(cf_api, "_create_code_flow_schedule", _fake_schedule)

    _manage(client, "create", {"name": "nightly"})
    wid = client._mem.workflow_id("nightly")
    _manage(client, "add_step", {"name": "nightly", "step_name": "s", "code": "print(1)"})
    r = _manage(client, "schedule", {"name": "nightly",
                                     "schedule": {"type": "cron", "expression": "0 2 * * *"}})
    assert r.status_code == 201, r.get_json()
    assert seen["workflow_id"] == wid and seen["schedule"]["type"] == "cron"


def test_schedule_requires_steps(client):
    _manage(client, "create", {"name": "empty"})
    r = _manage(client, "schedule", {"name": "empty", "schedule": {"type": "cron"}})
    assert r.status_code == 400 and "no steps" in r.get_json()["error"]


# ---- #23: update_step_code credential scan through the dispatch chokepoint ----

def test_update_step_code_rejects_credentials_through_dispatch(client):
    _manage(client, "create", {"name": "flow"})
    sid = _manage(client, "add_step", {"name": "flow", "step_name": "s",
                                       "code": "print('ok')"}).get_json()["step_id"]
    r = _manage(client, "update_step_code",
                {"name": "flow", "step_id": sid,
                 "code": 'password = "hunter2-not-a-placeholder"'})
    assert r.status_code == 400 and "credential" in r.get_json()["error"].lower()
    # the stored code was NOT changed
    cf = _manage(client, "get", {"name": "flow"}).get_json()["code_flow"]
    assert "print('ok')" in cf["nodes"][0]["config"]["code"]


# ---- #19: the real schedule SQL writer (stubbed cursor) ----

class _FakeCursor:
    def __init__(self):
        self.calls = []
        self.identity = 4242
    def execute(self, sql, *params):
        self.calls.append((sql, params))
    def fetchone(self):
        return (self.identity,)


class _FakeConn:
    def __init__(self, cur):
        self._c = cur
        self.committed = self.rolledback = self.closed = False
    def cursor(self):
        return self._c
    def commit(self):
        self.committed = True
    def rollback(self):
        self.rolledback = True
    def close(self):
        self.closed = True


def _fake_mgr(conn):
    import types
    return types.SimpleNamespace(_db_conn=lambda: conn)


def test_schedule_writer_uses_workflow_job_type_and_types_params(monkeypatch):
    import scheduler_routes
    cur = _FakeCursor()
    conn = _FakeConn(cur)
    monkeypatch.setattr(cf_api, "_get_manager", lambda: _fake_mgr(conn))
    monkeypatch.setattr(scheduler_routes, "_create_schedule", lambda cursor, job_id, sched: 77)

    resp, code = cf_api._create_code_flow_schedule(
        "nightly", 555, {"type": "cron", "cron_expression": "0 2 * * *"},
        {"period": "2026-07", "opts": {"x": 1}}, user_id=7, username="dev")

    assert code == 201 and resp["workflow_id"] == 555 and conn.committed
    # the ScheduledJobs insert uses the existing 'workflow' job type + TargetId=555
    job_insert = next(c for c in cur.calls if "INSERT INTO ScheduledJobs" in c[0])
    assert "'workflow'" in job_insert[0] and 555 in job_insert[1]
    # param typing: str -> 'string', dict -> json.dumps + 'json'
    param_inserts = [c for c in cur.calls if "ScheduledJobParameters" in c[0]]
    by_name = {c[1][1]: c[1] for c in param_inserts}
    assert by_name["period"][2] == "2026-07" and by_name["period"][3] == "string"
    import json as _json
    assert by_name["opts"][2] == _json.dumps({"x": 1}) and by_name["opts"][3] == "json"


def test_schedule_writer_rolls_back_on_invalid_schedule(monkeypatch):
    import scheduler_routes
    cur = _FakeCursor()
    conn = _FakeConn(cur)
    monkeypatch.setattr(cf_api, "_get_manager", lambda: _fake_mgr(conn))
    monkeypatch.setattr(scheduler_routes, "_create_schedule", lambda cursor, job_id, sched: None)

    resp, code = cf_api._create_code_flow_schedule(
        "nightly", 555, {"type": "bogus"}, {}, user_id=7, username="dev")
    assert code == 400 and conn.rolledback and not conn.committed
