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

    def _db_get_run_field(self, run_id, field):
        run = self.runs.get(run_id) or {}
        return run.get(field)

    def _db_set_run_status(self, run_id, status, only_if_in=None):
        run = self.runs.get(run_id)
        if not run:
            return False
        if only_if_in and run.get("status") not in only_if_in:
            return False
        run["status"] = status
        return True

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


class _EnvInjectCfg:
    AUTOMATIONS_ENV_CRED_INJECTION = True


class _NoInjectCfg:
    AUTOMATIONS_ENV_CRED_INJECTION = False


class TestRunner:
    def test_success_run_with_env_credential_injection_and_verify(self, mgr, monkeypatch):
        """Legacy P0/P1 delivery path (flag on): values as env vars."""
        import automations.runner as runner_mod
        monkeypatch.setattr(runner_mod, "_load_cfg", lambda: _EnvInjectCfg)
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

    def test_default_flag_keeps_credential_values_out_of_env(self, mgr, monkeypatch):
        """P2 default: env carries the run token, never the credential value."""
        import automations.runner as runner_mod
        monkeypatch.setattr(runner_mod, "_load_cfg", lambda: _NoInjectCfg)
        monkeypatch.setenv("CC_JWT_SECRET", "test-secret-p2")
        monkeypatch.setenv("AUTOMATIONS_RUNTIME_URL", "http://127.0.0.1:9")  # never called
        code = (
            "import os, json\n"
            "report = {'conn_env': os.environ.get('AIHUB_CONN_ERPDB'),\n"
            "          'token': bool(os.environ.get('AIHUB_RUN_TOKEN')),\n"
            "          'url': os.environ.get('AIHUB_RUNTIME_URL')}\n"
            "json.dump(report, open('report.json', 'w'))\n"
        )
        aid = _make_automation(mgr, code, {"name": "noinject", "connections": ["ERPDB"]})
        result = StubRunner(mgr).run(aid)
        assert result["status"] == "success", result
        report = json.load(open(os.path.join(result["workdir"], "report.json")))
        assert report["conn_env"] is None          # value NOT in env
        assert report["token"] is True             # token present instead
        assert report["url"] == "http://127.0.0.1:9"

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


# ---------------------------------------------------------------------------
# P2: aihub_runtime SDK + run-token resolution
# ---------------------------------------------------------------------------

class TestSdkEndToEnd:
    """Full P2 loop with a REAL subprocess and REAL HTTP: the runner injects a
    signed run token + PYTHONPATH; the script imports aihub_runtime and calls
    connection(); a stub resolve server verifies the token with shared_auth and
    enforces the allowlist."""

    @pytest.fixture
    def resolve_server(self, monkeypatch):
        import http.server
        import threading

        monkeypatch.setenv("CC_JWT_SECRET", "test-secret-e2e")
        requests_seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):  # keep test output clean
                pass

            def do_POST(self):
                from shared_auth import verify_automation_run_token
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests_seen.append(body)
                claims, err = verify_automation_run_token(body.get("token", ""))
                if err:
                    self._reply(403, {"error": f"invalid run token: {err}"})
                    return
                allowed = claims.get("connections" if body["kind"] == "connection" else "secrets") or []
                if body["name"] not in allowed:
                    self._reply(403, {"error": "not declared in manifest"})
                    return
                self._reply(200, {"value": f"resolved-{body['kind']}-{body['name']}"})

            def _reply(self, code, payload):
                data = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        monkeypatch.setenv("AUTOMATIONS_RUNTIME_URL", f"http://127.0.0.1:{server.server_port}")
        yield requests_seen
        server.shutdown()

    def test_sdk_resolves_connection_via_token(self, mgr, monkeypatch, resolve_server):
        import automations.runner as runner_mod
        monkeypatch.setattr(runner_mod, "_load_cfg", lambda: _NoInjectCfg)
        code = (
            "import aihub_runtime as aihub\n"
            "value = aihub.connection('ERPDB')\n"
            "period = aihub.input('period', 'none')\n"
            "with open('resolved.txt', 'w') as f:\n"
            "    f.write(value + '|' + period)\n"
            "aihub.log('resolved fine')\n"
        )
        aid = _make_automation(mgr, code, {
            "name": "sdk-e2e", "connections": ["ERPDB"],
            "inputs": [{"name": "period", "type": "string", "default": "2026-07"}],
        })
        result = StubRunner(mgr).run(aid)
        assert result["status"] == "success", result
        content = open(os.path.join(result["workdir"], "resolved.txt")).read()
        assert content == "resolved-connection-ERPDB|2026-07"
        assert "[aihub] resolved fine" in result["stdout_tail"]
        assert resolve_server[0]["kind"] == "connection"
        # egress logging: the SDK's HTTP call to the resolve server must appear
        log = open(os.path.join(result["workdir"], "run.log"), encoding="utf-8").read()
        assert "network egress" in log and "127.0.0.1" in log.split("network egress")[1]

    def test_sdk_undeclared_name_is_refused(self, mgr, monkeypatch, resolve_server):
        import automations.runner as runner_mod
        monkeypatch.setattr(runner_mod, "_load_cfg", lambda: _NoInjectCfg)
        code = (
            "import aihub_runtime as aihub\n"
            "try:\n"
            "    aihub.secret('NOT_DECLARED')\n"
            "    print('GOT IT (bad)')\n"
            "except aihub.AutomationRuntimeError as e:\n"
            "    print('refused:', e)\n"
            "    raise SystemExit(2)\n"
        )
        # manifest declares a connection but NOT the secret the script asks for
        aid = _make_automation(mgr, code, {"name": "sdk-refuse", "connections": ["ERPDB"]})
        result = StubRunner(mgr).run(aid)
        assert result["status"] == "failed"
        assert result["exit_code"] == 2
        assert "refused:" in result["stdout_tail"]

    def test_preflight_fails_when_no_token_and_no_env_injection(self, mgr, monkeypatch):
        import automations.runner as runner_mod
        monkeypatch.setattr(runner_mod, "_load_cfg", lambda: _NoInjectCfg)
        aid = _make_automation(mgr, "print(1)\n", {"name": "notoken", "connections": ["ERPDB"]})
        runner = StubRunner(mgr)
        monkeypatch.setattr(runner, "_mint_run_token", lambda *a, **k: None)
        result = runner.run(aid)
        assert result["status"] == "failed"
        assert "run-token signing is unavailable" in result["error"]

    def test_token_minted_even_without_declared_credentials(self, mgr, monkeypatch):
        """AIHUB-0031 F2: aihub.checkpoint() needs the run token, so it must be
        minted for EVERY run — not only when connections/secrets are declared."""
        import automations.runner as runner_mod
        monkeypatch.setattr(runner_mod, "_load_cfg", lambda: _NoInjectCfg)
        monkeypatch.setenv("CC_JWT_SECRET", "f2-token-secret")
        monkeypatch.setenv("AUTOMATIONS_RUNTIME_URL", "http://127.0.0.1:9")
        code = (
            "import os, json\n"
            "json.dump({'token': bool(os.environ.get('AIHUB_RUN_TOKEN')),\n"
            "           'url': bool(os.environ.get('AIHUB_RUNTIME_URL'))},\n"
            "          open('t.json', 'w'))\n"
        )
        aid = _make_automation(mgr, code, {"name": "checkpoint-only"})  # NO creds declared
        result = StubRunner(mgr).run(aid)
        assert result["status"] == "success", result
        report = json.load(open(os.path.join(result["workdir"], "t.json")))
        assert report == {"token": True, "url": True}

    def test_no_token_without_creds_still_runs(self, mgr, monkeypatch):
        """Signing unavailable + no creds declared: the run proceeds (only
        checkpoint()/resolve would fail inside, honestly, if called)."""
        import automations.runner as runner_mod
        monkeypatch.setattr(runner_mod, "_load_cfg", lambda: _NoInjectCfg)
        aid = _make_automation(mgr, "print('fine')\n", {"name": "no-sign-no-creds"})
        runner = StubRunner(mgr)
        monkeypatch.setattr(runner, "_mint_run_token", lambda *a, **k: None)
        result = runner.run(aid)
        assert result["status"] == "success", result


class TestServiceKeyAuth:
    """AIHUB-0031 F1: internal endpoints must accept BOTH the raw tenant
    API_KEY (scheduler convention) and the machine-derived internal service
    key (what CC sends when AI_HUB_API_KEY isn't pinned)."""

    def test_key_matrix(self, monkeypatch):
        import automations.api as api_mod
        monkeypatch.setenv("API_KEY", "raw-tenant-key")
        import role_decorators
        monkeypatch.setattr(role_decorators, "get_internal_api_key", lambda: "derived-internal-key")
        assert api_mod._service_key_ok("raw-tenant-key") is True
        assert api_mod._service_key_ok("derived-internal-key") is True
        assert api_mod._service_key_ok("wrong") is False
        assert api_mod._service_key_ok("") is False
        assert api_mod._service_key_ok(None) is False

    def test_internal_manage_accepts_derived_key(self, monkeypatch, mgr):
        from flask import Flask
        import automations.api as api_mod
        import role_decorators
        monkeypatch.setenv("API_KEY", "raw-tenant-key")
        monkeypatch.setattr(role_decorators, "get_internal_api_key", lambda: "derived-internal-key")
        monkeypatch.setattr(api_mod, "_manager", mgr)
        monkeypatch.setattr(api_mod, "_runner", StubRunner(mgr))
        app = Flask(__name__)
        app.register_blueprint(api_mod.automations_bp)
        client = app.test_client()
        r = client.post("/automations/api/internal/manage",
                        headers={"X-API-Key": "derived-internal-key"},
                        json={"action": "list",
                              "user_context": {"user_id": 7, "role": 2, "username": "cc"},
                              "payload": {}})
        assert r.status_code == 200, r.get_json()
        r = client.post("/automations/api/internal/manage",
                        headers={"X-API-Key": "nope"},
                        json={"action": "list",
                              "user_context": {"user_id": 7, "role": 2, "username": "cc"},
                              "payload": {}})
        assert r.status_code == 401


