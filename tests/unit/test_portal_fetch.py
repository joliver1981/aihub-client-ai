"""
Unit tests for the Command Center portal-fetch tool (browser-use RPA).
======================================================================
Exercises command_center/tools/portal_fetch.py — the CC-side core that calls the
isolated Browser Use service over HTTP and registers downloaded files as CC
artifacts. The service, CommonUtils URL helper, and artifact manager are all
stubbed, so these run fast with no live browser, network, or DB.

Run:
    python -m pytest tests/unit/test_portal_fetch.py -v
"""
import sys
import types

import pytest

sys.path.insert(0, r"C:/src/aihub-client-ai-dev")

from command_center.tools import portal_fetch as pf  # noqa: E402


def _raise(*_a, **_k):
    raise RuntimeError("boom")


# --------------------------------------------------------------------------
# _secret_keys — portal name -> local_secrets KEY NAMES (never raw creds)
# --------------------------------------------------------------------------

def test_secret_keys_basic():
    assert pf._secret_keys("acme") == {
        "username_secret": "PORTAL_ACME_USERNAME",
        "password_secret": "PORTAL_ACME_PASSWORD",
        "totp_secret": "PORTAL_ACME_TOTP",
    }


def test_secret_keys_slug_spaces_and_case():
    assert pf._secret_keys("Acme Vendor") == {
        "username_secret": "PORTAL_ACME_VENDOR_USERNAME",
        "password_secret": "PORTAL_ACME_VENDOR_PASSWORD",
        "totp_secret": "PORTAL_ACME_VENDOR_TOTP",
    }


def test_secret_keys_strips_leading_trailing_separators():
    # leading/trailing non-alphanumerics must not leak into the key name
    k = pf._secret_keys(".acme!")
    assert k["password_secret"] == "PORTAL_ACME_PASSWORD"


# --------------------------------------------------------------------------
# _register_artifacts — downloaded files -> CC artifact (download chip) blocks
# --------------------------------------------------------------------------

def test_register_artifacts_empty_returns_empty():
    assert pf._register_artifacts([], "sess", {"user_id": 1}) == []


def test_register_artifacts_registers_downloads(tmp_path, monkeypatch):
    f = tmp_path / "report.csv"
    f.write_bytes(b"month,amount\n2026-01,1250.00\n")  # bytes: avoid Windows \n->\r\n

    created = {}

    class _FakeMeta:
        def __init__(self, name):
            self._name = name

        def to_content_block(self):
            return {"type": "artifact", "name": self._name, "artifact_id": "a1"}

    class _FakeMgr:
        def create(self, name, atype, data, scoped):
            created["args"] = (name, atype, data, scoped)
            return _FakeMeta(name)

    # Stub routes.artifacts._get_artifact_manager
    fake_routes = types.ModuleType("routes")
    fake_artifacts = types.ModuleType("routes.artifacts")
    fake_artifacts._get_artifact_manager = lambda: _FakeMgr()
    fake_routes.artifacts = fake_artifacts
    monkeypatch.setitem(sys.modules, "routes", fake_routes)
    monkeypatch.setitem(sys.modules, "routes.artifacts", fake_artifacts)

    # Stub command_center.artifacts.artifact_models.ArtifactType
    class _FakeArtifactType:
        TEXT = "text"

        def __new__(cls, v):
            return v

    fake_pkg = types.ModuleType("command_center.artifacts")
    fake_models = types.ModuleType("command_center.artifacts.artifact_models")
    fake_models.ArtifactType = _FakeArtifactType
    fake_pkg.artifact_models = fake_models
    monkeypatch.setitem(sys.modules, "command_center.artifacts", fake_pkg)
    monkeypatch.setitem(sys.modules, "command_center.artifacts.artifact_models", fake_models)

    blocks = pf._register_artifacts([str(f)], "sess123", {"user_id": 7})

    assert len(blocks) == 1
    assert blocks[0]["type"] == "artifact"
    assert blocks[0]["name"] == "report.csv"
    name, atype, data, scoped = created["args"]
    assert name == "report.csv"
    assert atype == "csv"               # .csv -> csv artifact type
    assert data == b"month,amount\n2026-01,1250.00\n"
    assert scoped == "7/sess123"        # "{user_id}/{session_id}"


