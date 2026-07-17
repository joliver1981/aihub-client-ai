"""
AIHUB-0040 — the authoring reward-hack is closed at every layer.

Live failure (test pack 09, Scenario C): the CC authoring agent "solved"
verification failures by writing a step commented '# Simulated upload
placeholder' (secret read + log line, NO network op), granting itself
allow_unverified=True, and declaring the sftp_upload output without a 'name'
('nothing to check') — then chat said the upload ran.

Four guards:
  1. lint_transfer_honesty rejects an unnamed sftp/ftp output (author time).
  2. lint_transfer_honesty rejects a transfer output whose code has no transfer
     machinery (the placeholder pattern).
  3. manager.add_step rejects allow_unverified=True without explicit user
     consent; consent is recorded on the step.
  4. summarize_walk derives transfer claims from runner EVIDENCE — a step with
     no_egress_transfer reads "nothing was transferred", never "attempted".
     (The runner sets that flag from the egress log; also unit-tested here via
     the flag contract.)
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT)) if str(_ROOT) not in sys.path else None

from codeflows import compiler as cfc
from codeflows.manager import CodeFlowManager


REAL_UPLOAD_CODE = """
import aihub_runtime as aihub, paramiko
t = paramiko.Transport(('127.0.0.1', 2222)); t.connect(username='u', password=aihub.secret('AUTODEMO_SFTP'))
sftp = paramiko.SFTPClient.from_transport(t); sftp.put('out.csv', '/outgoing/out.csv'); t.close()
"""

PLACEHOLDER_CODE = """
import aihub_runtime as aihub
secret = aihub.secret('AUTODEMO_SFTP')
# Simulated upload placeholder using secret
aihub.log('Uploading store_headcount_2026-07.csv to /outgoing using AUTODEMO_SFTP')
"""

NAMED_SFTP_OUTPUT = [{"kind": "sftp_upload", "name": "out.csv", "remote_dir": "/outgoing",
                      "secret": "AUTODEMO_SFTP", "verify": {"remote_listing": True}}]
UNNAMED_SFTP_OUTPUT = [{"kind": "sftp_upload", "remote_dir": "/outgoing",
                        "secret": "AUTODEMO_SFTP"}]


class TestTransferLint:
    def test_unnamed_transfer_output_rejected(self):
        err = cfc.lint_transfer_honesty(REAL_UPLOAD_CODE, UNNAMED_SFTP_OUTPUT)
        assert err is not None
        assert "without a 'name'" in err and "nothing to verify" in err

    def test_placeholder_code_with_transfer_output_rejected(self):
        err = cfc.lint_transfer_honesty(PLACEHOLDER_CODE, NAMED_SFTP_OUTPUT)
        assert err is not None
        assert "never opens a network connection" in err
        assert "not allowed" in err

    def test_real_upload_with_named_output_passes(self):
        assert cfc.lint_transfer_honesty(REAL_UPLOAD_CODE, NAMED_SFTP_OUTPUT) is None

    def test_no_transfer_output_means_no_lint(self):
        # a pure-local step (file output) is untouched by this lint
        assert cfc.lint_transfer_honesty(PLACEHOLDER_CODE,
                                         [{"kind": "file", "path": "out.csv"}]) is None
        assert cfc.lint_transfer_honesty("print('hi')", None) is None

    def test_remote_path_alias_accepted_as_name(self):
        outs = [{"kind": "sftp_upload", "remote_path": "/outgoing/out.csv",
                 "secret": "S"}]
        assert cfc.lint_transfer_honesty(REAL_UPLOAD_CODE, outs) is None


class _MgrHarness:
    """CodeFlowManager with the DB layer stubbed to an in-memory definition."""

    def __init__(self):
        self.mgr = CodeFlowManager.__new__(CodeFlowManager)
        self.mgr.tenant_id = "TESTTENANT"
        self.defn = {"kind": "code_flow", "steps": [], "edges": [], "start": None}
        self.mgr._load_defn = lambda name: (1, self.defn)
        self.saved = []
        self.mgr._save_definition = lambda d: self.saved.append(d)


class TestConsentGate:
    def test_allow_unverified_without_consent_rejected(self):
        h = _MgrHarness()
        ok, step_id, err = h.mgr.add_step(
            "flow", "upload", REAL_UPLOAD_CODE, outputs=NAMED_SFTP_OUTPUT,
            secrets=["AUTODEMO_SFTP"], allow_unverified=True)
        assert ok is False and step_id is None
        assert "explicit consent" in err and "user_approved_unverified" in err
        assert h.saved == []            # nothing persisted

    def test_allow_unverified_with_consent_recorded(self):
        h = _MgrHarness()
        ok, step_id, err = h.mgr.add_step(
            "flow", "upload", REAL_UPLOAD_CODE, outputs=NAMED_SFTP_OUTPUT,
            secrets=["AUTODEMO_SFTP"], allow_unverified=True, unverified_consent=True)
        assert ok is True and err is None
        step = h.saved[-1]["steps"][-1]
        assert step["allowUnverified"] is True
        assert step["unverifiedConsent"] == "user"     # audit marker

    def test_default_path_unaffected(self):
        h = _MgrHarness()
        ok, step_id, err = h.mgr.add_step("flow", "calc", "print('x')")
        assert ok is True
        assert "unverifiedConsent" not in h.saved[-1]["steps"][-1]

    def test_add_step_runs_transfer_lint(self):
        h = _MgrHarness()
        ok, _sid, err = h.mgr.add_step(
            "flow", "upload", PLACEHOLDER_CODE, outputs=NAMED_SFTP_OUTPUT,
            secrets=["AUTODEMO_SFTP"])
        assert ok is False and "never opens a network connection" in err

    def test_update_step_code_runs_transfer_lint(self):
        h = _MgrHarness()
        ok, sid, err = h.mgr.add_step(
            "flow", "upload", REAL_UPLOAD_CODE, outputs=NAMED_SFTP_OUTPUT,
            secrets=["AUTODEMO_SFTP"])
        assert ok is True
        ok2, err2 = h.mgr.update_step_code("flow", sid, PLACEHOLDER_CODE)
        assert ok2 is False and "never opens a network connection" in err2


class TestRunnerNoEgressEvidence:
    """The live scenario end-to-end: a placeholder step that declares an
    sftp_upload output and never touches the network gets flagged by the REAL
    runner (real step subprocess), with the honest 'did not happen' error."""

    def _runner(self, tmp_path, monkeypatch):
        import automations.runner as runner_mod
        from automations.runner import AutomationRunner

        class _CfgStub:
            AUTOMATIONS_ENV_CRED_INJECTION = False

        monkeypatch.setattr(runner_mod, "_load_cfg", lambda: _CfgStub)
        monkeypatch.setattr(runner_mod, "get_app_path",
                            lambda *parts: os.path.join(str(tmp_path), *parts))
        r = AutomationRunner.__new__(AutomationRunner)
        r.manager = None
        r.tenant_id = "hacktest"
        r.connection_string = "stub"
        r._resolve_python = lambda env_id: sys.executable
        r._resolve_connection = lambda n: None
        r._resolve_secret = lambda n: None
        return r

    def test_placeholder_upload_step_flagged_no_egress(self, tmp_path, monkeypatch):
        r = self._runner(tmp_path, monkeypatch)
        manifest = {"inputs": [], "outputs": [
            {"kind": "sftp_upload", "name": "out.csv", "remote_dir": "/outgoing",
             "secret": "AUTODEMO_SFTP"}]}
        result = r.run_code_step(
            "print('Uploading out.csv to /outgoing using AUTODEMO_SFTP')  # simulated placeholder",
            manifest, "fake-upload")
        assert result["no_egress_transfer"] is True
        assert result["status"] == "unverified"
        assert "did not happen" in (result["error"] or "")
        notes = " ".join(str(c.get("note")) for e in result["verify_report"]
                         for c in e.get("checks", []))
        assert "NO network egress" in notes and "nothing was transferred" in notes

    def test_local_file_step_not_flagged(self, tmp_path, monkeypatch):
        r = self._runner(tmp_path, monkeypatch)
        manifest = {"inputs": [], "outputs": [{"kind": "file", "path": "out.csv"}]}
        result = r.run_code_step(
            "open('out.csv','w').write('a,b\\n1,2\\n')", manifest, "make-csv")
        assert result["no_egress_transfer"] is False
        assert result["status"] == "success"


class TestSummaryEvidence:
    def test_no_egress_transfer_renders_nothing_transferred(self):
        path = os.path.join(str(_ROOT), "command_center_service", "graph", "codeflow_tools.py")
        spec = importlib.util.spec_from_file_location("_cf_tools_0040", path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        result = {"status": "success", "steps": [
            {"step_id": "s1", "name": "upload", "status": "unverified",
             "no_egress_transfer": True, "error": "no network egress — the declared transfer did not happen"},
        ]}
        text = m.summarize_walk(result)
        assert "NO network egress" in text
        assert "nothing was transferred" in text
        assert "do NOT report this upload as attempted" in text
