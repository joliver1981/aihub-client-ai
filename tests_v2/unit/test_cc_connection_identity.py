"""Command Center platform calls carry the per-turn user
(workflow_tools.CURRENT_USER / identity_headers — docs/handoff-the-agent-
connection-acl.md §5 item 7, 2026-09-22).

THE CONTRACT (the same one test_cc_doc_identity holds _doc_identity_headers
to): the main app treats an ABSENT X-AIHub-User as service-internal =
UNRESTRICTED, so `_headers()` must attach a signed assertion whenever the
turn's context holds a real user, must attach NOTHING for anonymous / system
contexts (headless jobs keep today's behaviour on purpose), and must RAISE —
not silently degrade to unrestricted — when signing fails.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = str(Path(__file__).resolve().parents[2])
_CC = str(Path(_ROOT) / "command_center_service")


def _import_cc_workflow_tools():
    """Import the COMMAND CENTER graph.workflow_tools (same dance as
    test_cc_doc_identity: both builder_service and CC ship a `graph` package,
    so force CC first and restore afterwards)."""
    saved_path = list(sys.path)
    saved_mods = {k: v for k, v in sys.modules.items()
                  if k == "graph" or k.startswith("graph.")}
    try:
        for k in list(saved_mods):
            del sys.modules[k]
        sys.path.insert(0, _CC)
        import graph.workflow_tools as cc_wt  # noqa: PLC0415
        assert "command_center_service" in cc_wt.__file__.replace("\\", "/"), \
            f"resolved the wrong graph package: {cc_wt.__file__}"
        return cc_wt
    finally:
        sys.path[:] = saved_path
        for k in [k for k in sys.modules if k == "graph" or k.startswith("graph.")]:
            del sys.modules[k]
        sys.modules.update(saved_mods)


try:
    wt = _import_cc_workflow_tools()
except Exception as e:  # pragma: no cover - env-dependent
    pytest.skip(f"CC graph.workflow_tools not importable here: {e}", allow_module_level=True)

if _CC not in sys.path:
    sys.path.insert(0, _CC)          # cc_config for _headers()
sys.path.insert(0, _ROOT)
import shared_auth  # noqa: E402

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def jwt_env(monkeypatch):
    monkeypatch.setenv("API_KEY", "unit-test-tenant-key")
    monkeypatch.delenv("CC_JWT_SECRET", raising=False)


def _with_user(ctx, fn):
    tok = wt.CURRENT_USER.set(ctx)
    try:
        return fn()
    finally:
        wt.CURRENT_USER.reset(tok)


class TestConnectionIdentity:
    def test_no_user_in_context_sends_only_the_service_key(self):
        h = wt._headers()
        assert set(h) == {"X-API-Key"}

    def test_real_user_in_context_mints_a_verifiable_assertion(self):
        h = _with_user({"user_id": 349, "tenant_id": 1, "role": 1}, wt._headers)
        assert set(h) == {"X-API-Key", "X-AIHub-User"}
        claims, err = shared_auth.verify_token(h["X-AIHub-User"], shared_auth.AUD_INTERNAL)
        assert err is None and claims is not None
        assert claims["sub"] == "349" and claims["role"] == 1 and claims["tenant_id"] == 1

    def test_explicit_context_wins_over_the_contextvar(self):
        def _explicit():
            return wt.identity_headers({"user_id": 351, "tenant_id": 1, "role": 1})
        h = _with_user({"user_id": 349, "tenant_id": 1, "role": 1}, _explicit)
        claims, _ = shared_auth.verify_token(h["X-AIHub-User"], shared_auth.AUD_INTERNAL)
        assert claims["sub"] == "351" and claims["role"] == 1

    @pytest.mark.parametrize("role", [2, 3, "2", "3"])
    def test_developers_and_admins_stay_identity_less(self, role):
        # The platform treats role >= 2 as unrestricted for connections, and
        # its agent-listing routes would shrink an asserted Developer's view.
        ctx = {"user_id": 353, "tenant_id": 1, "role": role}
        assert wt.identity_headers(ctx) == {}
        assert set(_with_user(ctx, wt._headers)) == {"X-API-Key"}

    @pytest.mark.parametrize("ctx", [
        {}, {"user_id": None}, {"user_id": ""}, {"user_id": 0}, {"user_id": "0"},
        {"user_id": "anonymous"},
    ])
    def test_anonymous_and_system_contexts_send_nothing(self, ctx):
        assert _with_user(ctx, wt.identity_headers) == {}
        assert set(_with_user(ctx, wt._headers)) == {"X-API-Key"}

    def test_wrong_audience_rejected(self):
        h = _with_user({"user_id": 349, "role": 1}, wt.identity_headers)
        _claims, err = shared_auth.verify_token(h["X-AIHub-User"], shared_auth.AUD_CC)
        assert err is not None, "an INTERNAL assertion must not pass as a CC session"

    def test_signing_failure_raises_not_fail_open(self, monkeypatch):
        monkeypatch.setattr(shared_auth, "sign_user_assertion",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        with pytest.raises(RuntimeError):
            _with_user({"user_id": 349, "role": 1}, wt._headers)

    def test_context_does_not_leak_after_reset(self):
        _with_user({"user_id": 349, "role": 1}, wt._headers)
        assert set(wt._headers()) == {"X-API-Key"}
