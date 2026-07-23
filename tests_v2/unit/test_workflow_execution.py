"""Unit tests for workflow_execution.WorkflowExecutionEngine.

These exercise the pure-Python helper methods on the engine — variable
resolution, expression evaluation, conditional evaluation, file ops, pause /
resume / cancel state machinery, and the new approval/persist logic. Each
test patches out database connections, threading, and log_execution so the
engine can be instantiated without a SQL Server.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# =============================================================================
# Engine fixture — gives us a WorkflowExecutionEngine instance with DB stubbed
# =============================================================================

@pytest.fixture
def engine(monkeypatch):
    """Engine instance with DB connection patched out. log_execution is a no-op."""
    from workflow_execution import WorkflowExecutionEngine

    e = WorkflowExecutionEngine(connection_string="fake-dsn")
    # log_execution touches the DB; make it a no-op.
    monkeypatch.setattr(e, "log_execution", lambda *a, **kw: None)
    monkeypatch.setattr(e, "_create_step_execution", lambda *a, **kw: "fake-step-id")
    monkeypatch.setattr(e, "_update_step_status", lambda *a, **kw: None)
    monkeypatch.setattr(e, "_update_workflow_status", lambda *a, **kw: None)
    monkeypatch.setattr(e, "_update_workflow_variable", lambda *a, **kw: None)
    monkeypatch.setattr(e, "get_db_connection", lambda: MagicMock())
    return e


# =============================================================================
# find_variable
# =============================================================================

class TestFindVariable:
    def test_exact_match(self, engine):
        key, val = engine.find_variable("foo", {"foo": 42})
        assert key == "foo"
        assert val == 42

    def test_dollar_brace_wrapped_input_finds_unwrapped_key(self, engine):
        key, val = engine.find_variable("${foo}", {"foo": "bar"})
        assert key == "foo"
        assert val == "bar"

    def test_unwrapped_input_finds_wrapped_key(self, engine):
        key, val = engine.find_variable("foo", {"${foo}": "bar"})
        assert key == "${foo}"
        assert val == "bar"

    def test_not_found_returns_none_pair(self, engine):
        key, val = engine.find_variable("missing", {"foo": 1})
        assert key is None
        assert val is None

    def test_handles_empty_dict(self, engine):
        assert engine.find_variable("foo", {}) == (None, None)


# =============================================================================
# _replace_variable_references
# =============================================================================

class TestReplaceVariableReferences:
    def test_simple_substitution(self, engine):
        assert engine._replace_variable_references("Hello ${name}", {"name": "World"}) == "Hello World"

    def test_legacy_dollar_syntax(self, engine):
        assert engine._replace_variable_references("Hi $name!", {"name": "Alice"}) == "Hi Alice!"

    def test_missing_variable_kept_literal(self, engine):
        # When a variable is missing, the original text should be kept
        out = engine._replace_variable_references("Hi ${missing}", {})
        assert "${missing}" in out

    def test_nested_path(self, engine):
        out = engine._replace_variable_references(
            "Hi ${user.name}", {"user": {"name": "Bob"}}
        )
        assert out == "Hi Bob"

    def test_array_index_via_nested(self, engine):
        """Array index works when used after a dot path:
        ``${data.items[0]}`` works; ``${items[0]}`` does not (it's treated as
        a literal variable-name lookup for "items[0]").
        """
        out = engine._replace_variable_references(
            "${data.items[0]}", {"data": {"items": ["apple", "banana"]}}
        )
        assert out == "apple"

    def test_mixed_dot_and_index(self, engine):
        out = engine._replace_variable_references(
            "${data.users[1].email}",
            {"data": {"users": [{"email": "a@x"}, {"email": "b@y"}]}},
        )
        assert out == "b@y"

    def test_dict_value_serialized_as_json(self, engine):
        out = engine._replace_variable_references("${user}", {"user": {"name": "X"}})
        assert json.loads(out) == {"name": "X"}

    def test_bool_value_lowercase(self, engine):
        assert engine._replace_variable_references("${flag}", {"flag": True}) == "true"
        assert engine._replace_variable_references("${flag}", {"flag": False}) == "false"

    def test_non_string_input_returned_unchanged(self, engine):
        assert engine._replace_variable_references(None, {}) is None
        assert engine._replace_variable_references(123, {}) == 123

    def test_previous_step_output_reference(self, engine):
        variables = {"_previousStepOutput": {"value": 42}}
        out = engine._replace_variable_references(
            "Result: ${_previousStepOutput.value}", variables
        )
        assert out == "Result: 42"


class TestExtractVariableName:
    def test_dollar_brace(self, engine):
        assert engine._extract_variable_name("${foo}") == "foo"

    def test_legacy(self, engine):
        assert engine._extract_variable_name("$foo") == "foo"

    def test_plain(self, engine):
        assert engine._extract_variable_name("foo") == "foo"

    def test_non_string_passthrough(self, engine):
        assert engine._extract_variable_name(123) == 123


# =============================================================================
# _evaluate_value
# =============================================================================

class TestEvaluateValue:
    def test_int_literal(self, engine):
        assert engine._evaluate_value("42") == 42

    def test_float_literal(self, engine):
        assert engine._evaluate_value("3.14") == 3.14

    def test_true(self, engine):
        assert engine._evaluate_value("true") is True
        assert engine._evaluate_value("True") is True

    def test_false(self, engine):
        assert engine._evaluate_value("false") is False

    def test_null(self, engine):
        assert engine._evaluate_value("null") is None
        assert engine._evaluate_value("None") is None

    def test_json_object(self, engine):
        assert engine._evaluate_value('{"a":1}') == {"a": 1}

    def test_string_passthrough(self, engine):
        assert engine._evaluate_value("hello world") == "hello world"

    def test_non_string_passthrough(self, engine):
        assert engine._evaluate_value(42) == 42
        assert engine._evaluate_value([1, 2]) == [1, 2]


# =============================================================================
# _evaluate_comparison
# =============================================================================

class TestEvaluateComparison:
    @pytest.mark.parametrize("left,op,right,expected", [
        (1, "==", 1, True),
        (1, "==", 2, False),
        (1, "!=", 2, True),
        (1, "!=", 1, False),
        (2, ">", 1, True),
        (1, ">", 2, False),
        (1, "<", 2, True),
        (2, "<=", 2, True),
        (3, ">=", 3, True),
        ("a", "==", "a", True),
        ("a", "==", "b", False),
    ])
    def test_basic_comparisons(self, engine, left, op, right, expected):
        assert engine._evaluate_comparison(left, op, right) is expected

    def test_unknown_operator_returns_false(self, engine):
        assert engine._evaluate_comparison(1, "@@", 1) is False

    def test_incompatible_types_returns_false(self, engine):
        # Comparing dict > 1 raises, should be caught
        assert engine._evaluate_comparison({}, ">", 1) is False


# =============================================================================
# _evaluate_expression
# =============================================================================

class TestEvaluateExpression:
    def test_simple_truthy(self, engine):
        assert engine._evaluate_expression("1 == 1", {}) is True

    def test_simple_falsy(self, engine):
        assert engine._evaluate_expression("1 == 2", {}) is False

    def test_variable_reference_unwrapped(self, engine):
        assert engine._evaluate_expression("${status} == 'active'", {"status": "active"}) is True

    def test_len_builtin(self, engine):
        assert engine._evaluate_expression("len(${items}) > 2", {"items": [1, 2, 3]}) is True

    def test_invalid_expression_returns_false(self, engine):
        assert engine._evaluate_expression("this is not valid python &&", {}) is False

    def test_string_in_string(self, engine):
        assert engine._evaluate_expression("'b' in ${word}", {"word": "abc"}) is True


# =============================================================================
# _execute_conditional_node
# =============================================================================

class TestExecuteConditionalNode:
    def _node(self, **config):
        return {"id": "cond1", "type": "Conditional", "config": config}

    def test_comparison_true(self, engine):
        result = engine._execute_conditional_node(
            "exec1",
            self._node(conditionType="comparison", leftValue="5", operator=">", rightValue="3"),
            {},
        )
        assert result["success"] is True
        assert result["data"]["conditionResult"] is True

    def test_comparison_false(self, engine):
        result = engine._execute_conditional_node(
            "exec1",
            self._node(conditionType="comparison", leftValue="1", operator=">", rightValue="3"),
            {},
        )
        assert result["success"] is False

    def test_comparison_with_variables(self, engine):
        result = engine._execute_conditional_node(
            "exec1",
            self._node(conditionType="comparison", leftValue="${a}", operator="==", rightValue="${b}"),
            {"a": 5, "b": 5},
        )
        assert result["success"] is True

    def test_expression_type(self, engine):
        result = engine._execute_conditional_node(
            "exec1",
            self._node(conditionType="expression", expression="len(${arr}) == 3"),
            {"arr": [1, 2, 3]},
        )
        assert result["success"] is True

    def test_contains_type(self, engine):
        result = engine._execute_conditional_node(
            "exec1",
            self._node(conditionType="contains", containsText="hello world", searchText="world"),
            {},
        )
        assert result["success"] is True

    def test_exists_type_present(self, engine):
        result = engine._execute_conditional_node(
            "exec1",
            self._node(conditionType="exists", existsVariable="foo"),
            {"foo": 1},
        )
        assert result["success"] is True

    def test_exists_type_missing(self, engine):
        result = engine._execute_conditional_node(
            "exec1",
            self._node(conditionType="exists", existsVariable="bar"),
            {"foo": 1},
        )
        assert result["success"] is False

    @pytest.mark.parametrize("value,expected", [
        (None, True),
        ("", True),
        ([], True),
        ({}, True),
        ("hello", False),
        ([1], False),
        ({"x": 1}, False),
    ])
    def test_empty_type(self, engine, value, expected):
        result = engine._execute_conditional_node(
            "exec1",
            self._node(conditionType="empty", emptyVariable="${myvar}"),
            {"myvar": value},
        )
        assert result["success"] is expected

    def test_invalid_config_returns_failure(self, engine, monkeypatch):
        # Make _evaluate_value raise to force the except branch
        def boom(v):
            raise RuntimeError("explode")
        monkeypatch.setattr(engine, "_evaluate_value", boom)
        result = engine._execute_conditional_node(
            "exec1",
            self._node(conditionType="comparison", leftValue="x", operator="==", rightValue="y"),
            {},
        )
        assert result["success"] is False
        assert "error" in result


# =============================================================================
# _execute_set_variable_node
# =============================================================================

class TestExecuteSetVariableNode:
    def _node(self, **config):
        return {"id": "sv1", "type": "Set Variable", "config": config}

    def test_direct_string_value(self, engine):
        variables = {}
        result = engine._execute_set_variable_node(
            "exec1",
            self._node(variableName="greeting", valueSource="direct", valueExpression="hello"),
            variables,
        )
        assert result["success"] is True
        assert variables["greeting"] == "hello"

    def test_variable_substitution(self, engine):
        variables = {"name": "World"}
        result = engine._execute_set_variable_node(
            "exec1",
            self._node(variableName="msg", valueSource="direct", valueExpression="Hello ${name}"),
            variables,
        )
        assert result["success"] is True
        assert variables["msg"] == "Hello World"

    def test_no_variable_name_fails(self, engine):
        result = engine._execute_set_variable_node(
            "exec1",
            self._node(variableName="", valueSource="direct", valueExpression="x"),
            {},
        )
        assert result["success"] is False
        assert "No variable name" in str(result.get("error", ""))

    def test_python_expression_evaluation(self, engine):
        variables = {"a": 2, "b": 3}
        result = engine._execute_set_variable_node(
            "exec1",
            self._node(
                variableName="total",
                valueSource="direct",
                valueExpression="${a} + ${b}",
                evaluateAsExpression=True,
            ),
            variables,
        )
        assert result["success"] is True
        assert variables["total"] == 5

    def test_variable_name_sanitization(self, engine):
        """variableName with ${} wrapping is stripped, and non-identifier chars removed."""
        variables = {}
        result = engine._execute_set_variable_node(
            "exec1",
            self._node(variableName="${my-var}", valueSource="direct", valueExpression="x"),
            variables,
        )
        assert result["success"] is True
        # 'my-var' has '-' stripped -> 'myvar'
        assert "myvar" in variables


# =============================================================================
# File node operations
# =============================================================================

class TestFileNode:
    def _node(self, **config):
        return {"id": "file1", "type": "File", "config": config}

    def test_read_file(self, engine, tmp_path):
        f = tmp_path / "input.txt"
        f.write_text("hello world", encoding="utf-8")
        result = engine._execute_file_node(
            "exec1",
            self._node(operation="read", filePath=str(f)),
            {},
        )
        assert result["success"] is True
        assert result["data"]["content"] == "hello world"
        assert result["data"]["size"] == 11

    def test_write_file(self, engine, tmp_path):
        f = tmp_path / "out.txt"
        result = engine._execute_file_node(
            "exec1",
            self._node(
                operation="write",
                filePath=str(f),
                contentSource="direct",
                content="new content",
            ),
            {},
        )
        assert result["success"] is True
        assert f.read_text(encoding="utf-8") == "new content"

    def test_append_file(self, engine, tmp_path):
        f = tmp_path / "log.txt"
        f.write_text("line1\n", encoding="utf-8")
        result = engine._execute_file_node(
            "exec1",
            self._node(
                operation="append",
                filePath=str(f),
                contentSource="direct",
                content="line2",
            ),
            {},
        )
        assert result["success"] is True
        assert "line1" in f.read_text(encoding="utf-8")
        assert "line2" in f.read_text(encoding="utf-8")

    def test_check_existing_file(self, engine, tmp_path):
        f = tmp_path / "exists.txt"
        f.write_text("x", encoding="utf-8")
        result = engine._execute_file_node(
            "exec1",
            self._node(operation="check", filePath=str(f)),
            {},
        )
        assert result["success"] is True
        assert result["data"]["exists"] is True

    def test_check_missing_file(self, engine, tmp_path):
        result = engine._execute_file_node(
            "exec1",
            self._node(operation="check", filePath=str(tmp_path / "nope.txt")),
            {},
        )
        assert result["success"] is True
        assert result["data"]["exists"] is False

    def test_read_missing_file_fails_gracefully(self, engine, tmp_path):
        result = engine._execute_file_node(
            "exec1",
            self._node(operation="read", filePath=str(tmp_path / "ghost.txt")),
            {},
        )
        assert result["success"] is False
        assert "error" in result

    def test_delete_file(self, engine, tmp_path):
        f = tmp_path / "doomed.txt"
        f.write_text("bye", encoding="utf-8")
        result = engine._execute_file_node(
            "exec1",
            self._node(operation="delete", filePath=str(f)),
            {},
        )
        assert result["success"] is True
        assert not f.exists()

    def test_copy_file(self, engine, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text("data", encoding="utf-8")
        dst = tmp_path / "dst.txt"
        result = engine._execute_file_node(
            "exec1",
            # The node config key is destinationPath (not targetPath)
            self._node(operation="copy", filePath=str(src), destinationPath=str(dst)),
            {},
        )
        assert result["success"] is True
        assert dst.read_text(encoding="utf-8") == "data"
        assert src.exists()  # copy doesn't delete source

    def test_move_file(self, engine, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text("data", encoding="utf-8")
        dst = tmp_path / "dst.txt"
        result = engine._execute_file_node(
            "exec1",
            self._node(operation="move", filePath=str(src), destinationPath=str(dst)),
            {},
        )
        assert result["success"] is True
        assert dst.exists()
        assert not src.exists()

    def test_unknown_operation_fails(self, engine, tmp_path):
        result = engine._execute_file_node(
            "exec1",
            self._node(operation="frobnicate", filePath=str(tmp_path / "x")),
            {},
        )
        assert result["success"] is False

    def test_missing_path_fails(self, engine):
        result = engine._execute_file_node(
            "exec1",
            self._node(operation="read", filePath=""),
            {},
        )
        assert result["success"] is False

    def test_read_with_variable_in_path(self, engine, tmp_path):
        f = tmp_path / "templated.txt"
        f.write_text("yes", encoding="utf-8")
        result = engine._execute_file_node(
            "exec1",
            self._node(
                operation="read",
                filePath=str(tmp_path) + "/${name}.txt",
            ),
            {"name": "templated"},
        )
        assert result["success"] is True

    def test_read_persists_output_to_variable(self, engine, tmp_path):
        """When outputVariable is set, the read content lands in variables."""
        f = tmp_path / "src.txt"
        f.write_text("payload", encoding="utf-8")
        variables = {}
        result = engine._execute_file_node(
            "exec1",
            self._node(
                operation="read",
                filePath=str(f),
                outputVariable="contents",
            ),
            variables,
        )
        assert result["success"] is True
        assert variables.get("contents") == "payload"

    def test_check_persists_bool_to_variable(self, engine, tmp_path):
        variables = {}
        result = engine._execute_file_node(
            "exec1",
            self._node(
                operation="check",
                filePath=str(tmp_path / "ghost.txt"),
                outputVariable="found",
            ),
            variables,
        )
        assert result["success"] is True
        assert variables.get("found") is False


# =============================================================================
# Pause / Resume / Cancel
# =============================================================================

class TestPauseResumeCancel:
    def _seed(self, engine, execution_id="exec1", status="Running"):
        import queue
        engine._active_executions[execution_id] = {
            "status": status,
            "paused": False,
            "cancelled": False,
            "workflow_id": 1,
            "workflow_name": "test",
            "variables": {},
            "workflow_data": {"nodes": [], "connections": []},
            "current_node": "n1",
            "started_at": "2024-01-01T00:00:00",
        }
        engine._execution_queues[execution_id] = queue.Queue()

    def test_pause_running_workflow(self, engine):
        self._seed(engine)
        assert engine.pause_workflow("exec1") is True
        assert engine._active_executions["exec1"]["paused"] is True

    def test_pause_unknown_workflow_returns_false(self, engine):
        assert engine.pause_workflow("does-not-exist") is False

    def test_cannot_pause_non_running(self, engine):
        self._seed(engine, status="Completed")
        assert engine.pause_workflow("exec1") is False

    def test_resume_paused_workflow(self, engine):
        self._seed(engine, status="Paused")
        assert engine.resume_workflow("exec1") is True
        # Should have pushed 'resume' to queue
        cmd = engine._execution_queues["exec1"].get_nowait()
        assert cmd == "resume"

    def test_cannot_resume_running(self, engine):
        self._seed(engine, status="Running")
        assert engine.resume_workflow("exec1") is False

    def test_resume_unknown_workflow(self, engine):
        assert engine.resume_workflow("xxx") is False

    def test_cancel_running_workflow(self, engine):
        self._seed(engine, status="Running")
        assert engine.cancel_workflow("exec1") is True
        assert engine._active_executions["exec1"]["cancelled"] is True
        cmd = engine._execution_queues["exec1"].get_nowait()
        assert cmd == "cancel"

    def test_cannot_cancel_completed(self, engine):
        self._seed(engine, status="Completed")
        assert engine.cancel_workflow("exec1") is False

    def test_cannot_cancel_already_failed(self, engine):
        self._seed(engine, status="Failed")
        assert engine.cancel_workflow("exec1") is False

    def test_cannot_cancel_already_cancelled(self, engine):
        self._seed(engine, status="Cancelled")
        assert engine.cancel_workflow("exec1") is False

    def test_cancel_unknown_workflow(self, engine):
        assert engine.cancel_workflow("xxx") is False


# =============================================================================
# Array parsing helpers
# =============================================================================

class TestParseStringToList:
    def test_json_array(self, engine):
        assert engine._parse_string_to_list('["a", "b"]') == ["a", "b"]

    def test_python_repr_list(self, engine):
        assert engine._parse_string_to_list("['a', 'b']") == ["a", "b"]

    def test_csv(self, engine):
        assert engine._parse_string_to_list("a, b, c") == ["a", "b", "c"]

    def test_newlines(self, engine):
        assert engine._parse_string_to_list("a\nb\nc") == ["a", "b", "c"]

    def test_empty_returns_none(self, engine):
        assert engine._parse_string_to_list("") is None
        assert engine._parse_string_to_list("   ") is None

    def test_non_string_returns_none(self, engine):
        assert engine._parse_string_to_list(123) is None
        assert engine._parse_string_to_list([1, 2]) is None

    def test_invalid_json_falls_through(self, engine):
        # Looks like a list but isn't valid JSON — Python literal_eval picks it up
        result = engine._parse_string_to_list("['a',]")
        assert result == ["a"]


class TestAutoDetectArray:
    def test_direct_list(self, engine):
        arr, desc = engine._auto_detect_array([1, 2, 3])
        assert arr == [1, 2, 3]

    def test_all_files_key(self, engine):
        arr, _ = engine._auto_detect_array({"allFiles": ["a.txt", "b.txt"]})
        assert arr == ["a.txt", "b.txt"]

    def test_results_key(self, engine):
        arr, _ = engine._auto_detect_array({"results": [{"id": 1}]})
        assert arr == [{"id": 1}]

    def test_items_property(self, engine):
        arr, _ = engine._auto_detect_array({"items": [1, 2]})
        assert arr == [1, 2]

    def test_nested_data_list(self, engine):
        arr, _ = engine._auto_detect_array({"data": [1, 2]})
        assert arr == [1, 2]

    def test_no_array_returns_empty(self, engine):
        arr, _ = engine._auto_detect_array({"unrelated": "value"})
        assert arr == []

    def test_string_input_parsed(self, engine):
        arr, _ = engine._auto_detect_array("[1, 2, 3]")
        assert arr == [1, 2, 3]


# =============================================================================
# Connection lookup helpers
# =============================================================================

class TestConnectionLookups:
    def _seed_workflow(self, engine, connections):
        engine._active_executions["exec1"] = {
            "workflow_data": {"nodes": [], "connections": connections},
            "status": "Running",
            "paused": False,
            "cancelled": False,
            "variables": {},
        }

    def test_find_next_pass(self, engine):
        self._seed_workflow(
            engine,
            [
                {"source": "a", "target": "b", "type": "pass"},
                {"source": "a", "target": "c", "type": "fail"},
            ],
        )
        assert engine._find_next_pass_connection("exec1", "a") == "b"

    def test_find_next_fail(self, engine):
        self._seed_workflow(
            engine,
            [
                {"source": "a", "target": "b", "type": "pass"},
                {"source": "a", "target": "c", "type": "fail"},
            ],
        )
        assert engine._find_next_fail_connection("exec1", "a") == "c"

    def test_find_next_complete(self, engine):
        self._seed_workflow(
            engine, [{"source": "a", "target": "z", "type": "complete"}]
        )
        assert engine._find_next_complete_connection("exec1", "a") == "z"

    def test_get_all_connections_from_node(self, engine):
        self._seed_workflow(
            engine,
            [
                {"source": "a", "target": "b", "type": "pass"},
                {"source": "a", "target": "c", "type": "fail"},
                {"source": "x", "target": "y", "type": "pass"},
            ],
        )
        conns = engine._get_all_connections_from_node("exec1", "a")
        assert len(conns) == 2

    def test_no_matching_returns_none(self, engine):
        self._seed_workflow(engine, [])
        assert engine._find_next_pass_connection("exec1", "a") is None


# =============================================================================
# _check_data_dict / _to_dict helpers
# =============================================================================

class TestSmallHelpers:
    def test_check_data_dict_wraps_legacy_shape(self, engine):
        result = engine._check_data_dict(
            {"status": "ok", "columns": ["a"], "rows": [[1]]}
        )
        assert "data" in result
        assert result["data"]["columns"] == ["a"]

    def test_check_data_dict_passes_through_modern_shape(self, engine):
        modern = {"status": "ok", "data": {"x": 1}}
        assert engine._check_data_dict(modern) == modern


# =============================================================================
# get_active_executions_count / is_execution_active
# =============================================================================

class TestActiveExecutionAccessors:
    def test_zero_initially(self, engine):
        assert engine.get_active_executions_count() == 0
        assert engine.get_active_execution_ids() == []

    def test_returns_after_seed(self, engine):
        engine._active_executions["e1"] = {"status": "Running"}
        engine._active_executions["e2"] = {"status": "Paused"}
        assert engine.get_active_executions_count() == 2
        assert set(engine.get_active_execution_ids()) == {"e1", "e2"}

    def test_is_execution_active_true(self, engine):
        engine._active_executions["e1"] = {"status": "Running"}
        assert engine.is_execution_active("e1") is True

    def test_is_execution_active_false(self, engine):
        assert engine.is_execution_active("e1") is False


# =============================================================================
# _get_nested_value_for_variables
# =============================================================================

class TestNestedValue:
    def test_simple_dot(self, engine):
        assert engine._get_nested_value_for_variables({"a": {"b": 1}}, "a.b") == 1

    def test_array_index(self, engine):
        assert engine._get_nested_value_for_variables({"a": [10, 20]}, "a[1]") == 20

    def test_json_string_input(self, engine):
        # Engine parses the JSON automatically
        assert engine._get_nested_value_for_variables('{"x": 1}', "x") == 1

    def test_empty_path_returns_self(self, engine):
        assert engine._get_nested_value_for_variables({"a": 1}, "") == {"a": 1}

    def test_missing_path_returns_none(self, engine):
        assert engine._get_nested_value_for_variables({"a": 1}, "b.c") is None

    def test_none_object_returns_none(self, engine):
        assert engine._get_nested_value_for_variables(None, "x") is None


# =============================================================================
# _looks_like_python_expression
# =============================================================================

class TestLooksLikePythonExpression:
    def test_pure_string_no(self, engine):
        # Simple text doesn't look like an expression
        assert engine._looks_like_python_expression("hello") is False

    def test_function_call_yes(self, engine):
        assert engine._looks_like_python_expression("len(${arr})") is True

    def test_list_comprehension_yes(self, engine):
        assert engine._looks_like_python_expression("[x for x in items]") is True

    def test_fstring_yes(self, engine):
        assert engine._looks_like_python_expression('f"{name}"') is True

    def test_arithmetic_alone_does_not_match(self, engine):
        """Plain arithmetic isn't a strong-enough Python pattern to trigger
        auto-eval. The user must opt in via evaluateAsExpression."""
        assert engine._looks_like_python_expression("1 + 2") is False


# =============================================================================
# execution_data persistence shape - start_workflow happy path with mocked DB
# =============================================================================

class TestStartWorkflowHappy:
    def test_in_memory_state_populated(self, engine, monkeypatch):
        """start_workflow should populate _active_executions and create
        the background thread (which we stub out to a no-op)."""
        # Stub out the worker thread so it doesn't try to run the workflow
        monkeypatch.setattr(
            engine, "_execute_workflow_thread", lambda *a, **kw: None
        )

        # Mock DB
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = ("Test Workflow",)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        monkeypatch.setattr(engine, "get_db_connection", lambda: mock_conn)

        workflow_data = {
            "nodes": [
                {"id": "n1", "type": "Set Variable", "isStart": True, "config": {}},
            ],
            "connections": [],
            "variables": {"foo": {"type": "string", "defaultValue": "bar"}},
        }
        eid = engine.start_workflow(123, workflow_data, initiator="test-user")
        assert isinstance(eid, str)
        assert eid in engine._active_executions
        state = engine._active_executions[eid]
        assert state["workflow_id"] == 123
        assert state["workflow_name"] == "Test Workflow"
        assert state["status"] == "Running"
        assert state["paused"] is False
        assert state["cancelled"] is False

    def test_no_start_node_raises(self, engine, monkeypatch):
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = ("Test",)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        monkeypatch.setattr(engine, "get_db_connection", lambda: mock_conn)
        workflow_data = {"nodes": [{"id": "n1", "type": "X", "config": {}}]}
        with pytest.raises(ValueError, match="No start node"):
            engine.start_workflow(1, workflow_data)

    def test_workflow_not_found_raises(self, engine, monkeypatch):
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        monkeypatch.setattr(engine, "get_db_connection", lambda: mock_conn)
        with pytest.raises(ValueError, match="not found"):
            engine.start_workflow(999, {"nodes": []})


# =============================================================================
# _execute_node dispatch
# =============================================================================

class TestExecuteNodeDispatch:
    """Verify _execute_node routes to the right handler and produces next
    node based on the connection graph."""

    def _seed(self, engine, nodes, connections):
        engine._active_executions["exec1"] = {
            "workflow_data": {"nodes": nodes, "connections": connections},
            "status": "Running",
            "paused": False,
            "cancelled": False,
            "variables": {},
        }

    def test_unknown_node_type_succeeds_and_follows_pass(self, engine):
        """Unimplemented node types log a warning and follow the 'pass'
        connection (engine treats them as a success no-op)."""
        nodes = [
            {"id": "n1", "type": "Mystery", "config": {}},
            {"id": "n2", "type": "Mystery", "config": {}},
        ]
        connections = [{"source": "n1", "target": "n2", "type": "pass"}]
        self._seed(engine, nodes, connections)
        nxt = engine._execute_node("exec1", nodes[0], {})
        assert nxt == "n2"

    def test_set_variable_dispatches(self, engine):
        nodes = [
            {
                "id": "sv1",
                "type": "Set Variable",
                "config": {
                    "variableName": "foo",
                    "valueSource": "direct",
                    "valueExpression": "bar",
                },
            }
        ]
        self._seed(engine, nodes, [])
        variables = {}
        nxt = engine._execute_node("exec1", nodes[0], variables)
        assert variables.get("foo") == "bar"
        assert nxt is None  # no outgoing connection

    def test_no_pass_connection_returns_none(self, engine):
        nodes = [{"id": "n1", "type": "Mystery", "config": {}}]
        connections = [{"source": "n1", "target": "n2", "type": "fail"}]
        self._seed(engine, nodes, connections)
        nxt = engine._execute_node("exec1", nodes[0], {})
        # Only fail connection exists; default path returns None
        assert nxt is None


# =============================================================================
# _automation_stderr_tail (james 2026-07-23: debugger showed only 'exit code 1'
# while the real AttributeError sat in the run.log)
# =============================================================================

class TestAutomationStderrTail:
    def _write_log(self, tmp_path, stderr_body):
        log = ("automation: x (id)\nversion: v1\npython: py\n\n"
               "===== stdout =====\n(empty)\n\n"
               "===== stderr =====\n" + stderr_body + "\n\n"
               "===== result =====\nexit_code: 1  outcome: failed  (exit code 1)\n")
        (tmp_path / "run.log").write_text(log, encoding="utf-8")
        return str(tmp_path)

    def test_surfaces_the_actual_exception(self, engine, tmp_path):
        wd = self._write_log(
            tmp_path,
            'Traceback (most recent call last):\n'
            '  File "main.py", line 67, in <module>\n'
            '    for image_info in page.get_page_images(full=True):\n'
            "AttributeError: 'Page' object has no attribute 'get_page_images'")
        tail = engine._automation_stderr_tail(wd)
        assert "AttributeError: 'Page' object has no attribute" in tail
        assert "\n" not in tail  # single line for the debugger UI

    def test_empty_stderr_yields_empty(self, engine, tmp_path):
        assert engine._automation_stderr_tail(self._write_log(tmp_path, "(empty)")) == ""

    def test_missing_workdir_or_log_never_raises(self, engine, tmp_path):
        assert engine._automation_stderr_tail("") == ""
        assert engine._automation_stderr_tail(str(tmp_path / "nope")) == ""

    def test_tail_is_bounded(self, engine, tmp_path):
        wd = self._write_log(tmp_path, "\n".join(f"line {i}" * 30 for i in range(40)))
        assert len(engine._automation_stderr_tail(wd)) <= 600

    def test_wired_into_both_node_failure_paths(self):
        from pathlib import Path
        src = Path(__file__).resolve().parents[2].joinpath(
            "workflow_execution.py").read_text(encoding="utf-8", errors="replace")
        auto = src[src.find("def _execute_automation_node"):src.find("def _execute_code_step_node")]
        step = src[src.find("def _execute_code_step_node"):src.find("def _update_workflow_variable")]
        assert auto.count("_automation_stderr_tail") >= 1
        assert step.count("_automation_stderr_tail") >= 1
