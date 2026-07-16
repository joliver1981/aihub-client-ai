"""
Code Flow checkpoint honesty — aihub.checkpoint() inside a Code Flow step.

A Code Flow step has no live AutomationRuns row, so the checkpoint HTTP endpoint
(which needs a supervised run to pause/resume against) returns 403 — which used
to KILL the step right before an irreversible upload (the live-test symptom:
"upload-to-sftp FAILED … could not open checkpoint: HTTP 403 FORBIDDEN"; the
SFTP upload itself never ran).

Fix: the runner signals AIHUB_CHECKPOINTS_ENABLED=0 for a Code Flow step and
aihub.checkpoint() auto-approves with an honest log line (gates take effect once
promoted to a supervised Automation). Real Automations keep full gate behavior.
"""
from __future__ import annotations

import importlib.util
import os
import sys

import pytest

from automations.runner import AutomationRunner
import automations.runner as runner_mod

pytestmark = pytest.mark.unit


class _CfgStub:
    AUTOMATIONS_ENV_CRED_INJECTION = False


@pytest.fixture(autouse=True)
def _stub_cfg(monkeypatch):
    monkeypatch.setattr(runner_mod, "_load_cfg", lambda: _CfgStub)


def _runner(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_mod, "get_app_path",
                        lambda *parts: os.path.join(str(tmp_path), *parts))
    r = AutomationRunner.__new__(AutomationRunner)
    r.manager = None
    r.tenant_id = "cptest"
    r.connection_string = "stub"
    r._resolve_python = lambda env_id: sys.executable
    r._resolve_connection = lambda n: None
    r._resolve_secret = lambda n: None
    return r


def _load_sdk():
    path = os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "..",
        "automations", "sdk", "aihub_runtime", "__init__.py"))
    spec = importlib.util.spec_from_file_location("_aihub_runtime_cp", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ─────────────────────────── end-to-end (the user's scenario) ────────────────
class TestCodeStepCheckpointE2E:
    def test_checkpoint_before_upload_auto_approves_and_step_succeeds(self, tmp_path, monkeypatch):
        r = _runner(tmp_path, monkeypatch)
        # Mirrors the failing live step: gate BEFORE the (would-be) upload, then
        # produce the declared output. With the fix the gate auto-approves and
        # the code past it runs, so the step SUCCEEDS.
        code = (
            "import aihub_runtime as aihub\n"
            "aihub.checkpoint('about to upload store_headcount.csv to /outgoing')\n"
            "with open('out.txt', 'w') as f:\n"
            "    f.write('uploaded')\n"
            "print('past the gate')\n"
        )
        manifest = {"outputs": [{"path": "out.txt"}], "timeout_seconds": 60}
        res = r.run_code_step(code, manifest, "upload-step", workdir=str(tmp_path / "run1"))

        # Regression: before the fix this was 'failed' (checkpoint 403 killed the
        # step before the upload). Now the gate auto-approves and the step runs
        # through (success/unverified are both "ran to completion"; the point is
        # it is NOT failed and the post-gate code executed).
        assert res["status"] != "failed", res
        assert res["exit_code"] == 0, res
        # the code AFTER the gate actually ran
        assert os.path.isfile(os.path.join(res["workdir"], "out.txt"))
        stdout = res.get("stdout_tail") or ""
        assert "past the gate" in stdout
        # honest disclosure that the gate was auto-approved (not silently dropped)
        assert "auto-approved" in stdout.lower()

    def test_child_env_marks_checkpoints_unsupported(self, tmp_path, monkeypatch):
        r = _runner(tmp_path, monkeypatch)
        captured = {}

        def _fake_supervise(cmd, workdir, env, timeout, run_id, events):
            captured["env"] = dict(env)
            return 0, "", "", False, False  # exit_code, out, err, timed_out, aborted

        monkeypatch.setattr(r, "_supervise", _fake_supervise)
        r.run_code_step("print(1)\n", {"timeout_seconds": 30}, "s", workdir=str(tmp_path / "r"))
        assert captured["env"].get("AIHUB_CHECKPOINTS_ENABLED") == "0"


# ─────────────────────────── SDK unit (both directions) ──────────────────────
class TestSdkCheckpointGate:
    def test_auto_approves_without_http_when_disabled(self, monkeypatch):
        sdk = _load_sdk()
        monkeypatch.setenv("AIHUB_CHECKPOINTS_ENABLED", "0")
        # if it tried to reach the endpoint this would raise
        monkeypatch.setattr(sdk, "_runtime_post",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("HTTP called")))
        assert sdk.checkpoint("gate") is True

    def test_requires_token_when_enabled(self, monkeypatch):
        sdk = _load_sdk()
        monkeypatch.setenv("AIHUB_CHECKPOINTS_ENABLED", "1")
        monkeypatch.delenv("AIHUB_RUN_TOKEN", raising=False)
        with pytest.raises(sdk.AutomationRuntimeError):
            sdk.checkpoint("gate")

    def test_default_unset_still_uses_endpoint_path(self, monkeypatch):
        # env var absent (older runner / promoted automation) → NOT auto-approved;
        # falls through to the token requirement (proves we didn't blanket-skip).
        sdk = _load_sdk()
        monkeypatch.delenv("AIHUB_CHECKPOINTS_ENABLED", raising=False)
        monkeypatch.delenv("AIHUB_RUN_TOKEN", raising=False)
        with pytest.raises(sdk.AutomationRuntimeError):
            sdk.checkpoint("gate")