class TestResolveEndpoint:
    """The real Flask endpoint: signature, allowlist, and live-run checks."""

    @pytest.fixture
    def client(self, monkeypatch, mgr):
        from flask import Flask
        import automations.api as api_mod

        monkeypatch.setenv("CC_JWT_SECRET", "test-secret-endpoint")

        class EndpointRunner:
            def __init__(self):
                self.live_run = {"run_id": "run-1", "automation_id": "auto-1", "status": "running"}
            def get_run(self, run_id):
                return dict(self.live_run) if run_id == self.live_run["run_id"] else None
            def _resolve_connection(self, name):
                return "Driver=X;PWD=endpoint;" if name == "ERPDB" else None
            def _resolve_secret(self, name):
                return None

        self.runner = EndpointRunner()
        monkeypatch.setattr(api_mod, "_runner", self.runner)
        monkeypatch.setattr(api_mod, "_manager", mgr)
        app = Flask(__name__)
        app.register_blueprint(api_mod.automations_bp)
        return app.test_client()

    def _token(self, **kw):
        from shared_auth import sign_automation_run_token
        args = dict(automation_id="auto-1", run_id="run-1",
                    connections=["ERPDB"], secrets=[], ttl_seconds=300)
        args.update(kw)
        return sign_automation_run_token(**args)

    def test_valid_token_resolves(self, client):
        r = client.post("/automations/api/runtime/resolve",
                        json={"token": self._token(), "kind": "connection", "name": "ERPDB"})
        assert r.status_code == 200
        assert r.get_json()["value"] == "Driver=X;PWD=endpoint;"

    def test_undeclared_name_403(self, client):
        r = client.post("/automations/api/runtime/resolve",
                        json={"token": self._token(), "kind": "secret", "name": "SNEAKY"})
        assert r.status_code == 403

    def test_garbage_token_403(self, client):
        r = client.post("/automations/api/runtime/resolve",
                        json={"token": "junk", "kind": "connection", "name": "ERPDB"})
        assert r.status_code == 403

    def test_finished_run_403(self, client):
        self.runner.live_run["status"] = "success"  # run over -> token dead
        r = client.post("/automations/api/runtime/resolve",
                        json={"token": self._token(), "kind": "connection", "name": "ERPDB"})
        assert r.status_code == 403

    def test_mismatched_automation_403(self, client):
        r = client.post("/automations/api/runtime/resolve",
                        json={"token": self._token(automation_id="other"),
                              "kind": "connection", "name": "ERPDB"})
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# P3: CC internal manage endpoint
# ---------------------------------------------------------------------------

class TestInternalManage:
    """The CC tools' seam: X-API-Key auth + server-side role enforcement +
    action dispatch against the real manager/runner (stub DB)."""

    @pytest.fixture
    def client(self, monkeypatch, mgr):
        from flask import Flask
        import automations.api as api_mod

        monkeypatch.setenv("API_KEY", "svc-key-test")

        class ManageRunner(StubRunner):
            pass

        monkeypatch.setattr(api_mod, "_manager", mgr)
        monkeypatch.setattr(api_mod, "_runner", ManageRunner(mgr))
        monkeypatch.setattr(api_mod, "_tables_ensured", True)
        app = Flask(__name__)
        app.register_blueprint(api_mod.automations_bp)
        return app.test_client()

    def _post(self, client, action, payload=None, role=2, api_key="svc-key-test"):
        return client.post("/automations/api/internal/manage",
                           headers={"X-API-Key": api_key},
                           json={"action": action,
                                 "user_context": {"user_id": 7, "role": role, "username": "dev"},
                                 "payload": payload or {}})

    def test_bad_api_key_401(self, client):
        r = self._post(client, "list", api_key="wrong")
        assert r.status_code == 401

    def test_low_role_403_even_with_valid_key(self, client):
        r = self._post(client, "list", role=1)
        assert r.status_code == 403

    def test_full_build_flow(self, client, monkeypatch):
        import automations.runner as runner_mod
        monkeypatch.setattr(runner_mod, "_load_cfg", lambda: _EnvInjectCfg)
        # create
        r = self._post(client, "create", {"name": "cc-built", "description": "via CC",
                                          "provision_environment": False})
        assert r.status_code == 201, r.get_json()
        aid = r.get_json()["automation"]["automation_id"]
        # save code + manifest
        r = self._post(client, "save_code", {
            "automation_id": aid,
            "code": "with open('x.csv','w') as f: f.write('h\\n1\\n')\n",
            "manifest": dict(VALID_MANIFEST, name="cc-built",
                             outputs=[{"kind": "file", "path": "x.csv", "verify": {"min_rows": 1}}]),
        })
        assert r.status_code == 200 and r.get_json()["version"] == 1
        # dry run (latest)
        r = self._post(client, "dry_run", {"automation_id": aid})
        assert r.get_json()["status"] == "success", r.get_json()
        # promote
        r = self._post(client, "promote", {"automation_id": aid})
        assert r.get_json()["pinned_version"] == 1
        # real run
        r = self._post(client, "run", {"automation_id": aid})
        assert r.get_json()["status"] == "success"
        # history
        r = self._post(client, "runs", {"automation_id": aid})
        runs = r.get_json()["runs"]
        assert len(runs) == 2  # dry_run + run

    def test_save_code_rejects_secrets_via_manage(self, client):
        r = self._post(client, "create", {"name": "cc-sec", "provision_environment": False})
        aid = r.get_json()["automation"]["automation_id"]
        r = self._post(client, "save_code", {"automation_id": aid,
                                             "code": 'pw = "PWD=hunter22;"'})
        assert r.status_code == 400
        assert "credential" in r.get_json()["error"].lower()

    def test_unknown_action_400(self, client):
        r = self._post(client, "explode")
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# P4: webhook trigger
# ---------------------------------------------------------------------------

class TestWebhook:
    @pytest.fixture
    def client(self, monkeypatch, mgr):
        from flask import Flask
        import automations.api as api_mod

        monkeypatch.setenv("CC_JWT_SECRET", "hook-secret")
        self.runner = StubRunner(mgr)
        self.run_calls = []
        real_run = self.runner.run
        self.runner.run = lambda *a, **k: self.run_calls.append(k) or real_run(*a, **k)
        monkeypatch.setattr(api_mod, "_manager", mgr)
        monkeypatch.setattr(api_mod, "_runner", self.runner)
        app = Flask(__name__)
        app.register_blueprint(api_mod.automations_bp)
        return app.test_client()

    def _hook(self, aid):
        from automations.api import _webhook_token
        return f"/automations/api/hook/{aid}/{_webhook_token(aid)}"

    def test_token_is_deterministic_and_scoped(self, monkeypatch):
        monkeypatch.setenv("CC_JWT_SECRET", "hook-secret")
        from automations.api import _webhook_token
        assert _webhook_token("a1") == _webhook_token("a1")
        assert _webhook_token("a1") != _webhook_token("a2")

    def test_valid_hook_fires_202(self, client, mgr):
        import time
        aid = _make_automation(mgr, "print(1)\n", {"name": "hooked"})
        r = client.post(self._hook(aid), json={"inputs": {}})
        assert r.status_code == 202
        assert r.get_json()["run_id"]
        deadline = time.time() + 10
        while not self.run_calls and time.time() < deadline:
            time.sleep(0.05)
        assert self.run_calls and self.run_calls[0]["trigger"] == "webhook"

    def test_bad_token_403(self, client, mgr):
        aid = _make_automation(mgr, "print(1)\n", {"name": "hooked2"})
        r = client.post(f"/automations/api/hook/{aid}/wrongtoken", json={})
        assert r.status_code == 403

    def test_unpromoted_409(self, client, mgr):
        aid = _make_automation(mgr, "print(1)\n", {"name": "hooked3"}, promote=False)
        r = client.post(self._hook(aid), json={})
        assert r.status_code == 409

    def test_undeclared_input_400_before_running(self, client, mgr):
        aid = _make_automation(mgr, "print(1)\n", {"name": "hooked4"})
        r = client.post(self._hook(aid), json={"inputs": {"nope": 1}})
        assert r.status_code == 400
        assert not self.run_calls


# ---------------------------------------------------------------------------
# P5: solution bundle export / install round-trip
# ---------------------------------------------------------------------------

class TestSolutionRoundTrip:
    def test_pack_then_install(self, mgr, tmp_path, monkeypatch):
        import automations.manager as manager_mod_
        from solution_bundler import SolutionBundler
        from solution_installer import SolutionInstaller, InstallOptions, InstallResult

        # a source automation with code + manifest + a sample, promoted
        aid = _make_automation(mgr, "print('bundled code')\n", {
            "name": "export-me",
            "secrets": ["ACME_SFTP"],
            "packages": ["pdfplumber"],
        })
        mgr.add_sample(aid, 1, "sample.pdf", b"%PDF-sample")

        # both bundler and installer construct AutomationManager() themselves
        monkeypatch.setattr(manager_mod_, "AutomationManager", lambda *a, **k: mgr)

        bundler = SolutionBundler(None)
        entries, secret_prompts, display = bundler._pack_automation(aid)
        assert display == "export-me"
        assert secret_prompts == ["ACME_SFTP"]  # -> installer credential prompt
        names = [rel for rel, _ in entries]
        assert "automation.json" in names and "main.py" in names and "samples/sample.pdf" in names
        meta = json.loads(dict(entries)["automation.json"])
        assert meta["exported_version"] == 1 and meta["was_promoted"] is True
        # credential VALUES never exported
        assert "sftp-secret-value" not in str(entries)

        # lay the entries out as a staged bundle and install
        staged = tmp_path / "bundle" / "automations" / "export-me"
        for rel, data in entries:
            p = staged / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)

        installer = SolutionInstaller.__new__(SolutionInstaller)
        result = InstallResult(solution_id="t", solution_name="t")
        options = InstallOptions(name_suffix="_installed")
        installer._install_automations(None, tmp_path / "bundle", {}, options, result)

        assert result.assets and result.assets[0].status == "installed", result.assets
        assert result.assets[0].name == "export-me_installed"
        installed = [a for a in mgr.list_automations() if a["name"] == "export-me_installed"]
        assert installed, "installed automation not in registry"
        # deliberately NOT promoted on install — dry-run on the target first
        assert installed[0]["pinned_version"] == 0
        assert installed[0]["current_version"] == 1
        assert mgr.get_code(installed[0]["automation_id"]) == "print('bundled code')\n"

    def test_pack_unsaved_automation_is_skipped(self, mgr, monkeypatch):
        import automations.manager as manager_mod_
        from solution_bundler import SolutionBundler
        _, auto, _ = mgr.create_automation("empty-auto", "", 1)
        monkeypatch.setattr(manager_mod_, "AutomationManager", lambda *a, **k: mgr)
        entries, prompts, display = SolutionBundler(None)._pack_automation(auto["automation_id"])
        assert entries == []  # no saved version -> skip, never a half-bundle


