"""CC document-search identity minting (_doc_identity_headers).

THE CONTRACT UNDER TEST: the main app's internal document endpoints treat an
absent X-AIHub-User as service-internal = UNRESTRICTED. So CC must attach a
signed assertion whenever it has a real user, must attach NOTHING for
anonymous/system sessions (headless jobs keep today's behavior on purpose),
and must RAISE — not silently degrade to unrestricted — when signing fails.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = str(Path(__file__).resolve().parents[2])
_CC = str(Path(_ROOT) / "command_center_service")


def _import_cc_nodes():
    """Import the COMMAND CENTER graph.nodes (same dance as
    test_cc_automation_gating: both builder_service and CC ship a `graph`
    package, so force CC first and restore afterwards)."""
    saved_path = list(sys.path)
    saved_mods = {k: v for k, v in sys.modules.items()
                  if k == "graph" or k.startswith("graph.")}
    try:
        for k in list(saved_mods):
            del sys.modules[k]
        sys.path.insert(0, _CC)
        import graph.nodes as cc_nodes  # noqa: PLC0415
        assert "command_center_service" in cc_nodes.__file__.replace("\\", "/"), \
            f"resolved the wrong graph package: {cc_nodes.__file__}"
        return cc_nodes
    finally:
        sys.path[:] = saved_path
        for k in [k for k in sys.modules if k == "graph" or k.startswith("graph.")]:
            del sys.modules[k]
        sys.modules.update(saved_mods)


try:
    nodes = _import_cc_nodes()
except Exception as e:  # pragma: no cover - env-dependent
    pytest.skip(f"CC graph.nodes not importable here: {e}", allow_module_level=True)

sys.path.insert(0, _ROOT)
import shared_auth  # noqa: E402


@pytest.fixture
def jwt_env(monkeypatch):
    monkeypatch.setenv("API_KEY", "unit-test-tenant-key")
    monkeypatch.delenv("CC_JWT_SECRET", raising=False)


class TestDocIdentityHeaders:
    def test_real_user_mints_verifiable_assertion(self, jwt_env):
        h = nodes._doc_identity_headers(
            {"user_id": 125, "tenant_id": "t1", "role": 1})
        assert set(h) == {"X-AIHub-User"}
        claims, err = shared_auth.verify_token(h["X-AIHub-User"],
                                               shared_auth.AUD_INTERNAL)
        assert err is None and claims is not None
        assert claims["sub"] == "125"
        assert claims["role"] == 1
        assert claims["tenant_id"] == "t1"

    def test_wrong_audience_rejected(self, jwt_env):
        h = nodes._doc_identity_headers({"user_id": 125, "role": 1})
        _claims, err = shared_auth.verify_token(h["X-AIHub-User"],
                                                shared_auth.AUD_CC)
        assert err is not None, "an INTERNAL assertion must not pass as a CC session"

    @pytest.mark.parametrize("ctx", [
        None, {}, {"user_id": None}, {"user_id": ""}, {"user_id": 0},
        {"user_id": "anonymous"},
    ])
    def test_system_and_anonymous_send_nothing(self, jwt_env, ctx):
        assert nodes._doc_identity_headers(ctx) == {}

    def test_signing_failure_raises_not_fail_open(self, jwt_env, monkeypatch):
        monkeypatch.setattr(shared_auth, "sign_user_assertion",
                            lambda *a, **k: (_ for _ in ()).throw(
                                RuntimeError("boom")))
        with pytest.raises(RuntimeError):
            nodes._doc_identity_headers({"user_id": 125, "role": 1})

    def test_no_secret_configured_raises_not_fail_open(self, monkeypatch):
        monkeypatch.delenv("API_KEY", raising=False)
        monkeypatch.delenv("AI_HUB_API_KEY", raising=False)
        monkeypatch.delenv("CC_JWT_SECRET", raising=False)
        with pytest.raises(Exception):
            nodes._doc_identity_headers({"user_id": 125, "role": 1})
