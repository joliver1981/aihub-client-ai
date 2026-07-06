"""Bundle-integrity tests for the Solutions Author export → import pipeline.

Regression suite for the "SharePoint integration validated fine but never
made it into the zip / never installed" defect:

  1. The bundler packs tenant-configured integration *instances* (DB-backed,
     served by /api/integrations) — not just builtin template files — with
     secrets replaced by ${ITG_*} placeholders.
  2. The written manifest is reconciled to what the zip actually contains:
     no phantom entries, no sanitised/unsanitised duplicates.
  3. Builds that skip a selected asset report it (bundler.last_report) and
     the /api/solutions/build route refuses with 422 unless allow_partial.
  4. The installer recreates instance-format integrations through the real
     integrations API, and flags manifest entries with no backing file as
     failed assets instead of silently ignoring them.
"""
from __future__ import annotations

import io
import json
import sys
import types
import zipfile
from pathlib import Path
from typing import Any, Dict, List

import pytest
from flask import Flask, jsonify, request

from solution_bundler import SolutionBundler
from solution_installer import SolutionInstaller, InstallOptions, analyze_bundle
from solution_manifest import (
    SolutionManifest,
    find_missing_bundle_assets,
    safe_filename,
)


# ---------------------------------------------------------------------------
# Fixtures — a tiny Flask app standing in for the platform routes the
# bundler/installer call via test_client.
# ---------------------------------------------------------------------------

SHAREPOINT_INSTANCE = {
    "integration_id": 7,
    "integration_name": "AI Hub SharePoint Test",
    "description": "Team site connection",
    "template_key": "sharepoint_online_app",
    "platform_name": "SharePoint Online (Service Account)",
    "auth_type": "oauth2",
    "instance_config": json.dumps({"tenant_id": "11111111-2222-3333-4444-555555555555"}),
}


