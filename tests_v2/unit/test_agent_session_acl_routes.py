"""Classic agent routes for signed-in REGULAR users
(docs/handoff-the-agent-connection-acl.md §9, 2026-09-22).

THE CONTRACT: the group filter the X-AIHub-User assertion path already applied
now also applies to a flask-login SESSION whose role is below 2. Nothing else
changes:
  * no session, no assertion (API-key-only service caller) -> unfiltered
  * session role >= 2 (Developer / admin)                  -> unfiltered, and
                                                              the resolver is NOT called
  * session role 1 with grants   -> listings scoped to the granted agents;
                                    chat / knowledge / export on any other agent
                                    -> 403 {access: denied}
  * session role 1, no grants    -> empty listings; every agent 403
  * assertion role 1 (The Agent, CC) -> unchanged (still filtered)
  * resolver error               -> fails CLOSED ([] listing, 403 access)
  * AGENT_SESSION_ACL_ENFORCE=false -> unfiltered; the resolver still runs
                                    (dry-run log line)

Route source is lifted straight out of app.py (app_route_harness — decorators
dropped, so the login/API-key wrappers are not what is under test here); the
resolver is a recorder. Force-add to git (gitignore hides test*.py).
"""
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

import shared_auth as sa  # noqa: E402
from app_route_harness import load_app_symbols  # noqa: E402

pytestmark = pytest.mark.unit

_SECRET = "unit-test-secret-key-please-do-not-ship-0123456789"
# Dev-tenant shapes (2026-09-22): Alpha is shared with group A (ru_alex),
# Bravo with group B only, 386 with nobody.
AGENTS = [(1035, "Test Agent Alpha"), (1036, "Test Agent Bravo"), (386, "Retail Ops")]
GRANTS = {349: [1035], 352: []}          # ru_alex / ru_drew
DB_DOWN_UID = 999


class _User:
    def __init__(self, uid=None, role=None):
        self.is_authenticated = uid is not None
        self.id = uid
        self.role = role


class _Cursor:
    def __init__(self):
        self.rows = []

    def execute(self, sql, *params):
        if "FROM Agents" in sql:
            self.rows = [types.SimpleNamespace(agent_id=a, agent_name=n, agent_description="")
                         for a, n in AGENTS]

    def fetchall(self):
        return self.rows


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _Cursor()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CC_JWT_SECRET", _SECRET)
    monkeypatch.delenv("AGENT_SESSION_ACL_ENFORCE", raising=False)


@pytest.fixture
def resolver(monkeypatch):
    """A fake `DataUtils` whose accessible_agent_ids records every call."""
    calls = []

    def accessible_agent_ids(user_id, user_role=None):
        calls.append((int(user_id), user_role))
        if int(user_id) == DB_DOWN_UID:
            raise RuntimeError("db down")
        return list(GRANTS.get(int(user_id), []))

    fake = types.ModuleType("DataUtils")
    fake.accessible_agent_ids = accessible_agent_ids
    monkeypatch.setitem(sys.modules, "DataUtils", fake)

    ga = types.ModuleType("GeneralAgent")

    class GeneralAgent:
        def __init__(self, agent_id):
            self.agent_id = agent_id

        def run_text_only(self, prompt):
            return f"agent {self.agent_id} says hi"
    ga.GeneralAgent = GeneralAgent
    monkeypatch.setitem(sys.modules, "GeneralAgent", ga)
    return calls


@pytest.fixture
def harness(resolver):
    ns = {
        "request": request, "jsonify": jsonify, "os": os,
        "logger": logging.getLogger("t"),
        "current_user": _User(),
        "get_db_connection": lambda: _Conn(),
        "select_all_agents_and_tools": lambda: [
            {"agent_id": a, "agent_description": n, "tool_names": []} for a, n in AGENTS],
        "get_agent_by_id": lambda aid: [],            # export: 404 = the gate passed
        "get_agent_knowledge_for_user": lambda aid: [{"id": 1, "agent_id": aid}],
        "render_template": lambda *a, **k: "page",
    }
    load_app_symbols(["_InvalidUserAssertion", "_agent_visibility_filter",
                      "_session_agent_acl_enforced", "_session_agent_scope",
                      "_session_is_regular_user", "_agent_access_refusal",
                      "get_agents", "list_agents_for_selection", "chat_general_text",
                      "export_agent", "agent_knowledge_page",
                      "get_agent_knowledge_user_route"], ns)
    app = Flask("t")
    app.secret_key = "t"
    app.add_url_rule("/get/agents", "get_agents", ns["get_agents"])
    app.add_url_rule("/api/agents/list", "agents_list", ns["list_agents_for_selection"])
    app.add_url_rule("/chat/general/text", "chat_text", ns["chat_general_text"],
                     methods=["POST"])
    app.add_url_rule("/export/agent/<int:agent_id>", "export", ns["export_agent"],
                     methods=["GET"])
    app.add_url_rule("/agent_knowledge/<int:agent_id>", "kpage", ns["agent_knowledge_page"])
    app.add_url_rule("/get/agent_knowledge_user/<int:agent_id>", "kuser",
                     ns["get_agent_knowledge_user_route"])
    return ns, app.test_client()


def _get_agents_ids(client, headers=None):
    r = client.get("/get/agents", headers=headers or {})
    assert r.status_code == 200, r.data
    return sorted(a["agent_id"] for a in r.get_json()["data"])


