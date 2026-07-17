"""
AIHUB-0046 — an affirmative reply to a held builder plan EXECUTES it; held
plans read as awaiting confirmation; workflow-agent steps classify as mutating.

Live failure (0038 R1/R2 retests, two independent conversations): a held plan
needed ~3 turns + a magic "execute the plan now" phrasing because
  - _is_affirmative rejected "Yes, I confirm. Execute the plan now and actually
    create/save the workflow." (length>60 guard) and "build and save it now"
    (new-request-verb guard),
  - auto-confirm trigger (a) required the literal "shall i go ahead/proceed"
    phrasing in the builder reply (live phrasing varies),
  - the workflow_agent delegation step classified mutating=False
    (domain='agent'/action='workflow_agent' matches no mutating_actions entry),
  - the held-turn reply speculated "can't confirm it was created… double-check".

These tests drive the REAL build() node with a mocked delegator that records
every call (the same harness as test_cc_build_distiller_pin).
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_CC = str(_ROOT / "command_center_service")


def _import_cc_nodes():
    saved_path = list(sys.path)
    saved_graph = {k: v for k, v in sys.modules.items() if k == "graph" or k.startswith("graph.")}
    try:
        for k in list(saved_graph):
            del sys.modules[k]
        sys.path.insert(0, _CC)
        import graph.nodes as cc_nodes  # noqa: PLC0415
        assert "command_center_service" in cc_nodes.__file__.replace("\\", "/")
        return cc_nodes
    finally:
        sys.path[:] = saved_path


try:
    nodes = _import_cc_nodes()
    from langchain_core.messages import HumanMessage
except Exception as e:  # pragma: no cover
    pytest.skip(f"CC graph.nodes not importable here: {e}", allow_module_level=True)


HELD_PLAN = {"status": "draft", "steps": [
    {"description": "Create and save workflow truth-test-4 via the workflow agent",
     "domain": "agent", "action": "workflow_agent", "status": "pending"},
]}

HELD_RESULT = {
    "text": "Here is the proposed plan for truth-test-4. Confirm and I will build it.",
    "status": "completed",
    "plan": HELD_PLAN,
    "builder_session_id": "b-0046",
    "workflow_saved": None,
    "dropped_capability": None,
}

EXECUTED_RESULT = {
    "text": "Built truth-test-4.",
    "status": "completed",
    "plan": {"status": "completed", "steps": [dict(HELD_PLAN["steps"][0], status="completed")]},
    "builder_session_id": "b-0046",
    "workflow_saved": {"workflow_id": 1264, "status": "success",
                       "node_types": ["Database", "File"],
                       "nodes": [{"type": "Database", "configured": False},
                                 {"type": "File", "configured": False}],
                       "source": "db_readback"},
    "dropped_capability": None,
}


def _state(text):
    return {
        "messages": [HumanMessage(content=text)],
        "session_id": "t-0046",
        "user_context": {"user_id": 1, "role": 3, "tenant_id": 1, "username": "admin"},
        "active_delegation": {"agent_type": "builder", "builder_session_id": "b-0046",
                              "build_status": "in_progress", "builder_log": []},
    }


def _patch(monkeypatch, results):
    """Mock the delegator to pop from `results` per call, recording messages."""
    import command_center.orchestration.delegator as delegator_mod
    calls = []

    async def _fake_delegate(**kwargs):
        calls.append(kwargs.get("message"))
        return dict(results[min(len(calls) - 1, len(results) - 1)])

    monkeypatch.setattr(delegator_mod, "delegate_to_builder", _fake_delegate)

    import cc_config

    class _DistillLLM:
        async def ainvoke(self, msgs):
            class _R:
                content = "Distilled summary."
            return _R()

    llm_requests = []

    def _fake_get_step_llm(step_name):
        llm_requests.append(step_name)
        return _DistillLLM()

    monkeypatch.setattr(cc_config, "get_step_llm", _fake_get_step_llm)
    return calls, llm_requests


class TestAffirmativeExecutesHeldPlan:
    async def test_long_affirmative_triggers_execution(self, monkeypatch):
        """The live magic-words case, now via ANY yes-prefixed reply (>60 chars)."""
        calls, llm_requests = _patch(monkeypatch, [HELD_RESULT, EXECUTED_RESULT])
        out = await nodes.build(_state(
            "Yes, I confirm. Execute the plan now and actually create/save the workflow please."))
        assert len(calls) == 2                                  # auto-confirm second call
        assert calls[1] == "Yes, confirmed. Execute the plan now."
        # deterministic prefix rule — the affirmative mini-LLM was never needed
        assert "builder_affirmative_detector" not in llm_requests
        reply = out["messages"][0].content
        assert "Nothing has been built yet" not in reply        # executed, not held

    async def test_plain_yes_works_without_asking_phrases(self, monkeypatch):
        """Trigger (a) no longer needs 'shall i go ahead' wording in the reply —
        a held draft plan alone counts as awaiting confirmation."""
        calls, _ = _patch(monkeypatch, [HELD_RESULT, EXECUTED_RESULT])
        await nodes.build(_state("yes"))
        assert len(calls) == 2

    async def test_new_request_never_auto_confirms(self, monkeypatch):
        """BUG-R2-017 guard intact: a fresh request is not an affirmation."""
        calls, _ = _patch(monkeypatch, [HELD_RESULT])
        out = await nodes.build(_state("create an agent that emails me a daily digest"))
        assert len(calls) == 1                                  # no auto-confirm
        assert "Nothing has been built yet" in out["messages"][0].content

    async def test_question_marked_yes_never_auto_confirms(self, monkeypatch):
        calls, _ = _patch(monkeypatch, [HELD_RESULT])
        await nodes.build(_state("yes? what exactly will step 1 do"))
        assert len(calls) == 1


class TestHeldPlanFooter:
    async def test_held_plan_carries_awaiting_footer(self, monkeypatch):
        calls, _ = _patch(monkeypatch, [HELD_RESULT])
        out = await nodes.build(_state("please make me that workflow we discussed in detail"))
        reply = out["messages"][0].content
        assert "Nothing has been built yet" in reply
        assert "Reply **yes** to build it now" in reply

    async def test_executed_plan_has_no_held_footer(self, monkeypatch):
        calls, _ = _patch(monkeypatch, [EXECUTED_RESULT])
        out = await nodes.build(_state("build the workflow exactly as I described earlier today"))
        assert "Nothing has been built yet" not in out["messages"][0].content


class TestMutatingClassification:
    async def test_workflow_agent_draft_never_auto_executes_without_confirm(self, monkeypatch):
        """A 1-step workflow_agent draft with NO asking phrases used to satisfy
        the read-only auto-exec branch (mutating=False). It must hold instead."""
        silent_hold = dict(HELD_RESULT,
                           text="Plan drafted for truth-test-4.")   # no asking phrases
        calls, _ = _patch(monkeypatch, [silent_hold])
        out = await nodes.build(_state("set up the workflow using every detail I already gave"))
        assert len(calls) == 1                                  # no silent auto-exec
        assert "Nothing has been built yet" in out["messages"][0].content
