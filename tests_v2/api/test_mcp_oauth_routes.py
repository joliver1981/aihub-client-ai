"""Flask test-client tests for the My Connections OAuth routes in
builder_mcp/routes/mcp_routes.py (WI-2 redirect pinning, WI-3 role gate,
WI-4 enforcement, the self-brokering callback, and the admin helpers).

No database, no provider, no cloud: ``_load_server_config``,
``server_available_to_users``, ``_resolve_tenant_id`` and
``exchange_authorization_code`` are patched; ``api_key_or_session_required``
is replaced with a stub that marks the request as session auth; ``current_user``
is a fake with a role. The test client's host is ``localhost``, so the return
address the routes derive is ``http://localhost/api/mcp/oauth/callback``.
"""
from __future__ import annotations

import importlib
import sys
from functools import wraps
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlsplit

import pytest

pytestmark = pytest.mark.api

API_KEY = "TEST-API-KEY-0000"
BROKER = "https://ai-hub-api.azurewebsites.net"
CALLBACK = "/api/mcp/oauth/callback"
AUTH_ENDPOINT = "https://login.microsoftonline.com/tid/oauth2/v2.0/authorize"


def _cfg(secret=True, grant="authorization_code", **extra):
    cfg = {
        "oauth_grant_type": grant,
        "oauth_auth_endpoint": AUTH_ENDPOINT,
        "oauth_token_endpoint": "https://login.microsoftonline.com/tid/oauth2/v2.0/token",
        "oauth_client_id": "client-id",
        "oauth_scope": "User.Read offline_access",
    }
    if secret:
        cfg["oauth_client_secret"] = "s3cret"
    cfg.update(extra)
    return cfg


class FakeUser(SimpleNamespace):
    @property
    def is_authenticated(self):
        return True


@pytest.fixture(scope="module")
def routes_module():
    """Import mcp_routes with the auth decorator stubbed (session auth)."""
    import role_decorators as rd

    def stub(permissions=None, min_role=None):
        def decorator(f):
            @wraps(f)
            def wrapper(*args, **kwargs):
                from flask import g
                g.auth_method = "session"
                return f(*args, **kwargs)
            return wrapper
        return decorator

    orig = rd.api_key_or_session_required
    rd.api_key_or_session_required = stub
    import builder_mcp.routes.mcp_routes as m
    m = importlib.reload(m)
    yield m
    rd.api_key_or_session_required = orig
    importlib.reload(m)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("API_KEY", API_KEY)
    monkeypatch.setenv("AI_HUB_API_URL", BROKER + "/")      # trailing slash on purpose
    monkeypatch.delenv("OAUTH_REDIRECT_BASE_URL", raising=False)
    monkeypatch.delenv("OAUTH_REQUIRE_CLIENT_SECRET", raising=False)


@pytest.fixture
def harness(routes_module, env, monkeypatch):
    """App + client + the patch points, bundled."""
    from flask import Flask
    import builder_mcp.agent_integration.oauth_manager as om
    import builder_mcp.agent_integration.mcp_server_visibility as vis

    m = routes_module
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test"
    app.register_blueprint(m.mcp_bp)

    state = SimpleNamespace(cfg=_cfg(), published=True, user=FakeUser(id=7, role=1),
                            exchange_calls=[], token="tok")

    monkeypatch.setattr(om, "_load_server_config", lambda sid: dict(state.cfg))
    monkeypatch.setattr(om, "exchange_authorization_code",
                        lambda **kw: (state.exchange_calls.append(kw), state.token)[1])
    monkeypatch.setattr(om, "get_access_token", lambda sid, user_id=None: "svc-token")
    monkeypatch.setattr(vis, "server_available_to_users", lambda sid, cursor=None: state.published)
    monkeypatch.setattr(m, "_resolve_tenant_id", lambda: 42)
    monkeypatch.setattr(m, "_tenant_id_cache", {"id": 0, "at": 0.0})
    # current_user is a module global (imported name) — swap it per test
    monkeypatch.setattr(m, "current_user", state.user)

    state.app = app
    state.client = app.test_client()
    state.m = m

    def set_user(role):
        state.user = FakeUser(id=7, role=role)
        monkeypatch.setattr(m, "current_user", state.user)

    state.set_user = set_user
    return state


