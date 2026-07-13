"""
Unit tests for the on-the-fly Automations P0 core (automations/).

DB access is stubbed via the _db_* seams; subprocess execution is REAL
(sys.executable) so the runner's contract — env-var credential injection,
tri-state outcomes, skip-if-running, dry-run sample seeding, frozen-version
resolution — is exercised end to end on the filesystem.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Dict, List, Optional

import pytest

import automations.manager as manager_mod
from automations.manager import (
    AutomationManager,
    scan_for_secrets,
    validate_manifest,
)
from automations.runner import (
    AutomationRunner,
    resolve_inputs,
    verify_outputs,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# pure functions
# ---------------------------------------------------------------------------

class TestSecretScan:
    def test_detects_quoted_password_literal(self):
        assert scan_for_secrets('password = "hunter22x"')

    def test_detects_odbc_pwd_literal(self):
        assert scan_for_secrets('conn = "Server=x;PWD=s3cretpw;"')

    def test_detects_api_key_literal(self):
        assert scan_for_secrets('api_key = "sk_abcdefghijklmnop1234"')

    def test_allows_env_var_reads(self):
        code = 'conn_str = os.environ["AIHUB_CONN_ERPDB"]\npw = os.getenv("AIHUB_SECRET_ACME_SFTP")'
        assert scan_for_secrets(code) == []

    def test_allows_placeholder_and_plain_code(self):
        assert scan_for_secrets('template = "PWD={pwd}"\nx = 1\nprint("done")') == []


class TestValidateManifest:
    def _base(self) -> Dict:
        return {
            "name": "payroll",
            "entrypoint": "main.py",
            "timeout_seconds": 60,
            "inputs": [{"name": "period", "type": "string", "default": "current"}],
            "connections": ["ERPDB"],
            "secrets": ["ACME_SFTP"],
            "packages": ["pdfplumber"],
            "outputs": [{"kind": "file", "path": "out/x.csv", "verify": {"exists": True, "min_rows": 1}}],
        }

    def test_valid_manifest_passes(self):
        ok, errors = validate_manifest(self._base())
        assert ok, errors

    def test_missing_name_fails(self):
        m = self._base(); del m["name"]
        ok, errors = validate_manifest(m)
        assert not ok and any("name" in e for e in errors)

    def test_entrypoint_path_traversal_rejected(self):
        m = self._base(); m["entrypoint"] = "../evil.py"
        ok, errors = validate_manifest(m)
        assert not ok

    def test_bad_timeout_rejected(self):
        m = self._base(); m["timeout_seconds"] = 0
        assert not validate_manifest(m)[0]
        m["timeout_seconds"] = "600"
        assert not validate_manifest(m)[0]

    def test_bad_output_kind_rejected(self):
        m = self._base(); m["outputs"] = [{"kind": "carrier_pigeon"}]
        ok, errors = validate_manifest(m)
        assert not ok

    def test_file_output_requires_path(self):
        m = self._base(); m["outputs"] = [{"kind": "file"}]
        assert not validate_manifest(m)[0]

    def test_unknown_top_level_keys_tolerated(self):
        m = self._base(); m["future_field"] = {"anything": 1}
        assert validate_manifest(m)[0]


class TestResolveInputs:
    MANIFEST = {"inputs": [
        {"name": "period", "type": "string", "default": "current"},
        {"name": "region", "type": "string"},
    ]}

    def test_defaults_applied(self):
        resolved, err = resolve_inputs(self.MANIFEST, {"region": "EU"})
        assert err is None and resolved == {"period": "current", "region": "EU"}

    def test_missing_required_errors(self):
        resolved, err = resolve_inputs(self.MANIFEST, {})
        assert resolved is None and "region" in err

    def test_undeclared_extras_rejected(self):
        resolved, err = resolve_inputs(self.MANIFEST, {"region": "EU", "typo": 1})
        assert resolved is None and "typo" in err


class TestVerifyOutputs:
    def test_file_exists_and_min_rows_success(self, tmp_path):
        out = tmp_path / "out"; out.mkdir()
        (out / "r.csv").write_text("h1,h2\na,1\nb,2\n")
        manifest = {"outputs": [{"kind": "file", "path": "out/r.csv", "verify": {"min_rows": 2}}]}
        outcome, report = verify_outputs(manifest, str(tmp_path), {})
        assert outcome == "success"

    def test_missing_file_fails(self, tmp_path):
        manifest = {"outputs": [{"kind": "file", "path": "nope.csv"}]}
        outcome, report = verify_outputs(manifest, str(tmp_path), {})
        assert outcome == "failed"
        assert report[0]["checks"][0] == {"check": "exists", "ok": False}

    def test_min_rows_counts_csv_data_rows(self, tmp_path):
        (tmp_path / "r.csv").write_text("header\nonly_one_row\n")
        manifest = {"outputs": [{"kind": "file", "path": "r.csv", "verify": {"min_rows": 2}}]}
        assert verify_outputs(manifest, str(tmp_path), {})[0] == "failed"

    def test_path_template_substitution(self, tmp_path):
        (tmp_path / "out_2026-07.csv").write_text("h\nx\n")
        manifest = {"outputs": [{"kind": "file", "path": "out_{period}.csv"}]}
        assert verify_outputs(manifest, str(tmp_path), {"period": "2026-07"})[0] == "success"

    def test_remote_output_is_unverified_not_success(self, tmp_path):
        manifest = {"outputs": [{"kind": "sftp_upload", "secret": "X"}]}
        assert verify_outputs(manifest, str(tmp_path), {})[0] == "unverified"

    def test_no_declared_outputs_is_success(self, tmp_path):
        assert verify_outputs({}, str(tmp_path), {})[0] == "success"


# ---------------------------------------------------------------------------
# manager with stubbed DB
# ---------------------------------------------------------------------------

class StubManager(AutomationManager):
    """AutomationManager with the DB replaced by an in-memory dict."""

    def __init__(self, base_path: str):
        # bypass parent __init__ (no DB connection string / APP_ROOT paths)
        self.tenant_id = "testtenant"
        self.connection_string = "stub"
        self.base_path = base_path
        os.makedirs(base_path, exist_ok=True)
        self._rows: Dict[str, Dict] = {}

    def _db_insert_automation(self, row):
        self._rows[row["automation_id"]] = {
            **row, "current_version": 0, "pinned_version": 0, "status": "active",
            "created_at": None, "updated_at": None,
        }

    def _db_get_automation(self, automation_id):
        row = self._rows.get(automation_id)
        return dict(row) if row and row["status"] != "deleted" else None

    def _db_list_automations(self):
        return [dict(r) for r in self._rows.values() if r["status"] != "deleted"]

    def _db_update_automation(self, automation_id, fields):
        self._rows[automation_id].update(fields)


@pytest.fixture
def mgr(tmp_path):
    return StubManager(str(tmp_path / "automations_data"))


VALID_MANIFEST = {
    "name": "payroll",
    "entrypoint": "main.py",
    "timeout_seconds": 60,
    "inputs": [],
    "connections": [],
    "secrets": [],
    "packages": [],
    "outputs": [],
}


class TestManager:
    def test_create_builds_skeleton(self, mgr):
        ok, auto, err = mgr.create_automation("payroll", "desc", owner_user_id=1)
        assert ok, err
        adir = mgr.automation_dir(auto["automation_id"])
        assert os.path.isfile(os.path.join(adir, "manifest.json"))
        assert os.path.isfile(os.path.join(adir, "main.py"))
        assert os.path.isdir(os.path.join(adir, "versions"))
        assert os.path.isdir(os.path.join(adir, "runs"))

    def test_duplicate_name_rejected(self, mgr):
        assert mgr.create_automation("dup", "", 1)[0]
        ok, _, err = mgr.create_automation("DUP", "", 1)
        assert not ok and "already exists" in err

    def test_save_version_is_append_only(self, mgr):
        _, auto, _ = mgr.create_automation("v", "", 1)
        aid = auto["automation_id"]
        ok, v1, errs = mgr.save_version(aid, "print(1)\n", VALID_MANIFEST)
        assert ok and v1 == 1, errs
        ok, v2, _ = mgr.save_version(aid, "print(2)\n", VALID_MANIFEST)
        assert ok and v2 == 2
        # immutable history: v1 still holds the old code
        assert mgr.get_code(aid, 1) == "print(1)\n"
        assert mgr.get_code(aid, 2) == "print(2)\n"
        assert mgr.get_code(aid) == "print(2)\n"  # working copy = latest
        assert mgr.list_versions(aid) == [1, 2]

    def test_save_version_rejects_secrets(self, mgr):
        _, auto, _ = mgr.create_automation("sec", "", 1)
        ok, _, errors = mgr.save_version(auto["automation_id"], 'pw = "PWD=abc123;"', VALID_MANIFEST)
        assert not ok
        assert any("credential" in e.lower() for e in errors)

    def test_save_version_rejects_invalid_manifest(self, mgr):
        _, auto, _ = mgr.create_automation("bad", "", 1)
        bad = dict(VALID_MANIFEST, timeout_seconds=-5)
        ok, _, errors = mgr.save_version(auto["automation_id"], "print(1)", bad)
        assert not ok

    def test_save_refuses_disk_db_drift(self, mgr):
        _, auto, _ = mgr.create_automation("drift", "", 1)
        aid = auto["automation_id"]
        os.makedirs(mgr.version_dir(aid, 1))  # simulate drift: v1 exists, DB says 0
        ok, _, errors = mgr.save_version(aid, "print(1)", VALID_MANIFEST)
        assert not ok and any("drift" in e for e in errors)

    def test_promote_latest_and_explicit(self, mgr):
        _, auto, _ = mgr.create_automation("promo", "", 1)
        aid = auto["automation_id"]
        ok, _, err = mgr.promote(aid)
        assert not ok  # nothing saved yet
        mgr.save_version(aid, "print(1)", VALID_MANIFEST)
        mgr.save_version(aid, "print(2)", VALID_MANIFEST)
        ok, pinned, _ = mgr.promote(aid)  # promote latest
        assert ok and pinned == 2
        ok, pinned, _ = mgr.promote(aid, version=1)  # pin back
        assert ok and pinned == 1
        assert not mgr.promote(aid, version=9)[0]  # nonexistent

    def test_soft_delete_frees_name(self, mgr):
        _, auto, _ = mgr.create_automation("gone", "", 1)
        assert mgr.delete_automation(auto["automation_id"])[0]
        assert mgr.get_automation(auto["automation_id"]) is None
        assert mgr.create_automation("gone", "", 1)[0]  # name reusable

    def test_add_sample(self, mgr):
        _, auto, _ = mgr.create_automation("samp", "", 1)
        aid = auto["automation_id"]
        mgr.save_version(aid, "print(1)", VALID_MANIFEST)
        ok, err = mgr.add_sample(aid, 1, "input.pdf", b"%PDF-fake")
        assert ok, err
        assert not mgr.add_sample(aid, 1, "../evil.pdf", b"x")[0]
        assert not mgr.add_sample(aid, 9, "x.pdf", b"x")[0]  # no such version


# ---------------------------------------------------------------------------
# runner with stubbed DB + REAL subprocess
# ---------------------------------------------------------------------------

class StubRunner(AutomationRunner):
    def __init__(self, manager):
        self.manager = manager
        self.tenant_id = manager.tenant_id
        self.connection_string = "stub"
        self.runs: Dict[str, Dict] = {}
        self.live_run = False
        self.connections = {"ERPDB": "Driver={SQL Server};Server=t;PWD=injected;"}
        self.secrets = {"ACME_SFTP": "sftp-secret-value"}

    def _db_has_live_run(self, automation_id, max_age_seconds):
        return self.live_run

    def _db_insert_run(self, row):
        self.runs[row["run_id"]] = dict(row)

    def _db_finish_run(self, run_id, status, exit_code, verify_report, output_files, error):
        self.runs[run_id].update(status=status, exit_code=exit_code,
                                 verify_report=verify_report,
                                 output_files=output_files, error=error)

    def _db_get_run(self, run_id):
        return self.runs.get(run_id)

    def _db_list_runs(self, automation_id, limit=50):
        return [r for r in self.runs.values() if r["automation_id"] == automation_id]

    def _resolve_connection(self, name):
        return self.connections.get(name)

    def _resolve_secret(self, name):
        return self.secrets.get(name)

    def _resolve_python(self, environment_id):
        return sys.executable


def _make_automation(mgr, code: str, manifest_overrides: Optional[Dict] = None,
                     promote: bool = True) -> str:
    manifest = dict(VALID_MANIFEST)
    manifest.update(manifest_overrides or {})
    _, auto, err = mgr.create_automation(manifest["name"], "", 1)
    assert auto, err
    aid = auto["automation_id"]
    ok, _, errs = mgr.save_version(aid, code, manifest)
    assert ok, errs
    if promote:
        mgr.promote(aid)
    return aid


class TestRunner:
    def test_success_run_with_credential_injection_and_verify(self, mgr):
        code = (
            "import os\n"
            "os.makedirs('out', exist_ok=True)\n"
            "with open('out/result.csv', 'w') as f:\n"
            "    f.write('h\\n' + os.environ['AIHUB_CONN_ERPDB'] + '\\n'\n"
            "            + os.environ['AIHUB_SECRET_ACME_SFTP'] + '\\n')\n"
        )
        aid = _make_automation(mgr, code, {
            "name": "okrun",
            "connections": ["ERPDB"], "secrets": ["ACME_SFTP"],
            "outputs": [{"kind": "file", "path": "out/result.csv", "verify": {"min_rows": 2}}],
        })
        runner = StubRunner(mgr)
        result = runner.run(aid, trigger="manual", requested_by=1)
        assert result["status"] == "success", result
        assert result["exit_code"] == 0
        assert "out/result.csv" in [f.replace(os.sep, "/") for f in result["output_files"]]
        # credentials actually reached the subprocess
        produced = os.path.join(result["workdir"], "out", "result.csv")
        content = open(produced).read()
        assert "PWD=injected" in content and "sftp-secret-value" in content

    def test_nonzero_exit_is_failed(self, mgr):
        aid = _make_automation(mgr, "import sys\nsys.exit(3)\n", {"name": "boom"})
        result = StubRunner(mgr).run(aid)
        assert result["status"] == "failed" and result["exit_code"] == 3

    def test_missing_declared_output_is_failed_even_on_exit_zero(self, mgr):
        aid = _make_automation(mgr, "print('claims success, produces nothing')\n", {
            "name": "liar",
            "outputs": [{"kind": "file", "path": "out/missing.csv"}],
        })
        result = StubRunner(mgr).run(aid)
        assert result["status"] == "failed"
        assert result["exit_code"] == 0
        assert result["verify_report"][0]["checks"][0]["ok"] is False

    def test_remote_output_yields_unverified(self, mgr):
        aid = _make_automation(mgr, "print('uploaded... trust me')\n", {
            "name": "remote",
            "outputs": [{"kind": "sftp_upload", "secret": "ACME_SFTP"}],
        })
        result = StubRunner(mgr).run(aid)
        assert result["status"] == "unverified"

    def test_skip_if_running(self, mgr):
        aid = _make_automation(mgr, "print(1)\n", {"name": "busy"})
        runner = StubRunner(mgr)
        runner.live_run = True
        result = runner.run(aid, trigger="schedule")
        assert result["status"] == "skipped"
        assert runner.runs[result["run_id"]]["status"] == "skipped"

    def test_dry_run_allowed_while_running_and_seeds_samples(self, mgr):
        code = (
            "with open('sample.txt') as f: data = f.read()\n"
            "with open('echo.txt', 'w') as f: f.write(data)\n"
        )
        aid = _make_automation(mgr, code, {"name": "dry"}, promote=False)
        mgr.add_sample(aid, 1, "sample.txt", b"sample-content")
        runner = StubRunner(mgr)
        runner.live_run = True  # dry runs bypass the skip guard
        result = runner.run(aid, dry_run=True)
        assert result["status"] == "success", result
        assert open(os.path.join(result["workdir"], "echo.txt")).read() == "sample-content"

    def test_unpromoted_automation_refuses_real_run(self, mgr):
        aid = _make_automation(mgr, "print(1)\n", {"name": "unpinned"}, promote=False)
        result = StubRunner(mgr).run(aid)
        assert result["status"] == "error" and "promote" in result["error"]

    def test_run_executes_pinned_not_latest(self, mgr):
        aid = _make_automation(mgr, "open('v1.marker', 'w').write('1')\n", {"name": "pin"})
        mgr.save_version(aid, "open('v2.marker', 'w').write('2')\n", dict(VALID_MANIFEST, name="pin"))
        # pinned is still v1
        result = StubRunner(mgr).run(aid)
        assert result["status"] == "success"
        assert os.path.isfile(os.path.join(result["workdir"], "v1.marker"))
        assert not os.path.isfile(os.path.join(result["workdir"], "v2.marker"))
        # dry run executes the latest edit (v2)
        result2 = StubRunner(mgr).run(aid, dry_run=True)
        assert os.path.isfile(os.path.join(result2["workdir"], "v2.marker"))

    def test_missing_connection_fails_preflight(self, mgr):
        aid = _make_automation(mgr, "print(1)\n", {"name": "noconn", "connections": ["NOPE"]})
        result = StubRunner(mgr).run(aid)
        assert result["status"] == "failed"
        assert "NOPE" in result["error"]

    def test_missing_required_input_errors_before_execution(self, mgr):
        aid = _make_automation(mgr, "print(1)\n", {
            "name": "needsinput",
            "inputs": [{"name": "region", "type": "string"}],
        })
        result = StubRunner(mgr).run(aid)
        assert result["status"] == "error" and "region" in result["error"]

    @pytest.mark.slow
    def test_timeout_is_failed(self, mgr):
        aid = _make_automation(mgr, "import time\ntime.sleep(30)\n", {
            "name": "slowpoke", "timeout_seconds": 2,
        })
        result = StubRunner(mgr).run(aid)
        assert result["status"] == "failed"
        assert "timed out" in result["error"]

    def test_run_log_written(self, mgr):
        aid = _make_automation(mgr, "print('hello from automation')\n", {"name": "logged"})
        runner = StubRunner(mgr)
        result = runner.run(aid)
        log = open(os.path.join(result["workdir"], "run.log"), encoding="utf-8").read()
        assert "hello from automation" in log
        assert "outcome: success" in log


# ---------------------------------------------------------------------------
# P1: remote-output verification
# ---------------------------------------------------------------------------

from automations.remote_verify import parse_transfer_secret  # noqa: E402


class TestParseTransferSecret:
    def test_url_form(self):
        c = parse_transfer_secret("sftp://user:p%40ss@host.example:2222")
        assert c == {"host": "host.example", "port": 2222, "username": "user", "password": "p@ss"}

    def test_json_form(self):
        c = parse_transfer_secret('{"host": "h", "port": "21", "username": "u", "password": "p"}')
        assert c == {"host": "h", "port": 21, "username": "u", "password": "p"}

    def test_garbage_returns_none(self):
        assert parse_transfer_secret("just-a-plain-api-key") is None
        assert parse_transfer_secret("{not json") is None
        assert parse_transfer_secret("") is None
        assert parse_transfer_secret('{"port": 22}') is None  # no host


class TestRemoteVerifyWiring:
    """verify_outputs must translate the remote check's tri-state into the run
    outcome: True->success, False->FAILED, None->unverified."""

    MANIFEST = {"outputs": [{"kind": "sftp_upload", "secret": "S", "remote_dir": "/out",
                             "name": "f_{period}.csv", "verify": {"remote_listing": True}}]}

    def _wire(self, monkeypatch, tri_state, note="stub"):
        import automations.remote_verify as rv
        calls = {}

        def fake_check(kind, secret_value, remote_dir, filename, verify):
            calls.update(kind=kind, secret=secret_value, remote_dir=remote_dir, filename=filename)
            return tri_state, note

        monkeypatch.setattr(rv, "check_remote_output", fake_check)
        return calls

    def test_remote_ok_is_success(self, tmp_path, monkeypatch):
        calls = self._wire(monkeypatch, True)
        outcome, report = verify_outputs(self.MANIFEST, str(tmp_path), {"period": "07"},
                                         secret_resolver=lambda n: "sftp://u:p@h:22")
        assert outcome == "success"
        assert calls["filename"] == "f_07.csv"  # inputs substituted
        assert calls["secret"] == "sftp://u:p@h:22"

    def test_remote_missing_is_failed(self, tmp_path, monkeypatch):
        self._wire(monkeypatch, False)
        outcome, report = verify_outputs(self.MANIFEST, str(tmp_path), {"period": "07"},
                                         secret_resolver=lambda n: "sftp://u:p@h:22")
        assert outcome == "failed"

    def test_remote_uncheckable_is_unverified(self, tmp_path, monkeypatch):
        self._wire(monkeypatch, None)
        outcome, report = verify_outputs(self.MANIFEST, str(tmp_path), {"period": "07"},
                                         secret_resolver=lambda n: "sftp://u:p@h:22")
        assert outcome == "unverified"

    def test_no_resolver_is_unverified(self, tmp_path):
        outcome, report = verify_outputs(self.MANIFEST, str(tmp_path), {"period": "07"})
        assert outcome == "unverified"


# ---------------------------------------------------------------------------
# P1: scheduler 'automation' job type
# ---------------------------------------------------------------------------

def _import_job_scheduler():
    """Import job_scheduler, stubbing apscheduler if the test env lacks it
    (the scheduler service runs in its own conda env)."""
    import types
    from unittest.mock import MagicMock
    try:
        import apscheduler  # noqa: F401
    except ImportError:
        for mod in ["apscheduler", "apscheduler.schedulers", "apscheduler.schedulers.background",
                    "apscheduler.jobstores", "apscheduler.jobstores.sqlalchemy",
                    "apscheduler.jobstores.memory", "apscheduler.executors",
                    "apscheduler.executors.pool", "apscheduler.triggers",
                    "apscheduler.triggers.cron", "apscheduler.triggers.date",
                    "apscheduler.triggers.interval"]:
            if mod not in sys.modules:
                m = types.ModuleType(mod)
                for attr in ("BackgroundScheduler", "SQLAlchemyJobStore", "MemoryJobStore",
                             "ThreadPoolExecutor", "ProcessPoolExecutor",
                             "CronTrigger", "DateTrigger", "IntervalTrigger"):
                    setattr(m, attr, MagicMock())
                sys.modules[mod] = m
    import job_scheduler
    return job_scheduler


class TestSchedulerAutomationJob:
    def _svc(self, monkeypatch, response_status=200, response_body=None):
        js = _import_job_scheduler()
        svc = js.JobSchedulerService.__new__(js.JobSchedulerService)
        svc.api_base_url = "http://127.0.0.1:5000"
        records = []
        monkeypatch.setattr(svc, "_create_execution_record", lambda *a, **k: 77, raising=False)
        monkeypatch.setattr(svc, "_update_execution_record",
                            lambda eid, status, **k: records.append((status, k.get("result_message", ""))),
                            raising=False)
        monkeypatch.setattr(svc, "_increment_run_count", lambda *a, **k: None, raising=False)
        monkeypatch.setattr(svc, "_update_last_run_time", lambda *a, **k: None, raising=False)

        captured = {}

        class _Resp:
            status_code = response_status
            def json(self):
                return response_body or {}

        def _post(url, json=None, headers=None, timeout=None):
            captured["url"], captured["payload"], captured["headers"] = url, json, headers
            return _Resp()

        monkeypatch.setattr(js.requests, "post", _post)
        return svc, records, captured

    JOB = {"scheduled_job_id": 1, "schedule_id": 2, "job_name": "Nightly payroll",
           "parameters": {"automation_id": "abc-123", "inputs": '{"period": "2026-07"}',
                          "user_id": "13"}}

    def test_success_outcome_completed(self, monkeypatch):
        svc, records, captured = self._svc(monkeypatch, 200,
                                           {"status": "success", "run_id": "r1"})
        svc._execute_automation_job(self.JOB)
        assert captured["url"].endswith("/automations/api/internal/run")
        assert captured["payload"]["automation_id"] == "abc-123"
        assert captured["payload"]["inputs"] == {"period": "2026-07"}
        assert captured["payload"]["trigger"] == "schedule"
        assert records[-1][0] == "completed"
        assert "outcome=success" in records[-1][1]

    def test_failed_outcome_failed(self, monkeypatch):
        svc, records, _ = self._svc(monkeypatch, 200,
                                    {"status": "failed", "run_id": "r2", "error": "exit code 1"})
        svc._execute_automation_job(self.JOB)
        assert records[-1][0] == "failed"
        assert "exit code 1" in records[-1][1]

    def test_unverified_completed_but_labeled(self, monkeypatch):
        svc, records, _ = self._svc(monkeypatch, 200,
                                    {"status": "unverified", "run_id": "r3"})
        svc._execute_automation_job(self.JOB)
        assert records[-1][0] == "completed"
        assert "outcome=unverified" in records[-1][1]
        assert "not verified" in records[-1][1]

    def test_skipped_completed_with_note(self, monkeypatch):
        svc, records, _ = self._svc(monkeypatch, 200,
                                    {"status": "skipped", "run_id": "r4",
                                     "note": "a run is already in progress"})
        svc._execute_automation_job(self.JOB)
        assert records[-1][0] == "completed"
        assert "outcome=skipped" in records[-1][1]

    def test_missing_automation_id_fails_without_http(self, monkeypatch):
        svc, records, captured = self._svc(monkeypatch)
        job = {"scheduled_job_id": 1, "schedule_id": 2, "job_name": "broken", "parameters": {}}
        svc._execute_automation_job(job)
        assert records[-1][0] == "failed"
        assert "automation_id" in records[-1][1]
        assert "url" not in captured  # never called the API
