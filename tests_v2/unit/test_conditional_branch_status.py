"""Conditional branch-status contract (2026-08-05, wf 1218 'Horizon Replica').

A Conditional node signals its branch through result['success'] — False means
"take the fail/false edge", not "the step failed". Before this fix every FALSE
evaluation fell into the generic failure handler: the step was recorded as
Failed with "Node execution failed: Conditional" and counted in the run's
failed total, while routing then proceeded correctly down the fail edge. These
tests pin the corrected contract:

  FALSE + fail edge      -> step Completed, fail-edge target returned
  TRUE  + pass edge      -> step Completed, pass-edge target returned
  FALSE + no fail edge   -> legacy dead-end contract preserved (step Failed,
                            exception propagates)
  evaluation error       -> still the failure path (step Failed, fail edge)
"""
import os
import sys

import pytest

pytestmark = pytest.mark.unit

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)
os.chdir(_REPO)

from workflow_execution import WorkflowExecutionEngine  # noqa: E402

EXEC_ID = "test-exec"
CONNECTIONS = [
    {"source": "cond", "target": "true-node", "type": "pass"},
    {"source": "cond", "target": "false-node", "type": "fail"},
]
COND_NODE = {"id": "cond", "type": "Conditional", "label": "Template Exists?",
             "config": {"conditionType": "comparison", "leftValue": "a",
                        "operator": "==", "rightValue": "b"}}


def _engine(connections, cond_result):
    """Engine with stubbed persistence + a forced conditional outcome."""
    eng = WorkflowExecutionEngine("dummy-connection-string")
    eng._active_executions[EXEC_ID] = {
        "workflow_data": {"nodes": [], "connections": connections}}
    eng.status_calls = []
    eng.log_calls = []
    eng._create_step_execution = lambda *a, **k: "step-1"
    eng._update_step_status = (
        lambda exec_id, node_id, status, **k: eng.status_calls.append((node_id, status)))
    eng.log_execution = (
        lambda exec_id, node_id, level, msg, *a, **k: eng.log_calls.append((level, msg)))
    eng._execute_conditional_node = lambda *a, **k: cond_result
    return eng


def test_false_branch_is_completed_and_routes_fail_edge():
    eng = _engine(CONNECTIONS, {"success": False,
                                "data": {"conditionResult": False}})
    target = eng._execute_node(EXEC_ID, COND_NODE, {})
    assert target == "false-node"
    assert ("cond", "Completed") in eng.status_calls
    assert not any(s == "Failed" for _, s in eng.status_calls)
    assert not any(lvl == "error" for lvl, _ in eng.log_calls)


def test_true_branch_is_completed_and_routes_pass_edge():
    eng = _engine(CONNECTIONS, {"success": True,
                                "data": {"conditionResult": True}})
    target = eng._execute_node(EXEC_ID, COND_NODE, {})
    assert target == "true-node"
    assert ("cond", "Completed") in eng.status_calls
    assert not any(s == "Failed" for _, s in eng.status_calls)


def test_false_with_no_fail_edge_keeps_legacy_failure_contract():
    pass_only = [{"source": "cond", "target": "true-node", "type": "pass"}]
    eng = _engine(pass_only, {"success": False, "data": {}})
    with pytest.raises(ValueError):
        eng._execute_node(EXEC_ID, COND_NODE, {})
    assert ("cond", "Failed") in eng.status_calls


def test_false_with_complete_edge_routes_it_as_completed():
    conns = [{"source": "cond", "target": "always-node", "type": "complete"}]
    eng = _engine(conns, {"success": False, "data": {}})
    target = eng._execute_node(EXEC_ID, COND_NODE, {})
    assert target == "always-node"
    assert ("cond", "Completed") in eng.status_calls


def test_evaluation_error_still_fails_and_routes_fail_edge():
    eng = _engine(CONNECTIONS, {"success": False, "error": "boom", "data": {}})
    target = eng._execute_node(EXEC_ID, COND_NODE, {})
    assert target == "false-node"          # fail edge, via the failure handler
    assert ("cond", "Failed") in eng.status_calls