def _authorize(h, server_id=30):
    return h.client.get(f"/api/mcp/oauth/authorize/{server_id}")


def _location_params(resp):
    parts = urlsplit(resp.headers["Location"])
    return parts, {k: v[0] for k, v in parse_qs(parts.query).items()}


# ---------------------------------------------------------------------------
# WI-2: the provider sees the REGISTERED URI; the state carries the return address
# ---------------------------------------------------------------------------

def test_role1_published_server_redirects_to_provider_with_broker_uri(harness):
    from builder_mcp.agent_integration.oauth_state import verify_state_with_key
    r = _authorize(harness)
    assert r.status_code == 302, r.data
    parts, q = _location_params(r)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == AUTH_ENDPOINT
    assert q["redirect_uri"] == BROKER + CALLBACK, "provider must see the broker, not the browser host"
    assert q["client_id"] == "client-id" and q["response_type"] == "code"
    assert q["code_challenge_method"] == "S256" and q["code_challenge"]
    payload = verify_state_with_key(q["state"], API_KEY)
    assert payload["t"] == 42
    assert payload["r"] == "http://localhost" + CALLBACK, "return address = the origin the user is on"
    with harness.client.session_transaction() as sess:
        entry = sess[f"mcp_oauth_state_{payload['n']}"]
        assert entry["server_id"] == 30 and entry["user_id"] == 7 and entry["code_verifier"]


def test_registered_uri_resolution_order(harness, monkeypatch):
    m = harness.m
    assert m._oauth_registered_redirect_uri(cfg=_cfg()) == (BROKER + CALLBACK, "ai_hub_api_url")
    monkeypatch.setenv("OAUTH_REDIRECT_BASE_URL", "http://localhost:5001/")
    assert m._oauth_registered_redirect_uri(cfg=_cfg()) == ("http://localhost:5001" + CALLBACK, "env")
    monkeypatch.setenv("OAUTH_REDIRECT_BASE_URL", "http://localhost:5001" + CALLBACK)
    assert m._oauth_registered_redirect_uri(cfg=_cfg())[0] == "http://localhost:5001" + CALLBACK
    override = "https://hub.customer.example/api/mcp/oauth/callback"
    assert m._oauth_registered_redirect_uri(cfg=_cfg(oauth_redirect_uri=override)) == (override, "server")
    monkeypatch.delenv("OAUTH_REDIRECT_BASE_URL")
    monkeypatch.delenv("AI_HUB_API_URL")
    assert m._oauth_registered_redirect_uri(cfg=_cfg()) == (
        "https://ai-hub-api.azurewebsites.net" + CALLBACK, "default")


def test_redirect_uri_endpoint_reports_registered_uri_and_return_address(harness):
    r = harness.client.get("/api/mcp/oauth/redirect_uri?server_id=30")
    assert r.status_code == 200
    d = r.get_json()
    assert d["redirect_uri"] == BROKER + CALLBACK
    assert d["source"] == "ai_hub_api_url"
    assert d["return_address"] == "http://localhost" + CALLBACK
    assert d["tenant_id"] == 42
    assert "Web" in d["platform_note"]


# ---------------------------------------------------------------------------
# WI-3: any signed-in user may connect; service accounts stay developer-only
# ---------------------------------------------------------------------------

def test_role1_unpublished_server_is_refused_with_403(harness):
    harness.published = False
    r = _authorize(harness)
    assert r.status_code == 403
    assert b"Available to users on My Connections" in r.data
    assert r.headers["Content-Type"].startswith("text/html")


def test_developer_can_authorize_an_unpublished_server(harness):
    harness.published = False
    harness.set_user(role=2)
    assert _authorize(harness).status_code == 302


def test_admin_can_authorize_an_unpublished_server(harness):
    harness.published = False
    harness.set_user(role=3)
    assert _authorize(harness).status_code == 302


