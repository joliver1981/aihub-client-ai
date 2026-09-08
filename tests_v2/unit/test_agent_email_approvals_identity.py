"""Agent-email approvals — WHO the routes act as, and My Work running AS the
viewer (RU pack finding F-7 part 2, 2026-09-08: customer-data leak).

The leak: The Agent's My Work read-through (agent_service/readthrough.py)
called GET /api/agent-email/approvals?status=pending with the tenant service
key and no user. api_key_or_session_required treats a valid key as a trusted
internal caller, so the route fell back to user 1 / role 3, accessible_agent_ids
returned None (no filter) and EVERY pending approval on the install — full
bodies included — came back and was rendered to every My Work viewer, role 1
included. The same fallback let a regular user settle someone else's approval
through POST /api/work/decide with the row recording approver_user_id = 1.

THE CONTRACT now (mirrors app._caller_identity() for the doc-acl G1-G3 routes):
  platform routes (agent_email_routes.py, source lifted here — decorators
  dropped, exactly the code that ships):
    * X-AIHub-User ABSENT          -> unchanged: session user, else API-key
                                      admin fallback (scheduler/dispatcher)
    * PRESENT + valid, role >= 2   -> that user: accessible_agent_ids scopes
                                      the list, single GET / act-on answer 403
                                      outside it, the settled row records the
                                      REAL approver
    * PRESENT + valid, role < 2    -> 403 on list, GET and act-on, before any
                                      lookup (no id-oracle)
    * PRESENT + invalid            -> 403, never "treat as missing"
  The Agent (readthrough.email_pending / decide_email):
    * `user` is REQUIRED (TypeError otherwise — no silent widening)
    * a minted assertion rides on every call, sub/role from the verified
      principal
    * no identity or no signing secret fails CLOSED: nothing listed, nothing
      settled, no HTTP call at all
  main.py threading the verified principal into both is pinned in the
  sibling test_workitem_store_role_visibility.py (EmailReadthroughRunsAsTheViewer)
  — that file is unittest-based so the aihub-agent env, which has neither
  pytest nor flask, can run it.

Run (main-app env; needs flask + PyJWT):
  C:\\Users\\james\\miniconda3\\envs\\aihub2.1\\python.exe -m pytest tests_v2/unit/test_agent_email_approvals_identity.py -v
"""
import asyncio
import logging
import os
import sys
import types
from types import SimpleNamespace

