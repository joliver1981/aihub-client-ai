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
