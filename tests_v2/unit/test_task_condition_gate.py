"""Tests for the conditional-task gate (evaluate_next_condition).

A conditional task must be pruned from the queue BEFORE it reaches an executor,
so execute_next_task and every tool handler stay unconditional-by-construction.

The bug this guards: CC was asked to email "only if the total exceeds $200,000",
the total was $87,432.50, and it emailed anyway — the qualifier survived into the
task description but nothing ever evaluated it.
"""

import sys
import json
import types
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

# builder_service ALSO ships a `graph` package, and conftest puts it ahead of
# command_center_service on sys.path — so `import graph.nodes` silently resolves
# to the builder's. Force CC to the front and drop any already-bound `graph*`
# modules, matching the convention in test_cc_chat_route.py.
_SVC_ROOT = Path(__file__).resolve().parents[2] / "command_center_service"
sys.path = [str(_SVC_ROOT)] + [p for p in sys.path if p != str(_SVC_ROOT)]
for _m in [m for m in list(sys.modules) if m == "graph" or m.startswith("graph.")]:
    del sys.modules[_m]


# ── helpers ────────────────────────────────────────────────────────────────
class _Resp:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    """Returns a canned extractor payload; records what it was asked."""

    def __init__(self, payload):
        self.payload = payload
        self.prompts = []

    async def ainvoke(self, msgs):
        self.prompts.append(msgs[0].content)
        if isinstance(self.payload, Exception):
            raise self.payload
        return _Resp(self.payload if isinstance(self.payload, str) else json.dumps(self.payload))


def _task(tid, condition=None, desc="Send a short email summary", status="pending"):
    return {"id": tid, "description": desc, "condition": condition,
            "target_tool": "send_email", "status": status}


def _state(tasks, idx=0, results=None):
    return {"sub_tasks": tasks, "current_task_index": idx,
            "delegation_results": results or {}, "session_id": "s", "user_context": {}}


@pytest.fixture
def nodes():
    import graph.nodes as n
    return n


async def _run(nodes, state, payload):
    llm = _FakeLLM(payload)
    with patch.object(nodes, "get_step_llm", create=True), \
         patch("cc_config.get_step_llm", return_value=llm), \
         patch("graph.tracing.trace_log", MagicMock()), \
         patch.object(nodes, "trace_llm_call", MagicMock()), \
         patch.object(nodes, "_build_prior_task_context",
                      return_value="Invoice count: 7\nTotal amount_usd: $87,432.50"):
        out = await nodes.evaluate_next_condition(state)
    return out, llm


