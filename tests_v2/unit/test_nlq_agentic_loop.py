"""Unit tests — agentic loop + tools with a mocked OpenAI client (NLQ V3 P3).

No network, no database: a ScriptedClient replays canned tool_calls/answers, and
the DB-touching utilities (execute_sql_query_v2, schema YAML) are monkeypatched.
Exercises the loop's core behaviors: normal flow, SQL error self-repair, the
iteration cap, direct answers, malformed args, and that run_sql is gated.

NOTE: repo .gitignore ignores test*.py — commit with `git add -f`.
"""
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from nlq_agentic.state import AgentSessionState
from nlq_agentic.tools import ToolContext, build_tool_schemas, execute_tool
from nlq_agentic.loop import run_loop


# ── fake OpenAI client ───────────────────────────────────────────────────

class _Fn:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, id, name, arguments):
        self.id = id
        self.type = "function"
        self.function = _Fn(name, arguments)


class _Msg:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, message):
        self.message = message


class _Resp:
    def __init__(self, message):
        self.choices = [_Choice(message)]


class ScriptedClient:
    """Replays a fixed list of responses; records the kwargs of each create()."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            # Safety: if the script under-provisions, answer directly to end the loop.
            return _Resp(_Msg(content="(script exhausted)"))
        return self._responses.pop(0)


def _tool_calls(*calls, raw=None):
    """calls: (name, args_dict); raw overrides arguments string for index-0 (bad JSON tests)."""
    tcs = []
    for i, (name, args) in enumerate(calls):
        arguments = raw if (raw is not None and i == 0) else json.dumps(args)
        tcs.append(_ToolCall(f"call_{i}", name, arguments))
    return _Resp(_Msg(content=None, tool_calls=tcs))


def _text(content):
    return _Resp(_Msg(content=content, tool_calls=None))


# ── fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def ctx():
    st = AgentSessionState()
    st.connection_string = "FAKE_CONN"
    st.database_type = "SQL Server"
    st.connection_id = 999
    return ToolContext(st, row_cap=10000)


@pytest.fixture
def schemas():
    return build_tool_schemas(strict=True)


def _patch_sql(monkeypatch, results):
    """results: list of (df, error) tuples returned by successive execute_sql_query_v2 calls."""
    seq = list(results)
    calls = {"n": 0}

    def fake_exec(query, conn):
        calls["n"] += 1
        return seq.pop(0) if seq else (None, "no more scripted results")

    import AppUtils
    monkeypatch.setattr(AppUtils, "execute_sql_query_v2", fake_exec)
    return calls


# ── tests ────────────────────────────────────────────────────────────────

def test_normal_flow_details_query_respond(monkeypatch, ctx, schemas):
    df = pd.DataFrame({"order_count": [1234]})
    _patch_sql(monkeypatch, [(df, None)])
    import DataUtils
    monkeypatch.setattr(DataUtils, "get_enhanced_full_schema_with_column_details_as_yaml",
                        lambda tables, cid: "Orders:\n  columns: [order_id, total]")

    client = ScriptedClient([
        _tool_calls(("get_table_details", {"tables": ["Orders"]})),
        _tool_calls(("run_sql", {"query": "SELECT COUNT(*) AS order_count FROM Orders"})),
        _tool_calls(("respond", {"answer_kind": "table", "text": "Here you go", "dataset_ref": "dataset_1"})),
    ])
    res = run_loop(client, {"model": "m"}, "sys", "how many orders?", schemas, ctx, max_iterations=8)

    assert res.terminal["answer_kind"] == "table"
    assert res.terminal["dataset_ref"] == "dataset_1"
    assert res.iterations == 3
    assert "dataset_1" in ctx.state.datasets
    assert ctx.state.datasets["dataset_1"]["row_count"] == 1


def test_sql_error_triggers_self_repair(monkeypatch, ctx, schemas):
    good = pd.DataFrame({"n": [5]})
    _patch_sql(monkeypatch, [(None, "Invalid column 'totl'"), (good, None)])

    client = ScriptedClient([
        _tool_calls(("run_sql", {"query": "SELECT totl FROM Orders"})),
        _tool_calls(("run_sql", {"query": "SELECT total FROM Orders"})),
        _tool_calls(("respond", {"answer_kind": "text", "text": "5", "dataset_ref": None})),
    ])
    res = run_loop(client, {"model": "m"}, "sys", "q", schemas, ctx, max_iterations=8)

    assert res.terminal["answer_kind"] == "text"
    assert res.terminal["text"] == "5"
    assert res.iterations == 3


def test_iteration_cap_returns_unresolved(monkeypatch, ctx, schemas):
    import DataUtils
    monkeypatch.setattr(DataUtils, "get_enhanced_full_schema_with_column_details_as_yaml",
                        lambda tables, cid: "yaml")
    # Always ask for more table details, never respond.
    client = ScriptedClient([_tool_calls(("get_table_details", {"tables": ["T"]})) for _ in range(10)])
    res = run_loop(client, {"model": "m"}, "sys", "q", schemas, ctx, max_iterations=3)

    assert res.terminal is None
    assert res.iterations == 3


def test_direct_answer_without_tool_call(ctx, schemas):
    client = ScriptedClient([_text("The answer is 42.")])
    res = run_loop(client, {"model": "m"}, "sys", "q", schemas, ctx, max_iterations=8)

    assert res.terminal["answer_kind"] == "text"
    assert "42" in res.terminal["text"]
    assert res.iterations == 1


def test_malformed_tool_args_are_fed_back(monkeypatch, ctx, schemas):
    good = pd.DataFrame({"n": [1]})
    _patch_sql(monkeypatch, [(good, None)])
    client = ScriptedClient([
        _tool_calls(("run_sql", {}), raw="{not valid json"),   # malformed arguments
        _tool_calls(("run_sql", {"query": "SELECT 1 AS n"})),
        _tool_calls(("respond", {"answer_kind": "text", "text": "1", "dataset_ref": None})),
    ])
    res = run_loop(client, {"model": "m"}, "sys", "q", schemas, ctx, max_iterations=8)
    assert res.terminal["text"] == "1"
    # The malformed call must have produced a tool message telling it to resend.
    assert res.iterations == 3


def test_completion_exception_returns_error(ctx, schemas):
    class Boom:
        @property
        def chat(self): return self
        @property
        def completions(self): return self
        def create(self, **kwargs): raise RuntimeError("api down")
    res = run_loop(Boom(), {"model": "m"}, "sys", "q", schemas, ctx, max_iterations=8)
    assert res.terminal is None
    assert "api down" in res.error


# ── tool handler gating ──────────────────────────────────────────────────

def test_run_sql_blocks_write_without_executing(monkeypatch, ctx):
    import AppUtils

    def must_not_run(query, conn):
        raise AssertionError("execute_sql_query_v2 must not run for a blocked query")
    monkeypatch.setattr(AppUtils, "execute_sql_query_v2", must_not_run)

    out = execute_tool(ctx, "run_sql", {"query": "DROP TABLE Orders"})
    assert "REJECTED" in out
    assert not ctx.state.datasets


def test_run_sql_success_stores_dataset_and_caps(monkeypatch, ctx):
    df = pd.DataFrame({"a": range(3)})
    captured = {}

    def fake_exec(query, conn):
        captured["query"] = query
        return df, None
    import AppUtils
    monkeypatch.setattr(AppUtils, "execute_sql_query_v2", fake_exec)

    out = execute_tool(ctx, "run_sql", {"query": "SELECT a FROM t"})
    assert "dataset_1" in out
    assert ctx.state.datasets["dataset_1"]["row_count"] == 3
    # Row cap injected (SQL Server -> TOP) since the query had none.
    assert "TOP" in captured["query"].upper()


def test_get_table_details_returns_schema(monkeypatch, ctx):
    import DataUtils
    monkeypatch.setattr(DataUtils, "get_enhanced_full_schema_with_column_details_as_yaml",
                        lambda tables, cid: "SCHEMA_FOR:" + ",".join(tables))
    out = execute_tool(ctx, "get_table_details", {"tables": ["Orders", "Customers"]})
    assert out.startswith("SCHEMA_FOR:Orders,Customers")