# ---------------------------------------------------------------------------
# Studio Phase B: run event sidecar + supervised execution
# ---------------------------------------------------------------------------

class TestRunEvents:
    def test_events_sidecar_written_with_increasing_seq(self, mgr):
        aid = _make_automation(mgr, "print('line one')\nprint('line two')\n", {"name": "evented"})
        result = StubRunner(mgr).run(aid)
        assert result["status"] == "success"
        path = os.path.join(result["workdir"], "events.jsonl")
        events = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
        types = [e["type"] for e in events]
        assert types[0] == "run_started"
        assert types[-1] == "finished"
        assert "log" in types
        log_lines = [e["line"] for e in events if e["type"] == "log"]
        assert "line one" in log_lines and "line two" in log_lines
        seqs = [e["seq"] for e in events]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
        # sidecar is not swept as an output file
        assert "events.jsonl" not in result["output_files"]

    def test_finished_event_carries_honest_outcome(self, mgr):
        aid = _make_automation(mgr, "import sys\nsys.exit(4)\n", {"name": "evfail"})
        result = StubRunner(mgr).run(aid)
        path = os.path.join(result["workdir"], "events.jsonl")
        events = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
        finished = [e for e in events if e["type"] == "finished"][0]
        assert finished["status"] == "failed" and finished["exit_code"] == 4

    def test_abort_mid_run(self, mgr):
        import threading as _t
        import time as _time
        import automations.runner as runner_mod
        aid = _make_automation(mgr, "import time\nprint('starting')\ntime.sleep(60)\n",
                               {"name": "abortme", "timeout_seconds": 120})
        runner = StubRunner(mgr)

        def flip_to_aborting():
            deadline = _time.time() + 15
            while _time.time() < deadline:
                live = [r for r in runner.runs.values() if r.get("status") == "running"]
                if live:
                    live[0]["status"] = "aborting"
                    return
                _time.sleep(0.1)

        # speed up the status poll for the test
        old = runner_mod._STATUS_POLL_SECONDS
        runner_mod._STATUS_POLL_SECONDS = 0.3
        try:
            _t.Thread(target=flip_to_aborting, daemon=True).start()
            t0 = _time.time()
            result = runner.run(aid)
            elapsed = _time.time() - t0
        finally:
            runner_mod._STATUS_POLL_SECONDS = old
        assert result["status"] == "aborted", result
        assert result["error"] == "aborted by user"
        assert elapsed < 30  # killed, not waited out
        events = [json.loads(l) for l in
                  open(os.path.join(result["workdir"], "events.jsonl"), encoding="utf-8") if l.strip()]
        assert any(e["type"] == "abort" for e in events)

    def test_request_abort_only_hits_live_runs(self, mgr):
        aid = _make_automation(mgr, "print(1)\n", {"name": "done-run"})
        runner = StubRunner(mgr)
        result = runner.run(aid)
        ok, note = runner.request_abort(result["run_id"])
        assert not ok and "not live" in note


# ---------------------------------------------------------------------------
# Studio Phase C: checkpoint gates
# ---------------------------------------------------------------------------

from automations.checkpoints import (  # noqa: E402
    create_checkpoint, decide_checkpoint, get_checkpoint, list_checkpoints,
)


class TestCheckpointStore:
    def test_create_get_decide(self, tmp_path):
        cp = create_checkpoint(str(tmp_path), "about to upload 1,240 rows")
        assert get_checkpoint(str(tmp_path), cp["checkpoint_id"])["decision"] is None
        decided = decide_checkpoint(str(tmp_path), cp["checkpoint_id"], "proceed", 7)
        assert decided["decision"] == "proceed" and decided["decided_by"] == 7
        # first decision wins (idempotent)
        again = decide_checkpoint(str(tmp_path), cp["checkpoint_id"], "abort", 9)
        assert again["decision"] == "proceed"

    def test_list_and_missing(self, tmp_path):
        assert list_checkpoints(str(tmp_path)) == []
        create_checkpoint(str(tmp_path), "one")
        create_checkpoint(str(tmp_path), "two")
        assert len(list_checkpoints(str(tmp_path))) == 2
        assert get_checkpoint(str(tmp_path), "nope") is None
        assert decide_checkpoint(str(tmp_path), "nope", "proceed", 1) is None

    def test_message_truncated(self, tmp_path):
        cp = create_checkpoint(str(tmp_path), "x" * 2000)
        assert len(cp["message"]) == 500


class TestCheckpointEndpoints:
    @pytest.fixture
    def client(self, monkeypatch, mgr, tmp_path):
        from flask import Flask
        import automations.api as api_mod

        monkeypatch.setenv("CC_JWT_SECRET", "cp-secret")
        workdir = tmp_path / "runwork"
        workdir.mkdir()
        (workdir / "run.log").write_text("")

        class CpRunner(StubRunner):
            def get_run(self, run_id):
                if run_id != "run-cp":
                    return None
                return {"run_id": "run-cp", "automation_id": "auto-cp",
                        "status": self.runs.get("run-cp", {}).get("status", "running"),
                        "log_path": str(workdir / "run.log"), "requested_by": None}

        self.runner = CpRunner(mgr)
        self.runner.runs["run-cp"] = {"run_id": "run-cp", "status": "running"}
        self.workdir = str(workdir)
        monkeypatch.setattr(api_mod, "_manager", mgr)
        monkeypatch.setattr(api_mod, "_runner", self.runner)
        app = Flask(__name__)
        app.register_blueprint(api_mod.automations_bp)
        return app.test_client()

    def _token(self):
        from shared_auth import sign_automation_run_token
        return sign_automation_run_token("auto-cp", "run-cp", [], [], 300)

    def test_full_gate_flow(self, client):
        # script opens the gate
        r = client.post("/automations/api/runtime/checkpoint",
                        json={"token": self._token(), "message": "big upload ahead"})
        assert r.status_code == 200, r.get_json()
        cid = r.get_json()["checkpoint_id"]
        assert self.runner.runs["run-cp"]["status"] == "waiting"
        # SDK poll sees no decision yet
        r = client.get(f"/automations/api/runtime/checkpoint?token={self._token()}&checkpoint_id={cid}")
        assert r.get_json()["decision"] is None
        # human proceeds (via the internal manage action, as CC would)
        import os as _os
        _os.environ["API_KEY"] = "svc-key-cp"
        r = client.post("/automations/api/internal/manage",
                        headers={"X-API-Key": "svc-key-cp"},
                        json={"action": "checkpoint_decision",
                              "user_context": {"user_id": 7, "role": 2, "username": "dev"},
                              "payload": {"run_id": "run-cp", "checkpoint_id": cid,
                                          "decision": "proceed"}})
        assert r.status_code == 200, r.get_json()
        assert self.runner.runs["run-cp"]["status"] == "running"
        # SDK poll now sees proceed
        r = client.get(f"/automations/api/runtime/checkpoint?token={self._token()}&checkpoint_id={cid}")
        assert r.get_json()["decision"] == "proceed"
        # events recorded both sides of the gate
        events = [json.loads(l) for l in
                  open(os.path.join(self.workdir, "events.jsonl"), encoding="utf-8") if l.strip()]
        assert [e["type"] for e in events] == ["checkpoint", "checkpoint_decided"]

    def test_abort_decision_flips_run_to_aborting(self, client):
        r = client.post("/automations/api/runtime/checkpoint",
                        json={"token": self._token(), "message": "risky"})
        cid = r.get_json()["checkpoint_id"]
        import os as _os
        _os.environ["API_KEY"] = "svc-key-cp"
        r = client.post("/automations/api/internal/manage",
                        headers={"X-API-Key": "svc-key-cp"},
                        json={"action": "checkpoint_decision",
                              "user_context": {"user_id": 7, "role": 2, "username": "dev"},
                              "payload": {"run_id": "run-cp", "checkpoint_id": cid,
                                          "decision": "abort"}})
        assert r.status_code == 200
        assert self.runner.runs["run-cp"]["status"] == "aborting"

    def test_bad_token_403(self, client):
        r = client.post("/automations/api/runtime/checkpoint",
                        json={"token": "junk", "message": "x"})
        assert r.status_code == 403

    def test_events_payload_includes_pending_checkpoint(self, client):
        r = client.post("/automations/api/runtime/checkpoint",
                        json={"token": self._token(), "message": "pending gate"})
        assert r.status_code == 200
        import os as _os
        _os.environ["API_KEY"] = "svc-key-cp"
        r = client.post("/automations/api/internal/manage",
                        headers={"X-API-Key": "svc-key-cp"},
                        json={"action": "run_events",
                              "user_context": {"user_id": 7, "role": 2, "username": "dev"},
                              "payload": {"run_id": "run-cp", "after": 0}})
        body = r.get_json()
        assert body["pending_checkpoint"]["message"] == "pending gate"
        assert any(e["type"] == "checkpoint" for e in body["events"])