def _make_platform_app(created: List[Dict[str, Any]], existing_names=None) -> Flask:
    """Minimal stand-in for the main app: /api/integrations list + create."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    listed = [dict(SHAREPOINT_INSTANCE)]
    for n in (existing_names or []):
        listed.append({"integration_id": 900 + len(listed), "integration_name": n,
                       "template_key": "sharepoint_online_app", "auth_type": "oauth2",
                       "instance_config": "{}"})

    @app.route("/api/integrations", methods=["GET"])
    def list_integrations():
        return jsonify({"status": "success", "integrations": listed})

    @app.route("/api/integrations", methods=["POST"])
    def create_integration():
        body = request.get_json(silent=True) or {}
        created.append(body)
        return jsonify({"status": "success", "integration_id": 42}), 200

    @app.route("/api/integrations/<int:iid>", methods=["PUT"])
    def update_integration(iid):
        body = dict(request.get_json(silent=True) or {})
        body["_updated_id"] = iid
        created.append(body)
        return jsonify({"status": "success"}), 200

    @app.route("/api/solutions/workflows/import", methods=["POST"])
    def import_workflow():
        return jsonify({"status": "ok"}), 200

    return app


@pytest.fixture
def created_integrations() -> List[Dict[str, Any]]:
    return []


@pytest.fixture
def platform_app(created_integrations) -> Flask:
    return _make_platform_app(created_integrations)


@pytest.fixture
def template_dir(tmp_path, monkeypatch) -> Path:
    """Deterministic template resolution: make the in-process
    integration_manager lookup fail so the bundler falls back to reading the
    template JSON from a temp integrations/builtin dir we control."""
    d = tmp_path / "builtin_templates"
    d.mkdir()
    (d / "sharepoint_online_app.json").write_text(json.dumps({
        "template_key": "sharepoint_online_app",
        "platform_name": "SharePoint Online (Service Account)",
        "auth_type": "oauth2",
        "auth_config": {"grant_type": "client_credentials"},
    }), encoding="utf-8")

    stub = types.ModuleType("integration_manager")
    def _raise():
        raise RuntimeError("no manager in tests")
    stub.get_integration_manager = _raise
    monkeypatch.setitem(sys.modules, "integration_manager", stub)
    return d


def _manifest(**overrides) -> SolutionManifest:
    base = {"id": "hz_workflow", "name": "hz-cust-onboard", "version": "1.0.0", "assets": {}}
    base.update(overrides)
    return SolutionManifest.from_dict(base)


def _zip_names(zip_bytes: bytes) -> List[str]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        return [i.filename for i in zf.infolist() if not i.is_dir()]


def _zip_read(zip_bytes: bytes, name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        return zf.read(name)


# ---------------------------------------------------------------------------
# Bundler: integration instance export
# ---------------------------------------------------------------------------

def test_bundler_packs_configured_integration_instance(platform_app, template_dir, tmp_path, monkeypatch):
    bundler = SolutionBundler(platform_app)
    monkeypatch.setattr(bundler, "_integrations_root", lambda: template_dir)
    monkeypatch.setattr(bundler, "_workflows_root", lambda: tmp_path / "nope")

    manifest = _manifest()
    zip_bytes = bundler.build(
        manifest, integration_names=["AI Hub SharePoint Test"],
    )

    names = _zip_names(zip_bytes)
    entry = f"integrations/{safe_filename('AI Hub SharePoint Test')}.json"
    assert entry in names, f"integration instance file missing from zip: {names}"

    doc = json.loads(_zip_read(zip_bytes, entry))
    assert doc["kind"] == "integration_instance"
    assert doc["template_key"] == "sharepoint_online_app"
    assert doc["integration_name"] == "AI Hub SharePoint Test"
    assert doc["instance_config"]["tenant_id"] == "11111111-2222-3333-4444-555555555555"
    # Secrets never exported — only placeholders.
    for v in doc["credentials"].values():
        assert v.startswith("${ITG_"), f"credential value leaked: {v}"

    written = SolutionManifest.from_dict(json.loads(_zip_read(zip_bytes, "solution.json")))
    assert written.assets.integrations == [entry.split("/", 1)[1]]
    # Credential placeholders were auto-declared for the install wizard.
    declared = {c.placeholder for c in written.credentials}
    assert any(p.startswith("ITG_AI_HUB_SHAREPOINT_TEST_") for p in declared)
    # Instance credentials are optional: missing values must not block install.
    assert all(not c.required for c in written.credentials)

    assert bundler.last_report["skipped"] == []
    assert bundler.last_report["packed"]["integrations"] == [entry.split("/", 1)[1]]


def test_bundler_reports_unknown_integration_as_skipped(platform_app, tmp_path, monkeypatch):
    bundler = SolutionBundler(platform_app)
    monkeypatch.setattr(bundler, "_integrations_root", lambda: tmp_path / "nope")

    manifest = _manifest()
    zip_bytes = bundler.build(manifest, integration_names=["No Such Integration"])

    assert not any(n.startswith("integrations/") for n in _zip_names(zip_bytes))
    skipped = bundler.last_report["skipped"]
    assert len(skipped) == 1
    assert skipped[0]["kind"] == "integrations"
    assert skipped[0]["name"] == "No Such Integration"

    # Manifest must NOT claim the skipped asset.
    written = SolutionManifest.from_dict(json.loads(_zip_read(zip_bytes, "solution.json")))
    assert written.assets.integrations == []


def test_bundler_reconciles_wizard_prepopulated_manifest(platform_app, template_dir, tmp_path, monkeypatch):
    """The wizard pre-fills manifest.assets with optimistic display names
    (unsanitised, may not exist). The written manifest must contain only —
    and exactly — what was packed."""
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir()
    raw_name = "Customer Onboarding - Horizon Replica _w SP v2_ (Imported)"
    (wf_dir / f"{raw_name}.json").write_text('{"nodes": []}', encoding="utf-8")

    bundler = SolutionBundler(platform_app)
    monkeypatch.setattr(bundler, "_workflows_root", lambda: wf_dir)
    monkeypatch.setattr(bundler, "_integrations_root", lambda: template_dir)

    manifest = _manifest(assets={
        # what the wizard pre-populated (the user's actual broken manifest)
        "workflows": [f"{raw_name}.json"],
        "integrations": ["AI Hub SharePoint Test.json"],
    })
    zip_bytes = bundler.build(
        manifest,
        workflow_names=[f"{raw_name}.json"],
        integration_names=["AI Hub SharePoint Test"],
    )

    written = SolutionManifest.from_dict(json.loads(_zip_read(zip_bytes, "solution.json")))
    sanitised = f"{safe_filename(raw_name)}.json"
    assert written.assets.workflows == [sanitised], "no duplicate/unsanitised workflow entries"
    assert written.assets.integrations == [f"{safe_filename('AI Hub SharePoint Test')}.json"]

    # Nothing in the manifest is missing from the zip.
    assert find_missing_bundle_assets(written, _zip_names(zip_bytes)) == []


# ---------------------------------------------------------------------------
# Manifest ↔ bundle cross-check
# ---------------------------------------------------------------------------

def test_find_missing_bundle_assets_flags_users_broken_bundle():
    """Replays the exact hz_workflow_v1.0.0 bundle that shipped without the
    SharePoint integration."""
    manifest = _manifest(assets={
        "workflows": [
            "Customer Onboarding - Horizon Replica _w SP v2_ (Imported).json",
            "Customer Onboarding - Horizon Replica _w SP v2_ _Imported_.json",
        ],
        "integrations": ["AI Hub SharePoint Test.json"],
    })
    names = [
        "solution.json",
        "branding.json",
        "workflows/Customer Onboarding - Horizon Replica _w SP v2_ _Imported_.json",
    ]
    missing = find_missing_bundle_assets(manifest, names)
    # Both workflow spellings resolve to the same real file → not missing.
    # The integration has no file at all → missing.
    assert missing == [{"kind": "integrations", "name": "AI Hub SharePoint Test.json"}]


def test_find_missing_bundle_assets_dir_kinds():
    manifest = _manifest(assets={"agents": ["My Helper Agent", "Ghost Agent"]})
    names = ["agents/My Helper Agent/config.json", "solution.json"]
    missing = find_missing_bundle_assets(manifest, names)
    assert missing == [{"kind": "agents", "name": "Ghost Agent"}]


def test_analyze_bundle_reports_missing_assets(tmp_path):
    p = tmp_path / "broken.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("solution.json", json.dumps({
            "id": "demo", "name": "Demo",
            "assets": {"integrations": ["AI Hub SharePoint Test.json"], "workflows": []},
        }))
    result = analyze_bundle(p)
    assert result["valid"] is True
    assert result["missing_assets"] == [
        {"kind": "integrations", "name": "AI Hub SharePoint Test.json"}
    ]


# ---------------------------------------------------------------------------
# Installer: integration instance + inventory failures
# ---------------------------------------------------------------------------

def _instance_bundle(tmp_path, *, credentials=None, manifest_assets=None) -> Path:
    doc = {
        "kind": "integration_instance",
        "format_version": 1,
        "template_key": "sharepoint_online_app",
        "integration_name": "AI Hub SharePoint Test",
        "description": "Team site connection",
        "auth_type": "oauth2",
        "instance_config": {"tenant_id": "t-1"},
        "credentials": credentials if credentials is not None else {
            "client_id": "${ITG_AI_HUB_SHAREPOINT_TEST_CLIENT_ID}",
            "client_secret": "${ITG_AI_HUB_SHAREPOINT_TEST_CLIENT_SECRET}",
        },
    }
    p = tmp_path / "bundle.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("solution.json", json.dumps({
            "id": "demo", "name": "Demo",
            "assets": manifest_assets or {"integrations": ["AI Hub SharePoint Test.json"]},
        }))
        zf.writestr("integrations/AI Hub SharePoint Test.json", json.dumps(doc))
    return p


def test_installer_creates_integration_instance(platform_app, created_integrations, tmp_path):
    bundle = _instance_bundle(tmp_path)
    installer = SolutionInstaller(platform_app)
    result = installer.install(bundle, options=InstallOptions(credentials={
        "ITG_AI_HUB_SHAREPOINT_TEST_CLIENT_ID": "cid-123",
        # secret intentionally left blank by the installing user
    }))

    assert len(created_integrations) == 1
    payload = created_integrations[0]
    assert payload["template_key"] == "sharepoint_online_app"
    assert payload["instance_config"] == {"tenant_id": "t-1"}
    assert payload["credentials"] == {"client_id": "cid-123"}, \
        "unresolved placeholder must be dropped, resolved one passed through"

    rows = [a for a in result.assets if a.kind == "integration"]
    assert len(rows) == 1
    assert rows[0].status == "installed"
    assert rows[0].resource_id == 42
    assert "client_secret" in rows[0].detail  # tells the user what to finish

    assert result.success is True


def test_installer_conflict_rename(created_integrations, tmp_path):
    app = _make_platform_app(created_integrations,
                             existing_names=["AI Hub SharePoint Test"])
    bundle = _instance_bundle(tmp_path)
    installer = SolutionInstaller(app)
    result = installer.install(bundle, options=InstallOptions(conflict_mode="rename"))

    assert created_integrations[0]["integration_name"] == "AI Hub SharePoint Test_2"
    rows = [a for a in result.assets if a.kind == "integration"]
    assert rows[0].status == "installed"


def test_installer_conflict_skip(created_integrations, tmp_path):
    app = _make_platform_app(created_integrations,
                             existing_names=["AI Hub SharePoint Test"])
    bundle = _instance_bundle(tmp_path)
    installer = SolutionInstaller(app)
    result = installer.install(bundle, options=InstallOptions(conflict_mode="skip"))

    assert created_integrations == []
    rows = [a for a in result.assets if a.kind == "integration"]
    assert rows[0].status == "skipped"


def test_installer_flags_manifest_entry_missing_from_bundle(platform_app, tmp_path):
    """The user's exact failure: manifest declares the integration, zip has
    no integrations/ folder. Install must NOT report clean success."""
    p = tmp_path / "broken.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("solution.json", json.dumps({
            "id": "hz_workflow", "name": "hz-cust-onboard",
            "assets": {
                "workflows": ["wf.json"],
                "integrations": ["AI Hub SharePoint Test.json"],
            },
        }))
        zf.writestr("workflows/wf.json", '{"nodes": []}')

    installer = SolutionInstaller(platform_app)
    result = installer.install(p, options=InstallOptions())

    failed = [a for a in result.assets if a.status == "failed"]
    assert any(
        a.kind == "integration" and "AI Hub SharePoint Test" in a.name for a in failed
    ), f"missing integration not flagged: {[a.to_dict() for a in result.assets]}"
    assert result.success is False, "partial bundle must not report success"


# ---------------------------------------------------------------------------
# Build route: 422 on skipped assets unless allow_partial
# ---------------------------------------------------------------------------

def _make_builder_app(monkeypatch, report: Dict[str, Any]):
    """Real solution_builder_routes blueprint with a stub logged-in developer
    (mirrors tests_v2/unit/test_solution_routes.py) and the build step
    replaced by a canned (zip_bytes, report) pair."""
    import solution_builder_routes as sbr
    from flask_login import LoginManager, UserMixin

    class _StubUser(UserMixin):
        id = 1
        role = 2
        tenant_id = 1
        username = "tester"

    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test"
    app.config["TESTING"] = True

    lm = LoginManager()
    lm.init_app(app)
    user = _StubUser()

    @lm.user_loader
    def _load_user(uid):  # pragma: no cover
        return user

    @app.route("/login")
    def login():  # pragma: no cover
        return "login"

    @app.route("/")
    def home():  # pragma: no cover
        return "home"

    @app.before_request
    def _auto_login():
        from flask_login import login_user as _li
        _li(user)

    monkeypatch.setattr(sbr, "_require_flag", lambda: None)
    monkeypatch.setattr(
        sbr, "_build_zip_from_request_body", lambda: (b"PK\x05\x06" + b"\x00" * 18, report)
    )
    app.register_blueprint(sbr.solution_builder_bp)
    return app


def test_build_route_422_when_assets_skipped(monkeypatch):
    report = {
        "packed": {},
        "skipped": [{"kind": "integrations", "name": "AI Hub SharePoint Test",
                     "reason": "no configured integration or builtin template with this name"}],
        "validation_warnings": [],
    }
    app = _make_builder_app(monkeypatch, report)
    client = app.test_client()

    resp = client.post("/api/solutions/build", json={"manifest": {"id": "x"}})
    assert resp.status_code == 422
    body = resp.get_json()
    assert body["skipped"][0]["name"] == "AI Hub SharePoint Test"
    assert "could not be packaged" in body["error"]


def test_build_route_allows_partial_optin(monkeypatch):
    report = {
        "packed": {},
        "skipped": [{"kind": "integrations", "name": "X", "reason": "r"}],
        "validation_warnings": [],
    }
    app = _make_builder_app(monkeypatch, report)
    client = app.test_client()

    resp = client.post(
        "/api/solutions/build",
        json={"manifest": {"id": "x"}, "allow_partial": True},
    )
    assert resp.status_code == 200
    assert resp.mimetype == "application/zip"


def test_build_route_clean_build_streams_zip(monkeypatch):
    report = {"packed": {}, "skipped": [], "validation_warnings": []}
    app = _make_builder_app(monkeypatch, report)
    client = app.test_client()

    resp = client.post("/api/solutions/build", json={"manifest": {"id": "x", "version": "1.0.0"}})
    assert resp.status_code == 200
    assert resp.mimetype == "application/zip"
    assert "x_v1.0.0.zip" in (resp.headers.get("Content-Disposition") or "")