def test_client_credentials_requires_developer_for_session_users(harness):
    harness.cfg = _cfg(grant="client_credentials")
    r = _authorize(harness)
    assert r.status_code == 403 and r.get_json()["status"] == "error"
    harness.set_user(role=2)
    r = _authorize(harness)
    assert r.status_code == 200 and r.get_json() == {"status": "success", "has_token": True}


def test_client_credentials_json_shape_is_unchanged_for_admin_page(harness):
    harness.cfg = _cfg(grant="client_credentials")
    harness.set_user(role=3)
    assert harness.client.get("/api/mcp/oauth/authorize/30").get_json()["status"] == "success"


# ---------------------------------------------------------------------------
# Pre-flight: no client secret → stop on-prem, name the fix
# ---------------------------------------------------------------------------

def test_missing_client_secret_is_refused_before_the_provider(harness):
    harness.cfg = _cfg(secret=False)
    r = _authorize(harness)
    assert r.status_code == 409
    assert b"client secret" in r.data and b"MCP Servers page" in r.data


def test_missing_client_secret_allowed_when_check_disabled(harness, monkeypatch):
    harness.cfg = _cfg(secret=False)
    monkeypatch.setenv("OAUTH_REQUIRE_CLIENT_SECRET", "false")
    assert _authorize(harness).status_code == 302


def test_unconfigured_server_is_refused(harness):
    harness.cfg = {}
    r = _authorize(harness)
    assert r.status_code == 400 and b"not configured" in r.data


# ---------------------------------------------------------------------------
# Session hygiene
# ---------------------------------------------------------------------------

def test_abandoned_flows_are_pruned_to_three_pending(harness):
    for _ in range(6):
        assert _authorize(harness).status_code == 302
    with harness.client.session_transaction() as sess:
        pending = [k for k in sess.keys() if k.startswith("mcp_oauth_state_")]
    assert len(pending) == 3


# ---------------------------------------------------------------------------
# Callback: verify, bounce, exchange with the REGISTERED uri
# ---------------------------------------------------------------------------

def _signed(return_url, nonce=None):
    from builder_mcp.agent_integration.oauth_state import sign_state
    return sign_state(API_KEY, 42, return_url, nonce=nonce)


def test_callback_refuses_an_unverifiable_state(harness):
    r = harness.client.get("/api/mcp/oauth/callback?code=abc&state=garbage")
    assert r.status_code == 400 and b"could not be verified" in r.data
    assert b"garbage" not in r.data


def test_callback_bounces_to_the_return_address_when_on_another_origin(harness):
    # The localhost-pin test (T2) and the per-server override case: the provider
    # sends the browser to THIS origin, but the flow started on 10.0.0.7.
    state, _ = _signed("http://10.0.0.7:5001" + CALLBACK)
    r = harness.client.get(f"/api/mcp/oauth/callback?code=abc&state={state}")
    assert r.status_code == 302
    parts, q = _location_params(r)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "http://10.0.0.7:5001" + CALLBACK
    assert q == {"code": "abc", "state": state}
    assert harness.exchange_calls == []


def test_callback_same_origin_exchanges_with_the_registered_uri(harness):
    state, nonce = _signed("http://localhost" + CALLBACK)
    with harness.client.session_transaction() as sess:
        sess[f"mcp_oauth_state_{nonce}"] = {"server_id": 30, "user_id": 7,
                                             "code_verifier": "ver", "created": 0}
    r = harness.client.get(f"/api/mcp/oauth/callback?code=abc&state={state}")
    assert r.status_code == 200 and b"Connected" in r.data, r.data
    assert harness.exchange_calls == [{
        "server_id": 30, "user_id": 7, "code": "abc",
        "redirect_uri": BROKER + CALLBACK,      # NOT the return address
        "code_verifier": "ver",
    }]
    with harness.client.session_transaction() as sess:
        assert f"mcp_oauth_state_{nonce}" not in sess, "single use"


def test_callback_without_a_pending_entry_is_a_state_mismatch(harness):
    state, _ = _signed("http://localhost" + CALLBACK)
    r = harness.client.get(f"/api/mcp/oauth/callback?code=abc&state={state}")
    assert r.status_code == 400 and b"mismatch" in r.data
    assert harness.exchange_calls == []