class TestInlineWaitCheckpointAware:
    """AIHUB-0058 (james live): the inline dry-run blocked in runner.run()
    until the client read-timed out while the script sat at a human-approval
    checkpoint — the agent then claimed the run 'could not start' (it was
    WAITING for the user's decision). _await_inline_result returns fast runs
    byte-identically, surfaces a pending checkpoint immediately, and honors an
    inline budget with an honest still-running payload."""

    class _Clock:
        def __init__(self):
            self.t = 0.0
        def time(self):
            return self.t
        def sleep(self, s):
            self.t += s

    class _Thread:
        def __init__(self, alive=True):
            self._alive = alive
        def is_alive(self):
            return self._alive

    class _Runner:
        def __init__(self, run=None):
            self._run = run
        def get_run(self, run_id):
            return self._run

    @staticmethod
    def _api():
        import importlib
        try:
            return importlib.import_module("automations.api")
        except Exception as e:  # pragma: no cover - env-dependent
            import pytest as _pt
            _pt.skip(f"automations.api not importable here: {e}")

    def test_fast_run_returns_exact_result(self):
        api = self._api()
        holder = {"result": {"status": "success", "run_id": "r1", "verify_report": [1]}}
        payload, code = api._await_inline_result(
            self._Runner(), "r1", holder, self._Thread(alive=False),
            cap_s=5, poll_s=0.1, clock=self._Clock())
        assert payload is holder["result"] and code == 200

    def test_error_result_maps_to_400(self):
        api = self._api()
        holder = {"result": {"status": "error", "error": "boom"}}
        payload, code = api._await_inline_result(
            self._Runner(), "r1", holder, self._Thread(alive=False),
            cap_s=5, poll_s=0.1, clock=self._Clock())
        assert code == 400

    def test_pending_checkpoint_returns_immediately_with_question(self):
        api = self._api()
        pc = {"checkpoint_id": "c1", "message": "About to upload 11 rows — proceed?"}
        payload, code = api._await_inline_result(
            self._Runner(run={"status": "waiting"}), "r1", {}, self._Thread(alive=True),
            cap_s=60, poll_s=0.1, clock=self._Clock(),
            live_payload=lambda run, after: {"pending_checkpoint": pc})
        assert code == 200
        assert payload["waiting_on_checkpoint"] is True
        assert payload["pending_checkpoint"] is pc
        assert "NOT failed" in payload["note"]

    def test_waiting_without_pending_checkpoint_keeps_polling_to_result(self):
        """After a proceed decision the run may read 'waiting' briefly with no
        open checkpoint — that must NOT early-return; the loop continues to
        the real result."""
        api = self._api()
        holder = {}
        thread = self._Thread(alive=True)
        clock = self._Clock()
        calls = {"n": 0}
        class _R(self._Runner):
            def get_run(self, run_id):
                calls["n"] += 1
                if calls["n"] >= 3:
                    thread._alive = False
                    holder["result"] = {"status": "success", "run_id": "r1"}
                return {"status": "waiting"}
        payload, code = api._await_inline_result(
            _R(), "r1", holder, thread, cap_s=60, poll_s=0.1, clock=clock,
            live_payload=lambda run, after: {"pending_checkpoint": None})
        assert code == 200 and payload["status"] == "success"

    def test_inline_cap_returns_honest_still_running(self):
        api = self._api()
        payload, code = api._await_inline_result(
            self._Runner(run={"status": "running"}), "r9", {}, self._Thread(alive=True),
            cap_s=3, poll_s=1.0, clock=self._Clock())
        assert code == 200
        assert payload["inline_wait_elapsed"] is True
        assert payload["run_id"] == "r9"
        assert "NOT a failure" in payload["note"]