def test_register_artifacts_skips_missing_file(monkeypatch):
    # a path that doesn't exist on disk must be skipped, not crash
    monkeypatch.setitem(sys.modules, "routes", types.ModuleType("routes"))
    assert pf._register_artifacts(["/nope/missing.pdf"], "s", {"user_id": 1}) == []


# --------------------------------------------------------------------------
# fetch_portal — HTTP flow + error handling (service stubbed)
# --------------------------------------------------------------------------

def _patch_base(monkeypatch, base="http://127.0.0.1:5101"):
    import CommonUtils
    monkeypatch.setattr(CommonUtils, "get_browser_use_api_base_url", lambda: base)


def test_fetch_portal_service_url_unavailable(monkeypatch):
    import CommonUtils
    monkeypatch.setattr(CommonUtils, "get_browser_use_api_base_url", _raise)
    res = pf.fetch_portal("acme", "https://acme.com", "do it")
    assert res["status"] == "error"
    assert "service URL unavailable" in res["error"]
    assert res["blocks"] == [] and res["file_count"] == 0


def test_fetch_portal_happy_path(monkeypatch):
    _patch_base(monkeypatch)

    class _Resp:
        status_code = 200

        def json(self):
            return {"status": "ok", "error": None, "final_result": "done",
                    "files": ["/dl/report.csv"]}

    captured = {}

    def _post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["payload"] = json
        captured["headers"] = headers
        return _Resp()

    monkeypatch.setattr(pf.requests, "post", _post)
    monkeypatch.setattr(pf, "_register_artifacts",
                        lambda files, sid, uc: [{"type": "artifact", "name": "report.csv"}])

    res = pf.fetch_portal("Acme Vendor", "https://acme.com", "download statement",
                          session_id="s1", user_context={"user_id": 3})

    assert res["status"] == "ok"
    assert res["file_count"] == 1
    assert res["blocks"][0]["name"] == "report.csv"
    assert res["final_result"] == "done"
    # the tool sends only secret KEY NAMES, never raw credentials
    assert captured["payload"]["username_secret"] == "PORTAL_ACME_VENDOR_USERNAME"
    assert "password" not in captured["payload"]
    assert captured["url"].endswith("/portal/fetch")


def test_fetch_portal_non_200(monkeypatch):
    _patch_base(monkeypatch)

    class _Resp:
        status_code = 500
        text = "kaboom"

        def json(self):
            return {}

    monkeypatch.setattr(pf.requests, "post", lambda *a, **k: _Resp())
    res = pf.fetch_portal("acme", "https://acme.com", "x")
    assert res["status"] == "error"
    assert "500" in res["error"]
    assert res["file_count"] == 0


def test_fetch_portal_unreachable(monkeypatch):
    _patch_base(monkeypatch)
    monkeypatch.setattr(pf.requests, "post", _raise)
    res = pf.fetch_portal("acme", "https://acme.com", "x")
    assert res["status"] == "error"
    assert "could not reach" in res["error"]


# --------------------------------------------------------------------------
# credential modes on the wire: inline (ad-hoc) vs key-name references
# --------------------------------------------------------------------------

def _capture_post(monkeypatch):
    captured = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"status": "ok", "files": []}

    def _post(url, json=None, headers=None, timeout=None):
        captured["payload"] = json
        return _Resp()

    monkeypatch.setattr(pf.requests, "post", _post)
    return captured


def test_fetch_portal_inline_creds_payload(monkeypatch):
    _patch_base(monkeypatch)
    captured = _capture_post(monkeypatch)
    pf.fetch_portal("portalx", "https://www.portalx.com", "download receipts",
                    inline_creds={"username": "user123", "password": "password456"})
    p = captured["payload"]
    # ad-hoc: raw creds present, NO key-name references
    assert p["username"] == "user123" and p["password"] == "password456"
    assert "username_secret" not in p and "password_secret" not in p


def test_fetch_portal_key_overrides_payload(monkeypatch):
    _patch_base(monkeypatch)
    captured = _capture_post(monkeypatch)
    pf.fetch_portal("acme", "https://acme.com", "x",
                    secret_key_overrides={"username_secret": "PORTAL_U13_ACME_USERNAME",
                                          "password_secret": "PORTAL_U13_ACME_PASSWORD",
                                          "totp_secret": None})
    p = captured["payload"]
    # saved portal: key-name references present, raw creds absent, None totp filtered out
    assert p["username_secret"] == "PORTAL_U13_ACME_USERNAME"
    assert "username" not in p
    assert "totp_secret" not in p
