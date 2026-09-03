"""/api/agents/<id>/chat — agent-visibility authorization
(doc-acl G3, 2026-09-03; docs/handoff-doc-acl-g1-g3.md §5).

THE CONTRACT: you may delegate only to agents you can already SEE.
  * no assertion            -> unfiltered; the agent is reachable (every
                               existing session, classic-UI and CC caller)
  * assertion, agent shared -> reachable
  * assertion, not shared   -> 403 "You do not have access to that agent."
  * assertion, no groups    -> accessible_agent_ids -> [] -> 403
  * body user_id naming an authorized user + assertion naming an unauthorized
    one -> 403: authorization comes from the ASSERTION, never the body
  * forged assertion        -> 403 (strict mode), never "treat as missing"
  * resolver failure        -> fails CLOSED in strict mode
  * _agent_visibility_filter() non-strict (the listing endpoints) keeps the
    prior fall-back-to-unfiltered behavior on a bad assertion

The route source is lifted from app.py (app_route_harness). DataUtils and the
NLQ engine factory are stubbed at the module boundary so the reachable path
runs the route's own data-agent branch end to end.
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
AGENT = 7
# What accessible_agent_ids resolves per user id (None = admin / unfiltered).
VISIBLE = {5: [AGENT, 9], 6: [9], 10: [], 12: None}


class _World:
    """The route plus recorders for everything it touches."""

    def __init__(self, monkeypatch, resolver_raises=False):
        self.resolver_calls = []
        self.chat_calls = []

        def _accessible_agent_ids(user_id, user_role=None):
            self.resolver_calls.append((user_id, user_role))
            if resolver_raises:
                raise RuntimeError("db down")
            return VISIBLE[int(user_id)]

        data_utils = types.ModuleType("DataUtils")
        data_utils.accessible_agent_ids = _accessible_agent_ids
        data_utils.is_data_agent = lambda agent_id: True
        monkeypatch.setitem(sys.modules, "DataUtils", data_utils)

        nlq = types.ModuleType("nlq_engine_factory")
        nlq.create_nlq_engine = lambda **kw: object()
        monkeypatch.setitem(sys.modules, "nlq_engine_factory", nlq)

        def _process_chat_data_request(engine, agent_id, prompt, history, **kw):
            self.chat_calls.append((agent_id, prompt))
            return {"answer": f"agent {agent_id} says hi", "conversation_history": []}, None

        ns = {"request": request, "jsonify": jsonify,
              "logger": logging.getLogger("test_agent_chat_visibility"),
              "process_chat_data_request": _process_chat_data_request,
              "enhance_engines": {}, "nlq_systems": {},
              "active_agents": {}, "load_agents": lambda **kw: None}
        load_app_symbols(["_InvalidUserAssertion", "_agent_visibility_filter",
                          "api_agent_chat"], ns)
        self.ns = ns
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.add_url_rule("/api/agents/<int:agent_id>/chat", "api_agent_chat",
                         ns["api_agent_chat"], methods=["POST"])
        self.app = app
        self.client = app.test_client()

    def chat(self, agent_id=AGENT, assertion=None, **body):
        headers = {"X-AIHub-User": assertion} if assertion else {}
        payload = {"prompt": "hello", "history": "[]"}
        payload.update(body)
        return self.client.post(f"/api/agents/{agent_id}/chat", json=payload,
                                headers=headers)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CC_JWT_SECRET", _SECRET)


@pytest.fixture
def world(monkeypatch):
    return _World(monkeypatch)


def _assertion(uid, role=1, **kw):
    return sa.sign_user_assertion(uid, "t1", role, **kw)


# ---------------------------------------------------------------- reachable
def test_no_assertion_is_unfiltered_and_reachable(world):
    r = world.chat()
    assert r.status_code == 200
    assert r.get_json()["response"] == f"agent {AGENT} says hi"
    assert world.resolver_calls == [], "no assertion -> the resolver is never consulted"


def test_shared_agent_is_reachable(world):
    r = world.chat(assertion=_assertion(5))
    assert r.status_code == 200
    assert world.resolver_calls == [(5, 1)]
    assert world.chat_calls == [(AGENT, "hello")]


def test_admin_is_unfiltered(world):
    r = world.chat(assertion=_assertion(12, role=3))
    assert r.status_code == 200 and world.chat_calls


# ---------------------------------------------------------------- refused
def test_unshared_agent_is_403(world):
    r = world.chat(assertion=_assertion(6))
    assert r.status_code == 403
    assert r.get_json() == {"status": "error",
                            "response": "You do not have access to that agent."}
    assert world.chat_calls == [], "the agent must not run for a refused caller"


def test_user_with_no_groups_is_403(world):
    r = world.chat(assertion=_assertion(10))
    assert r.status_code == 403
    assert world.chat_calls == []


def test_authorization_comes_from_the_assertion_not_the_body(world):
    # Body names user 5 (who CAN see the agent); the assertion is user 6.
    r = world.chat(assertion=_assertion(6), user_id=5, session_id="cc-sess-1")
    assert r.status_code == 403
    assert world.resolver_calls == [(6, 1)]
    assert world.chat_calls == []


@pytest.mark.parametrize("make_token", [
    pytest.param(lambda: "garbage.token.value", id="garbage"),
    pytest.param(lambda: sa.sign_cc_token({"user_id": 5, "role": 3, "tenant_id": "t1"}),
                 id="wrong-audience-cc-session"),
    pytest.param(lambda: _assertion(5, ttl_seconds=-60), id="expired"),
])
def test_forged_assertion_is_403_not_missing(world, make_token):
    r = world.chat(assertion=make_token())
    assert r.status_code == 403
    assert r.get_json()["response"] == "invalid user assertion"
    assert world.resolver_calls == [] and world.chat_calls == []


def test_resolver_failure_fails_closed_for_chat(monkeypatch):
    w = _World(monkeypatch, resolver_raises=True)
    r = w.chat(assertion=_assertion(5))
    assert r.status_code == 403
    assert w.chat_calls == []


# ---------------------------------------------------------------- the helper
def test_visibility_filter_non_strict_keeps_listing_behavior(world):
    """The listing endpoints (strict=False) must be untouched by G3: a bad
    assertion still logs and falls back to unfiltered there."""
    vf = world.ns["_agent_visibility_filter"]
    with world.app.test_request_context("/x", headers={"X-AIHub-User": "garbage"}):
        assert vf() is None
        with pytest.raises(world.ns["_InvalidUserAssertion"]):
            vf(strict=True)
    with world.app.test_request_context("/x", headers={"X-AIHub-User": _assertion(6)}):
        assert vf() == [9] and vf(strict=True) == [9]
    with world.app.test_request_context("/x"):
        assert vf() is None and vf(strict=True) is None


def test_visibility_filter_strict_fails_closed_on_resolver_error(monkeypatch):
    w = _World(monkeypatch, resolver_raises=True)
    vf = w.ns["_agent_visibility_filter"]
    with w.app.test_request_context("/x", headers={"X-AIHub-User": _assertion(5)}):
        assert vf() is None            # listing: prior behavior
        assert vf(strict=True) == []   # authorization: deny