def _list_ids(client, headers=None):
    r = client.get("/api/agents/list", headers=headers or {})
    assert r.status_code == 200, r.data
    return sorted(a["agent_id"] for a in r.get_json()["agents"])


def _chat(client, aid):
    return client.post("/chat/general/text", json={"agent_id": aid, "prompt": "hi"})


def _assertion(uid, role):
    return {"X-AIHub-User": sa.sign_user_assertion(uid, "t1", role)}


# ------------------------------------------------------------------ listings

def test_no_session_no_assertion_is_unchanged(harness, resolver):
    ns, c = harness
    assert _get_agents_ids(c) == [386, 1035, 1036]
    assert _list_ids(c) == [386, 1035, 1036]
    assert resolver == []


def test_role1_session_listings_are_scoped(harness, resolver):
    ns, c = harness
    ns["current_user"] = _User(349, 1)
    assert _get_agents_ids(c) == [1035]
    assert _list_ids(c) == [1035]
    assert resolver == [(349, 1), (349, 1)]


def test_role1_session_without_grants_sees_nothing(harness):
    ns, c = harness
    ns["current_user"] = _User(352, 1)
    assert _get_agents_ids(c) == []
    assert _list_ids(c) == []


def test_role2_session_is_unrestricted_and_resolver_not_called(harness, resolver):
    ns, c = harness
    ns["current_user"] = _User(353, 2)
    assert _get_agents_ids(c) == [386, 1035, 1036]
    assert _list_ids(c) == [386, 1035, 1036]
    ns["current_user"] = _User(13, 3)
    assert _list_ids(c) == [386, 1035, 1036]
    assert resolver == []


def test_assertion_path_is_unchanged(harness, resolver):
    ns, c = harness                          # no session, a role-1 assertion
    assert _list_ids(c, _assertion(349, 1)) == [1035]
    assert _list_ids(c, _assertion(352, 1)) == []
    r = c.get("/api/agents/list", headers={"X-AIHub-User": "garbage"})
    assert r.status_code == 200               # listing: bad assertion logs, unfiltered (pre-existing)


def test_kill_switch_restores_the_old_listing_but_still_resolves(harness, resolver, monkeypatch):
    monkeypatch.setenv("AGENT_SESSION_ACL_ENFORCE", "false")
    ns, c = harness
    ns["current_user"] = _User(352, 1)
    assert _get_agents_ids(c) == [386, 1035, 1036]
    assert resolver == [(352, 1)]


def test_resolver_error_fails_closed_for_a_session(harness):
    ns, c = harness
    ns["current_user"] = _User(DB_DOWN_UID, 1)
    assert _get_agents_ids(c) == []
    assert _list_ids(c) == []


# ------------------------------------------------------------ access routes

def test_role1_session_chat_knowledge_export_on_unshared_agent_are_403(harness):
    ns, c = harness
    ns["current_user"] = _User(349, 1)
    for aid in (1036, 386):
        r = _chat(c, aid)
        assert r.status_code == 403 and r.get_json()["access"] == "denied", aid
        assert "do not have access" in r.get_json()["response"]
        assert c.get(f"/export/agent/{aid}").status_code == 403
        assert c.get(f"/agent_knowledge/{aid}").status_code == 403
        assert c.get(f"/get/agent_knowledge_user/{aid}").status_code == 403


def test_role1_session_shared_agent_passes_the_gate(harness):
    ns, c = harness
    ns["current_user"] = _User(349, 1)
    r = _chat(c, 1035)
    assert r.status_code == 200 and "agent 1035 says hi" in r.get_json()["response"]
    assert c.get("/export/agent/1035").status_code == 404          # get_agent_by_id stub: gate passed
    assert c.get("/agent_knowledge/1035").status_code == 200
    assert c.get("/get/agent_knowledge_user/1035").status_code == 200


def test_role1_session_bad_agent_id_is_denied_not_500(harness):
    ns, c = harness
    ns["current_user"] = _User(349, 1)
    r = c.post("/chat/general/text", json={"agent_id": "abc", "prompt": "hi"})
    assert r.status_code == 403


def test_role2_session_and_service_callers_chat_any_agent(harness, resolver):
    ns, c = harness
    assert _chat(c, 386).status_code == 200            # no session, no assertion
    ns["current_user"] = _User(353, 2)
    assert _chat(c, 386).status_code == 200
    assert c.get("/export/agent/386").status_code == 404
    assert resolver == []


def test_assertion_role1_chat_is_still_refused(harness):
    ns, c = harness
    r = c.post("/chat/general/text", json={"agent_id": 1036, "prompt": "hi"},
               headers=_assertion(349, 1))
    assert r.status_code == 403 and r.get_json()["access"] == "denied"
    r = c.post("/chat/general/text", json={"agent_id": 1035, "prompt": "hi"},
               headers=_assertion(349, 1))
    assert r.status_code == 200
    r = c.post("/chat/general/text", json={"agent_id": 1035, "prompt": "hi"},
               headers={"X-AIHub-User": "garbage"})
    assert r.status_code == 403                        # strict: forged = hard 403


def test_resolver_error_fails_closed_for_access(harness):
    ns, c = harness
    ns["current_user"] = _User(DB_DOWN_UID, 1)
    assert _chat(c, 1035).status_code == 403


def test_kill_switch_opens_access_for_a_session(harness, monkeypatch):
    monkeypatch.setenv("AGENT_SESSION_ACL_ENFORCE", "0")
    ns, c = harness
    ns["current_user"] = _User(352, 1)
    assert _chat(c, 386).status_code == 200
