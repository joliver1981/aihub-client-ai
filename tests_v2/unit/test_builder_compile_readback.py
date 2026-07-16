"""
AIHUB-0034 — the builder emits the persisted node read-back (`workflow_saved`)
regardless of WHICH graph node compiled the workflow.

The prior fix "didn't fire live" because `compile_result` was returned by a node
but was NOT a declared BuilderState channel, so LangGraph dropped it and
`final_state["compile_result"]` was always None. Two guards, both tested here:
  1. `compile_result` is now a declared channel (schema test).
  2. `_extract_compile_result` finds the compile result in ALL three places it
     can land (top-level, plan-step result, execution_results), so the read-back
     fires whether the build came from handle_agent_response or execute().
"""
from __future__ import annotations

import importlib.util
import os

import pytest

pytestmark = pytest.mark.unit


def _chat_mod():
    # extract_compile_result lives in a dependency-free helper (chat.py itself
    # needs sse_starlette, which the test env lacks). Load it by file path.
    path = os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "..",
        "builder_service", "routes", "compile_readback.py"))
    spec = importlib.util.spec_from_file_location("_builder_compile_readback", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    # expose under the name the tests call
    m._extract_compile_result = m.extract_compile_result
    return m


_SUCCESS_CR = {
    "status": "success",
    "workflow_id": 1252,
    "workflow_data": {"nodes": [
        {"type": "Database"}, {"type": "Set Variable"}, {"type": "File"}]},
}


class TestSchemaChannel:
    def test_compile_result_is_a_declared_builder_channel(self):
        # If this key is missing, LangGraph silently drops a node's
        # `compile_result` return and the read-back never fires live.
        # Load builder_service/graph/__init__.py by file path — a bare
        # `from graph import ...` resolves to command_center_service/graph
        # when that package is already imported (suite-order shadow).
        path = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "..", "..",
            "builder_service", "graph", "__init__.py"))
        spec = importlib.util.spec_from_file_location("_builder_graph_state", path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        assert "compile_result" in m.BuilderState.__annotations__


class TestExtractCompileResult:
    def test_top_level(self):
        m = _chat_mod()
        assert m._extract_compile_result({"compile_result": _SUCCESS_CR}) is _SUCCESS_CR

    def test_from_plan_step_result(self):
        # the execute() first-message path: compile_result lives inside a plan step
        m = _chat_mod()
        state = {"current_plan": {"steps": [
            {"result": {}},
            {"result": {"compile_result": _SUCCESS_CR}},
        ]}}
        assert m._extract_compile_result(state) is _SUCCESS_CR

    def test_from_execution_results(self):
        m = _chat_mod()
        state = {"execution_results": [{"foo": 1}, {"compile_result": _SUCCESS_CR}]}
        assert m._extract_compile_result(state) is _SUCCESS_CR

    def test_ignores_non_saving_compile(self):
        m = _chat_mod()
        # an 'error' compile (no saved workflow) must NOT be reported as saved
        state = {"compile_result": {"status": "error", "error": "boom"}}
        assert m._extract_compile_result(state) is None

    def test_ignores_compile_without_workflow_data(self):
        m = _chat_mod()
        state = {"compile_result": {"status": "success", "workflow_id": 5}}
        assert m._extract_compile_result(state) is None

    def test_none_when_absent(self):
        m = _chat_mod()
        assert m._extract_compile_result({"messages": []}) is None

    def test_later_plan_step_wins(self):
        m = _chat_mod()
        early = {"status": "success", "workflow_id": 1,
                 "workflow_data": {"nodes": [{"type": "Database"}]}}
        state = {"current_plan": {"steps": [
            {"result": {"compile_result": early}},
            {"result": {"compile_result": _SUCCESS_CR}},
        ]}}
        assert m._extract_compile_result(state) is _SUCCESS_CR
