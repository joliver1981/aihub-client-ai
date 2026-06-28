"""Regression tests for nested-loop routing and wildcard file checks in the
workflow execution engine.

Background: these fixes were lost twice to dev-tree drift (uncommitted edits
overwritten). This test exists so that if the engine's loop routing or the
File-node wildcard support ever reverts to the broken base behavior, CI fails
loudly instead of the bug silently returning.

What broke (the "only the last customer's template uploaded" bug):
  _find_end_loop_node and _execute_loop_body_branch resolved a loop's End Loop
  by grabbing the FIRST End Loop they encountered, ignoring which loop it
  belonged to. For a nested topology, the OUTER loop's End Loop resolved to the
  INNER End Loop, so any node placed BETWEEN the inner End Loop and the outer
  End Loop (e.g. an Upload step) ran only once at the very end instead of once
  per outer iteration.

The body-branch pass-through (Fix B) is exercised end-to-end by the live
workflow retest; here we lock down the routing function it shares the logic
with (_find_end_loop_node) plus the wildcard file check.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from workflow_execution import WorkflowExecutionEngine


def _engine_with_workflow(nodes, connections):
    engine = WorkflowExecutionEngine("dummy-connection-string")
    # log_execution writes to the DB; stub it for pure-routing unit tests.
    engine.log_execution = lambda *a, **k: None
    exec_id = "test-exec"
    engine._active_executions[exec_id] = {
        "workflow_data": {"nodes": nodes, "connections": connections}
    }
    return engine, exec_id


def _nested_workflow(tag_loops=True):
    """outer(startCust) -> preA -> inner(startFiles) -> bodyB -> endFiles
       -> upload -> endCust   (all 'pass' connections)."""
    def end(node_id, owner):
        cfg = {"loopNodeId": owner} if tag_loops else {}
        return {"id": node_id, "type": "End Loop", "config": cfg}

    nodes = [
        {"id": "startCust", "type": "Loop", "config": {}},
        {"id": "preA", "type": "Integration", "config": {}},
        {"id": "startFiles", "type": "Loop", "config": {}},
        {"id": "bodyB", "type": "Integration", "config": {}},
        end("endFiles", "startFiles"),
        {"id": "upload", "type": "Integration", "config": {}},
        end("endCust", "startCust"),
    ]
    pairs = [
        ("startCust", "preA"), ("preA", "startFiles"), ("startFiles", "bodyB"),
        ("bodyB", "endFiles"), ("endFiles", "upload"), ("upload", "endCust"),
    ]
    connections = [{"source": s, "target": t, "type": "pass"} for s, t in pairs]
    return nodes, connections


def test_outer_loop_resolves_to_outer_end_loop():
    """The core fix: the OUTER loop must resolve to endCust, NOT the inner endFiles."""
    engine, exec_id = _engine_with_workflow(*_nested_workflow(tag_loops=True))
    assert engine._find_end_loop_node(exec_id, "startCust") == "endCust"


def test_inner_loop_resolves_to_inner_end_loop():
    engine, exec_id = _engine_with_workflow(*_nested_workflow(tag_loops=True))
    assert engine._find_end_loop_node(exec_id, "startFiles") == "endFiles"


def test_legacy_untagged_end_loops_fall_back_to_first_found():
    """Workflows whose End Loop nodes have no loopNodeId keep the old behavior
    (first End Loop reached), so this change can't regress existing flows."""
    engine, exec_id = _engine_with_workflow(*_nested_workflow(tag_loops=False))
    # BFS from the outer loop reaches endFiles first -> fallback returns it.
    assert engine._find_end_loop_node(exec_id, "startCust") == "endFiles"


def test_single_loop_still_resolves():
    nodes = [
        {"id": "loop1", "type": "Loop", "config": {}},
        {"id": "body", "type": "Integration", "config": {}},
        {"id": "end1", "type": "End Loop", "config": {"loopNodeId": "loop1"}},
    ]
    conns = [
        {"source": "loop1", "target": "body", "type": "pass"},
        {"source": "body", "target": "end1", "type": "pass"},
    ]
    engine, exec_id = _engine_with_workflow(nodes, conns)
    assert engine._find_end_loop_node(exec_id, "loop1") == "end1"


def test_file_check_exact_path():
    engine, exec_id = _engine_with_workflow([], [])
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "MyTemplate.xlsx")
        with open(p, "w") as f:
            f.write("x")
        res = engine._file_check_operation(exec_id, "n", p, {}, {})
        assert res["success"] is True
        assert res["data"]["exists"] is True
        missing = os.path.join(d, "Nope.xlsx")
        res2 = engine._file_check_operation(exec_id, "n", missing, {}, {})
        assert res2["data"]["exists"] is False


def test_file_check_wildcard():
    engine, exec_id = _engine_with_workflow([], [])
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "Dollar Tree_Template.xlsx"), "w") as f:
            f.write("x")
        pattern = os.path.join(d, "Dollar Tree*.xlsx")
        res = engine._file_check_operation(exec_id, "n", pattern, {}, {})
        assert res["data"]["exists"] is True
        assert res["data"]["pattern"] is True
        assert res["data"]["matchCount"] == 1
        assert res["data"]["firstMatch"].endswith("Dollar Tree_Template.xlsx")

        no_match = os.path.join(d, "MegaMart*.xlsx")
        res2 = engine._file_check_operation(exec_id, "n", no_match, {}, {})
        assert res2["data"]["exists"] is False
        assert res2["data"]["matchCount"] == 0


if __name__ == "__main__":
    # Allow running directly (no pytest needed): execute every test_* function.
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
