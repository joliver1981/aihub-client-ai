"""P0 safety net for the AI Workflow Builder (WorkflowAgent) — Option A hardening.

Characterization + contract tests around the pieces the hardening will change:
  * phase state machine (_auto_update_phase)          -> changed in P3
  * command surfacing in process_message              -> changed in P1
  * node-type contract (Server drift)                 -> fixed  in P4

Hermetic: the LLM and the langchain executor construction are mocked, so these
run in CI with no network/DB. A separate live scaffold (skipped without creds)
exercises a real conversation.

NOTE: repo .gitignore ignores test*.py — commit with `git add -f`.

These tests pin CURRENT behavior (including the fragile bits) so the P1–P4
refactors are caught. Tests that assert the DESIRED post-fix state are marked
xfail with the phase that flips them.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def wf(monkeypatch):
    """A WorkflowAgent with the LLM + executor construction mocked out."""
    import WorkflowAgent as WA
    monkeypatch.setattr(WA.WorkflowAgent, "_initialize_llm",
                        lambda self: setattr(self, "llm", MagicMock()))
    monkeypatch.setattr(WA.WorkflowAgent, "_build_agent_executor",
                        lambda self: setattr(self, "agent_executor", MagicMock()))
    agent = WA.WorkflowAgent(session_id="test-session")
    return agent


def _phase(name):
    import WorkflowAgent as WA
    return getattr(WA.BuilderPhase, name)


# ── phase state machine (characterization — P3 will change these) ────────

def test_discovery_advances_on_process_name(wf):
    assert wf.phase == _phase("DISCOVERY")
    wf.requirements.process_name = "Invoice approval"
    wf._auto_update_phase()
    assert wf.phase == _phase("REQUIREMENTS")


def test_discovery_advances_on_message_count_alone(wf):
    # FRAGILITY PIN: 5+ context messages advances the phase regardless of content.
    assert wf.phase == _phase("DISCOVERY")
    wf.conversation_context = [{"role": "user", "content": "hi"} for _ in range(5)]
    wf._auto_update_phase()
    assert wf.phase == _phase("REQUIREMENTS")


def test_planning_advances_on_incidental_yes(wf):
    # FRAGILITY PIN (the exact bug james flagged): a bare 'yes' anywhere in the
    # last messages jumps PLANNING -> BUILDING, even mid-sentence.
    wf.phase = _phase("PLANNING")
    wf.conversation_context = [{"role": "user", "content": "yes, but first let me explain the edge cases"}]
    wf._auto_update_phase()
    assert wf.phase == _phase("BUILDING")


def test_planning_advances_on_make_it(wf):
    wf.phase = _phase("PLANNING")
    wf.conversation_context = [{"role": "user", "content": "ok make it"}]
    wf._auto_update_phase()
    assert wf.phase == _phase("BUILDING")


def test_requirements_advances_on_keyword_ready(wf):
    wf.phase = _phase("REQUIREMENTS")
    wf.conversation_context = [{"role": "user", "content": "that's everything, ready"}]
    wf._auto_update_phase()
    assert wf.phase == _phase("PLANNING")


def test_building_advances_to_refinement_on_generated_commands(wf):
    wf.phase = _phase("BUILDING")
    wf.generated_commands = [{"type": "add_node"}]
    wf._auto_update_phase()
    assert wf.phase == _phase("REFINEMENT")


# ── command surfacing (characterization — P1 will change the source) ─────

def test_process_message_surfaces_tool_generated_commands(wf):
    wf.agent_executor = MagicMock()
    wf.agent_executor.invoke.return_value = {"output": "Here is your workflow."}
    wf.generated_commands = [{"type": "add_node", "node_type": "Database"}]

    response, metadata = wf.process_message("build it")

    assert metadata["workflow_commands"] is not None
    assert metadata["workflow_commands"]["action"] == "build_workflow"
    assert metadata["workflow_commands"]["commands"] == wf.generated_commands


def test_process_message_no_commands_when_none_generated(wf):
    wf.agent_executor = MagicMock()
    wf.agent_executor.invoke.return_value = {"output": "Tell me more about your process."}
    wf.generated_commands = None

    response, metadata = wf.process_message("I want to automate invoices")
    assert metadata["workflow_commands"] is None
    assert metadata["phase"] in {p.value for p in __import__("WorkflowAgent").BuilderPhase}


def test_process_message_swallows_errors_into_fallback(wf):
    import config as cfg
    wf.agent_executor = MagicMock()
    wf.agent_executor.invoke.side_effect = RuntimeError("LLM down")

    response, metadata = wf.process_message("build it")
    assert response == cfg.WORKFLOW_AGENT_FALLBACK_RESPONSE
    assert "error" in metadata


# ── node-type contract (Server drift — P4 removes it) ────────────────────

def test_canonical_node_types_have_no_server():
    from system_prompts import VALID_WORKFLOW_NODE_TYPES
    assert "Server" not in VALID_WORKFLOW_NODE_TYPES


@pytest.mark.xfail(reason="Server drift in the WorkflowAgent prompt — removed in P4", strict=True)
def test_workflow_agent_prompt_has_no_server_node(wf):
    # The system prompt still literally lists 'Server' as a valid node type
    # (WorkflowAgent.py:626) even though no engine handler exists. P4 removes it;
    # this flips to passing then.
    assert "Server" not in wf.SYSTEM


@pytest.mark.xfail(reason="Server drift in the CommandGenerator prompt — removed in P4", strict=True)
def test_command_generator_prompt_has_no_server_node():
    import CommandGenerator as CG
    assert "Server" not in CG.COMMAND_GENERATOR_SYSTEM_PROMPT


# ── live conversation smoke (opt-in; real LLM) ───────────────────────────

@pytest.mark.skipif(
    __import__("os").getenv("RUN_WORKFLOW_LIVE", "").lower() not in ("1", "true", "yes"),
    reason="live WorkflowAgent smoke — set RUN_WORKFLOW_LIVE=1 (needs LLM creds)",
)
def test_live_build_conversation_reaches_plan_or_commands():
    """A real WorkflowAgent, driven through a short build, should reach a plan
    or emit commands without crashing. Non-deterministic — smoke only."""
    import WorkflowAgent as WA
    agent = WA.WorkflowAgent(session_id="live-smoke", is_builder_delegation=True)
    turns = [
        "Build a workflow that reads invoices from a folder, extracts the total, "
        "and emails a summary to finance@example.com.",
        "Yes, that plan looks right — build it.",
    ]
    last_meta = {}
    for t in turns:
        _resp, last_meta = agent.process_message(t)
    assert last_meta.get("workflow_plan") or last_meta.get("workflow_commands"), \
        f"no plan or commands after conversation; phase={last_meta.get('phase')}"


@pytest.mark.xfail(reason="prompt omits real 'Portal' node type — added in P4 alongside Server removal", strict=True)
def test_workflow_agent_prompt_lists_real_node_types(wf):
    # Drift caught by this battery: the prompt not only lists a bogus 'Server'
    # but OMITS the real 'Portal' type (which has an engine handler). P4 syncs
    # the prompt list to the canonical set.
    from system_prompts import VALID_WORKFLOW_NODE_TYPES
    missing = [t for t in VALID_WORKFLOW_NODE_TYPES if t not in wf.SYSTEM]
    assert not missing, f"prompt is missing canonical node types: {missing}"