def test_schedule_payload_carries_authoritative_automation_name():
    """James UX 2026-07-20: the CC panel showed the raw automation id — the
    schedule result must carry the server-known NAME for the panel label."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "automations" / "api.py").read_text(encoding="utf-8")
    body = src[src.find("def _create_automation_schedule"):]
    body = body[:body.find("@automations_bp.route", 10)]
    assert '"automation_name": auto["name"]' in body


# ---------------------------------------------------------------------------
# Delete from Mission Control (james 2026-07-21: "easy way to delete")
# ---------------------------------------------------------------------------

class TestDeleteAutomation:
    """_delete_automation_impl: guard -> deactivate schedules -> soft delete.
    A soft-deleted automation with a live schedule would keep firing failed
    runs forever, so schedule shut-off comes FIRST and its failure refuses
    the delete."""

    class _RecordingConn:
        """Fake pyodbc conn capturing UPDATE statements; rowcount injectable."""
        def __init__(self, rowcount=0, fail=False):
            self.rowcount, self.fail = rowcount, fail
            self.statements, self.committed, self.rolled_back = [], False, False

        def cursor(self):
            outer = self

            class C:
                def execute(self, sql, *params):
                    if outer.fail:
                        raise RuntimeError("db down")
                    outer.statements.append(" ".join(sql.split()))
                    self.rowcount = outer.rowcount
            return C()

        def commit(self): self.committed = True
        def rollback(self): self.rolled_back = True
        def close(self): pass

    @pytest.fixture
    def api_mod(self, monkeypatch, mgr):
        import automations.api as api_mod
        runner = StubRunner(mgr)
        # base-class list_active_runs opens a real DB conn; quiet board default
        monkeypatch.setattr(runner, "list_active_runs", lambda: [])
        monkeypatch.setattr(api_mod, "_manager", mgr)
        monkeypatch.setattr(api_mod, "_runner", runner)
        monkeypatch.setattr(api_mod, "_tables_ensured", True)
        return api_mod

    def _make(self, mgr, name="del-me"):
        ok, auto, err = mgr.create_automation(name, "d", owner_user_id=7)
        assert ok, err
        return auto["automation_id"]

    def test_delete_soft_deletes_and_reports_schedules(self, api_mod, mgr, monkeypatch):
        aid = self._make(mgr)
        conn = self._RecordingConn(rowcount=2)
        monkeypatch.setattr(mgr, "_db_conn", lambda: conn)
        payload, code = api_mod._delete_automation_impl(aid)
        assert code == 200
        assert payload["deleted"] == aid and payload["name"] == "del-me"
        assert payload["schedules_deactivated"] == 2
        assert conn.committed and not conn.rolled_back
        # both scheduler tables flipped inactive, filtered by the automation param
        joined = " || ".join(conn.statements)
        assert "UPDATE j SET j.IsActive = 0" in joined
        assert "UPDATE s SET s.IsActive = 0" in joined
        assert joined.count("ParameterName = 'automation_id'") == 2
        # gone from every list; name freed for reuse
        assert all(a["automation_id"] != aid for a in mgr.list_automations())
        assert mgr.create_automation("del-me", "again", owner_user_id=7)[0]

    def test_delete_refused_while_run_in_flight(self, api_mod, mgr, monkeypatch):
        aid = self._make(mgr, "busy")
        monkeypatch.setattr(
            api_mod._runner, "list_active_runs",
            lambda: [{"run_id": "r-1", "automation_id": aid, "status": "running"}])
        payload, code = api_mod._delete_automation_impl(aid)
        assert code == 409
        assert "abort it" in payload["error"] and payload["active_run_id"] == "r-1"
        # nothing touched: still listed, not deleted
        assert any(a["automation_id"] == aid for a in mgr.list_automations())

    def test_delete_refused_when_schedule_shutoff_fails(self, api_mod, mgr, monkeypatch):
        aid = self._make(mgr, "sched-broken")
        monkeypatch.setattr(mgr, "_db_conn", lambda: self._RecordingConn(fail=True))
        payload, code = api_mod._delete_automation_impl(aid)
        assert code == 500
        assert "delete refused" in payload["error"]
        assert any(a["automation_id"] == aid for a in mgr.list_automations())

    def test_delete_unknown_id_404(self, api_mod):
        payload, code = api_mod._delete_automation_impl("nope")
        assert code == 404

    def test_mission_control_ui_has_delete_affordance(self, api_mod):
        page = api_mod._RUNS_PAGE
        assert "delAuto(event," in page                  # per-row button wired
        assert "ev.stopPropagation()" in page            # doesn't trigger the row's loadRuns
        assert "{method:'DELETE'}" in page               # hits the real endpoint
        assert "Delete automation" in page               # confirm dialog with consequences
        assert "schedules are deactivated" in page


class TestInternalManageDelete:
    """action='delete' rides the same guarded impl as the Mission Control
    button (james 2026-07-21: CC needs a delete tool too)."""

    @pytest.fixture
    def client(self, monkeypatch, mgr):
        from flask import Flask
        import automations.api as api_mod
        monkeypatch.setenv("API_KEY", "svc-key-test")
        runner = StubRunner(mgr)
        monkeypatch.setattr(runner, "list_active_runs", lambda: [])
        monkeypatch.setattr(api_mod, "_manager", mgr)
        monkeypatch.setattr(api_mod, "_runner", runner)
        monkeypatch.setattr(api_mod, "_tables_ensured", True)
        app = Flask(__name__)
        app.register_blueprint(api_mod.automations_bp)
        return app.test_client()

    def _post(self, client, payload, role=2):
        return client.post("/automations/api/internal/manage",
                           headers={"X-API-Key": "svc-key-test"},
                           json={"action": "delete",
                                 "user_context": {"user_id": 7, "role": role, "username": "dev"},
                                 "payload": payload})

    def test_delete_dispatches_to_guarded_impl(self, client, monkeypatch, mgr):
        import automations.api as api_mod
        seen = {}
        monkeypatch.setattr(api_mod, "_delete_automation_impl",
                            lambda aid: (seen.setdefault("aid", aid),
                                         {"deleted": aid, "name": "x",
                                          "schedules_deactivated": 1})[1:] and
                                        ({"deleted": aid, "name": "x",
                                          "schedules_deactivated": 1}, 200))
        r = self._post(client, {"automation_id": "abc-123"})
        assert r.status_code == 200
        assert seen["aid"] == "abc-123"
        assert r.get_json()["schedules_deactivated"] == 1

    def test_delete_still_role_gated(self, client):
        r = self._post(client, {"automation_id": "abc"}, role=1)
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Checkpoint -> My Approvals bridge (james 2026-07-21: queue + attachments)
# ---------------------------------------------------------------------------

from pathlib import Path


class _ApprovalConn:
    """Fake pyodbc conn recording executes; parameterizable rowcount."""
    def __init__(self):
        self.executed = []          # list of (sql-normalized, params)
        self.committed = False

    def cursor(self):
        outer = self

        class C:
            def execute(self, sql, *params):
                outer.executed.append((" ".join(sql.split()), params))
        return C()

    def commit(self): self.committed = True
    def rollback(self): pass
    def close(self): pass


class TestCheckpointApprovalBridge:
    @pytest.fixture
    def api_mod(self, monkeypatch, mgr):
        import automations.api as api_mod
        monkeypatch.setattr(api_mod, "_manager", mgr)
        monkeypatch.setattr(api_mod, "_runner", StubRunner(mgr))
        return api_mod

    # -- file validation ----------------------------------------------------
    def test_validate_files_accepts_workdir_relative(self, api_mod, tmp_path):
        (tmp_path / "out").mkdir()
        f = tmp_path / "out" / "report.csv"
        f.write_text("a,b\n1,2\n")
        atts, err = api_mod._validate_checkpoint_files(str(tmp_path), ["out/report.csv"])
        assert err is None and len(atts) == 1
        assert atts[0]["name"] == "report.csv"
        assert atts[0]["relpath"] == "out/report.csv"
        assert atts[0]["size"] == f.stat().st_size

    def test_validate_files_rejects_traversal_and_missing(self, api_mod, tmp_path):
        _, err = api_mod._validate_checkpoint_files(str(tmp_path), ["../evil.txt"])
        assert "outside the run's working directory" in err
        _, err2 = api_mod._validate_checkpoint_files(str(tmp_path), ["nope.txt"])
        assert "does not exist" in err2
        _, err3 = api_mod._validate_checkpoint_files(str(tmp_path), [f"f{i}" for i in range(11)])
        assert "at most 10" in err3

    # -- assignee default ---------------------------------------------------
    def test_assignee_defaults_to_requesting_user(self, api_mod):
        run = {"requested_by": 13}
        assert api_mod._resolve_checkpoint_assignee(run, None) == 13
        assert api_mod._resolve_checkpoint_assignee(run, 7) == 7
        assert api_mod._resolve_checkpoint_assignee(run, "not-a-user") == 13

    # -- queue row creation -------------------------------------------------
    def test_bridge_row_shape(self, api_mod, mgr):
        from automations import approval_store
        run = {"run_id": "run-x", "automation_id": "auto-x", "requested_by": 13}
        checkpoint = {"checkpoint_id": "abc123def456", "message": "send it?",
                      "attachments": [{"name": "r.xlsx", "relpath": "r.xlsx", "size": 10}]}
        req_id = api_mod._create_checkpoint_approval_row(run, checkpoint, 13)
        # rows are JSON files in the tenant _approvals/ sidecar — the Azure
        # app login has no DDL (ApprovalRequests FK + CREATE both refused
        # live), so the bridge uses the same sidecar pattern as checkpoints
        row = approval_store.get_row(mgr.base_path, req_id)
        assert row and row["status"] == "Pending"
        assert row["assigned_to_id"] == 13 and row["assigned_to_type"] == "user"
        assert row["description"] == "send it?"
        meta = json.loads(row["approval_data"])
        assert meta["source"] == "automation" and meta["run_id"] == "run-x"
        assert meta["checkpoint_id"] == "abc123def456"
        assert meta["attachments"] == [{"name": "r.xlsx", "size": 10}]

    # -- decision mirror ----------------------------------------------------
    def test_mirror_updates_pending_row(self, api_mod, mgr):
        from automations import approval_store
        seeded = approval_store.add_row(mgr.base_path, "t", "d", 13, "{}")
        api_mod._mirror_checkpoint_decision_to_queue(
            {"approval_request_id": seeded["request_id"]}, "proceed", 13)
        row = approval_store.get_row(mgr.base_path, seeded["request_id"])
        assert row["status"] == "Approved" and row["responded_by"] == "13"
        # first decision wins — a second mirror cannot flip it
        api_mod._mirror_checkpoint_decision_to_queue(
            {"approval_request_id": seeded["request_id"]}, "abort", 9)
        assert approval_store.get_row(mgr.base_path, seeded["request_id"])["status"] == "Approved"

    def test_mirror_noop_without_bridged_row(self, api_mod, mgr):
        # no approval_request_id on the checkpoint -> nothing raises, no row
        from automations import approval_store
        api_mod._mirror_checkpoint_decision_to_queue({"approval_request_id": None}, "abort", 1)
        assert approval_store.list_rows(mgr.base_path) == []

    # -- checkpoint store carries the new fields ----------------------------
    def test_checkpoint_record_attachments_and_request_id(self, tmp_path):
        from automations import checkpoints as cp
        c = cp.create_checkpoint(str(tmp_path), "m",
                                 attachments=[{"name": "a.txt", "relpath": "a.txt", "size": 1}])
        assert c["attachments"][0]["name"] == "a.txt"
        assert c["approval_request_id"] is None
        cp.set_approval_request_id(str(tmp_path), c["checkpoint_id"], "REQ-9")
        assert cp.get_checkpoint(str(tmp_path), c["checkpoint_id"])["approval_request_id"] == "REQ-9"

    # -- dead runs cancel their open queue rows -----------------------------
    def test_finish_run_cancels_open_approvals(self, mgr, tmp_path, monkeypatch):
        from automations import checkpoints as cp
        from automations import approval_store
        workdir = tmp_path / "wd"
        workdir.mkdir()
        (workdir / "run.log").write_text("")
        open_row = approval_store.add_row(mgr.base_path, "open", "d", 13, "{}")
        done_row = approval_store.add_row(mgr.base_path, "done", "d", 13, "{}")
        open_cp = cp.create_checkpoint(str(workdir), "undecided")
        cp.set_approval_request_id(str(workdir), open_cp["checkpoint_id"], open_row["request_id"])
        done_cp = cp.create_checkpoint(str(workdir), "decided")
        cp.set_approval_request_id(str(workdir), done_cp["checkpoint_id"], done_row["request_id"])
        cp.decide_checkpoint(str(workdir), done_cp["checkpoint_id"], "proceed", 1)

        runner = StubRunner(mgr)
        monkeypatch.setattr(runner, "_db_get_run",
                            lambda rid: {"run_id": rid, "log_path": str(workdir / "run.log")})
        runner._cancel_open_checkpoint_approvals("run-z")
        assert approval_store.get_row(mgr.base_path, open_row["request_id"])["status"] == "Cancelled"
        # the decided checkpoint's row is untouched (still whatever it was)
        assert approval_store.get_row(mgr.base_path, done_row["request_id"])["status"] == "Pending"

    # -- SDK sends files/assignee -------------------------------------------
    def test_sdk_checkpoint_posts_files_and_assignee(self, monkeypatch, tmp_path):
        import importlib
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "automations" / "sdk"))
        import aihub_runtime
        importlib.reload(aihub_runtime)
        captured = {}

        def fake_post(path, body):
            captured.update(body)
            raise aihub_runtime.AutomationRuntimeError("stop here")
        monkeypatch.setattr(aihub_runtime, "_runtime_post", fake_post)
        monkeypatch.setenv("AIHUB_RUN_TOKEN", "tok")
        monkeypatch.delenv("AIHUB_CHECKPOINTS_ENABLED", raising=False)
        with pytest.raises(aihub_runtime.AutomationRuntimeError):
            aihub_runtime.checkpoint("go?", files=["out/r.csv"], assignee=13)
        assert captured["files"] == ["out/r.csv"]
        assert captured["assignee"] == 13
        assert captured["message"] == "go?"

    # -- approvals endpoints (app.py) source contracts ----------------------
    def test_app_approvals_handle_automation_rows(self):
        src = Path(__file__).resolve().parents[2].joinpath("app.py").read_text(
            encoding="utf-8", errors="replace")
        # all four approval surfaces consult the file store: user list,
        # admin list, detail, and the decide handler
        assert src.count("from automations import approval_store") >= 4
        assert "approval_type='automation'" in src
        assert "'workflow' AS approval_type" in src
        assert "from automations.api import _decide_checkpoint" in src
        # queue decision routes to the paused run with the proceed/abort map
        assert '"proceed" if status == "approved" else "abort"' in src

    def test_approvals_ui_shows_type_and_attachments(self):
        page = Path(__file__).resolve().parents[2].joinpath(
            "templates", "approvals.html").read_text(encoding="utf-8", errors="replace")
        assert 'data-sort="approval_type">Type' in page
        assert "modalAttachments" in page
        assert "/attachments/" in page and "checkpoints/" in page

    def test_mission_control_gate_renders_attachments(self, api_mod):
        assert "p.attachments" in api_mod._RUNS_PAGE
        assert "/attachments/${encodeURIComponent(a.name)}" in api_mod._RUNS_PAGE


class TestAutomationNodeInDesigner:
    """james 2026-07-21: the Automation node existed in the ENGINE since P4a
    but never in the designer UI; Portal shipped with no palette shading."""

    def _root(self):
        return Path(__file__).resolve().parents[2]

    def test_palette_has_automation_and_versioned_pins(self):
        page = self._root().joinpath("templates", "workflow_tool.html").read_text(
            encoding="utf-8", errors="replace")
        assert "data-type=\"Automation\"" in page
        assert "filename='js/workflow.js', v=4" in page
        assert "filename='css/workflow_node_colors.css', v=3" in page

    def test_css_shades_portal_and_automation(self):
        css = self._root().joinpath("static", "css", "workflow_node_colors.css").read_text(
            encoding="utf-8", errors="replace")
        assert ".tool-item[data-type=\"Portal\"]" in css
        assert ".workflow-node[data-type=\"Portal\"]" in css
        assert ".tool-item[data-type=\"Automation\"]" in css
        assert ".workflow-node[data-type=\"Automation\"]" in css

    def test_designer_js_registers_automation(self):
        js = self._root().joinpath("static", "js", "workflow.js").read_text(
            encoding="utf-8", errors="replace")
        assert "'Automation': {" in js
        # the picker module supplies automationId/Name (fields carry no name attr)
        assert 'id="autoNodeSelect"' in js
        assert js.count("case 'Automation':") == 2  # create + load icon switches

    def test_engine_accepts_json_string_inputs(self):
        eng = self._root().joinpath("workflow_execution.py").read_text(
            encoding="utf-8", errors="replace")
        assert "isinstance(raw_inputs, str)" in eng
        assert "'inputs' is not valid JSON" in eng


class TestReviewItems:
    """Non-blocking per-exception review rows (james 2026-07-21: 'kick the
    exceptions out to the queue and move on') + My Approvals column sorting."""

    def test_sdk_review_item_posts_and_never_raises(self, monkeypatch):
        import importlib
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "automations" / "sdk"))
        import aihub_runtime
        importlib.reload(aihub_runtime)
        captured = {}
        monkeypatch.setattr(aihub_runtime, "_runtime_post",
                            lambda p, b: (captured.update(b), {"request_id": "REQ-R"})[1])
        monkeypatch.setenv("AIHUB_RUN_TOKEN", "tok")
        monkeypatch.delenv("AIHUB_CHECKPOINTS_ENABLED", raising=False)
        rid = aihub_runtime.review_item("emp not found", title="Exc — a.pdf",
                                        files=["exceptions/a.pdf"], assignee=13)
        assert rid == "REQ-R"
        assert captured["files"] == ["exceptions/a.pdf"] and captured["assignee"] == 13
        # queue failure NEVER breaks the batch
        def boom(p, b):
            raise RuntimeError("queue down")
        monkeypatch.setattr(aihub_runtime, "_runtime_post", boom)
        assert aihub_runtime.review_item("x") is None

    def test_review_endpoint_creates_row_and_serves_attachment(self, monkeypatch, mgr, tmp_path):
        from flask import Flask
        import automations.api as api_mod
        from automations import approval_store
        workdir = tmp_path / "wd"
        workdir.mkdir()
        (workdir / "run.log").write_text("")
        (workdir / "exceptions").mkdir()
        (workdir / "exceptions" / "bad.pdf").write_bytes(b"%PDF-1.4 fake")

        class RvRunner(StubRunner):
            def get_run(self, run_id):
                if run_id != "run-rv":
                    return None
                return {"run_id": "run-rv", "automation_id": "auto-rv", "status": "running",
                        "log_path": str(workdir / "run.log"), "requested_by": 13}
        monkeypatch.setenv("CC_JWT_SECRET", "rv-secret")
        monkeypatch.setattr(api_mod, "_manager", mgr)
        monkeypatch.setattr(api_mod, "_runner", RvRunner(mgr))
        app = Flask(__name__)
        app.register_blueprint(api_mod.automations_bp)
        client = app.test_client()
        from shared_auth import sign_automation_run_token
        tok = sign_automation_run_token("auto-rv", "run-rv", [], [], 300)
        r = client.post("/automations/api/runtime/review_item",
                        json={"token": tok, "message": "emp not found",
                              "title": "Dayforce exception — bad.pdf",
                              "files": ["exceptions/bad.pdf"]})
        assert r.status_code == 200, r.get_json()
        rid = r.get_json()["request_id"]
        assert r.get_json()["queued_for_review"] is True
        row = approval_store.get_row(mgr.base_path, rid)
        meta = json.loads(row["approval_data"])
        assert row["status"] == "Pending" and meta["kind"] == "review"
        assert meta["attachments"][0]["relpath"] == "exceptions/bad.pdf"
        # traversal refused
        r2 = client.post("/automations/api/runtime/review_item",
                         json={"token": tok, "message": "x", "files": ["../evil"]})
        assert r2.status_code == 400

    def test_app_decide_settles_review_without_run_decide(self):
        src = Path(__file__).resolve().parents[2].joinpath("app.py").read_text(
            encoding="utf-8", errors="replace")
        # review rows settle BEFORE the checkpoint-decide import runs
        review_at = src.index('automation_meta.get("kind") == "review"')
        decide_at = src.index("from automations.api import _decide_checkpoint")
        assert review_at < decide_at
        assert "Exception review recorded" in src

    def test_approvals_page_sortable_and_review_links(self):
        page = Path(__file__).resolve().parents[2].joinpath(
            "templates", "approvals.html").read_text(encoding="utf-8", errors="replace")
        assert page.count('class="sortable"') >= 8
        assert "applySort" in page and "sort-ind" in page
        assert "/automations/api/approvals/" in page      # review attachment href
        assert "Automation exception review" in page


