"""
Regression tests pinning the fixes from the Code Flows adversarial review
(ws7gscy67). Each test names the finding it guards.

DB-free: the walk uses a tiny stub runner (no subprocess) where the point is
routing/status logic; the engine-node test drives _execute_code_step_node with
a stub AutomationRunner and stubbed persistence.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

from codeflows import compiler

pytestmark = pytest.mark.unit


class _StubRunner:
    """run_code_step returns a canned result; records the (step_name, inputs,
    workdir) it was called with so tests can assert threading/labels."""
    def __init__(self, results):
        # results: list of dicts (one per call, in order) OR a single dict reused
        self._results = results
        self._i = 0
        self.calls = []

    def run_code_step(self, code, manifest, step_name, inputs=None,
                      environment_id=None, run_id=None, workdir=None):
        self.calls.append({"step_name": step_name, "inputs": inputs or {}, "workdir": workdir})
        if isinstance(self._results, list):
            r = self._results[min(self._i, len(self._results) - 1)]
            self._i += 1
        else:
            r = self._results
        out = dict(r)
        out.setdefault("workdir", workdir or "")
        out.setdefault("output_files", [])
        return out


# ---------------------------------------------------- #7 max_steps truncation

def test_walk_truncated_at_max_steps_is_failed_not_success():
    r = _StubRunner({"status": "success"})
    # 3 linear steps, cap the walk at 2 -> the 3rd never runs
    defn = {"name": "long",
            "steps": [{"id": "s1", "code": "x"}, {"id": "s2", "code": "x"}, {"id": "s3", "code": "x"}],
            "edges": [{"from": "s1", "to": "s2", "on": "pass"},
                      {"from": "s2", "to": "s3", "on": "pass"}]}
    res = compiler.dry_run_walk(defn, r, "/tmp/x", max_steps=2)
    assert res["status"] == "failed"
    assert any("max_steps" in (s.get("error") or "") for s in res["steps"])


# ------------------------------------------------------------ #25 cycle guard

def test_walk_cycle_guard_terminates_and_fails():
    r = _StubRunner({"status": "success"})
    defn = {"name": "loop",
            "steps": [{"id": "a", "code": "x"}, {"id": "b", "code": "x"}],
            "edges": [{"from": "a", "to": "b", "on": "complete"},
                      {"from": "b", "to": "a", "on": "complete"}]}
    res = compiler.dry_run_walk(defn, r, "/tmp/x", max_steps=100)
    assert res["status"] == "failed"
    assert any("cycle guard" in (s.get("error") or "") for s in res["steps"])
    assert len(res["steps"]) < 100  # bounded


# ------------------------------------- #9/#24 unverified routing + allowUnverified

def test_unverified_step_fails_without_optin():
    r = _StubRunner({"status": "unverified", "exit_code": 0})
    defn = {"name": "u", "steps": [{"id": "s1", "code": "x"}], "edges": []}
    res = compiler.dry_run_walk(defn, r, "/tmp/x")
    assert res["status"] == "failed"          # unverified is NOT a pass by default


def test_unverified_step_passes_with_allow_unverified():
    r = _StubRunner({"status": "unverified", "exit_code": 0})
    defn = {"name": "u",
            "steps": [{"id": "s1", "code": "x", "allowUnverified": True}], "edges": []}
    res = compiler.dry_run_walk(defn, r, "/tmp/x")
    assert res["status"] == "success"         # opted in -> treated as pass


def test_allow_unverified_compiles_into_node_config():
    defn = {"name": "u",
            "steps": [{"id": "s1", "code": "x", "allowUnverified": True}], "edges": []}
    wf = compiler.compile_to_workflow(defn)
    assert wf["nodes"][0]["config"]["allowUnverified"] is True


# ------------------------------------ #21 _next_step complete-fallback + ordering

class TestNextStepRouting:
    def test_complete_edge_followed_on_success_and_failure(self):
        edges = [{"from": "a", "to": "b", "on": "complete"}]
        assert compiler._next_step(edges, "a", True) == "b"
        assert compiler._next_step(edges, "a", False) == "b"

    def test_primary_preferred_over_complete(self):
        edges = [{"from": "a", "to": "b", "on": "pass"}, {"from": "a", "to": "c", "on": "complete"}]
        assert compiler._next_step(edges, "a", True) == "b"     # pass beats complete
        assert compiler._next_step(edges, "a", False) == "c"    # no fail edge -> complete fallback

    def test_first_matching_edge_wins(self):
        edges = [{"from": "a", "to": "b", "on": "pass"}, {"from": "a", "to": "c", "on": "pass"}]
        assert compiler._next_step(edges, "a", True) == "b"

    def test_no_route_returns_none(self):
        assert compiler._next_step([], "a", True) is None


# ------------------------------ #4 whole-object substitution parity with engine

class TestSubstitutionParity:
    def test_whole_dict_is_json_stringified(self):
        assert compiler._substitute("${x}", {"x": {"k": 1}}) == json.dumps({"k": 1})

    def test_whole_list_is_json_stringified(self):
        assert compiler._substitute("${x}", {"x": [1, 2]}) == "[1, 2]"

    def test_string_element_stays_a_string(self):
        assert compiler._substitute("${x[0]}", {"x": ["/p/a", "/p/b"]}) == "/p/a"

    def test_embedded_scalar_stringified(self):
        assert compiler._substitute("n=${x}", {"x": 5}) == "n=5"

    def test_unresolved_passes_through_literally(self):
        assert compiler._substitute("${missing}", {}) == "${missing}"


# ---------------------------------------------- #20 summarize_walk honest report

class TestSummarizeWalk:
    def _s(self):
        # Load by file path — `import graph.codeflow_tools` is unreliable in the
        # suite because builder_service/graph shadows command_center_service/graph
        # on sys.path.
        import importlib.util
        path = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "..", "..",
            "command_center_service", "graph", "codeflow_tools.py"))
        spec = importlib.util.spec_from_file_location("_cc_codeflow_tools", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.summarize_walk

    def test_error_status(self):
        out = self._s()({"status": "error", "error": "boom"})
        assert "could not run" in out.lower() and "boom" in out

    def test_mixed_walk_surfaces_failed_step_details(self):
        out = self._s()({"status": "failed", "steps": [
            {"status": "success", "name": "a", "output_files": ["/p/f.csv"]},
            {"status": "failed", "name": "b", "exit_code": 2, "stderr_tail": "kaboom"},
        ]})
        assert "✗" in out and "exit 2" in out and "kaboom" in out and "/p/f.csv" in out

    def test_unverified_not_rendered_as_success(self):
        out = self._s()({"status": "failed", "steps": [
            {"status": "unverified", "name": "up", "error": "no remote listing"},
        ]})
        assert "✓" not in out          # never a clean check for unverified
        assert "⚠" in out

    def test_handled_failure_headline_not_plain_success(self):
        out = self._s()({"status": "success", "steps": [
            {"status": "failed", "name": "boom", "exit_code": 1, "stderr_tail": "e"},
            {"status": "success", "name": "alert"},
        ]})
        first = out.splitlines()[0]
        assert "did not pass" in first          # annotated, not a bare "success"

    def test_notes_real_execution(self):
        out = self._s()({"status": "success", "steps": [{"status": "success", "name": "a"}]})
        assert "for real" in out.lower()


# --------------------------- #18/#6 engine node behavioral (the scheduled path)

def _engine(monkeypatch, runner):
    import workflow_execution
    eng = workflow_execution.WorkflowExecutionEngine.__new__(
        workflow_execution.WorkflowExecutionEngine)
    eng.log_execution = lambda *a, **k: None
    eng._update_workflow_variable = lambda *a, **k: None
    eng._determine_variable_type = lambda v: "object"
    # the node does `from automations.runner import AutomationRunner; AutomationRunner()`
    monkeypatch.setattr("automations.runner.AutomationRunner", lambda *a, **k: runner)
    return eng


def test_engine_node_publishes_files_and_threads_them_to_next_step(monkeypatch, tmp_path):
    wd = str(tmp_path / "s1")
    runner = _StubRunner([
        {"status": "success", "output_files": ["out.csv"], "workdir": wd},   # s1
        {"status": "success", "output_files": ["final.csv"], "workdir": str(tmp_path / "s2")},  # s2
    ])
    eng = _engine(monkeypatch, runner)
    variables = {}

    defn = {"name": "chain",
            "steps": [{"id": "s1", "name": "pull", "code": "print(1)",
                       "outputs": [{"kind": "file", "path": "out.csv"}]},
                      {"id": "s2", "name": "push", "code": "print(2)",
                       "inputs": [{"name": "src", "type": "string", "default": "${s1_files[0]}"}]}],
            "edges": [{"from": "s1", "to": "s2", "on": "pass"}]}
    wf = compiler.compile_to_workflow(defn)
    n1 = next(n for n in wf["nodes"] if n["id"] == "s1")
    n2 = next(n for n in wf["nodes"] if n["id"] == "s2")

    r1 = eng._execute_code_step_node("exec1", n1, variables)
    assert r1["success"] is True
    # s1 published its files into engine variables under s1_files
    assert variables["s1_files"] == [os.path.join(wd, "out.csv")]
    # engine uses the node's human label, not step-<id>  (#5/#12)
    assert runner.calls[0]["step_name"] == "pull"

    r2 = eng._execute_code_step_node("exec1", n2, variables)
    assert r2["success"] is True
    # the engine resolved ${s1_files[0]} to s1's absolute file path (#18 cross-step)
    assert runner.calls[1]["inputs"]["src"] == os.path.join(wd, "out.csv")


def test_engine_node_continue_on_error_flips_failed_to_pass(monkeypatch, tmp_path):
    runner = _StubRunner({"status": "failed", "exit_code": 1, "workdir": str(tmp_path)})
    eng = _engine(monkeypatch, runner)
    defn = {"name": "c", "steps": [{"id": "s1", "name": "x", "code": "x", "continueOnError": True}],
            "edges": []}
    node = compiler.compile_to_workflow(defn)["nodes"][0]
    res = eng._execute_code_step_node("e", node, {})
    assert res["success"] is True          # continueOnError -> engine takes the pass edge


def test_engine_node_allow_unverified(monkeypatch, tmp_path):
    runner = _StubRunner({"status": "unverified", "exit_code": 0, "workdir": str(tmp_path)})
    eng = _engine(monkeypatch, runner)
    # without opt-in: failed
    plain = compiler.compile_to_workflow(
        {"name": "u", "steps": [{"id": "s1", "name": "x", "code": "x"}], "edges": []})["nodes"][0]
    assert eng._execute_code_step_node("e", plain, {})["success"] is False
    # with opt-in: pass
    opted = compiler.compile_to_workflow(
        {"name": "u2", "steps": [{"id": "s1", "name": "x", "code": "x", "allowUnverified": True}],
         "edges": []})["nodes"][0]
    assert eng._execute_code_step_node("e", opted, {})["success"] is True
