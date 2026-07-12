"""Unit tests — P4 additions: charts, formatting, ask_user, dataset reuse (NLQ V3).

No network/DB: chart rendering is real matplotlib (Agg); formatting monkeypatches
the dictionary lookup; the loop uses the ScriptedClient from the P3 loop tests.

NOTE: repo .gitignore ignores test*.py — commit with `git add -f`.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import config as cfg
from nlq_agentic.state import AgentSessionState
from nlq_agentic.tools import ToolContext, build_tool_schemas, execute_tool
from nlq_agentic.loop import run_loop, LoopResult
from nlq_agentic import contract, charts, formatting


# ── minimal fake OpenAI client (mirrors the P3 loop tests) ───────────────

class _Fn:
    def __init__(self, name, arguments):
        self.name, self.arguments = name, arguments


class _ToolCall:
    def __init__(self, id, name, arguments):
        self.id, self.type, self.function = id, "function", _Fn(name, arguments)


class _Msg:
    def __init__(self, content=None, tool_calls=None):
        self.content, self.tool_calls = content, tool_calls


class _Choice:
    def __init__(self, message):
        self.message = message


class _Resp:
    def __init__(self, message):
        self.choices = [_Choice(message)]


class ScriptedClient:
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
        return self._responses.pop(0) if self._responses else _Resp(_Msg(content="(exhausted)"))


def _tool_calls(*calls):
    import json
    tcs = [_ToolCall(f"call_{i}", name, json.dumps(args)) for i, (name, args) in enumerate(calls)]
    return _Resp(_Msg(content=None, tool_calls=tcs))


@pytest.fixture(autouse=True)
def _plain_mode(monkeypatch):
    monkeypatch.setattr(cfg, "ENABLE_RICH_CONTENT_RENDERING", False, raising=False)


@pytest.fixture
def ctx():
    st = AgentSessionState()
    st.connection_string = "FAKE"
    st.database_type = "SQL Server"
    st.connection_id = 1
    return ToolContext(st, row_cap=10000)


# ── charts.render_chart (real matplotlib) ────────────────────────────────

def test_render_bar_chart_returns_base64():
    df = pd.DataFrame({"category": ["A", "B", "C"], "revenue": [10, 20, 30]})
    b64, err = charts.render_chart(df, "bar", x_column="category", y_column="revenue", title="Rev")
    assert err is None and isinstance(b64, str) and len(b64) > 100
    assert charts.img_html(b64).startswith('<img src="data:image/png;base64,')


def test_render_chart_bad_column_returns_error():
    df = pd.DataFrame({"a": [1, 2]})
    b64, err = charts.render_chart(df, "bar", x_column="nope", y_column="a")
    assert b64 is None and "nope" in err


def test_render_chart_empty_df():
    b64, err = charts.render_chart(pd.DataFrame(), "bar")
    assert b64 is None and "empty" in err


# ── create_chart / get_dataset_preview handlers ──────────────────────────

def test_create_chart_stores_pending_chart(ctx):
    df = pd.DataFrame({"category": ["A", "B"], "revenue": [10, 20]})
    ref = ctx.state.add_dataset(df, "SELECT ...")
    out = execute_tool(ctx, "create_chart",
                       {"dataset_ref": ref, "chart_type": "bar",
                        "x_column": "category", "y_column": "revenue", "title": "t"})
    assert "rendered successfully" in out.lower()
    assert ctx.state.pending_chart and ctx.state.pending_chart["html"].startswith("<img")


def test_create_chart_missing_dataset(ctx):
    out = execute_tool(ctx, "create_chart",
                       {"dataset_ref": "dataset_9", "chart_type": "bar",
                        "x_column": "a", "y_column": "b", "title": ""})
    assert "no dataset" in out.lower() and ctx.state.pending_chart is None


def test_create_chart_bad_column_reports_and_no_pending(ctx):
    df = pd.DataFrame({"a": [1, 2]})
    ref = ctx.state.add_dataset(df, "SELECT a")
    out = execute_tool(ctx, "create_chart",
                       {"dataset_ref": ref, "chart_type": "bar",
                        "x_column": "missing", "y_column": "a", "title": ""})
    assert "could not be rendered" in out.lower()
    assert ctx.state.pending_chart is None


def test_get_dataset_preview(ctx):
    df = pd.DataFrame({"a": range(50)})
    ref = ctx.state.add_dataset(df, "SELECT a")
    out = execute_tool(ctx, "get_dataset_preview", {"dataset_ref": ref, "n_rows": 5})
    assert ref in out and "of 50 rows" in out


def test_get_dataset_preview_unknown_ref(ctx):
    out = execute_tool(ctx, "get_dataset_preview", {"dataset_ref": "nope", "n_rows": 5})
    assert "no dataset" in out.lower()


# ── contract: chart + ask_user paths ─────────────────────────────────────

def test_contract_chart_answer():
    st = AgentSessionState()
    st.add_dataset(pd.DataFrame({"a": [1]}), "SELECT a")
    st.pending_chart = {"b64": "ABC", "html": '<img src="data:image/png;base64,ABC"/>', "dataset_ref": "dataset_1"}
    lr = LoopResult(terminal={"tool": "respond", "answer_kind": "chart", "text": "cap", "dataset_ref": None})
    answer, explain, clarify, atype, special, _, _, _ = contract.build_result(lr, st, "chart it", _Eng())
    assert atype == "chart" and answer == "See chart..."
    assert "data:image/png;base64" in special and explain == "cap"


def test_contract_chart_without_render_degrades_to_table():
    st = AgentSessionState()
    st.add_dataset(pd.DataFrame({"a": [1, 2]}), "SELECT a")
    st.pending_chart = None
    lr = LoopResult(terminal={"tool": "respond", "answer_kind": "chart", "text": "", "dataset_ref": None})
    res = contract.build_result(lr, st, "q", _Eng())
    assert res[3] == "dataframe"


def test_contract_ask_user_sets_clarify():
    st = AgentSessionState()
    lr = LoopResult(terminal={"tool": "ask_user", "answer_kind": "text",
                              "text": "Which year did you mean?", "dataset_ref": None})
    answer, explain, clarify, atype, special, _, _, _ = contract.build_result(lr, st, "q", _Eng())
    assert atype == "string" and clarify == "Which year did you mean?" and answer == clarify


class _Eng:
    pass


# ── deterministic formatting ─────────────────────────────────────────────

def test_format_currency_and_percent(monkeypatch):
    monkeypatch.setattr(formatting, "load_column_formats", lambda cid: {
        "revenue": {"format": "currency", "units": "USD"},
        "margin": {"format": "percentage", "units": ""},
    })
    df = pd.DataFrame({"revenue": [1234.5, 10], "margin": [12.3, 4.0], "name": ["a", "b"]})
    out = formatting.format_dataframe_for_display(df, connection_id=1)
    assert out["revenue"].iloc[0] == "$1,234.50"
    assert out["margin"].iloc[0] == "12.3%"
    assert out["name"].iloc[0] == "a"   # untouched


def test_format_leaves_unknown_formats_alone(monkeypatch):
    monkeypatch.setattr(formatting, "load_column_formats", lambda cid: {
        "code": {"format": "identifier", "units": ""},
    })
    df = pd.DataFrame({"code": [1, 2, 3]})
    out = formatting.format_dataframe_for_display(df, connection_id=1)
    assert list(out["code"]) == [1, 2, 3]


def test_format_no_dictionary_is_noop(monkeypatch):
    monkeypatch.setattr(formatting, "load_column_formats", lambda cid: {})
    df = pd.DataFrame({"revenue": [1.0]})
    out = formatting.format_dataframe_for_display(df, connection_id=1)
    assert out["revenue"].iloc[0] == 1.0


def test_format_does_not_mutate_source(monkeypatch):
    monkeypatch.setattr(formatting, "load_column_formats", lambda cid: {
        "revenue": {"format": "currency", "units": "USD"}})
    df = pd.DataFrame({"revenue": [5.0]})
    _ = formatting.format_dataframe_for_display(df, connection_id=1)
    assert df["revenue"].iloc[0] == 5.0   # original untouched (chart safety)


# ── end-to-end loop: query -> chart -> respond(chart) ────────────────────

def test_loop_chart_flow(monkeypatch, ctx):
    df = pd.DataFrame({"category": ["A", "B"], "revenue": [10, 20]})

    def fake_exec(q, c):
        return df, None
    import AppUtils
    monkeypatch.setattr(AppUtils, "execute_sql_query_v2", fake_exec)

    schemas = build_tool_schemas(strict=True)
    client = ScriptedClient([
        _tool_calls(("run_sql", {"query": "SELECT category, revenue FROM t"})),
        _tool_calls(("create_chart", {"dataset_ref": "dataset_1", "chart_type": "bar",
                                       "x_column": "category", "y_column": "revenue", "title": "R"})),
        _tool_calls(("respond", {"answer_kind": "chart", "text": "here's the chart", "dataset_ref": None})),
    ])
    res = run_loop(client, {"model": "m"}, "sys", "chart revenue by category", schemas, ctx, max_iterations=8)
    assert res.terminal["answer_kind"] == "chart"
    assert ctx.state.pending_chart is not None
    out = contract.build_result(res, ctx.state, "q", _Eng())
    assert out[3] == "chart" and "data:image/png;base64" in out[4]


def test_loop_ask_user_flow(ctx):
    schemas = build_tool_schemas(strict=True)
    client = ScriptedClient([
        _tool_calls(("ask_user", {"question": "Which region?"})),
    ])
    res = run_loop(client, {"model": "m"}, "sys", "sales?", schemas, ctx, max_iterations=8)
    assert res.terminal["tool"] == "ask_user"
    out = contract.build_result(res, ctx.state, "q", _Eng())
    assert out[2] == "Which region?"   # clarify field