class TestGroupRoutingAndSettingsPanel:
    """james 2026-07-21 round 3: approvals routable to GROUPS, Mission
    Control settings panel (additive to chat), designer Automation node
    picker + manifest-driven inputs."""

    # -- store: group visibility -------------------------------------------
    def test_group_rows_visible_to_members_only(self, mgr):
        from automations import approval_store
        g = approval_store.add_row(mgr.base_path, "g", "d", 55, "{}",
                                   assigned_to_type="group")
        u = approval_store.add_row(mgr.base_path, "u", "d", 13, "{}")
        anyone = approval_store.add_row(mgr.base_path, "open", "d", None, "{}")
        member = approval_store.list_rows(mgr.base_path, assigned_to_id=13,
                                          member_of_group_ids=[55])
        non_member = approval_store.list_rows(mgr.base_path, assigned_to_id=13,
                                              member_of_group_ids=[99])
        ids = lambda rows: {r["request_id"] for r in rows}
        assert ids(member) == {g["request_id"], u["request_id"], anyone["request_id"]}
        assert ids(non_member) == {u["request_id"], anyone["request_id"]}

    # -- api: group resolution + row shape ---------------------------------
    def test_resolve_assignee_group_by_name_and_id(self, monkeypatch, mgr):
        import automations.api as api_mod
        monkeypatch.setattr(api_mod, "_manager", mgr)

        class GConn:
            def cursor(self):
                class C:
                    def execute(self, sql, *p):
                        self._p = p
                    def fetchone(self):
                        return (7, "Payroll Administrators") if self._p else None
                return C()
            def close(self): pass
        monkeypatch.setattr(mgr, "_db_conn", lambda: GConn())
        assert api_mod._resolve_assignee_group("Payroll Administrators") == (7, "Payroll Administrators")
        assert api_mod._resolve_assignee_group(7) == (7, "Payroll Administrators")
        assert api_mod._resolve_assignee_group(None) == (None, None)

    def test_checkpoint_row_group_routing(self, monkeypatch, mgr):
        import automations.api as api_mod
        from automations import approval_store
        monkeypatch.setattr(api_mod, "_manager", mgr)
        run = {"run_id": "r-g", "automation_id": "a-g", "requested_by": 13}
        cp = {"checkpoint_id": "cpg", "message": "m", "attachments": []}
        rid = api_mod._create_checkpoint_approval_row(run, cp, 13, group=(7, "Payroll Administrators"))
        row = approval_store.get_row(mgr.base_path, rid)
        assert row["assigned_to_type"] == "group" and row["assigned_to_id"] == 7
        assert json.loads(row["approval_data"])["group_name"] == "Payroll Administrators"

    # -- SDK: group passthrough --------------------------------------------
    def test_sdk_group_passthrough(self, monkeypatch):
        import importlib
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "automations" / "sdk"))
        import aihub_runtime
        importlib.reload(aihub_runtime)
        captured = {}
        monkeypatch.setattr(aihub_runtime, "_runtime_post",
                            lambda p, b: (captured.update(b), {"request_id": "R"})[1])
        monkeypatch.setenv("AIHUB_RUN_TOKEN", "tok")
        monkeypatch.delenv("AIHUB_CHECKPOINTS_ENABLED", raising=False)
        aihub_runtime.review_item("x", assignee_group="Payroll Administrators")
        assert captured["assignee_group"] == "Payroll Administrators"

    # -- app.py contracts ---------------------------------------------------
    def test_user_approvals_include_group_membership(self):
        src = Path(__file__).resolve().parents[2].joinpath("app.py").read_text(
            encoding="utf-8", errors="replace")
        assert "SELECT group_id FROM [UserGroups] WHERE user_id = ?" in src
        assert "member_of_group_ids=my_groups" in src
        assert 'f"Group: {gname}"' in src

    # -- Mission Control settings panel ------------------------------------
    def test_settings_panel_wired(self, monkeypatch, mgr):
        import automations.api as api_mod
        page = api_mod._RUNS_PAGE
        assert "openSettings(event," in page
        assert "Save as new version" in page and "Promote latest" in page
        # saves go through the SAME versioned endpoint chat uses (PUT code)
        assert "method:'PUT'" in page and "/code'" in page
        assert "code:codeRes.code,manifest:m" in page.replace(" ", "")
        assert "same as chat edits" in page

    # -- designer node UX ---------------------------------------------------
    def test_designer_automation_picker(self):
        root = Path(__file__).resolve().parents[2]
        js = root.joinpath("static", "js", "workflow.js").read_text(
            encoding="utf-8", errors="replace")
        assert 'id="autoNodeSelect"' in js
        assert "AutomationNode.setup(currentConfig)" in js
        assert "AutomationNode.getConfig()" in js
        mod = root.joinpath("static", "js", "automation_node.js").read_text(
            encoding="utf-8", errors="replace")
        assert "/automations/api/list" in mod
        assert "manifest.inputs" in mod
        assert "auto-node-input" in mod and "data-name" in mod
        page = root.joinpath("templates", "workflow_tool.html").read_text(
            encoding="utf-8", errors="replace")
        assert "automation_node.js?v=" in page  # >= threshold, exact pins broke on every bump
        assert "filename='js/workflow.js', v=4" in page.replace('"', "'")


class TestGetAutomationIncludesManifest:
    """james 2026-07-21: the Settings panel showed 'no inputs declared' — the
    user-facing GET lacked the manifest (only internal manage 'get' had it)."""

    def test_route_source_includes_manifest(self):
        src = Path(__file__).resolve().parents[2].joinpath(
            "automations", "api.py").read_text(encoding="utf-8", errors="replace")
        route = src.split('def get_automation(automation_id):')[1].split("@automations_bp.route")[0]
        assert 'auto["manifest"]' in route and "get_manifest" in route