# ── the number is compared in Python, never by the model ───────────────────
class TestNumericTier:
    @pytest.mark.asyncio
    async def test_below_threshold_is_skipped(self, nodes):
        """87,432.50 > 200,000 is false -> the email task must not run."""
        state = _state([_task("t1", status="completed"),
                        _task("t2", condition="the invoice total exceeds $200,000")], idx=1)
        out, _ = await _run(nodes, state,
                            {"kind": "numeric", "left": 87432.50, "op": ">", "right": 200000})
        assert out["current_task_index"] == 2
        assert out["sub_tasks"][1]["status"] == "skipped"
        assert "condition not met" in out["sub_tasks"][1]["skip_reason"]

    @pytest.mark.asyncio
    async def test_above_threshold_runs(self, nodes):
        state = _state([_task("t1", status="completed"),
                        _task("t2", condition="the invoice total exceeds $50,000")], idx=1)
        out, _ = await _run(nodes, state,
                            {"kind": "numeric", "left": 87432.50, "op": ">", "right": 50000})
        assert out == {}, "an allowed task must leave state untouched"

    @pytest.mark.asyncio
    async def test_currency_strings_are_coerced(self, nodes):
        """The extractor may echo '$87,432.50' rather than a bare number."""
        state = _state([_task("t1", status="completed"),
                        _task("t2", condition="the total exceeds $200,000")], idx=1)
        out, _ = await _run(nodes, state,
                            {"kind": "numeric", "left": "$87,432.50", "op": ">", "right": "$200,000"})
        assert out["sub_tasks"][1]["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_comparison_is_not_delegated_to_the_model(self, nodes):
        """Even if the model's payload implies otherwise, Python decides.
        A payload with no verdict field at all must still resolve correctly."""
        state = _state([_task("t1", status="completed"),
                        _task("t2", condition="the total is at least $90,000")], idx=1)
        out, _ = await _run(nodes, state,
                            {"kind": "numeric", "left": 87432.50, "op": ">=", "right": 90000})
        assert out["sub_tasks"][1]["status"] == "skipped"

    @pytest.mark.parametrize("op,left,right,should_run", [
        (">", 10, 5, True), (">", 5, 10, False),
        (">=", 5, 5, True), ("<", 1, 2, True),
        ("<=", 3, 2, False), ("==", 7, 7, True), ("!=", 7, 7, False),
    ])
    @pytest.mark.asyncio
    async def test_operators(self, nodes, op, left, right, should_run):
        state = _state([_task("t1", status="completed"), _task("t2", condition="x")], idx=1)
        out, _ = await _run(nodes, state,
                            {"kind": "numeric", "left": left, "op": op, "right": right})
        assert (out == {}) is should_run


# ── qualitative predicates fall back to the model's verdict ────────────────
class TestJudgmentTier:
    @pytest.mark.asyncio
    async def test_true_runs(self, nodes):
        state = _state([_task("t1", status="completed"),
                        _task("t2", condition="the report mentions overdue invoices")], idx=1)
        out, _ = await _run(nodes, state,
                            {"kind": "judgment", "verdict": "true", "reason": "two are overdue"})
        assert out == {}

    @pytest.mark.asyncio
    async def test_false_skips(self, nodes):
        state = _state([_task("t1", status="completed"),
                        _task("t2", condition="the report mentions overdue invoices")], idx=1)
        out, _ = await _run(nodes, state,
                            {"kind": "judgment", "verdict": "false", "reason": "none overdue"})
        assert out["sub_tasks"][1]["status"] == "skipped"
        assert "none overdue" in out["sub_tasks"][1]["skip_reason"]


# ── fail closed ────────────────────────────────────────────────────────────
class TestFailClosed:
    @pytest.mark.asyncio
    async def test_unknown_skips(self, nodes):
        state = _state([_task("t1", status="completed"), _task("t2", condition="x")], idx=1)
        out, _ = await _run(nodes, state, {"kind": "judgment", "verdict": "unknown"})
        assert out["sub_tasks"][1]["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_llm_error_skips(self, nodes):
        state = _state([_task("t1", status="completed"), _task("t2", condition="x")], idx=1)
        out, _ = await _run(nodes, state, RuntimeError("model down"))
        assert out["sub_tasks"][1]["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_unparseable_numeric_skips(self, nodes):
        state = _state([_task("t1", status="completed"), _task("t2", condition="x")], idx=1)
        out, _ = await _run(nodes, state,
                            {"kind": "numeric", "left": "not a number", "op": ">", "right": 5})
        assert out["sub_tasks"][1]["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_unknown_operator_skips(self, nodes):
        state = _state([_task("t1", status="completed"), _task("t2", condition="x")], idx=1)
        out, _ = await _run(nodes, state,
                            {"kind": "numeric", "left": 1, "op": "=~", "right": 5})
        assert out["sub_tasks"][1]["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_garbage_json_skips(self, nodes):
        state = _state([_task("t1", status="completed"), _task("t2", condition="x")], idx=1)
        out, _ = await _run(nodes, state, "not json at all")
        assert out["sub_tasks"][1]["status"] == "skipped"


# ── the safety property: null condition changes nothing ────────────────────
class TestUnconditionalPassthrough:
    @pytest.mark.asyncio
    async def test_null_condition_is_a_no_op(self, nodes):
        state = _state([_task("t1", condition=None)], idx=0)
        out, llm = await _run(nodes, state, {"kind": "numeric", "left": 1, "op": ">", "right": 0})
        assert out == {}
        assert llm.prompts == [], "an unconditional task must not cost an LLM call"

    @pytest.mark.asyncio
    async def test_empty_queue_is_a_no_op(self, nodes):
        out, _ = await _run(nodes, _state([], idx=0), {})
        assert out == {}

    @pytest.mark.asyncio
    async def test_index_past_end_is_a_no_op(self, nodes):
        out, _ = await _run(nodes, _state([_task("t1", condition="x")], idx=5), {})
        assert out == {}


# ── consecutive conditional tasks ──────────────────────────────────────────
class TestConsecutiveConditions:
    @pytest.mark.asyncio
    async def test_two_failing_conditions_are_both_skipped(self, nodes):
        """A task sitting right after a skipped one must still be judged —
        otherwise it reaches the executor unchecked."""
        state = _state([_task("t1", status="completed"),
                        _task("t2", condition="the total exceeds $200,000"),
                        _task("t3", condition="the total exceeds $300,000")], idx=1)
        out, _ = await _run(nodes, state,
                            {"kind": "numeric", "left": 87432.50, "op": ">", "right": 200000})
        assert out["sub_tasks"][1]["status"] == "skipped"
        assert out["sub_tasks"][2]["status"] == "skipped"
        assert out["current_task_index"] == 3

    @pytest.mark.asyncio
    async def test_stops_at_the_first_unconditional_task(self, nodes):
        state = _state([_task("t1", status="completed"),
                        _task("t2", condition="the total exceeds $200,000"),
                        _task("t3", condition=None)], idx=1)
        out, _ = await _run(nodes, state,
                            {"kind": "numeric", "left": 87432.50, "op": ">", "right": 200000})
        assert out["sub_tasks"][1]["status"] == "skipped"
        assert out["sub_tasks"][2]["status"] == "pending"
        assert out["current_task_index"] == 2


# ── number coercion ────────────────────────────────────────────────────────
class TestCoerceNumber:
    def test_formats(self, nodes):
        c = nodes._coerce_number
        assert c("$87,432.50") == 87432.50
        assert c(87432.5) == 87432.5
        assert c("200000") == 200000.0
        assert c("-12.5") == -12.5
        assert c("1,234") == 1234.0

    def test_rejects_non_numbers(self, nodes):
        c = nodes._coerce_number
        assert c("not a number") is None
        assert c(None) is None
        assert c(True) is None, "a bool must not be read as 1"
        assert c("") is None


# ── wiring guards ──────────────────────────────────────────────────────────
def _src(rel):
    return (Path(__file__).resolve().parents[2] / rel).read_text(encoding="utf-8")


def test_gate_runs_before_the_executor_not_inside_it():
    """The queue is pruned upstream; the executor stays dumb."""
    s = _src("command_center_service/graph/cc_graph.py")
    assert 'graph.add_edge("execute_next_task", "evaluate_next_condition")' in s
    assert '"evaluate_next_condition",\n        wrap_router("route_task_loop"' in s
    # The loop must be ENTERED through the check, or task 1 runs unjudged.
    assert '"execute_next_task": "evaluate_next_condition"' in s


def test_executor_has_no_condition_logic():
    """If a gate ever appears inside execute_next_task, this design was lost."""
    import re as _re
    s = _src("command_center_service/graph/nodes.py")
    start = s.index("async def execute_next_task")
    # End at the next line beginning in column 0 (any top-level statement, not
    # just a def) so the span is this function's body and nothing after it.
    nxt = _re.search(r"\n(?=[A-Za-z_@])", s[start + 10:])
    body = s[start:start + 10 + nxt.start()] if nxt else s[start:]
    assert "condition" not in body.lower()


def test_decomposer_copies_the_predicate_and_leaves_description_alone():
    s = _src("command_center_service/graph/nodes.py")
    assert '"condition": "predicate or null"' in s
    assert "never a rewrite" in s


def test_aggregate_must_report_skips():
    s = _src("command_center_service/graph/nodes.py")
    assert 'status "skipped" DID NOT HAPPEN' in s
