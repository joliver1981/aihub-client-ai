"""
Code Flows authoring — compiler + in-process dry-run walk (codeflows/compiler.py).
Real subprocess execution via the Automations runner, DB-free.
"""
from __future__ import annotations

import os
import sys

import pytest

from automations.runner import AutomationRunner
import automations.runner as runner_mod
from codeflows import compiler

pytestmark = pytest.mark.unit


class _CfgStub:
    AUTOMATIONS_ENV_CRED_INJECTION = False


@pytest.fixture(autouse=True)
def _stub_cfg(monkeypatch):
    monkeypatch.setattr(runner_mod, "_load_cfg", lambda: _CfgStub)


def _runner():
    r = AutomationRunner.__new__(AutomationRunner)
    r.manager = None
    r.tenant_id = "cftest"
    r.connection_string = "stub"
    r._resolve_python = lambda env_id: sys.executable
    r._resolve_connection = lambda name: None
    r._resolve_secret = lambda name: None
    return r


# ------------------------------------------------------------------- validate

class TestValidate:
    def test_valid(self):
        defn = {"name": "f", "steps": [{"id": "s1", "code": "print(1)"}], "edges": []}
        ok, errs = compiler.validate_definition(defn)
        assert ok, errs

    def test_missing_name_and_code(self):
        ok, errs = compiler.validate_definition({"steps": [{"id": "s1"}]})
        assert not ok
        assert any("name" in e for e in errs) and any("no code" in e for e in errs)

    def test_edge_to_unknown_step(self):
        defn = {"name": "f", "steps": [{"id": "s1", "code": "x"}],
                "edges": [{"from": "s1", "to": "ghost", "on": "pass"}]}
        ok, errs = compiler.validate_definition(defn)
        assert not ok and any("ghost" in e for e in errs)

    def test_bad_edge_type_and_dup_ids(self):
        defn = {"name": "f",
                "steps": [{"id": "s1", "code": "x"}, {"id": "s1", "code": "y"}],
                "edges": [{"from": "s1", "to": "s1", "on": "sideways"}]}
        ok, errs = compiler.validate_definition(defn)
        assert not ok
        assert any("duplicate" in e for e in errs) and any("'on'" in e for e in errs)


# -------------------------------------------------------------------- compile

class TestCompile:
    def test_shape_matches_engine_contract(self):
        defn = {
            "name": "recon",
            "steps": [
                {"id": "s1", "name": "pull", "code": "print(1)", "connections": ["ERPDB"],
                 "outputs": [{"kind": "file", "path": "out.csv"}]},
                {"id": "s2", "name": "upload", "code": "print(2)", "secrets": ["SFTP"]},
                {"id": "alert", "name": "alert", "code": "print('!')"},
            ],
            "edges": [{"from": "s1", "to": "s2", "on": "pass"},
                      {"from": "s1", "to": "alert", "on": "fail"}],
        }
        wf = compiler.compile_to_workflow(defn)
        assert wf["kind"] == "code_flow"
        assert {n["id"] for n in wf["nodes"]} == {"s1", "s2", "alert"}
        assert all(n["type"] == "Code Step" for n in wf["nodes"])
        s1 = next(n for n in wf["nodes"] if n["id"] == "s1")
        assert s1["isStart"] is True and s1["label"] == "pull"
        assert s1["config"]["connections"] == ["ERPDB"]
        assert s1["config"]["outputVariable"] == "s1_out" and s1["config"]["filesVariable"] == "s1_files"
        # edges are flat source/target/type (the engine's contract, NOT from/to)
        assert {"source": "s1", "target": "s2", "type": "pass",
                "sourceAnchor": "Right", "targetAnchor": "Left"} in wf["connections"]
        assert any(c["type"] == "fail" and c["target"] == "alert" for c in wf["connections"])
        # exactly one start
        assert sum(1 for n in wf["nodes"] if n["isStart"]) == 1


# ------------------------------------------------------------------- dry-run

class TestDryRunWalk:
    def test_two_step_happy_path_passes_file_forward(self, tmp_path):
        r = _runner()
        defn = {
            "name": "chain",
            "steps": [
                {"id": "s1", "name": "produce", "code":
                    "with open('data.txt','w') as f: f.write('hello')\n",
                 "outputs": [{"kind": "file", "path": "data.txt"}]},
                {"id": "s2", "name": "consume", "code":
                    "import aihub_runtime as aihub\n"
                    "src = aihub.input('src')\n"
                    "data = open(src).read()\n"
                    "with open('echo.txt','w') as f: f.write(data.upper())\n",
                 "inputs": [{"name": "src", "type": "string", "default": "${s1_files[0]}"}],
                 "outputs": [{"kind": "file", "path": "echo.txt"}]},
            ],
            "edges": [{"from": "s1", "to": "s2", "on": "pass"}],
        }
        res = compiler.dry_run_walk(defn, r, str(tmp_path / "flow1"))
        assert res["status"] == "success", res
        assert [s["status"] for s in res["steps"]] == ["success", "success"]
        # s2 actually read s1's file (proves ${s1_files[0]} substitution across steps)
        s2 = res["steps"][1]
        echo = os.path.join(s2["output_files"][0])
        assert open(echo).read() == "HELLO"

    def test_failure_routes_to_fail_edge_then_stops(self, tmp_path):
        r = _runner()
        defn = {
            "name": "guarded",
            "steps": [
                {"id": "s1", "name": "boom", "code": "import sys; sys.exit(3)\n"},
                {"id": "alert", "name": "alert", "code":
                    "with open('alerted.txt','w') as f: f.write('notified')\n",
                 "outputs": [{"kind": "file", "path": "alerted.txt"}]},
                {"id": "s2", "name": "never", "code": "print('should not run')\n"},
            ],
            "edges": [{"from": "s1", "to": "s2", "on": "pass"},
                      {"from": "s1", "to": "alert", "on": "fail"}],
        }
        res = compiler.dry_run_walk(defn, r, str(tmp_path / "flow2"))
        visited = [s["step_id"] for s in res["steps"]]
        assert visited == ["s1", "alert"]          # failure took the fail edge
        assert "s2" not in visited                 # pass edge NOT taken on failure
        assert res["steps"][0]["status"] == "failed"
        assert res["steps"][1]["status"] == "success"
        # the alert step ran to a clean end -> overall the flow handled the failure
        assert res["status"] == "success"

    def test_unhandled_failure_fails_the_flow(self, tmp_path):
        r = _runner()
        defn = {
            "name": "unhandled",
            "steps": [{"id": "s1", "name": "boom", "code": "import sys; sys.exit(1)\n"}],
            "edges": [],
        }
        res = compiler.dry_run_walk(defn, r, str(tmp_path / "flow3"))
        assert res["status"] == "failed"
        assert res["steps"][0]["status"] == "failed"

    def test_missing_declared_output_is_failed(self, tmp_path):
        r = _runner()
        defn = {"name": "liar",
                "steps": [{"id": "s1", "name": "liar", "code": "print('nothing produced')\n",
                           "outputs": [{"kind": "file", "path": "missing.csv"}]}],
                "edges": []}
        res = compiler.dry_run_walk(defn, r, str(tmp_path / "flow4"))
        assert res["status"] == "failed" and res["steps"][0]["status"] == "failed"

    def test_invalid_definition_errors(self, tmp_path):
        r = _runner()
        res = compiler.dry_run_walk({"steps": [{"id": "s1"}]}, r, str(tmp_path / "flow5"))
        assert res["status"] == "error"
