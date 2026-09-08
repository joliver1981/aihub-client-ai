"""Reserved platform secret names (2026-09-08).

Root cause being guarded: a key a user pasted into The Agent's chat was saved to the
encrypted Local Secrets store under the platform's own name API_KEY (2026-09-05). The
Browser Use service resolved that name store-first, gated internal calls on the pasted
value, and every Command Center portal run 401'd for three days while /health said ok.

Covers:
  * the denylist + rename helper in local_secrets (identity tier everywhere, platform
    namespaces on the service path only, idempotent, bounded)
  * LocalSecretsManager.set refuses identity names at the chokepoint
  * quarantine_reserved_secrets heals an already-poisoned store (value preserved, dry-run)
  * the three write paths: /workflow/secrets/store (app.py, via the ast harness — the
    exact shipped source), the Local Secrets page add + import routes (blueprint)
  * portal_fetch / portal_workflow_run: a 401 from the Browser Use service is reported as
    AI Hub's own token gate, never as a portal credential problem
  * The Agent's store_platform_secret reports the FINAL name on a rename (SDK env only)

Run: C:\\Users\\james\\miniconda3\\envs\\aihub2.1\\python.exe -m pytest tests_v2/unit/test_reserved_secret_names.py -q
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from unittest import mock

import pytest
from flask import Flask, jsonify, request

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import local_secrets as LS  # noqa: E402
from app_route_harness import load_app_symbols  # noqa: E402


# ---------------------------------------------------------------------------
# helper tier
# ---------------------------------------------------------------------------
IDENTITY = ["API_KEY", "AI_HUB_API_KEY", "CC_JWT_SECRET"]


@pytest.mark.parametrize("name", IDENTITY)
def test_identity_names_are_renamed_on_every_path(name):
    for platform_ns in (False, True):
        final, reason = LS.resolve_reserved_secret_name(name, platform_namespaces=platform_ns)
        assert final == "CUSTOM_" + name
        assert reason and "reserved for the platform" in reason


def test_identity_check_is_case_and_whitespace_insensitive():
    final, reason = LS.resolve_reserved_secret_name("  api_key ")
    assert final == "CUSTOM_API_KEY" and reason


@pytest.mark.parametrize("name", ["SENDGRID_API_KEY", "CUSTOM_API_KEY", "MY_PORTAL_PASSWORD",
                                  "TEST_SHARED_KEY", "USER_FOO"])
def test_free_names_pass_through_unchanged(name):
    assert LS.resolve_reserved_secret_name(name) == (name, None)
    assert LS.resolve_reserved_secret_name(name, platform_namespaces=True) == (name, None)


@pytest.mark.parametrize("name", ["PORTAL_U13_ACME_PASSWORD", "INT_42_API_KEY", "CONN_PWD_154",
                                  "OAUTH_SHAREPOINT_ONLINE_CLIENT_SECRET", "SOL_X_CONN_PWD",
                                  "USER_ANTHROPIC_API_KEY", "EMAIL_SMTP_PASSWORD", "WINRM_PWD",
                                  "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AZURE_OPENAI_API_KEY"])
def test_platform_namespaces_reserved_only_on_the_service_path(name):
    # UI path (Local Secrets page): a human may edit these — untouched.
    assert LS.resolve_reserved_secret_name(name) == (name, None)
    # Service path (The Agent): renamed, with a reason.
    final, reason = LS.resolve_reserved_secret_name(name, platform_namespaces=True)
    assert final == "CUSTOM_" + name and reason


def test_vendor_keys_are_not_quarantined_the_store_is_their_legit_home(tmp_path):
    """automations/runner.py resolves manifest `secrets` from the store BY NAME and injects
    them as env vars — a bare ANTHROPIC_API_KEY entry is how an automation gets its key. The
    startup quarantine must leave it alone (2026-09-08 it renamed a live one; reverted)."""
    m = LS.LocalSecretsManager(str(tmp_path))
    m.set("ANTHROPIC_API_KEY", "tenant-automation-key")   # direct platform/admin write is fine
    assert LS.quarantine_reserved_secrets(m, dry_run=True) == []
    assert m.get("ANTHROPIC_API_KEY") == "tenant-automation-key"


def test_rename_is_a_single_hop_and_never_reserved():
    final, _ = LS.resolve_reserved_secret_name("API_KEY", platform_namespaces=True)
    assert LS.reserved_secret_reason(final, platform_namespaces=True) is None
    # the prefix itself never collides, so re-resolving the result is a no-op
    assert LS.resolve_reserved_secret_name(final, platform_namespaces=True) == (final, None)


def test_denylist_not_allowlist():
    """A brand-new, unknown name must be accepted — the guard is a denylist (james)."""
    assert LS.resolve_reserved_secret_name("SOME_VENDOR_NOBODY_HEARD_OF_TOKEN")[1] is None


# ---------------------------------------------------------------------------
# chokepoint + quarantine (real manager on a tmp data dir)
# ---------------------------------------------------------------------------
@pytest.fixture
def manager(tmp_path):
    return LS.LocalSecretsManager(str(tmp_path))


def test_manager_set_refuses_identity_names(manager):
    for name in IDENTITY:
        with pytest.raises(ValueError, match="platform-reserved"):
            manager.set(name, "x")
    assert manager.list() == []
    manager.set("CUSTOM_API_KEY", "x")           # the rename target is fine
    manager.set("PORTAL_U13_ACME_PASSWORD", "x")  # platform code writes these directly
    assert {s["name"] for s in manager.list()} == {"CUSTOM_API_KEY", "PORTAL_U13_ACME_PASSWORD"}


def test_quarantine_renames_poisoned_entries_and_preserves_the_value(manager):
    # Poison the store the way the pre-guard code could (bypass set()'s new refusal).
    raw = manager._load_secrets(use_cache=False)
    raw["API_KEY"] = {"value": "pasted-14-chars", "description": "API key provided by user in chat",
                      "category": "api_keys", "created": "2026-09-05T16:00:24", "updated": "x"}
    raw["SENDGRID_API_KEY"] = {"value": "keep", "description": "", "category": "api_keys",
                               "created": "c", "updated": "u"}
    manager._save_secrets(raw)

    preview = LS.quarantine_reserved_secrets(manager, dry_run=True)
    assert preview == [{"from": "API_KEY", "to": "CUSTOM_API_KEY"}]
    assert manager.exists("API_KEY"), "dry_run must not write"

    moved = LS.quarantine_reserved_secrets(manager)
    assert moved == [{"from": "API_KEY", "to": "CUSTOM_API_KEY"}]
    assert not manager.exists("API_KEY")
    assert manager.get("CUSTOM_API_KEY") == "pasted-14-chars"
    meta = {s["name"]: s for s in manager.list()}["CUSTOM_API_KEY"]
    assert meta["created"] == "2026-09-05T16:00:24"
    assert "renamed from API_KEY" in meta["description"]
    assert manager.get("SENDGRID_API_KEY") == "keep"
    # idempotent
    assert LS.quarantine_reserved_secrets(manager) == []


def test_quarantine_never_clobbers_an_existing_custom_entry(manager):
    raw = manager._load_secrets(use_cache=False)
    raw["API_KEY"] = {"value": "pasted", "description": "", "category": "api_keys",
                      "created": "c", "updated": "u"}
    raw["CUSTOM_API_KEY"] = {"value": "mine", "description": "", "category": "api_keys",
                             "created": "c", "updated": "u"}
    manager._save_secrets(raw)
    assert LS.quarantine_reserved_secrets(manager) == [{"from": "API_KEY", "to": "CUSTOM_CUSTOM_API_KEY"}]
    assert manager.get("CUSTOM_API_KEY") == "mine"
    assert manager.get("CUSTOM_CUSTOM_API_KEY") == "pasted"


# ---------------------------------------------------------------------------
# write path 1: /workflow/secrets/store (The Agent) — exact app.py source
# ---------------------------------------------------------------------------
@pytest.fixture
def service_store_app(manager):
    ns = {"request": request, "jsonify": jsonify, "logger": logging.getLogger("t"),
          "get_secrets_manager": lambda: manager}
    load_app_symbols(["workflow_secrets_store"], ns)
    app = Flask("t")
    app.add_url_rule("/workflow/secrets/store", "store", ns["workflow_secrets_store"], methods=["POST"])
    return app.test_client()


def test_service_store_renames_api_key_and_reports_final_name(service_store_app, manager):
    r = service_store_app.post("/workflow/secrets/store",
                               json={"name": "API_KEY", "value": "pasted-in-chat",
                                     "description": "API key provided by user in chat"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] is True
    assert body["name"] == "CUSTOM_API_KEY"
    assert body["renamed"] is True and body["requested_name"] == "API_KEY"
    assert "reserved for the platform" in body["reason"]
    assert not manager.exists("API_KEY")
    assert manager.get("CUSTOM_API_KEY") == "pasted-in-chat"


@pytest.mark.parametrize("name", ["PORTAL_U13_MERIDIAN_VENDOR_PORTAL_PASSWORD", "CONN_PWD_154",
                                  "INT_27_CLIENT_SECRET", "USER_ANTHROPIC_API_KEY"])
def test_service_store_keeps_the_agent_out_of_platform_namespaces(service_store_app, manager, name):
    r = service_store_app.post("/workflow/secrets/store", json={"name": name, "value": "v"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["name"] == "CUSTOM_" + name and body["renamed"] is True
    assert not manager.exists(name)


def test_service_store_free_name_unchanged_and_not_flagged(service_store_app, manager):
    r = service_store_app.post("/workflow/secrets/store", json={"name": "sendgrid_api_key", "value": "v"})
    body = r.get_json()
    assert body == {"success": True, "name": "SENDGRID_API_KEY", "is_update": False}
    assert manager.get("SENDGRID_API_KEY") == "v"


def test_service_store_still_validates_format(service_store_app):
    r = service_store_app.post("/workflow/secrets/store", json={"name": "9BAD-NAME", "value": "v"})
    assert r.status_code == 400 and r.get_json()["success"] is False


# ---------------------------------------------------------------------------
# write paths 2 + 3: the Local Secrets page (blueprint) — identity tier only
# ---------------------------------------------------------------------------
@pytest.fixture
def ui_client(manager, monkeypatch):
    import local_secrets_routes as R
    monkeypatch.setattr(R, "get_secrets_manager", lambda: manager)
    app = Flask("ui")
    app.config["LOGIN_DISABLED"] = True  # flask_login.login_required passes through
    app.register_blueprint(R.secrets_bp)
    return app.test_client()


def test_ui_add_renames_identity_name_and_explains(ui_client, manager):
    r = ui_client.post("/api/local-secrets", json={"name": "API_KEY", "value": "pasted"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["name"] == "CUSTOM_API_KEY" and body["renamed"] is True
    assert "stored as 'CUSTOM_API_KEY'" in body["message"]
    assert manager.get("CUSTOM_API_KEY") == "pasted" and not manager.exists("API_KEY")


def test_ui_add_still_lets_a_human_edit_platform_namespaced_entries(ui_client, manager):
    manager.set("PORTAL_U13_ACME_PASSWORD", "old", category="portal")
    r = ui_client.post("/api/local-secrets", json={"name": "PORTAL_U13_ACME_PASSWORD", "value": "rotated"})
    body = r.get_json()
    assert body["name"] == "PORTAL_U13_ACME_PASSWORD" and body["is_update"] is True
    assert "renamed" not in body
    assert manager.get("PORTAL_U13_ACME_PASSWORD") == "rotated"


def test_ui_import_renames_identity_names_and_reports_them(ui_client, manager):
    r = ui_client.post("/api/local-secrets/import", json={"secrets": {
        "CC_JWT_SECRET": {"value": "s", "description": "d", "category": "api_keys"},
        "SENDGRID_API_KEY": "plain",
    }})
    body = r.get_json()
    assert body["imported"] == 2 and body["skipped"] == 0
    assert body["renamed"] == [{"from": "CC_JWT_SECRET", "to": "CUSTOM_CC_JWT_SECRET",
                                "reason": mock.ANY}]
    assert manager.get("CUSTOM_CC_JWT_SECRET") == "s" and not manager.exists("CC_JWT_SECRET")
    assert manager.get("SENDGRID_API_KEY") == "plain"


# ---------------------------------------------------------------------------
# portal_fetch: a 401 from the service is AI Hub's gate, never the portal's
# ---------------------------------------------------------------------------
def _cc_tools():
    from command_center.tools import portal_fetch as pf
    return pf


class _Resp:
    def __init__(self, code, text="", data=None):
        self.status_code, self.text, self._data = code, text, data

    def json(self):
        return self._data


def test_service_error_text_distinguishes_internal_gate_from_portal_login():
    pf = _cc_tools()
    msg = pf.service_error_text(401, '{"detail":"invalid or missing internal token"}')
    assert "NOT a portal login failure" in msg
    assert "do not ask the user for portal credentials" in msg
    assert "internal_auth" in msg
    assert pf.service_error_text(500, "boom") == "service returned 500: boom"


def test_start_and_fetch_surface_the_internal_gate_wording_on_401(monkeypatch):
    pf = _cc_tools()
    monkeypatch.setattr(pf, "browser_use_base_url", lambda: "http://127.0.0.1:5101")
    monkeypatch.setattr(pf.requests, "post", lambda *a, **k: _Resp(401, "invalid or missing internal token"))
    monkeypatch.setattr(pf.requests, "get", lambda *a, **k: _Resp(401, "invalid or missing internal token"))
    for out in (pf.start_portal_fetch("Meridian", "http://localhost:3000", "download invoice"),
                pf.fetch_portal("Meridian", "http://localhost:3000", "download invoice"),
                pf.get_portal_result("run-1")):
        assert "NOT a portal login failure" in out["error"], out
        assert "expired" not in out["error"].split("NOT an expired")[0]


def test_portal_workflow_run_uses_the_same_wording(monkeypatch):
    _cc_tools()
    from command_center.tools import portal_workflow_run as pwr
    monkeypatch.setattr(pwr, "browser_use_base_url", lambda: "http://127.0.0.1:5101")
    monkeypatch.setattr(pwr.portal_workflows, "get_workflow",
                        lambda uid, name: {"name": name, "start_url": "http://localhost:3000",
                                           "goal": "download", "steps": []})
    monkeypatch.setattr(pwr, "_credential_key_names", lambda *a, **k: {})
    monkeypatch.setattr(pwr.requests, "post", lambda *a, **k: _Resp(403, "forbidden"))
    res = pwr.run_workflow_by_name("Meridian", session_id="s", user_context={"user_id": 13})
    assert res["status"] == "error"
    assert "NOT a portal login failure" in res["error"]


# ---------------------------------------------------------------------------
# The Agent tool reports the FINAL name (needs claude_agent_sdk; self-skips)
# ---------------------------------------------------------------------------
def test_agent_tool_reports_final_name_on_rename():
    sys.path.insert(0, os.path.join(_ROOT, "agent_service"))
    try:
        import platform_tools as PT
    except ImportError as e:  # no SDK in this env
        pytest.skip(f"needs the aihub-agent env (claude_agent_sdk): {e}")
    if not hasattr(PT.store_platform_secret, "handler"):
        pytest.skip("stub SDK without .handler")

    async def fake_post(path, body):
        assert body["name"] == "API_KEY"
        return ({"success": True, "name": "CUSTOM_API_KEY", "is_update": False, "renamed": True,
                 "requested_name": "API_KEY",
                 "reason": "'API_KEY' is reserved for the platform's own credentials"}, 200)

    async def fake_get(path):
        return {"secrets": [{"name": "CUSTOM_API_KEY"}]}

    token = PT.CURRENT_USER.set({"user_id": 13, "role": 2, "username": "dev"})
    try:
        with mock.patch.object(PT, "_post", fake_post), mock.patch.object(PT, "_get", fake_get):
            res = asyncio.run(PT.store_platform_secret.handler({"name": "API_KEY", "value": "v"}))
    finally:
        PT.CURRENT_USER.reset(token)
    text = res["content"][0]["text"]
    assert "CUSTOM_API_KEY" in text and "was NOT written" in text
    assert "v'" not in text  # value never echoed
