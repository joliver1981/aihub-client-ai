"""Data Explorer chat / refresh for signed-in REGULAR users
(docs/handoff-the-agent-connection-acl.md §9, 2026-09-22).

The Data Explorer's agent dropdown was always group-scoped
(select_user_agents_and_connections), but /data_explorer/chat and
/data_explorer/refresh accepted ANY agent_id from any session — and a data
agent's chat queries that agent's connection. Contract:
  * role 1, agent not shared with their groups -> 403 {access: denied} BEFORE
    any session / engine work
  * role 1, shared agent                        -> the gate passes (the route
    then fails on the missing session, which is what proves the order)
  * role >= 2                                   -> unchanged, resolver not called
  * resolver error                              -> 403 (fail closed)
  * AGENT_SESSION_ACL_ENFORCE=false             -> unchanged
Route source lifted from routes/data_explorer.py (app_route_harness `path=`).
Force-add to git (gitignore hides test*.py).
"""
import logging
import os
import sys
import types
import uuid
from pathlib import Path

import pytest
from flask import Flask, jsonify, request, session

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from app_route_harness import load_app_symbols  # noqa: E402

pytestmark = pytest.mark.unit

DE_PY = str(_ROOT / "routes" / "data_explorer.py")
GRANTS = {349: [876], 352: []}
DB_DOWN_UID = 999


class _User:
    def __init__(self, uid, role):
        self.is_authenticated = True
        self.id = uid
        self.role = role


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("AGENT_SESSION_ACL_ENFORCE", raising=False)


@pytest.fixture
def resolver(monkeypatch):
    calls = []

    def accessible_agent_ids(user_id, user_role=None):
        calls.append((int(user_id), user_role))
        if int(user_id) == DB_DOWN_UID:
            raise RuntimeError("db down")
        return list(GRANTS.get(int(user_id), []))

    fake = types.ModuleType("DataUtils")
    fake.accessible_agent_ids = accessible_agent_ids
    monkeypatch.setitem(sys.modules, "DataUtils", fake)
    return calls


@pytest.fixture
def harness(resolver):
    ns = {
        "request": request, "jsonify": jsonify, "session": session, "os": os,
        "logging": logging, "uuid": uuid,
        "current_user": _User(349, 1),
        "_get_session_engine": lambda sid: None,
    }
    load_app_symbols(["_regular_user_agent_refusal", "data_explorer_chat",
                      "data_explorer_refresh_query"], ns, path=DE_PY)
    app = Flask("t")
    app.secret_key = "t"
    app.add_url_rule("/data_explorer/chat", "chat", ns["data_explorer_chat"], methods=["POST"])
    app.add_url_rule("/data_explorer/refresh", "refresh", ns["data_explorer_refresh_query"],
                     methods=["POST"])
    return ns, app.test_client()


def _chat(c, aid):
    return c.post("/data_explorer/chat", json={"agent_id": aid, "question": "q", "history": "[]"})


def _refresh(c, aid):
    return c.post("/data_explorer/refresh", json={"agent_id": aid, "sql": "select 1"})


def test_role1_unshared_agent_is_denied_before_any_session_work(harness):
    ns, c = harness
    for aid in (228, 14):
        r = _chat(c, aid)
        assert r.status_code == 403 and r.get_json()["access"] == "denied", aid
        r = _refresh(c, aid)
        assert r.status_code == 403 and r.get_json()["access"] == "denied", aid


def test_role1_shared_agent_passes_the_gate(harness):
    ns, c = harness
    r = _chat(c, 876)
    assert r.status_code == 400 and "No session" in r.get_json()["error"]   # gate passed
    r = _refresh(c, 876)
    assert r.status_code == 400 and "Session expired" in r.get_json()["error"]


def test_role1_without_grants_is_denied_everywhere(harness):
    ns, c = harness
    ns["current_user"] = _User(352, 1)
    assert _chat(c, 876).status_code == 403
    assert _refresh(c, 876).status_code == 403


def test_role2_is_unchanged_and_resolver_not_called(harness, resolver):
    ns, c = harness
    ns["current_user"] = _User(353, 2)
    assert _chat(c, 228).status_code == 400
    assert _refresh(c, 228).status_code == 400
    assert resolver == []


def test_resolver_error_fails_closed(harness):
    ns, c = harness
    ns["current_user"] = _User(DB_DOWN_UID, 1)
    assert _chat(c, 876).status_code == 403


def test_kill_switch_restores_the_old_behaviour(harness, monkeypatch):
    monkeypatch.setenv("AGENT_SESSION_ACL_ENFORCE", "false")
    ns, c = harness
    ns["current_user"] = _User(352, 1)
    assert _chat(c, 876).status_code == 400
