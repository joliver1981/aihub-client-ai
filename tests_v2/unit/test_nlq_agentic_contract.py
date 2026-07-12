"""Unit tests — contract mapping, state, and engine surface (NLQ V3 P3).

Verifies the agentic engine is shape-compatible with LLMDataEngine: the 8-tuple
and rich-dict return shapes, table/text/unresolved answers, the empty-schema
guard, and the fallback-to-legacy wall. No network/DB.

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
from nlq_agentic.loop import LoopResult
from nlq_agentic import contract


# ── a minimal engine stub for the rich-content renderer hook ─────────────

class _RenderEngine:
    def format_response_with_rich_content(self, answer, answer_type, context=None):
        return {"type": "rich_content", "blocks": [{"type": "text", "content": str(answer)}]}, "rich_content"


class _NullEngine:
    """Used when rich content is disabled — should never be called."""
    def format_response_with_rich_content(self, *a, **k):
        raise AssertionError("rich renderer called while disabled")


@pytest.fixture(autouse=True)
def _plain_mode(monkeypatch):
    # Default most tests to the 8-tuple shape; rich-dict has its own test.
    monkeypatch.setattr(cfg, "ENABLE_RICH_CONTENT_RENDERING", False, raising=False)


def _state_with_dataset():
    st = AgentSessionState()
    df = pd.DataFrame({"name": ["A", "B"], "revenue": [10, 20]})
    ref = st.add_dataset(df, "SELECT name, revenue FROM t")
    st.current_query = "SELECT name, revenue FROM t"
    return st, ref, df


# ── state ────────────────────────────────────────────────────────────────

def test_state_add_dataset_increments_refs():
    st = AgentSessionState()
    r1 = st.add_dataset(pd.DataFrame({"a": [1]}), "SELECT a FROM t")
    r2 = st.add_dataset(pd.DataFrame({"b": [2]}), "SELECT b FROM u")
    assert r1 == "dataset_1" and r2 == "dataset_2"
    assert st.get_dataset("dataset_1")["row_count"] == 1
    assert "dataset_1" in st.datasets_summary()


def test_state_history_roundtrip():
    st = AgentSessionState()
    st.add_message("hi", is_user=True)
    st.add_message("hello", is_user=False)
    txt = st.recent_history_text()
    assert "User: hi" in txt and "Assistant: hello" in txt


# ── contract: core shapes ────────────────────────────────────────────────

def test_text_answer_tuple():
    st = AgentSessionState()
    lr = LoopResult(terminal={"answer_kind": "text", "text": "42 orders", "dataset_ref": None}, iterations=2)
    res = contract.build_result(lr, st, "how many?", _NullEngine())
    answer, explain, clarify, atype, special, q, revised, return_query = res
    assert atype == "string" and answer == "42 orders" and q == "how many?"


def test_table_answer_returns_dataframe():
    st, ref, df = _state_with_dataset()
    lr = LoopResult(terminal={"answer_kind": "table", "text": "top rows", "dataset_ref": ref}, iterations=3)
    res = contract.build_result(lr, st, "show me", _NullEngine())
    answer, explain, clarify, atype, special, _, _, return_query = res
    assert atype == "dataframe"
    assert isinstance(answer, pd.DataFrame) and len(answer) == 2
    assert "=== Data Query ===" in return_query
    assert explain == "top rows"


def test_table_answer_bad_ref_falls_back_to_last_dataset():
    st, ref, df = _state_with_dataset()
    lr = LoopResult(terminal={"answer_kind": "table", "text": "", "dataset_ref": "dataset_999"}, iterations=3)
    res = contract.build_result(lr, st, "show", _NullEngine())
    assert res[3] == "dataframe" and isinstance(res[0], pd.DataFrame)


def test_table_answer_no_dataset_degrades_to_text():
    st = AgentSessionState()
    lr = LoopResult(terminal={"answer_kind": "table", "text": "here", "dataset_ref": "dataset_1"}, iterations=2)
    res = contract.build_result(lr, st, "show", _NullEngine())
    assert res[3] == "string" and res[0] == "here"


def test_unresolved_returns_fallback_string():
    st = AgentSessionState()
    lr = LoopResult(terminal=None, iterations=8)
    res = contract.build_result(lr, st, "q", _NullEngine())
    assert res[3] == "string" and res[0] == cfg.DATA_AGENT_FALLBACK_RESPONSE


def test_timeout_returns_time_message():
    st = AgentSessionState()
    lr = LoopResult(terminal=None, timed_out=True, iterations=4)
    res = contract.build_result(lr, st, "q", _NullEngine())
    assert res[3] == "string" and "time" in res[0].lower()


def test_empty_text_answer_uses_fallback():
    st = AgentSessionState()
    lr = LoopResult(terminal={"answer_kind": "text", "text": "  ", "dataset_ref": None}, iterations=1)
    res = contract.build_result(lr, st, "q", _NullEngine())
    assert res[0] == cfg.DATA_AGENT_FALLBACK_RESPONSE


# ── contract: rich-dict shape ────────────────────────────────────────────

def test_rich_dict_shape(monkeypatch):
    monkeypatch.setattr(cfg, "ENABLE_RICH_CONTENT_RENDERING", True, raising=False)
    st = AgentSessionState()
    lr = LoopResult(terminal={"answer_kind": "text", "text": "hi", "dataset_ref": None}, iterations=1)
    res = contract.build_result(lr, st, "q", _RenderEngine())
    assert isinstance(res, dict)
    for key in ("answer", "answer_type", "rich_content", "rich_content_enabled",
                "explain", "clarify", "special_message", "query"):
        assert key in res
    assert res["answer"] == "hi" and res["rich_content_enabled"] is True


# ── engine surface (no LLM / DB) ─────────────────────────────────────────

def test_engine_constructs_and_pickles():
    import pickle
    from nlq_agentic import AgenticNLQEngine
    eng = AgenticNLQEngine()
    eng.add_message_to_hist("hi", is_user=True)
    eng._client = object()  # simulate a live client
    blob = pickle.dumps(eng)
    eng2 = pickle.loads(blob)
    assert eng2._client is None                 # dropped on pickle
    assert eng2.environment is eng2.state        # alias re-established
    assert eng2.question_count == 0
    assert eng2.state.chat_history[0]["content"] == "hi"


def test_engine_history_helpers():
    from nlq_agentic import AgenticNLQEngine
    eng = AgenticNLQEngine()
    eng.add_message_to_hist("q1", is_user=True)
    eng.clear_chat_hist()
    assert eng.state.chat_history == []
    eng.set_conversation_history("[{'role':'Q','content':'hello'},{'role':'A','content':'hi'}]")
    assert len(eng.state.chat_history) == 2
    assert eng.state.chat_history[0]["role"] == "user"
    assert eng.explain() == ""


def test_engine_empty_schema_guard(monkeypatch):
    from nlq_agentic import AgenticNLQEngine
    import DataUtils
    monkeypatch.setattr(DataUtils, "get_connection_string", lambda aid: ("CONN", 1, "SQL Server"))
    monkeypatch.setattr(DataUtils, "query_app_database", lambda q, p=None: [])
    monkeypatch.setattr(DataUtils, "get_enhanced_table_metadata_as_yaml", lambda cid: "")
    monkeypatch.setattr(DataUtils, "get_table_descriptions_as_yaml", lambda cid: "")

    import nlq_engine_factory as factory
    factory.agentic_breaker.record_success()  # ensure closed

    eng = AgenticNLQEngine()
    res = eng.get_answer(42, "how many orders?")
    answer = res["answer"] if isinstance(res, dict) else res[0]
    assert "hasn't been set up" in answer or "documented" in answer


def test_engine_falls_back_to_legacy_on_failure(monkeypatch):
    from nlq_agentic import AgenticNLQEngine
    import nlq_engine_factory as factory

    monkeypatch.setattr(cfg, "NLQ_AGENTIC_FALLBACK", True, raising=False)
    factory.agentic_breaker.record_success()

    # Make the agentic path blow up as early as possible.
    def boom(self, agent_id):
        raise RuntimeError("kaboom in target-db resolution")
    monkeypatch.setattr(AgenticNLQEngine, "_set_target_database", boom)

    class _FakeLegacy:
        def clear_chat_hist(self): pass
        def add_message_to_hist(self, *a, **k): pass
        def get_answer(self, agent_id, q, recursion_depth=0):
            return ("LEGACY_ANSWER", "", "", "string", "", q, "", "")
    monkeypatch.setattr(factory, "_construct_legacy", lambda enhance=False: _FakeLegacy())

    eng = AgenticNLQEngine()
    res = eng.get_answer(7, "q")
    answer = res[0] if isinstance(res, tuple) else res.get("answer")
    assert answer == "LEGACY_ANSWER"


def test_engine_fallback_disabled_returns_error(monkeypatch):
    from nlq_agentic import AgenticNLQEngine
    import nlq_engine_factory as factory

    monkeypatch.setattr(cfg, "NLQ_AGENTIC_FALLBACK", False, raising=False)
    factory.agentic_breaker.record_success()

    def boom(self, agent_id):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(AgenticNLQEngine, "_set_target_database", boom)

    eng = AgenticNLQEngine()
    res = eng.get_answer(7, "q")
    answer = res[0] if isinstance(res, tuple) else res.get("answer")
    assert answer == cfg.DATA_AGENT_FALLBACK_RESPONSE