class TestPlatformAiSeam:
    """james 2026-07-21 round 4: aihub.llm (plain prompts) + aihub.ai_extract
    (JSON) brokered by the APP — central model resolution, no per-automation
    key/model; assignee accepts username; input descriptions rendered."""

    # -- central model resolution ------------------------------------------
    def test_model_resolution_chain(self, monkeypatch):
        import automations.api as api_mod
        monkeypatch.delenv("AUTOMATIONS_AI_MODEL", raising=False)
        monkeypatch.setattr(api_mod.cfg, "ANTHROPIC_MODEL", "claude-platform-default",
                            raising=False)
        assert api_mod._automations_ai_model(None) == "claude-platform-default"
        assert api_mod._automations_ai_model("  ") == "claude-platform-default"
        monkeypatch.setenv("AUTOMATIONS_AI_MODEL", "claude-ops-knob")
        assert api_mod._automations_ai_model(None) == "claude-ops-knob"
        assert api_mod._automations_ai_model("claude-explicit") == "claude-explicit"

    # -- image validation ---------------------------------------------------
    def test_ai_images_validated(self, tmp_path):
        import automations.api as api_mod
        (tmp_path / "p.png").write_bytes(b"\x89PNG fake")
        blocks, err = api_mod._load_ai_images(str(tmp_path), ["p.png"])
        assert err is None and blocks[0]["source"]["media_type"] == "image/png"
        _, err = api_mod._load_ai_images(str(tmp_path), ["../evil.png"])
        assert "outside the run's working directory" in err
        _, err = api_mod._load_ai_images(str(tmp_path), ["missing.png"])
        assert "does not exist" in err
        (tmp_path / "x.exe").write_bytes(b"MZ")
        _, err = api_mod._load_ai_images(str(tmp_path), ["x.exe"])
        assert "must be png/jpg/webp/gif" in err

    # -- endpoint: plain vs json modes (mocked LLM) -------------------------
    def _ai_client(self, monkeypatch, mgr, tmp_path, replies):
        from flask import Flask
        import automations.api as api_mod
        workdir = tmp_path / "wd"
        workdir.mkdir()
        (workdir / "run.log").write_text("")

        class AiRunner(StubRunner):
            def get_run(self, run_id):
                if run_id != "run-ai":
                    return None
                return {"run_id": "run-ai", "automation_id": "auto-ai", "status": "running",
                        "log_path": str(workdir / "run.log"), "requested_by": 13}
        monkeypatch.setenv("CC_JWT_SECRET", "ai-secret")
        monkeypatch.setattr(api_mod, "_manager", mgr)
        monkeypatch.setattr(api_mod, "_runner", AiRunner(mgr))
        monkeypatch.setattr(api_mod.cfg, "ANTHROPIC_API_KEY", "k-test", raising=False)

        calls = {"n": 0}

        class FakeResp:
            status_code = 200
            def __init__(self, text):
                self._text = text
            def json(self):
                return {"content": [{"type": "text", "text": self._text}]}

        def fake_post(url, headers=None, json=None, timeout=None):
            calls["n"] += 1
            calls["api_key"] = (headers or {}).get("x-api-key")
            calls["last_kwargs"] = json or {}
            assert "api.anthropic.com" in url
            return FakeResp(replies[min(calls["n"] - 1, len(replies) - 1)])
        import requests as _requests
        monkeypatch.setattr(_requests, "post", fake_post)
        app = Flask(__name__)
        app.register_blueprint(api_mod.automations_bp)
        from shared_auth import sign_automation_run_token
        return app.test_client(), sign_automation_run_token("auto-ai", "run-ai", [], [], 300), calls

    def test_plain_prompt_returns_text(self, monkeypatch, mgr, tmp_path):
        client, tok, calls = self._ai_client(monkeypatch, mgr, tmp_path, ["a fine summary"])
        r = client.post("/automations/api/runtime/ai",
                        json={"token": tok, "prompt": "Summarize."})
        body = r.get_json()
        assert r.status_code == 200, body
        assert body["text"] == "a fine summary" and "json" not in body
        assert calls["api_key"] == "k-test"          # the APP's key, not the run's
        assert "temperature" not in calls["last_kwargs"]  # reasoning models 400 on it

    def test_json_mode_parses_with_self_repair(self, monkeypatch, mgr, tmp_path):
        client, tok, calls = self._ai_client(monkeypatch, mgr, tmp_path,
                                             ["not json at all", '{"a": 1}'])
        r = client.post("/automations/api/runtime/ai",
                        json={"token": tok, "prompt": "Extract.", "schema": {"a": "number"}})
        body = r.get_json()
        assert r.status_code == 200, body
        assert body["json"] == {"a": 1}
        assert calls["n"] == 2                       # one self-repair retry

    def test_requires_prompt_and_valid_token(self, monkeypatch, mgr, tmp_path):
        client, tok, _ = self._ai_client(monkeypatch, mgr, tmp_path, ["x"])
        assert client.post("/automations/api/runtime/ai",
                           json={"token": tok}).status_code == 400
        assert client.post("/automations/api/runtime/ai",
                           json={"token": "bad", "prompt": "x"}).status_code == 403

    # -- SDK surface --------------------------------------------------------
    def test_sdk_llm_and_ai_extract_bodies(self, monkeypatch):
        import importlib
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "automations" / "sdk"))
        import aihub_runtime
        importlib.reload(aihub_runtime)
        seen = []
        monkeypatch.setattr(aihub_runtime, "_runtime_post",
                            lambda p, b: (seen.append((p, b)), {"text": "T", "json": {"k": 1}})[1])
        monkeypatch.setenv("AIHUB_RUN_TOKEN", "tok")
        assert aihub_runtime.llm("hello", system="be brief") == "T"
        p, b = seen[-1]
        assert p.endswith("/runtime/ai") and b["prompt"] == "hello"
        assert b["system"] == "be brief" and "json" not in b and "schema" not in b
        assert aihub_runtime.ai_extract("extract", images=["a.png"],
                                        schema={"k": "number"}) == {"k": 1}
        p2, b2 = seen[-1]
        assert b2["json"] is True and b2["schema"] == {"k": "number"}
        assert b2["images"] == ["a.png"]

    # -- assignee username resolution --------------------------------------
    def test_assignee_accepts_username(self, monkeypatch, mgr):
        import automations.api as api_mod
        monkeypatch.setattr(api_mod, "_manager", mgr)

        class UConn:
            def cursor(self):
                class C:
                    def execute(self, sql, *p):
                        self._u = p[0] if p else None
                    def fetchone(self):
                        return (42,) if self._u == "donnah" else None
                return C()
            def close(self): pass
        monkeypatch.setattr(mgr, "_db_conn", lambda: UConn())
        run = {"requested_by": 13}
        assert api_mod._resolve_assignee(run, "donnah") == (42, None)
        assert api_mod._resolve_assignee(run, 7) == (7, None)
        assert api_mod._resolve_assignee(run, None) == (13, None)
        uid, note = api_mod._resolve_assignee(run, "no-such-user")
        assert uid == 13 and "could not be resolved" in note

    # -- description hints rendered ----------------------------------------
    def test_input_descriptions_rendered(self):
        import automations.api as api_mod
        assert "inp.description" in api_mod._RUNS_PAGE
        mod = Path(__file__).resolve().parents[2].joinpath(
            "static", "js", "automation_node.js").read_text(encoding="utf-8", errors="replace")
        assert "inp.description" in mod


class TestOrphanReaper:
    """james 2026-07-21 ('Go for it'): heartbeat-based startup reaper — after
    4 orphaned-run incidents in one day, non-terminal runs with no living
    supervisor are finalized honestly, their open gates aborted, and their
    approval rows cancelled; runs supervised by ANY living process are never
    touched (fresh heartbeat)."""

    def _mk_run(self, tmp_path, name, hb_age=None, started_min_ago=60):
        import datetime
        import time as _t
        wd = tmp_path / name
        wd.mkdir()
        (wd / "run.log").write_text("")
        if hb_age is not None:
            hb = wd / "_heartbeat"
            hb.write_text(str(_t.time()))
            old = _t.time() - hb_age
            os.utime(hb, (old, old))
        return {"run_id": f"run-{name}", "automation_id": "auto-r", "status": "waiting",
                "log_path": str(wd / "run.log"),
                "started_at": datetime.datetime.utcnow()
                - datetime.timedelta(minutes=started_min_ago)}

    def test_reaps_stale_and_absent_spares_fresh_and_young(self, mgr, tmp_path, monkeypatch):
        from automations import checkpoints as cp
        runner = StubRunner(mgr)
        stale = self._mk_run(tmp_path, "stale", hb_age=600)
        fresh = self._mk_run(tmp_path, "fresh", hb_age=5)
        absent = self._mk_run(tmp_path, "absent", hb_age=None)
        young = self._mk_run(tmp_path, "young", hb_age=None, started_min_ago=1)
        # an undecided gate on the stale run must be aborted by the reaper
        gate = cp.create_checkpoint(os.path.dirname(stale["log_path"]), "pending gate")
        monkeypatch.setattr(runner, "_db_list_nonterminal_runs",
                            lambda: [stale, fresh, absent, young])
        finished = []
        monkeypatch.setattr(runner, "_db_finish_run",
                            lambda run_id, status, *a: finished.append((run_id, status, a[-1])))
        report = runner.reap_orphan_runs()
        reaped_ids = {r["run_id"] for r in report}
        assert reaped_ids == {"run-stale", "run-absent"}
        assert {f[0] for f in finished} == {"run-stale", "run-absent"}
        assert all(f[1] == "aborted" for f in finished)
        assert any("stale" in (f[2] or "") for f in finished)
        decided = cp.get_checkpoint(os.path.dirname(stale["log_path"]), gate["checkpoint_id"])
        assert decided["decision"] == "abort" and decided["decided_by"] == "system:reaper"

    def test_supervision_loop_writes_heartbeat(self):
        src = Path(__file__).resolve().parents[2].joinpath(
            "automations", "runner.py").read_text(encoding="utf-8", errors="replace")
        assert src.count("self._touch_heartbeat(workdir)") == 2  # pre-loop + status-poll tick

    def test_manage_reap_action(self, monkeypatch, mgr):
        from flask import Flask
        import automations.api as api_mod
        monkeypatch.setenv("API_KEY", "svc-key-reap")
        runner = StubRunner(mgr)
        monkeypatch.setattr(runner, "reap_orphan_runs",
                            lambda grace_s=300, stale_s=180: [{"run_id": "r1"}])
        monkeypatch.setattr(api_mod, "_manager", mgr)
        monkeypatch.setattr(api_mod, "_runner", runner)
        monkeypatch.setattr(api_mod, "_tables_ensured", True)
        app = Flask(__name__)
        app.register_blueprint(api_mod.automations_bp)
        r = app.test_client().post("/automations/api/internal/manage",
                                   headers={"X-API-Key": "svc-key-reap"},
                                   json={"action": "reap",
                                         "user_context": {"user_id": 7, "role": 2, "username": "dev"},
                                         "payload": {}})
        assert r.status_code == 200 and r.get_json()["count"] == 1

    def test_sdk_zombie_poll_aborts_on_dead_run(self, monkeypatch):
        import importlib
        import io
        import sys as _sys
        import urllib.error as _ue
        _sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "automations" / "sdk"))
        import aihub_runtime
        importlib.reload(aihub_runtime)
        monkeypatch.setenv("AIHUB_RUN_TOKEN", "tok")
        monkeypatch.setenv("AIHUB_RUNTIME_URL", "http://127.0.0.1:1")
        monkeypatch.delenv("AIHUB_CHECKPOINTS_ENABLED", raising=False)
        monkeypatch.setattr(aihub_runtime, "_runtime_post",
                            lambda p, b: {"checkpoint_id": "c1", "poll_seconds": 0})
        import time as _real_time
        monkeypatch.setattr(_real_time, "sleep", lambda s: None)

        def dead_run(*a, **k):
            raise _ue.HTTPError("u", 403, "forbidden", {},
                                io.BytesIO(b'{"error": "run token does not match a live run"}'))
        monkeypatch.setattr(aihub_runtime._urlrequest, "urlopen", dead_run)
        with pytest.raises(aihub_runtime.AutomationAborted):
            aihub_runtime.checkpoint("gate?")

    def test_app_startup_hook_present(self):
        src = Path(__file__).resolve().parents[2].joinpath("app.py").read_text(
            encoding="utf-8", errors="replace")
        assert "_automation_reaper_startup" in src
        assert "reap_orphan_runs()" in src
        assert 'name="automation-reaper"' in src


