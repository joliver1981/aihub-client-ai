"""
Code Flows — v0 tests for the inline Code Step execution (AutomationRunner.
run_code_step / _execute_code). The Code Step is the atom of a Code Flow: an
LLM-authored Python step that lives in a workflow node and runs through the
Automations runner machinery (env + aihub_runtime SDK + output verification +
honest tri-state). See docs/code-flows-plan.md.

Real subprocess execution (sys.executable), DB-free — the shared executor is
exercised end to end.
"""
from __future__ import annotations

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
    # _execute_code reads cfg for the legacy env-inject flag; code steps force
    # it on regardless, but keep the config import out of the test path.
    monkeypatch.setattr(runner_mod, "_load_cfg", lambda: _CfgStub)


def _runner():
    r = AutomationRunner.__new__(AutomationRunner)
    r.manager = None                       # run_code_step needs no manager
    r.tenant_id = "codeflowtest"
    r.connection_string = "stub"
    r._resolve_python = lambda env_id: sys.executable
    r._resolve_connection = lambda name: None
    r._resolve_secret = lambda name: None
    return r


class TestCodeStepRunner:
    def test_success_with_sdk_input_and_verified_output(self, tmp_path):
        r = _runner()
        code = (
            "import aihub_runtime as aihub\n"
            "period = aihub.input('period', 'none')\n"
            "with open('out.txt', 'w') as f:\n"
            "    f.write('period=' + period)\n"
            "aihub.log('wrote out.txt')\n"
        )
        manifest = {
            "inputs": [{"name": "period", "type": "string", "default": "2026-07"}],
            "outputs": [{"kind": "file", "path": "out.txt", "verify": {"exists": True}}],
            "timeout_seconds": 60,
        }
        res = r.run_code_step(code, manifest, "extract", inputs={"period": "2026-07"},
                              workdir=str(tmp_path / "run1"))
        assert res["status"] == "success", res
        assert "out.txt" in res["output_files"]
        assert open(os.path.join(res["workdir"], "out.txt")).read() == "period=2026-07"
        # events sidecar was written (powers the live per-node viewer)
        assert os.path.isfile(os.path.join(res["workdir"], "events.jsonl"))

    def test_nonzero_exit_is_failed(self, tmp_path):
        r = _runner()
        res = r.run_code_step("import sys\nsys.exit(2)\n", {"outputs": []},
                              "boom", workdir=str(tmp_path / "run2"))
        assert res["status"] == "failed" and res["exit_code"] == 2

    def test_missing_declared_output_is_failed_even_on_exit_zero(self, tmp_path):
        """Honesty carries into code steps: exit 0 but no declared file -> failed."""
        r = _runner()
        res = r.run_code_step("print('did nothing')\n",
                              {"outputs": [{"kind": "file", "path": "nope.csv"}]},
                              "liar", workdir=str(tmp_path / "run3"))
        assert res["status"] == "failed"

    def test_credentials_delivered_to_step_via_env(self, tmp_path):
        """A code step resolves EXISTING connections/secrets — reaches the code
        (env-var delivery for the no-run-row step path, v0)."""
        r = _runner()
        r._resolve_connection = lambda n: "Driver=X;PWD=stepcred;" if n == "ERPDB" else None
        r._resolve_secret = lambda n: "sftp://u:p@h:22" if n == "ACME" else None
        code = (
            "import aihub_runtime as aihub\n"
            "with open('c.txt', 'w') as f:\n"
            "    f.write(aihub.connection('ERPDB') + '|' + aihub.secret('ACME'))\n"
        )
        manifest = {"connections": ["ERPDB"], "secrets": ["ACME"],
                    "outputs": [{"kind": "file", "path": "c.txt"}], "timeout_seconds": 60}
        res = r.run_code_step(code, manifest, "creds", workdir=str(tmp_path / "run4"))
        assert res["status"] == "success", res
        content = open(os.path.join(res["workdir"], "c.txt")).read()
        assert "PWD=stepcred" in content and "sftp://u:p@h:22" in content

    def test_missing_connection_fails_preflight(self, tmp_path):
        r = _runner()  # _resolve_connection returns None
        res = r.run_code_step("print(1)\n", {"connections": ["NOPE"], "outputs": []},
                              "x", workdir=str(tmp_path / "run5"))
        assert res["status"] == "failed" and "NOPE" in res["error"]

    def test_undeclared_input_rejected(self, tmp_path):
        r = _runner()
        res = r.run_code_step("print(1)\n",
                              {"inputs": [{"name": "a", "type": "string"}], "outputs": []},
                              "x", inputs={"a": "1", "typo": "2"}, workdir=str(tmp_path / "run6"))
        assert res["status"] == "error" and "typo" in res["error"]


class TestCodeStepEngineRegistration:
    """The engine must dispatch 'Code Step' and it must be a valid node type."""

    def test_code_step_is_a_valid_node_type(self):
        from system_prompts import VALID_WORKFLOW_NODE_TYPES
        assert "Code Step" in VALID_WORKFLOW_NODE_TYPES

    def test_engine_dispatches_code_step(self):
        import inspect
        import workflow_execution
        src = inspect.getsource(workflow_execution)
        assert "node_type == 'Code Step'" in src
        assert "_execute_code_step_node" in src


class TestAutomationRegressionAfterRefactor:
    """The asset path must still route through the shared executor unchanged —
    _execute is now a thin wrapper over _execute_code."""

    def test_execute_delegates_to_execute_code(self):
        import inspect
        src = inspect.getsource(AutomationRunner._execute)
        assert "_execute_code(" in src
        assert "identity=" in src
