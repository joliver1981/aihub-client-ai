"""The Builder's sign-in token check carries the platform API key (2026-11-16).

THE BUG: a user who opens the Builder through /builder arrives with a
one-hour token; the Builder service asks the main app whether it is valid
(POST /api/validate-builder-token). That call went out with no credentials,
so the main app's global sign-in guard answered 401 before the route ran,
and the Builder treated every user as not signed in.

THE CONTRACT
  * Both Builder calls to /api/validate-builder-token (the token check and
    "create a session for this token") send X-API-Key with the service's
    platform key, the header the guard already accepts from the Command
    Center.
  * The guard itself is unchanged: the same call without a valid key is
    still refused.

End to end with the code that ships: the Builder's functions are lifted from
builder_service/routes/chat.py, the guard from auth_middleware.py, the key
extraction from role_decorators.py and the route from app.py
(app_route_harness; no service is started). Force-add to git (gitignore
hides test*.py).
"""
import asyncio
import datetime as dt
import logging
import os
import sys
import types
from pathlib import Path
from typing import Optional

import pytest
from flask import Flask, jsonify, redirect, request, url_for

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

httpx = pytest.importorskip("httpx")
pydantic = pytest.importorskip("pydantic")
fastapi = pytest.importorskip("fastapi")

from app_route_harness import load_app_symbols  # noqa: E402

pytestmark = pytest.mark.unit

PLATFORM_KEY = "unit-test-platform-key-0123456789"
GOOD_TOKEN = "good-token"
USER = {"user_id": 7, "role": 2, "tenant_id": 1, "username": "dev", "name": "Dev User"}


# ─── the main app side: real guard, real route ──────────────────────────

@pytest.fixture()
def main_app(monkeypatch):
    """A Flask app with the shipped sign-in guard in front of the shipped
    /api/validate-builder-token route. Nobody is signed in, so only a valid
    platform key gets a request past the guard."""
    # role_decorators as the guard imports it: the real key extraction, and a
    # validator that knows exactly one key.
    rd_ns = {"request": request}
    load_app_symbols(["_get_api_key_from_request"], rd_ns,
                     path=str(_ROOT / "role_decorators.py"))
    role_decorators = types.ModuleType("role_decorators")
    role_decorators._get_api_key_from_request = rd_ns["_get_api_key_from_request"]
    role_decorators.validate_api_key = lambda key: {"valid": key == PLATFORM_KEY}
    monkeypatch.setitem(sys.modules, "role_decorators", role_decorators)

    guard_ns = {
        "os": os, "request": request, "jsonify": jsonify, "redirect": redirect,
        "url_for": url_for, "auth_logger": logging.getLogger("t.guard"),
        "current_user": types.SimpleNamespace(is_authenticated=False),
        "AUTH_MIDDLEWARE_DRY_RUN": False,
    }
    load_app_symbols(["UNPROTECTED_ENDPOINTS", "ALTERNATIVE_AUTH_ENDPOINTS",
                      "SELF_AUTHENTICATING_PREFIXES", "is_local_request",
                      "is_api_request", "has_valid_api_key", "check_scheduler_auth",
                      "require_login_middleware"], guard_ns,
                     path=str(_ROOT / "auth_middleware.py"))

    route_ns = {
        "request": request, "jsonify": jsonify, "dt": dt,
        "builder_tokens": {GOOD_TOKEN: {
            **USER, "expires": dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)}},
    }
    load_app_symbols(["validate_builder_token"], route_ns)

    app = Flask(__name__)
    app.add_url_rule("/login", "login", lambda: "login page")
    app.add_url_rule("/api/validate-builder-token", "validate_builder_token",
                     route_ns["validate_builder_token"], methods=["POST"])
    app.before_request(guard_ns["require_login_middleware"])
    return app


# ─── the Builder side: real functions, calls routed into main_app ───────

class _Reply:
    def __init__(self, flask_response):
        self.status_code = flask_response.status_code
        self._json = flask_response.get_json(silent=True)

    def json(self):
        return self._json