class TestClearStaleButton:
    """james 2026-07-21: user-triggerable reap from Mission Control."""

    def test_reap_route_and_button_wired(self, monkeypatch, mgr):
        import automations.api as api_mod
        page = api_mod._RUNS_PAGE
        assert "clearStale()" in page and "Clear stale runs" in page
        assert "/automations/api/reap" in page
        assert "never touched" in page          # honest semantics in the confirm
        # the route exists, is user-facing (gate), and returns the report
        src = Path(__file__).resolve().parents[2].joinpath(
            "automations", "api.py").read_text(encoding="utf-8", errors="replace")
        block = src.split('@automations_bp.route("/api/reap"')[1].split("@automations_bp.route")[0]
        assert "automations_gate" in block and "reap_orphan_runs()" in block


class TestMissionControlInlineJsParses:
    """Regression for the 'AUTOMATIONS list vanished' bug (james 2026-07-21):
    a confirm() string in the inline page carried a Python-interpreted
    newline inside single quotes -> JS SyntaxError -> the WHOLE script died
    and every JS-rendered section went blank. Pin: the served page's script
    must actually parse."""

    def test_inline_script_parses_with_node(self, tmp_path):
        import re
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            pytest.skip("node not available to parse-check the inline script")
        import automations.api as api_mod
        script = re.search(r"<script>(.*?)</script>", api_mod._RUNS_PAGE, re.S).group(1)
        p = tmp_path / "mc_page.js"
        p.write_text(script, encoding="utf-8")
        proc = subprocess.run([node, "--check", str(p)], capture_output=True, text=True)
        assert proc.returncode == 0, f"Mission Control inline JS is broken:\n{proc.stderr[:800]}"


class TestGlobFileOutputs:
    """james 2026-07-22: CC declared 'flagged_invoices_*.csv' for a
    timestamped output; the literal exists-check failed a run whose upload
    verified. Declared file outputs now support globs — the NEWEST match is
    verified; literal paths behave exactly as before."""

    def _verify(self, tmp_path, path_decl, verify=None):
        from automations.runner import verify_outputs
        manifest = {"outputs": [{"kind": "file", "path": path_decl,
                                 **({"verify": verify} if verify else {})}]}
        return verify_outputs(manifest, str(tmp_path), {})

    def test_glob_matches_newest_file(self, tmp_path):
        import os, time
        old = tmp_path / "flagged_invoices_20260721_090000.csv"
        old.write_text("h\r\na,1\n")
        t = time.time() - 3600
        os.utime(old, (t, t))
        new = tmp_path / "flagged_invoices_20260722_172220.csv"
        new.write_text("h\na,1\nb,2\n")
        outcome, report = self._verify(tmp_path, "flagged_invoices_*.csv",
                                       {"min_rows": 2})
        assert outcome == "success", report
        checks = report[0]["checks"]
        assert checks[0]["ok"] and "20260722_172220" in checks[0]["note"]
        assert checks[1] == {"check": "min_rows", "expected": 2, "actual": 2, "ok": True}

    def test_glob_no_match_fails_honestly(self, tmp_path):
        outcome, report = self._verify(tmp_path, "flagged_invoices_*.csv")
        assert outcome == "failed"
        assert report[0]["checks"][0]["ok"] is False
        assert "no file matched" in report[0]["checks"][0]["note"]

    def test_literal_paths_unchanged(self, tmp_path):
        (tmp_path / "report.csv").write_text("h\nrow\n")
        outcome, report = self._verify(tmp_path, "report.csv", {"min_rows": 1})
        assert outcome == "success"
        assert report[0]["checks"][0] == {"check": "exists", "ok": True}


class TestRemoteWildcardVerify:
    """james 2026-07-22 round 5: the starred candidate ('flagged_invoices_
    *.csv') was stat'ed literally — a Windows-hosted SFTP server rejects '*'
    (WinError 123 relayed), the check returned None, and the candidate loop
    treated None as 'server unreachable' and never tried the REAL filename.
    Patterns now match the listing (newest wins); post-connect errors are
    per-candidate misses (False), never loop-aborting Nones."""

    class _Attr:
        def __init__(self, name, size, mtime):
            self.filename, self.st_size, self.st_mtime = name, size, mtime

    def _fake_paramiko(self, monkeypatch, entries, stat_exc=None):
        import sys as _sys
        import types as _types
        outer = self

        class FakeSftp:
            def listdir_attr(self, d):
                return entries
            def stat(self, path):
                if stat_exc:
                    raise stat_exc
                for a in entries:
                    if path.endswith("/" + a.filename):
                        return a
                raise FileNotFoundError(path)

        class FakeClient:
            def set_missing_host_key_policy(self, p): pass
            def connect(self, *a, **k): pass
            def open_sftp(self): return FakeSftp()
            def close(self): pass
        mod = _types.ModuleType("paramiko")
        mod.SSHClient = FakeClient
        mod.AutoAddPolicy = object
        monkeypatch.setitem(_sys.modules, "paramiko", mod)

    def test_pattern_matches_newest_remote_file(self, monkeypatch):
        from automations.remote_verify import check_remote_output
        self._fake_paramiko(monkeypatch, [
            self._Attr("flagged_invoices_20260721_090000.csv", 100, 1000),
            self._Attr("flagged_invoices_20260722_140137.csv", 123, 2000),
            self._Attr("other.txt", 5, 3000),
        ])
        ok, note = check_remote_output("sftp_upload", "sftp://u:p@h:22", "/outgoing",
                                       "flagged_invoices_*.csv", {})
        assert ok is True
        assert "20260722_140137" in note and "matched 2 file(s)" in note

    def test_pattern_no_match_is_false_not_none(self, monkeypatch):
        from automations.remote_verify import check_remote_output
        self._fake_paramiko(monkeypatch, [self._Attr("other.txt", 5, 1)])
        ok, note = check_remote_output("sftp_upload", "sftp://u:p@h:22", "/outgoing",
                                       "flagged_invoices_*.csv", {})
        assert ok is False and "nothing matching" in note

    def test_weird_name_stat_error_is_candidate_miss(self, monkeypatch):
        from automations.remote_verify import check_remote_output
        self._fake_paramiko(monkeypatch, [], stat_exc=OSError(
            "The filename, directory name, or volume label syntax is incorrect"))
        ok, note = check_remote_output("sftp_upload", "sftp://u:p@h:22", "/outgoing",
                                       "literal:name.csv", {})
        # False -> the runner's candidate loop keeps trying other filenames
        assert ok is False and "not checkable" in note


class TestInlineBuilderChatRelay:
    """james 2026-07-22: the Workflow Designer's Automation node gains a
    'Build new with AI' drawer — chat relayed to the REAL CC authoring agent
    through /automations/api/builder-chat. Contracts: Developer-gated, signed
    CC JWT (never page-claimed identity), CC stays on 127.0.0.1, SSE streamed
    through, and the first-message primer teaches the inline-build shape."""

    def test_primer_first_message_skip_dry_run(self):
        from automations.api import _compose_inline_build_message
        out = _compose_inline_build_message("extract diagrams", True, True, "Invoice Intake")
        assert "Workflow Designer" in out
        assert '"Invoice Intake"' in out
        assert "Skip the dry-run" in out
        assert "Do NOT schedule" in out
        assert "manifest inputs" in out
        assert out.strip().endswith("My request: extract diagrams")

    def test_primer_first_message_keep_dry_run(self):
        from automations.api import _compose_inline_build_message
        out = _compose_inline_build_message("extract diagrams", True, False, "")
        assert "dry-run at the end is fine" in out
        assert "Skip the dry-run" not in out

    def test_later_turns_pass_through_untouched(self):
        from automations.api import _compose_inline_build_message
        assert _compose_inline_build_message("also crop them", False, True, "WF") == "also crop them"

    def test_route_contracts_in_source(self):
        import inspect
        import automations.api as api_mod
        src = inspect.getsource(api_mod)
        i = src.find('def builder_chat')
        assert i != -1
        gate_zone = src[max(0, i - 200):i]
        assert "@automations_gate" in gate_zone, "builder-chat must be Developer-gated"
        body = src[i:i + 4000]
        assert "sign_cc_token" in body, "identity must be the server-signed CC JWT"
        assert "127.0.0.1" in body, "CC must be reached on loopback only"
        assert "/api/chat" in body, "CC mounts chat at /api/chat (bare /chat 404s)"
        assert "stream=True" in body
        assert "text/event-stream" in body
        assert "Bearer" in body


class TestAutomationNodeBuilderDrawer:
    """UI contracts for automation_node.js v2 (all-in-one-file by design so
    workflow.js and the engine stay untouched)."""

    def _js(self):
        from pathlib import Path
        return Path(__file__).resolve().parents[2].joinpath(
            "static", "js", "automation_node.js").read_text(encoding="utf-8")

    def test_js_parses_with_node(self, tmp_path):
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            pytest.skip("node not available")
        from pathlib import Path
        src = Path(__file__).resolve().parents[2] / "static" / "js" / "automation_node.js"
        proc = subprocess.run([node, "--check", str(src)], capture_output=True, text=True)
        assert proc.returncode == 0, f"automation_node.js broken:\n{proc.stderr[:800]}"

    def test_build_button_and_drawer_wiring(self):
        js = self._js()
        assert "autoNodeBuildBtn" in js
        assert "Build new with AI" in js
        assert "/automations/api/builder-chat" in js

    def test_go_live_checkbox_defaults_checked(self):
        js = self._js()
        assert 'id="abdGoLive" checked' in js, "skip-dry-run/go-live must default ON (james)"

    def test_bind_promotes_deterministically(self):
        js = self._js()
        assert "/promote" in js, "bind must promote via the API, not chat prose"
        assert "current_version" in js  # detection keys on a SAVED version, not mere creation

    def test_variable_hint_present(self):
        js = self._js()
        assert "variable_name" in js  # ${variable_name} guidance on inputs

    def test_designer_pins_v2(self):
        from pathlib import Path
        html = Path(__file__).resolve().parents[2].joinpath(
            "templates", "workflow_tool.html").read_text(encoding="utf-8", errors="replace")
        import re
        m = re.search(r"automation_node\.js\?v=(\d+)", html)
        assert m and int(m.group(1)) >= 2


class TestAutomationNodeVariableSubstitution:
    """The engine already substitutes ${variable} references (dot paths,
    array indices) into Automation-node string inputs — pin the wiring so it
    can never silently regress; the drawer now advertises it in the UI."""

    def test_engine_wires_substitution_into_node_inputs(self):
        from pathlib import Path
        src = Path(__file__).resolve().parents[2].joinpath(
            "workflow_execution.py").read_text(encoding="utf-8", errors="replace")
        i = src.find("def _execute_automation_node")
        assert i != -1
        body = src[i:i + 6000]
        assert "_replace_variable_references" in body
