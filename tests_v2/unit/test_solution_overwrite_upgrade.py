"""Solutions installer: conflict_mode "overwrite" as an UPGRADE path.

Regression suite for the 2026-10-09 Acme Commercial Invoice Lines 1.0.0 → 1.1.0
reinstall (Overwrite) defects:

  1. The automation asset failed with "an automation named … already exists"
     instead of saving the bundle's code as a new version of the existing one.
  2. The workflow's Automation node came out with an EMPTY automationId.
  3. The workflow's variables were reset to the bundle defaults, wiping the
     client's own values (folder paths).

rename / skip behaviour must stay exactly as it was.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from flask import Flask, jsonify, request

from solution_installer import SolutionInstaller, InstallOptions


# ---------------------------------------------------------------------------
# In-memory AutomationManager stand-in
# ---------------------------------------------------------------------------

class FakeAutomationManager:
    """Mirrors the real manager's contract: versions are append-only, the pin
    only moves on promote(), create refuses a duplicate name."""

    store: Dict[str, Dict[str, Any]] = {}

    def __init__(self, *a, **k):
        pass

    # registry
    def list_automations(self):
        return [dict(a["row"]) for a in self.store.values() if a["row"]["status"] != "deleted"]

    def resolve_owner_user_id(self, candidate):
        return candidate or 1

    def create_automation(self, name, description, owner_user_id, **k):
        if any(a["name"].lower() == name.lower() for a in self.list_automations()):
            return False, None, f"an automation named '{name}' already exists"
        aid = f"auto-{len(self.store) + 1}"
        self.store[aid] = {"row": {"automation_id": aid, "name": name, "description": description,
                                   "current_version": 0, "pinned_version": 0, "status": "active"},
                           "versions": {}, "sources": {}}
        return True, dict(self.store[aid]["row"]), None

    def delete_automation(self, aid):
        self.store[aid]["row"]["status"] = "deleted"
        return True, None

    def _db_update_automation(self, aid, fields):
        self.store[aid]["row"].update(fields)

    # versions
    def save_version(self, aid, code, manifest=None):
        a = self.store[aid]
        v = a["row"]["current_version"] + 1
        a["versions"][v] = {"code": code, "manifest": json.loads(json.dumps(manifest)), "samples": {}}
        a["row"]["current_version"] = v
        return True, v, []

    def add_sample(self, aid, version, filename, content):
        self.store[aid]["versions"][version]["samples"][filename] = content
        return True, None

    def get_code(self, aid, version=None):
        v = self.store[aid]["versions"].get(version or self.store[aid]["row"]["current_version"])
        return v and v["code"]

    def get_manifest(self, aid, version=None):
        v = self.store[aid]["versions"].get(version or self.store[aid]["row"]["current_version"])
        return v and json.loads(json.dumps(v["manifest"]))

    def promote(self, aid, version=None):
        target = version or self.store[aid]["row"]["current_version"]
        self.store[aid]["row"]["pinned_version"] = target
        return True, target, None

    def set_version_source(self, aid, version, source):
        self.store[aid]["sources"][version] = dict(source)
        return True

    def get_version_source(self, aid, version):
        return self.store[aid]["sources"].get(version)


@pytest.fixture
def fake_mgr(monkeypatch):
    FakeAutomationManager.store = {}
    stub = types.ModuleType("automations.manager")
    stub.AutomationManager = FakeAutomationManager
    monkeypatch.setitem(sys.modules, "automations.manager", stub)
    return FakeAutomationManager()


# ---------------------------------------------------------------------------
# Bundle + platform fixtures
# ---------------------------------------------------------------------------

AUTO = "ci-prepare-batch"
WF = "Acme Commercial Invoice Lines"


def _workflow_doc(input_default="C:\\AIHub\\ACME_CI\\input"):
    return {
        "nodes": [{"id": "n1", "type": "Automation",
                   "config": {"automationId": "", "automationName": AUTO,
                              "inputs": {"input_folder": "${inputFolder}"}}}],
        "connections": [],
        "variables": {
            "inputFolder": {"name": "inputFolder", "type": "string", "defaultValue": input_default},
            "outputWorkbook": {"name": "outputWorkbook", "type": "string",
                               "defaultValue": "C:\\AIHub\\ACME_CI\\output\\CI_Lines.xlsx"},
        },
    }


def _make_bundle(root: Path, *, version="1.1.0", code="print('v2')\n", sid="acme-ci") -> Path:
    b = root / f"bundle_{version}"
    (b / "automations" / AUTO).mkdir(parents=True)
    (b / "workflows").mkdir()
    (b / "solution.json").write_text(json.dumps({
        "id": sid, "name": "Acme Commercial Invoice Lines", "version": version,
        "assets": {"automations": [AUTO], "workflows": [f"{WF}.json"]},
    }), encoding="utf-8")
    (b / "automations" / AUTO / "automation.json").write_text(json.dumps({
        "name": AUTO, "description": f"prepare batch {version}", "exported_version": 4,
        "manifest": {"name": AUTO, "entrypoint": "main.py", "inputs": [], "packages": ["xlrd"]},
    }), encoding="utf-8")
    (b / "automations" / AUTO / "main.py").write_text(code, encoding="utf-8")
    (b / "workflows" / f"{WF}.json").write_text(json.dumps(_workflow_doc()), encoding="utf-8")
    return b


def _platform(imports: List[Dict[str, Any]], response: Optional[Dict[str, Any]] = None) -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = True

    @app.route("/api/solutions/workflows/import", methods=["POST"])
    def import_workflow():
        imports.append(request.get_json(silent=True) or {})
        return jsonify(response or {"status": "installed"}), 201

    return app


def _assets(result, kind):
    return [a for a in result.assets if a.kind == kind]


def _install(app, bundle, mode, suffix="_up"):
    return SolutionInstaller(app).install(
        bundle, options=InstallOptions(name_suffix=suffix, conflict_mode=mode),
        installer_user_id=7)


# ---------------------------------------------------------------------------
# Automation: overwrite upgrades in place
# ---------------------------------------------------------------------------

def test_fresh_install_stamps_provenance(fake_mgr, tmp_path):
    imports: List[Dict[str, Any]] = []
    r = _install(_platform(imports), _make_bundle(tmp_path, version="1.0.0"), "rename")
    (a,) = _assets(r, "automation")
    assert a.status == "installed"
    (aid,) = list(fake_mgr.store)
    src = fake_mgr.get_version_source(aid, 1)
    assert src["solution_id"] == "acme-ci" and src["solution_version"] == "1.0.0"
    assert fake_mgr.store[aid]["row"]["pinned_version"] == 0  # still unpromoted


def test_overwrite_saves_new_version_of_existing_and_promotes_unchanged_install(fake_mgr, tmp_path):
    imports: List[Dict[str, Any]] = []
    app = _platform(imports)
    _install(app, _make_bundle(tmp_path, version="1.0.0", code="print('v1')\n"), "rename")
    (aid,) = list(fake_mgr.store)
    fake_mgr.promote(aid, 1)  # the client dry-ran and promoted 1.0.0's code

    r = _install(app, _make_bundle(tmp_path, version="1.1.0", code="print('v2')\n"), "overwrite")
    (a,) = _assets(r, "automation")
    assert a.status == "updated", a.detail
    assert r.success
    assert list(fake_mgr.store) == [aid], "must update the existing automation, not create one"
    row = fake_mgr.store[aid]["row"]
    assert row["current_version"] == 2 and row["pinned_version"] == 2
    assert fake_mgr.get_code(aid, 2) == "print('v2')\n"
    assert fake_mgr.get_manifest(aid, 2)["name"] == f"{AUTO}_up"
    assert row["description"] == "prepare batch 1.1.0"
    assert fake_mgr.get_version_source(aid, 2)["solution_version"] == "1.1.0"
    assert "PROMOTED" in a.detail


def test_overwrite_keeps_unpromoted_when_pinned_version_was_edited_locally(fake_mgr, tmp_path):
    imports: List[Dict[str, Any]] = []
    app = _platform(imports)
    _install(app, _make_bundle(tmp_path, version="1.0.0", code="print('v1')\n"), "rename")
    (aid,) = list(fake_mgr.store)
    fake_mgr.save_version(aid, "print('local fix')\n", {"name": f"{AUTO}_up"})  # v2, no provenance
    fake_mgr.promote(aid, 2)

    r = _install(app, _make_bundle(tmp_path, version="1.1.0"), "overwrite")
    (a,) = _assets(r, "automation")
    assert a.status == "updated"
    row = fake_mgr.store[aid]["row"]
    assert row["current_version"] == 3 and row["pinned_version"] == 2
    assert "NOT promoted" in a.detail and "v2" in a.detail and "v3" in a.detail


def test_overwrite_keeps_unpromoted_for_legacy_install_without_provenance(fake_mgr, tmp_path):
    """1.0.0 installs made before provenance existed: never auto-promoted."""
    ok, auto, _ = fake_mgr.create_automation(f"{AUTO}_up", "old", 1)
    fake_mgr.save_version(auto["automation_id"], "print('v1')\n", {"name": f"{AUTO}_up"})
    fake_mgr.promote(auto["automation_id"], 1)

    r = _install(_platform([]), _make_bundle(tmp_path), "overwrite")
    (a,) = _assets(r, "automation")
    assert a.status == "updated"
    assert fake_mgr.store[auto["automation_id"]]["row"]["pinned_version"] == 1
    assert "NOT promoted" in a.detail and "keep running the promoted v1" in a.detail


def test_overwrite_with_identical_code_is_a_noop(fake_mgr, tmp_path):
    app = _platform([])
    _install(app, _make_bundle(tmp_path, version="1.0.0", code="same\n"), "rename")
    (aid,) = list(fake_mgr.store)
    r = _install(app, _make_bundle(tmp_path, version="1.0.1", code="same\n"), "overwrite")
    (a,) = _assets(r, "automation")
    assert a.status == "skipped" and "already up to date" in a.detail
    assert fake_mgr.store[aid]["row"]["current_version"] == 1


def test_rename_mode_still_fails_on_existing_automation(fake_mgr, tmp_path):
    """Unchanged behaviour: only overwrite upgrades."""
    app = _platform([])
    _install(app, _make_bundle(tmp_path, version="1.0.0"), "rename")
    r = _install(app, _make_bundle(tmp_path, version="1.1.0"), "rename")
    (a,) = _assets(r, "automation")
    assert a.status == "failed" and "already exists" in a.detail
    (aid,) = list(fake_mgr.store)
    assert fake_mgr.store[aid]["row"]["current_version"] == 1


# ---------------------------------------------------------------------------
# Workflow: Automation node bound to the existing automation on overwrite
# ---------------------------------------------------------------------------

def test_overwrite_binds_workflow_node_to_existing_automation(fake_mgr, tmp_path):
    imports: List[Dict[str, Any]] = []
    app = _platform(imports)
    _install(app, _make_bundle(tmp_path, version="1.0.0", code="print('v1')\n"), "rename")
    (aid,) = list(fake_mgr.store)
    imports.clear()

    _install(app, _make_bundle(tmp_path, version="1.1.0"), "overwrite")
    (payload,) = imports
    assert payload["conflict_mode"] == "overwrite"
    cfg = payload["workflow"]["nodes"][0]["config"]
    assert cfg["automationId"] == aid
    assert cfg["automationName"] == f"{AUTO}_up"


def test_overwrite_workflow_reported_as_updated_with_kept_variables(fake_mgr, tmp_path):
    app = _platform([], response={"status": "installed", "overwritten": True,
                                  "preserved_variables": ["inputFolder"], "workflow_id": 9})
    r = _install(app, _make_bundle(tmp_path), "overwrite")
    (w,) = _assets(r, "workflow")
    assert w.status == "updated" and "inputFolder" in w.detail and w.resource_id == 9


# ---------------------------------------------------------------------------
# Import route: variable values preserved on overwrite only
# ---------------------------------------------------------------------------

def test_merge_preserved_variables():
    from workflow_export_routes import merge_preserved_variables
    old = _workflow_doc(input_default="D:\\CI_INPUT")
    old["variables"]["gone"] = {"type": "string", "defaultValue": "x"}
    old["variables"]["outputWorkbook"]["type"] = "number"  # type changed → not kept
    new = _workflow_doc()
    new["variables"]["added"] = {"type": "string", "defaultValue": "new"}
    kept = merge_preserved_variables(new, old)
    assert kept == ["inputFolder"]
    assert new["variables"]["inputFolder"]["defaultValue"] == "D:\\CI_INPUT"
    assert new["variables"]["outputWorkbook"]["defaultValue"].endswith("CI_Lines.xlsx")
    assert new["variables"]["added"]["defaultValue"] == "new"
    assert "gone" not in new["variables"]
    # {"workflow": {...}} wrapper shape works too
    wrapped = {"workflow": _workflow_doc()}
    assert merge_preserved_variables(wrapped, {"workflow": old}) == ["inputFolder"]


@pytest.fixture
def route_client(tmp_path, monkeypatch):
    import workflow_export_routes as wer
    saved: List[Any] = []
    app_stub = types.ModuleType("app")
    app_stub.save_workflow_to_database = lambda name, wf: saved.append((name, wf)) or 55
    monkeypatch.setitem(sys.modules, "app", app_stub)
    monkeypatch.setattr(wer, "_require_flag", lambda: None)
    monkeypatch.setattr(wer, "_workflows_root", lambda: tmp_path / "workflows")
    monkeypatch.setattr(wer, "_load_existing_workflow_db", lambda name: None)  # file fallback
    app = Flask(__name__)
    app.config.update(TESTING=True, LOGIN_DISABLED=True)
    from flask_login import LoginManager
    LoginManager(app)
    app.register_blueprint(wer.workflow_export_bp)
    return app.test_client(), tmp_path / "workflows", saved


def _seed_existing(wf_dir: Path):
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / f"{WF}_up.json").write_text(
        json.dumps(_workflow_doc(input_default="D:\\CI_INPUT")), encoding="utf-8")


def test_import_route_overwrite_keeps_client_variable_values(route_client):
    client, wf_dir, saved = route_client
    _seed_existing(wf_dir)
    resp = client.post("/api/solutions/workflows/import", json={
        "name": f"{WF}_up", "workflow": _workflow_doc(), "conflict_mode": "overwrite"})
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["overwritten"] is True and body["preserved_variables"] == ["inputFolder"]
    on_disk = json.loads((wf_dir / f"{WF}_up.json").read_text(encoding="utf-8"))
    assert on_disk["variables"]["inputFolder"]["defaultValue"] == "D:\\CI_INPUT"
    assert saved[-1][1]["variables"]["inputFolder"]["defaultValue"] == "D:\\CI_INPUT"


def test_import_route_rename_unchanged(route_client):
    client, wf_dir, saved = route_client
    _seed_existing(wf_dir)
    resp = client.post("/api/solutions/workflows/import", json={
        "name": f"{WF}_up", "workflow": _workflow_doc(), "conflict_mode": "rename"})
    body = resp.get_json()
    assert body["name"] == f"{WF}_up_2" and body["overwritten"] is False
    assert saved[-1][1]["variables"]["inputFolder"]["defaultValue"] == "C:\\AIHub\\ACME_CI\\input"


def test_import_route_overwrite_db_failure_restores_previous_file(route_client):
    client, wf_dir, _ = route_client
    _seed_existing(wf_dir)
    before = (wf_dir / f"{WF}_up.json").read_bytes()

    def boom(name, wf):
        raise ValueError("db down")
    sys.modules["app"].save_workflow_to_database = boom
    resp = client.post("/api/solutions/workflows/import", json={
        "name": f"{WF}_up", "workflow": _workflow_doc(), "conflict_mode": "overwrite"})
    assert resp.status_code == 500
    assert (wf_dir / f"{WF}_up.json").read_bytes() == before


# ---------------------------------------------------------------------------
# Real AutomationManager provenance sidecar (filesystem only)
# ---------------------------------------------------------------------------

def test_manager_version_source_roundtrip(tmp_path):
    from automations.manager import AutomationManager, VERSION_SOURCE_FILENAME
    mgr = AutomationManager.__new__(AutomationManager)
    mgr.base_path = str(tmp_path)
    (Path(mgr.version_dir("a1", 1))).mkdir(parents=True)
    assert mgr.get_version_source("a1", 1) is None
    assert mgr.set_version_source("a1", 1, {"solution_id": "acme-ci"})
    assert mgr.get_version_source("a1", 1) == {"solution_id": "acme-ci"}
    assert (Path(mgr.version_dir("a1", 1)) / VERSION_SOURCE_FILENAME).is_file()
    assert mgr.set_version_source("a1", 9, {}) is False  # no such version
    assert mgr.get_version_source("a1", 0) is None
