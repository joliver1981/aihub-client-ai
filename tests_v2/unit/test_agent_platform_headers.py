"""platform_tools._headers() identity minting (doc-acl G3, 2026-09-03).

THE CONTRACT (the same one tests_v2/unit/test_cc_doc_identity.py holds CC
to): the main app's identity-aware routes treat an ABSENT X-AIHub-User as
service-internal = UNRESTRICTED, so The Agent's platform calls must attach a
signed assertion whenever the turn runs for a real user, must attach NOTHING
for the service principal (user_id 0, the contextvar default) or an
identity-less run, and must RAISE — not silently degrade to unrestricted —
when signing fails. The raise reaches the model as an honest tool error, not
as a quietly unrestricted answer.

Runs standalone (aihub-agent python test_agent_platform_headers.py — that env
has no pytest) or under pytest; self-skips where claude_agent_sdk is absent
(the main-app sweep). Force-add to git (gitignore hides test*.py).
"""
import asyncio
import os
import sys
from unittest import mock

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

try:
    import platform_tools                      # noqa: E402
    from platform_tools import CURRENT_USER    # noqa: E402
    import shared_auth                         # noqa: E402
    HAVE_SDK = True
except ImportError as e:
    HAVE_SDK = False
    _IMPORT_ERR = e

if not HAVE_SDK:
    try:
        import pytest
        pytestmark = pytest.mark.skip(
            reason=f"needs the aihub-agent env (claude_agent_sdk): {_IMPORT_ERR}")
    except ImportError:
        pass

_SECRET = "unit-test-secret-key-please-do-not-ship-0123456789"
REAL = {"user_id": "141", "role": 2, "tenant_id": "t1", "username": "developer"}


class _env:
    """Deterministic signing secret for the duration of one test."""

    def __enter__(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("CC_JWT_SECRET", "API_KEY", "AI_HUB_API_KEY")}
        os.environ["CC_JWT_SECRET"] = _SECRET
        return self

    def __exit__(self, *exc):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _with_user(user):
    tok = CURRENT_USER.set(dict(user)) if user is not None else None
    return tok


def test_real_user_mints_verifiable_assertion():
    with _env():
        tok = CURRENT_USER.set(dict(REAL))
        try:
            h = platform_tools._headers()
        finally:
            CURRENT_USER.reset(tok)
        assert h["X-API-Key"] and h["Connection"] == "close"
        claims, err = shared_auth.verify_token(h["X-AIHub-User"], shared_auth.AUD_INTERNAL)
        assert err is None and claims, err
        assert claims["sub"] == "141" and claims["role"] == 2 and claims["tenant_id"] == "t1"


def test_assertion_is_not_a_cc_session_token():
    with _env():
        tok = CURRENT_USER.set(dict(REAL))
        try:
            h = platform_tools._headers()
        finally:
            CURRENT_USER.reset(tok)
        _claims, err = shared_auth.verify_token(h["X-AIHub-User"], shared_auth.AUD_CC)
        assert err is not None, "an INTERNAL assertion must not pass as a CC session"


def test_service_principal_and_identity_less_send_nothing():
    with _env():
        # the contextvar DEFAULT is the service principal (user_id 0)
        assert "X-AIHub-User" not in platform_tools._headers()
        for uid in (None, "", 0, "0"):
            tok = CURRENT_USER.set({"user_id": uid, "role": 2})
            try:
                assert "X-AIHub-User" not in platform_tools._headers(), uid
            finally:
                CURRENT_USER.reset(tok)


def test_signing_failure_raises_not_fail_open():
    with _env():
        tok = CURRENT_USER.set(dict(REAL))
        try:
            with mock.patch.object(shared_auth, "sign_user_assertion",
                                   side_effect=RuntimeError("boom")):
                try:
                    platform_tools._headers()
                except RuntimeError as e:
                    assert "could not sign" in str(e)
                else:
                    raise AssertionError("_headers() must RAISE when signing fails — "
                                         "an identity-less call runs UNRESTRICTED")
        finally:
            CURRENT_USER.reset(tok)


def test_no_secret_configured_raises_not_fail_open():
    with _env():
        for k in ("CC_JWT_SECRET", "API_KEY", "AI_HUB_API_KEY"):
            os.environ.pop(k, None)
        tok = CURRENT_USER.set(dict(REAL))
        try:
            try:
                platform_tools._headers()
            except Exception:
                pass
            else:
                raise AssertionError("no signing secret must not degrade to unrestricted")
        finally:
            CURRENT_USER.reset(tok)


def test_ask_agent_surfaces_the_failure_as_an_honest_error():
    """The raise must reach the model as a tool error, never as a silent
    unrestricted call: ask_agent catches it and no HTTP request is made."""
    with _env():
        tok = CURRENT_USER.set(dict(REAL))
        try:
            with mock.patch.object(shared_auth, "sign_user_assertion",
                                   side_effect=RuntimeError("boom")), \
                 mock.patch.object(platform_tools.httpx.AsyncClient, "post",
                                   side_effect=AssertionError("must not call the main app")):
                res = asyncio.run(platform_tools.ask_agent.handler(
                    {"agent_id": 1, "question": "x"}))
        finally:
            CURRENT_USER.reset(tok)
        text = " ".join(c.get("text", "") for c in res.get("content", []))
        assert res.get("is_error") is True
        assert "could not sign" in text, text


def _main():
    fails = 0
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:
            fails += 1
            print(f"FAIL {name}: {e!r}")
    print(f"{len(tests) - fails}/{len(tests)} PASS")
    return 1 if fails else 0


if __name__ == "__main__":
    if not HAVE_SDK:
        print(f"SKIP: needs aihub-agent env ({_IMPORT_ERR})")
        sys.exit(0)
    sys.exit(_main())