import pytest
from flask import Flask, jsonify, request

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_HERE = os.path.dirname(os.path.abspath(__file__))
for p in (_ROOT, os.path.join(_ROOT, "agent_service"), _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

pytest.importorskip("jwt", reason="PyJWT not installed in this environment")

import shared_auth as sa  # noqa: E402
from app_route_harness import load_app_symbols  # noqa: E402

pytestmark = pytest.mark.unit

ROUTES_PY = os.path.join(_ROOT, "agent_email_routes.py")
_SECRET = "unit-test-secret-key-please-do-not-ship-0123456789"

# Seats mirror docs/openclaw-tester-setup-regular-users.md (ids as on the dev DB)
ALEX, CASEY = 349, 351       # role 1
ERIN = 353                   # role 2
ADMIN = 13                   # role 3
ORPHAN_AGENT = 1037          # "Test Agent Orphan": in NO group
ERIN_AGENT = 77              # an agent in one of Erin's groups
ACCESS = {ERIN: [ERIN_AGENT], ALEX: [], CASEY: []}

PROBE = {"approval_id": 5001, "agent_id": ORPHAN_AGENT, "status": "pending",
         "subject": "PROBE lease renewal terms", "to_addresses": ["probe@example.com"],
         "draft_body": "PROBE-BODY confidential rent figure 12345", "final_body": None}
ERINS = {"approval_id": 5002, "agent_id": ERIN_AGENT, "status": "pending",
         "subject": "Erin's agent mail", "to_addresses": ["x@example.com"],
         "draft_body": "erin body", "final_body": None}


def _assertion(uid, role, *, aud=sa.AUD_INTERNAL, ttl=300, secret=_SECRET):
    return sa._encode({"sub": str(uid), "tenant_id": 1, "role": role}, aud, ttl, secret)


# ---------------------------------------------------------------------------
# Fakes for the two modules the routes import lazily
# ---------------------------------------------------------------------------

class _FakeSend:
    """agent_email_send stand-in: records what the routes asked for."""

    def __init__(self, rows):
        self.rows = [dict(r) for r in rows]
        self.list_calls, self.get_calls = [], []
        self.reject_calls, self.send_calls = [], []

    def list_approvals(self, status=None, agent_ids=None, limit=200):
        self.list_calls.append((status, agent_ids))
        if agent_ids is not None and len(agent_ids) == 0:
            return []
        return [dict(r) for r in self.rows
                if (agent_ids is None or r["agent_id"] in agent_ids)
                and (status in (None, "all") or r["status"] == status)]

    def get_approval(self, approval_id):
        self.get_calls.append(approval_id)
        return next((dict(r) for r in self.rows if r["approval_id"] == approval_id), None)

    def reject_approval(self, approval_id, approver_user_id, comments=None):
        self.reject_calls.append((approval_id, approver_user_id, comments))
        return {"success": True, "status": "rejected"}

    def send_approved_email(self, approval_id, final_body, *, approver_user_id, comments=None):
        self.send_calls.append((approval_id, final_body, approver_user_id, comments))
        return {"success": True, "status": "sent"}


def _fake_datautils():
    m = types.ModuleType("DataUtils")

    def accessible_agent_ids(user_id, user_role=None):
        if int(user_role or 0) >= 3:
            return None
        return list(ACCESS.get(int(user_id), []))
    m.accessible_agent_ids = accessible_agent_ids
    return m


@pytest.fixture
def send():
    return _FakeSend([PROBE, ERINS])


@pytest.fixture
def routes(monkeypatch, send):
    """A bare Flask app carrying the three approval routes, source lifted from
    agent_email_routes.py. current_user defaults to the API-key
    (AnonymousUserMixin-like) principal: no id, no role."""
    monkeypatch.setenv("CC_JWT_SECRET", _SECRET)
    monkeypatch.setitem(sys.modules, "agent_email_send", send)
    monkeypatch.setitem(sys.modules, "DataUtils", _fake_datautils())
    ns = {"request": request, "jsonify": jsonify,
          "logger": logging.getLogger("test.agent_email_routes"),
          "current_user": SimpleNamespace(),
          "_attach_agent_names": lambda approvals: approvals}
    load_app_symbols(["_get_current_user_id", "_get_current_user_role",
                      "_InvalidUserAssertion", "_asserted_identity",
                      "_approval_actor", "_approval_agent_scope",
                      "_may_act_on_agent", "list_agent_email_approvals",
                      "get_agent_email_approval", "act_on_agent_email_approval"],
                     ns, path=ROUTES_PY)
    app = Flask("agent_email_routes_under_test")
    app.add_url_rule("/api/agent-email/approvals", "list_approvals",
                     ns["list_agent_email_approvals"], methods=["GET"])
    app.add_url_rule("/api/agent-email/approvals/<int:approval_id>", "get_approval",
                     ns["get_agent_email_approval"], methods=["GET"])
    app.add_url_rule("/api/agent-email/approvals/<int:approval_id>", "act_on_approval",
                     ns["act_on_agent_email_approval"], methods=["POST"])
    client = app.test_client()
    client.ns = ns
    return client


def _hdr(uid, role, **kw):
    return {"X-API-Key": "tenant-key", "X-AIHub-User": _assertion(uid, role, **kw)}


# ---------------------------------------------------------------------------
# Platform routes: assertion ABSENT -> unchanged
# ---------------------------------------------------------------------------

class TestAssertionAbsentIsUnchanged:

    def test_api_key_without_assertion_keeps_the_admin_fallback(self, routes, send):
        r = routes.get("/api/agent-email/approvals?status=pending",
                       headers={"X-API-Key": "tenant-key"})
        assert r.status_code == 200
        assert [a["approval_id"] for a in r.get_json()["approvals"]] == [5001, 5002]
        assert send.list_calls[0] == ("pending", None)          # no filter: today's posture
        r = routes.post("/api/agent-email/approvals/5001", json={"action": "reject"},
                        headers={"X-API-Key": "tenant-key"})
        assert r.status_code == 200
        assert send.reject_calls == [(5001, 1, None)]           # the admin fallback, as before

    def test_session_user_without_assertion_is_scoped_as_before(self, routes, send):
        routes.ns["current_user"] = SimpleNamespace(id=ERIN, role=2, is_authenticated=True)
        r = routes.get("/api/agent-email/approvals?status=pending")
        assert r.status_code == 200
        assert [a["approval_id"] for a in r.get_json()["approvals"]] == [5002]
        assert send.list_calls[0] == ("pending", [ERIN_AGENT])
        r = routes.get("/api/agent-email/approvals/5001")
        assert r.status_code == 403
        r = routes.post("/api/agent-email/approvals/5002", json={"action": "reject"})
        assert r.status_code == 200
        assert send.reject_calls == [(5002, ERIN, None)]


# ---------------------------------------------------------------------------
# Platform routes: assertion PRESENT + valid
# ---------------------------------------------------------------------------

class TestAssertedUserIsScoped:

    def test_developer_sees_only_her_agents_and_never_the_orphan_body(self, routes, send):
        r = routes.get("/api/agent-email/approvals?status=pending", headers=_hdr(ERIN, 2))
        assert r.status_code == 200
        body = r.get_json()
        assert [a["approval_id"] for a in body["approvals"]] == [5002]
        assert "12345" not in r.get_data(as_text=True)
        assert send.list_calls[0] == ("pending", [ERIN_AGENT])
        assert body["statistics"]["pending"] == 1                # stats scoped too

    def test_developer_with_no_agents_gets_an_empty_list_not_everything(self, routes, send):
        ACCESS_BACKUP = dict(ACCESS)
        try:
            ACCESS[ERIN] = []
            r = routes.get("/api/agent-email/approvals?status=pending", headers=_hdr(ERIN, 2))
            assert r.status_code == 200
            assert r.get_json()["approvals"] == []
            assert send.list_calls[0] == ("pending", [])           # deny-all, fail-closed
        finally:
            ACCESS.clear()
            ACCESS.update(ACCESS_BACKUP)

    def test_admin_assertion_is_unfiltered(self, routes, send):
        r = routes.get("/api/agent-email/approvals?status=pending", headers=_hdr(ADMIN, 3))
        assert r.status_code == 200
        assert [a["approval_id"] for a in r.get_json()["approvals"]] == [5001, 5002]
        assert send.list_calls[0] == ("pending", None)

    def test_single_get_outside_scope_is_403(self, routes, send):
        r = routes.get("/api/agent-email/approvals/5001", headers=_hdr(ERIN, 2))
        assert r.status_code == 403
        assert "12345" not in r.get_data(as_text=True)
        r = routes.get("/api/agent-email/approvals/5002", headers=_hdr(ERIN, 2))
        assert r.status_code == 200
        assert r.get_json()["approval"]["approval_id"] == 5002

    def test_act_on_outside_scope_is_403_and_nothing_is_settled(self, routes, send):
        r = routes.post("/api/agent-email/approvals/5001", json={"action": "reject"},
                        headers=_hdr(ERIN, 2))
        assert r.status_code == 403
        r = routes.post("/api/agent-email/approvals/5001",
                        json={"action": "approve", "final_body": "edited"},
                        headers=_hdr(ERIN, 2))
        assert r.status_code == 403
        assert send.reject_calls == [] and send.send_calls == []

    def test_settled_row_records_the_real_approver(self, routes, send):
        r = routes.post("/api/agent-email/approvals/5002",
                        json={"action": "reject", "comments": "no"}, headers=_hdr(ERIN, 2))
        assert r.status_code == 200
        assert send.reject_calls == [(5002, ERIN, "no")]         # Erin, not user 1
        r = routes.post("/api/agent-email/approvals/5001",
                        json={"action": "approve", "final_body": "go"}, headers=_hdr(ADMIN, 3))
        assert r.status_code == 200
        assert send.send_calls == [(5001, "go", ADMIN, None)]    # the admin who acted, not 1


class TestRegularUserIsDeniedOutright:

    @pytest.mark.parametrize("uid", [ALEX, CASEY])
    def test_role1_list_get_and_act_are_403_before_any_lookup(self, routes, send, uid):
        r = routes.get("/api/agent-email/approvals?status=pending", headers=_hdr(uid, 1))
        assert r.status_code == 403
        r = routes.get("/api/agent-email/approvals/5001", headers=_hdr(uid, 1))
        assert r.status_code == 403
        r = routes.post("/api/agent-email/approvals/5001", json={"action": "reject"},
                        headers=_hdr(uid, 1))
        assert r.status_code == 403
        r = routes.post("/api/agent-email/approvals/5001",
                        json={"action": "approve", "final_body": "x"}, headers=_hdr(uid, 1))
        assert r.status_code == 403
        assert send.list_calls == [] and send.get_calls == []
        assert send.reject_calls == [] and send.send_calls == []

    def test_missing_role_claim_fails_closed(self, routes, send):
        tok = sa._encode({"sub": str(ERIN), "tenant_id": 1}, sa.AUD_INTERNAL, 300, _SECRET)
        r = routes.get("/api/agent-email/approvals?status=pending",
                       headers={"X-API-Key": "k", "X-AIHub-User": tok})
        assert r.status_code == 403
        assert send.list_calls == []


class TestInvalidAssertionIs403NeverIgnored:

    @pytest.mark.parametrize("bad", [
        "not-a-jwt",
        _assertion(ADMIN, 3, secret="some-other-secret-entirely-0123456789abcdef"),
        _assertion(ADMIN, 3, ttl=-30),
        _assertion(ADMIN, 3, aud=sa.AUD_CC),
    ], ids=["garbage", "forged", "expired", "wrong-audience"])
    def test_every_route_answers_403_and_touches_nothing(self, routes, send, bad):
        h = {"X-API-Key": "tenant-key", "X-AIHub-User": bad}
        assert routes.get("/api/agent-email/approvals?status=pending", headers=h).status_code == 403
        assert routes.get("/api/agent-email/approvals/5001", headers=h).status_code == 403
        assert routes.post("/api/agent-email/approvals/5001", json={"action": "reject"},
                           headers=h).status_code == 403
        assert send.list_calls == [] and send.get_calls == [] and send.reject_calls == []

    def test_no_signing_secret_on_the_server_is_403_not_admin(self, routes, send, monkeypatch):
        h = _hdr(ADMIN, 3)
        for var in ("CC_JWT_SECRET", "API_KEY", "AI_HUB_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        assert routes.get("/api/agent-email/approvals?status=pending", headers=h).status_code == 403
        assert send.list_calls == []


# ---------------------------------------------------------------------------
# The Agent side: readthrough runs AS the viewer
# ---------------------------------------------------------------------------

def _import_readthrough():
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        if "claude_agent_sdk" not in sys.modules:
            _sdk = types.ModuleType("claude_agent_sdk")
            _sdk.tool = lambda *_a, **_k: (lambda fn: fn)
            _sdk.create_sdk_mcp_server = lambda *a, **k: None
            sys.modules["claude_agent_sdk"] = _sdk
    import readthrough
    return readthrough


class _Resp:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body
        self.text = str(body)

    def json(self):
        return self._body


class _FakeAsyncClient:
    calls = []
    reply = _Resp(200, {"approvals": []})

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None, headers=None):
        _FakeAsyncClient.calls.append(("GET", url, params, headers, None))
        return _FakeAsyncClient.reply

    async def post(self, url, json=None, headers=None):
        _FakeAsyncClient.calls.append(("POST", url, None, headers, json))
        return _FakeAsyncClient.reply


@pytest.fixture
def rt(monkeypatch):
    readthrough = _import_readthrough()
    monkeypatch.setenv("CC_JWT_SECRET", _SECRET)
    monkeypatch.setattr(readthrough.httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.reply = _Resp(200, {"approvals": [dict(ERINS)]})
    return readthrough


ERIN_USER = {"user_id": ERIN, "role": 2, "tenant_id": 1, "username": "dev_erin"}
CASEY_USER = {"user_id": CASEY, "role": 1, "tenant_id": 1, "username": "ru_casey"}


def _asserted(headers):
    claims, err = sa.verify_token(headers["X-AIHub-User"], sa.AUD_INTERNAL, _SECRET)
    assert err is None, err
    return claims


class TestEmailPendingRunsAsTheViewer:

    def test_assertion_rides_along_with_the_service_key(self, rt):
        rows = asyncio.run(rt.email_pending(ERIN_USER))
        assert [r["approval_id"] for r in rows] == [5002]
        (method, url, params, headers, _), = _FakeAsyncClient.calls
        assert method == "GET" and url.endswith("/api/agent-email/approvals")
        assert params == {"status": "pending"}
        assert headers.get("X-API-Key")                         # service key still there
        claims = _asserted(headers)
        assert claims["sub"] == str(ERIN) and claims["role"] == 2 and claims["tenant_id"] == 1

    def test_403_for_a_regular_user_is_an_empty_queue(self, rt):
        _FakeAsyncClient.reply = _Resp(403, {"status": "error", "message": "Developer access required"})
        assert asyncio.run(rt.email_pending(CASEY_USER)) == []
        assert _asserted(_FakeAsyncClient.calls[0][3])["sub"] == str(CASEY)

    @pytest.mark.parametrize("user", [None, {}, {"user_id": 0, "role": 2},
                                      {"user_id": None, "role": 3}, {"user_id": "", "role": 3}],
                             ids=["none", "empty", "service-principal", "null-id", "blank-id"])
    def test_no_identity_lists_nothing_and_makes_no_call(self, rt, user):
        assert asyncio.run(rt.email_pending(user)) == []
        assert _FakeAsyncClient.calls == []

    def test_no_signing_secret_fails_closed(self, rt, monkeypatch):
        for var in ("CC_JWT_SECRET", "API_KEY", "AI_HUB_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        assert asyncio.run(rt.email_pending(ERIN_USER)) == []
        assert _FakeAsyncClient.calls == []

    def test_user_is_required(self, rt):
        with pytest.raises(TypeError):
            asyncio.run(rt.email_pending())                       # the drift we are guarding


class TestDecideEmailRunsAsTheUser:

    def test_assertion_rides_along_and_server_status_passes_through(self, rt):
        _FakeAsyncClient.reply = _Resp(403, {"status": "error", "message": "Not authorized for this agent"})
        data, status = asyncio.run(rt.decide_email(5001, "reject", None, "nope", user=CASEY_USER))
        assert status == 403 and "Not authorized" in data["message"]
        (method, url, _, headers, body), = _FakeAsyncClient.calls
        assert method == "POST" and url.endswith("/api/agent-email/approvals/5001")
        assert body == {"action": "reject", "comments": "nope"}
        assert _asserted(headers)["sub"] == str(CASEY)

    def test_approve_keeps_the_body_only_contract(self, rt):
        _FakeAsyncClient.reply = _Resp(200, {"status": "success"})
        data, status = asyncio.run(rt.decide_email(5002, "approve", "edited text", "",
                                                   user=ERIN_USER))
        assert status == 200
        assert _FakeAsyncClient.calls[0][4] == {"action": "approve", "comments": "",
                                                "final_body": "edited text"}

    def test_no_identity_is_403_and_nothing_is_sent(self, rt):
        data, status = asyncio.run(rt.decide_email(5001, "reject", None, "", user={"user_id": 0}))
        assert status == 403 and "identity" in data["error"]
        assert _FakeAsyncClient.calls == []

    def test_user_is_keyword_only_and_required(self, rt):
        with pytest.raises(TypeError):
            asyncio.run(rt.decide_email(5001, "reject", None, ""))
        with pytest.raises(TypeError):
            asyncio.run(rt.decide_email(5001, "reject", None, "", ERIN_USER))
