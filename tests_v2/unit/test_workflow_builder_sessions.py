"""P2 tests — durable AI Workflow Builder sessions (Option A hardening).

Verifies serialize/restore round-trips the build state, and that the disk store
persists + reloads it (survives a "restart" = a fresh from_state). Hermetic: LLM
+ executor mocked; the store writes to a tmp dir.

NOTE: repo .gitignore ignores test*.py — commit with `git add -f`.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import config as cfg


@pytest.fixture
def WA(monkeypatch):
    import WorkflowAgent as _WA
    monkeypatch.setattr(_WA.WorkflowAgent, "_initialize_llm",
                        lambda self: setattr(self, "llm", MagicMock()))
    monkeypatch.setattr(_WA.WorkflowAgent, "_build_agent_executor",
                        lambda self: setattr(self, "agent_executor", MagicMock()))
    return _WA


def _populate(agent, WA):
    agent.phase = WA.BuilderPhase.PLANNING
    agent.requirements.process_name = "Invoice intake"
    agent.requirements.stakeholders = ["finance@example.com"]
    agent.requirements.data_sources = [{"source": "C:/invoices"}]
    agent.workflow_plan = "1. Folder Selector\n2. Document\n3. AI Extract"
    agent.generated_commands = [{"type": "add_node", "node_type": "Document"}]
    agent.conversation_context = [{"role": "user", "content": "automate invoices"},
                                  {"role": "assistant", "content": "here is a plan"}]
    agent.chat_history = [WA.HumanMessage(content="automate invoices"),
                          WA.AIMessage(content="here is a plan")]
    return agent


# ── serialize / restore round-trip ───────────────────────────────────────

def test_serialize_restore_roundtrip(WA):
    agent = _populate(WA.WorkflowAgent(session_id="rt-1"), WA)
    state = agent.serialize_state()

    restored = WA.WorkflowAgent.from_state(state)
    assert restored.session_id == "rt-1"
    assert restored.phase == WA.BuilderPhase.PLANNING
    assert restored.requirements.process_name == "Invoice intake"
    assert restored.requirements.stakeholders == ["finance@example.com"]
    assert restored.workflow_plan == agent.workflow_plan
    assert restored.generated_commands == agent.generated_commands
    assert restored.conversation_context == agent.conversation_context
    # chat_history round-trips to the right langchain message types
    assert [type(m).__name__ for m in restored.chat_history] == ["HumanMessage", "AIMessage"]
    assert restored.chat_history[0].content == "automate invoices"


def test_serialize_state_is_json_safe(WA):
    import json
    agent = _populate(WA.WorkflowAgent(session_id="rt-2"), WA)
    # Must serialize without error (no langchain objects leaking through).
    json.dumps(agent.serialize_state())


def test_from_state_tolerates_bad_phase(WA):
    agent = WA.WorkflowAgent(session_id="rt-3")
    state = agent.serialize_state()
    state["phase"] = "not-a-real-phase"
    restored = WA.WorkflowAgent.from_state(state)
    assert restored.phase == WA.BuilderPhase.DISCOVERY


# ── disk store: survives a "restart" ─────────────────────────────────────

@pytest.fixture
def store(monkeypatch, tmp_path):
    import builder_session_store as bss
    monkeypatch.setattr(bss, "_dir", lambda: str(tmp_path))
    monkeypatch.setattr(cfg, "WORKFLOW_DURABLE_SESSIONS", True, raising=False)
    return bss


def test_store_save_load_roundtrip(store, WA):
    agent = _populate(WA.WorkflowAgent(session_id="disk-1"), WA)
    assert store.save_session("disk-1", agent.serialize_state()) is True

    # "restart": nothing in memory; load from disk and rehydrate.
    loaded = store.load_session("disk-1")
    assert loaded is not None
    restored = WA.WorkflowAgent.from_state(loaded)
    assert restored.phase == WA.BuilderPhase.PLANNING
    assert restored.requirements.process_name == "Invoice intake"
    assert restored.workflow_plan == agent.workflow_plan


def test_store_missing_returns_none(store):
    assert store.load_session("nope") is None


def test_store_delete(store, WA):
    store.save_session("disk-2", WA.WorkflowAgent(session_id="disk-2").serialize_state())
    assert store.load_session("disk-2") is not None
    store.delete_session("disk-2")
    assert store.load_session("disk-2") is None


def test_store_disabled_is_noop(store, monkeypatch, WA):
    monkeypatch.setattr(cfg, "WORKFLOW_DURABLE_SESSIONS", False, raising=False)
    assert store.save_session("disk-3", WA.WorkflowAgent(session_id="disk-3").serialize_state()) is False
    assert store.load_session("disk-3") is None


def test_store_never_raises_on_bad_state(store):
    # Non-serializable content must not raise (best-effort persistence).
    class Weird:
        pass
    # default=str handles most; ensure it doesn't blow up the request path.
    assert store.save_session("disk-4", {"x": Weird()}) in (True, False)