def test_callback_replay_is_refused(harness):
    state, nonce = _signed("http://localhost" + CALLBACK)
    with harness.client.session_transaction() as sess:
        sess[f"mcp_oauth_state_{nonce}"] = {"server_id": 30, "user_id": 7,
                                             "code_verifier": "ver", "created": 0}
    assert harness.client.get(f"/api/mcp/oauth/callback?code=abc&state={state}").status_code == 200
    assert harness.client.get(f"/api/mcp/oauth/callback?code=abc&state={state}").status_code == 400
    assert len(harness.exchange_calls) == 1


def test_callback_surfaces_provider_rejection_verbatim_but_escaped(harness):
    state, nonce = _signed("http://localhost" + CALLBACK)
    with harness.client.session_transaction() as sess:
        sess[f"mcp_oauth_state_{nonce}"] = {"server_id": 30, "user_id": 7,
                                             "code_verifier": "ver", "created": 0}
    import builder_mcp.agent_integration.oauth_manager as om

    def boom(**kw):
        raise RuntimeError("OAuth auth-code exchange failed (HTTP 401): AADSTS7000218 <b>x</b>")
    om.exchange_authorization_code = boom
    r = harness.client.get(f"/api/mcp/oauth/callback?code=abc&state={state}")
    assert r.status_code == 500
    assert b"AADSTS7000218" in r.data and b"&lt;b&gt;" in r.data and b"<b>x</b>" not in r.data


def test_callback_provider_error_is_escaped_not_reflected(harness):
    r = harness.client.get("/api/mcp/oauth/callback?error=%3Cscript%3Ealert(1)%3C/script%3E"
                           "&error_description=user+cancelled")
    assert r.status_code == 400
    assert b"<script>alert" not in r.data and b"&lt;script&gt;" in r.data
    assert b"user cancelled" in r.data


def test_callback_missing_params(harness):
    assert harness.client.get("/api/mcp/oauth/callback?state=x").status_code == 400
    assert harness.client.get("/api/mcp/oauth/callback?code=x").status_code == 400


# ---------------------------------------------------------------------------
# Self-verify endpoint (twin of the cloud one) + broker check plumbing
# ---------------------------------------------------------------------------

def test_verify_endpoint_answers_ok_or_reason_and_never_echoes_state(harness):
    state, _ = _signed("http://localhost" + CALLBACK)
    r = harness.client.post("/api/mcp/oauth/verify", json={"state": state})
    assert r.get_json() == {"ok": True, "tenant_id": 42, "return_origin": "http://localhost"}
    assert state.encode() not in r.data
    r = harness.client.post("/api/mcp/oauth/verify", json={"state": state[:-2] + "zz"})
    assert r.get_json() == {"ok": False, "reason": "bad_signature"}
    assert harness.client.post("/api/mcp/oauth/verify", json={}).status_code == 400


def test_broker_check_posts_a_signed_state_to_the_verify_url(harness, monkeypatch):
    calls = []

    class Resp:
        status_code = 200
        headers = {"Content-Type": "application/json"}

        def json(self):
            return {"ok": True, "tenant_id": 42, "return_origin": "http://localhost"}

    import requests
    monkeypatch.setattr(requests, "post", lambda url, **kw: (calls.append((url, kw)), Resp())[1])
    r = harness.client.get("/api/mcp/oauth/broker_check?server_id=30")
    d = r.get_json()
    assert d["ok"] is True and d["verify_url"] == BROKER + "/api/mcp/oauth/verify"
    assert d["broker_tenant_id"] == 42 and d["return_address"] == "http://localhost" + CALLBACK
    (url, kw), = calls
    from builder_mcp.agent_integration.oauth_state import verify_state_with_key
    assert verify_state_with_key(kw["json"]["state"], API_KEY)["r"] == "http://localhost" + CALLBACK


def test_broker_check_reports_unreachable_broker(harness, monkeypatch):
    import requests

    def down(url, **kw):
        raise requests.ConnectionError("refused")
    monkeypatch.setattr(requests, "post", down)
    d = harness.client.get("/api/mcp/oauth/broker_check").get_json()
    assert d["ok"] is False and "unreachable" in d["reason"]