@pytest.fixture()
def builder(main_app, monkeypatch):
    sent = []
    client = main_app.test_client()

    class _AsyncClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None):
            sent.append({"url": url, "headers": dict(headers or {})})
            path = "/" + url.split("/", 3)[3]
            return _Reply(client.post(path, json=json, headers=headers))

    builder_config = types.ModuleType("builder_config")
    builder_config.AI_HUB_API_KEY = PLATFORM_KEY
    monkeypatch.setitem(sys.modules, "builder_config", builder_config)

    created = []

    class _Sessions:
        def create_session(self, title=None, user_context=None):
            created.append(user_context)
            return types.SimpleNamespace(session_id="s1", to_dict=lambda: {"session_id": "s1"})

    ns = {
        "logger": logging.getLogger("t.builder"),
        "httpx": types.SimpleNamespace(AsyncClient=_AsyncClient,
                                       TimeoutException=httpx.TimeoutException,
                                       RequestError=httpx.RequestError),
        "BaseModel": pydantic.BaseModel, "Optional": Optional,
        "HTTPException": fastapi.HTTPException,
        "get_base_url": lambda: "http://127.0.0.1:5001",
        "UserContext": lambda **kw: types.SimpleNamespace(**kw),
        "session_manager": _Sessions(),
    }
    load_app_symbols(["_main_app_headers", "TokenValidationRequest", "validate_token",
                      "create_session_with_user"], ns,
                     path=str(_ROOT / "builder_service" / "routes" / "chat.py"))
    return types.SimpleNamespace(ns=ns, sent=sent, created=created, config=builder_config)


def _check(builder, token):
    return asyncio.run(builder.ns["validate_token"](builder.ns["TokenValidationRequest"](token=token)))


class TestHeaders:
    def test_platform_key_is_sent(self, builder):
        assert builder.ns["_main_app_headers"]() == {
            "Content-Type": "application/json", "X-API-Key": PLATFORM_KEY}

    def test_no_key_configured_sends_none_and_does_not_fail(self, builder):
        builder.config.AI_HUB_API_KEY = ""
        assert builder.ns["_main_app_headers"]() == {"Content-Type": "application/json"}

    def test_key_lookup_failure_does_not_fail_the_call(self, builder, monkeypatch):
        monkeypatch.setitem(sys.modules, "builder_config", types.ModuleType("builder_config"))
        assert builder.ns["_main_app_headers"]() == {"Content-Type": "application/json"}


class TestTokenCheck:
    def test_user_arriving_from_builder_link_is_signed_in(self, builder):
        result = _check(builder, GOOD_TOKEN)
        assert result == {"valid": True, "user": USER}
        assert builder.sent[0]["url"] == "http://127.0.0.1:5001/api/validate-builder-token"
        assert builder.sent[0]["headers"]["X-API-Key"] == PLATFORM_KEY

    def test_unknown_token_is_still_refused(self, builder):
        assert _check(builder, "not-a-token")["valid"] is False

    def test_wrong_key_is_stopped_by_the_guard(self, builder):
        """What every call looked like to the guard before the fix."""
        builder.config.AI_HUB_API_KEY = "some-other-key"
        assert _check(builder, GOOD_TOKEN) == {"valid": False, "error": "Validation request failed"}

    def test_no_key_is_stopped_by_the_guard(self, builder):
        builder.config.AI_HUB_API_KEY = ""
        assert _check(builder, GOOD_TOKEN) == {"valid": False, "error": "Validation request failed"}


class TestSessionForToken:
    def test_session_is_created_for_the_signed_in_user(self, builder):
        out = asyncio.run(builder.ns["create_session_with_user"](GOOD_TOKEN, "New Chat"))
        assert out == {"session_id": "s1"}
        assert builder.created[0].username == "dev"
        assert builder.sent[0]["headers"]["X-API-Key"] == PLATFORM_KEY

    def test_bad_token_is_401(self, builder):
        with pytest.raises(fastapi.HTTPException) as err:
            asyncio.run(builder.ns["create_session_with_user"]("not-a-token", "New Chat"))
        assert err.value.status_code == 401


class TestGuardIsUnchanged:
    def test_call_without_a_key_is_refused(self, main_app):
        resp = main_app.test_client().post("/api/validate-builder-token",
                                           json={"token": GOOD_TOKEN})
        assert resp.status_code == 401
        assert resp.get_json()["error"] == "Authentication required"

    def test_call_with_the_key_reaches_the_route(self, main_app):
        resp = main_app.test_client().post("/api/validate-builder-token",
                                           json={"token": GOOD_TOKEN},
                                           headers={"X-API-Key": PLATFORM_KEY})
        assert resp.status_code == 200
        assert resp.get_json()["valid"] is True


def test_every_token_check_uses_the_shared_headers():
    """Both call sites must go through _main_app_headers, so neither can be
    sent without the key again."""
    src = (_ROOT / "builder_service" / "routes" / "chat.py").read_text(encoding="utf-8")
    assert src.count("/api/validate-builder-token") == 2
    assert src.count("headers=_main_app_headers()") == 2
