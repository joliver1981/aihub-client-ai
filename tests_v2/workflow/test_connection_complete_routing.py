"""'Complete' connection routing in the server-side engine.

A 'complete' connection is followed regardless of node outcome (the frontend
simulator's shouldFollowPath semantics). The engine is single-path, so the
specific outcome connection wins when both are present:
  * success: pass, else complete
  * failure: fail, else complete, else the workflow fails
"""
from __future__ import annotations

import pytest


MISSING_FILE = "C:/this/file/does/not/exist_xyz_123.txt"


def _step_lookup(result):
    return {
        (s.get("node_name") or s.get("node_label") or s.get("step_name") or "?"): s.get("status")
        for s in result.get("steps") or []
    }


def _set_var_node(make_node, node_id, var_name, label, left=500):
    return make_node(
        node_id, "Set Variable",
        config={
            "variableName": var_name,
            "valueSource": "direct",
            "valueExpression": "fired",
            "evaluateAsExpression": False,
        },
        label=label, left=left,
    )


def _failing_file_node(make_node, label="read_missing"):
    return make_node(
        "node-0", "File",
        config={
            "operation": "read",
            "filePath": MISSING_FILE,
            "outputVariable": "content",
            "saveToVariable": True,
        },
        label=label, is_start=True,
    )


def test_complete_followed_on_success(save_workflow, run_workflow,
                                      make_node, make_conn):
    """A node whose only outgoing connection is 'complete' continues there."""
    n0 = make_node(
        "node-0", "Set Variable",
        config={"variableName": "a", "valueSource": "direct",
                "valueExpression": "1"},
        label="set_a", is_start=True,
    )
    n1 = _set_var_node(make_node, "node-1", "after_complete", "after_complete")
    wid = save_workflow(
        "complete_on_success",
        nodes=[n0, n1],
        connections=[make_conn("node-0", "node-1", "complete")],
    )
    result = run_workflow(wid, timeout=30)
    statuses = _step_lookup(result)
    assert result["status"] == "completed", (
        f"status={result['status']} steps={statuses}"
    )
    assert statuses.get("after_complete") == "Completed", (
        f"complete connection not followed on success: {statuses}"
    )


def test_complete_followed_on_failure(save_workflow, run_workflow,
                                      make_node, make_conn):
    """A failing node with only a 'complete' connection continues there
    instead of failing the whole workflow (the Move-then-Alert scenario)."""
    n0 = _failing_file_node(make_node)
    n1 = _set_var_node(make_node, "node-1", "after_complete", "after_complete")
    wid = save_workflow(
        "complete_on_failure",
        nodes=[n0, n1],
        connections=[make_conn("node-0", "node-1", "complete")],
    )
    result = run_workflow(wid, timeout=30)
    statuses = _step_lookup(result)
    assert result["status"] == "completed", (
        f"workflow should absorb the failure via the complete connection: "
        f"status={result['status']} steps={statuses}"
    )
    assert statuses.get("read_missing") == "Failed"
    assert statuses.get("after_complete") == "Completed", (
        f"complete connection not followed on failure: {statuses}"
    )


def test_fail_preferred_over_complete_on_failure(save_workflow, run_workflow,
                                                 make_node, make_conn):
    n0 = _failing_file_node(make_node)
    n_fail = _set_var_node(make_node, "node-1", "took_fail", "took_fail")
    n_complete = _set_var_node(make_node, "node-2", "took_complete",
                               "took_complete", left=800)
    wid = save_workflow(
        "fail_beats_complete",
        nodes=[n0, n_fail, n_complete],
        connections=[
            make_conn("node-0", "node-1", "fail"),
            make_conn("node-0", "node-2", "complete"),
        ],
    )
    result = run_workflow(wid, timeout=30)
    statuses = _step_lookup(result)
    assert statuses.get("took_fail") == "Completed", (
        f"fail connection should win on failure: {statuses}"
    )
    assert statuses.get("took_complete") is None, (
        f"complete branch should not run when a fail connection exists: {statuses}"
    )


def test_pass_preferred_over_complete_on_success(save_workflow, run_workflow,
                                                 make_node, make_conn):
    n0 = make_node(
        "node-0", "Set Variable",
        config={"variableName": "a", "valueSource": "direct",
                "valueExpression": "1"},
        label="set_a", is_start=True,
    )
    n_pass = _set_var_node(make_node, "node-1", "took_pass", "took_pass")
    n_complete = _set_var_node(make_node, "node-2", "took_complete",
                               "took_complete", left=800)
    wid = save_workflow(
        "pass_beats_complete",
        nodes=[n0, n_pass, n_complete],
        connections=[
            make_conn("node-0", "node-1", "pass"),
            make_conn("node-0", "node-2", "complete"),
        ],
    )
    result = run_workflow(wid, timeout=30)
    statuses = _step_lookup(result)
    assert result["status"] == "completed", (
        f"status={result['status']} steps={statuses}"
    )
    assert statuses.get("took_pass") == "Completed", (
        f"pass connection should win on success: {statuses}"
    )
    assert statuses.get("took_complete") is None, (
        f"complete branch should not run when a pass connection exists: {statuses}"
    )
