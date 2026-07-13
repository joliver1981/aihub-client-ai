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

    def _mock_build(self):
        # Preserve a test-configured executor across phase rebuilds (update_phase
        # calls _build_agent_executor; without this a phase change would clobber
        # a mock the test set up).
        if getattr(self, "agent_executor", None) is None:
            self.agent_executor = MagicMock()
    monkeypatch.setattr(WA.WorkflowAgent, "_build_agent_executor", _mock_build)
    agent = WA.WorkflowAgent(session_id="test-session")
    return agent


def _phase(name):
    import WorkflowAgent as WA
    return getattr(WA.BuilderPhase, name)


# ── phase state machine (P3: now deterministic, state-driven) ────────────

def test_phase_reflects_requirements(wf):
    assert wf.phase == _phase("DISCOVERY")
    wf.requirements.process_name = "Invoice approval"
    wf._auto_update_phase()
    assert wf.phase == _phase("REQUIREMENTS")


def test_phase_advances_to_planning_on_plan(wf):
    wf.requirements.process_name = "X"
    wf.workflow_plan = "1. Folder Selector\n2. Alert"
    wf._auto_update_phase()
    assert wf.phase == _phase("PLANNING")


def test_phase_advances_to_refinement_on_commands(wf):
    wf.generated_commands = [{"type": "add_node"}]
    wf._auto_update_phase()
    assert wf.phase == _phase("REFINEMENT")


def test_incidental_yes_no_longer_jumps_to_building(wf):
    # The exact fragility james flagged is FIXED: a bare 'yes' with no plan and
    # no commands does NOT advance the phase.
    wf.phase = _phase("PLANNING")
    wf.conversation_context = [{"role": "user", "content": "yes, but first let me explain the edge cases"}]
    wf._auto_update_phase()
    assert wf.phase == _phase("PLANNING")  # stayed put — no state progress


def test_message_count_no_longer_advances(wf):
    # A long chat with no gathered requirements does NOT advance a phase.
    assert wf.phase == _phase("DISCOVERY")
    wf.conversation_context = [{"role": "user", "content": "hmm"} for _ in range(9)]
    wf._auto_update_phase()
    assert wf.phase == _phase("DISCOVERY")


def test_keyword_ready_without_progress_does_not_advance(wf):
    wf.phase = _phase("REQUIREMENTS")
    wf.conversation_context = [{"role": "user", "content": "that's everything, ready"}]
    wf._auto_update_phase()
    assert wf.phase == _phase("REQUIREMENTS")  # no plan yet -> stays


def test_phase_is_forward_only(wf):
    # A refined workflow never regresses even if current state looks earlier.
    wf.phase = _phase("REFINEMENT")
    wf.generated_commands = None
    wf.workflow_plan = None
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


def test_workflow_agent_prompt_has_no_server_node(wf):
    # P4: the prompt now sources node types from the canonical set, so the bogus
    # 'Server' type (no engine handler) is gone. Guard against exact-token drift.
    import re
    assert not re.search(r"\bServer\b", wf.SYSTEM)


def test_command_generator_prompt_has_no_server_node():
    import re
    import CommandGenerator as CG
    assert not re.search(r"\bServer\b", CG.COMMAND_GENERATOR_SYSTEM_PROMPT)


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


def test_workflow_agent_prompt_lists_real_node_types(wf):
    # P4: prompt is sourced from canonical, so every real node type (incl.
    # the previously-omitted 'Portal') is present.
    from system_prompts import VALID_WORKFLOW_NODE_TYPES
    missing = [t for t in VALID_WORKFLOW_NODE_TYPES if t not in wf.SYSTEM]
    assert not missing, f"prompt is missing canonical node types: {missing}"


def test_command_generator_prompt_lists_real_node_types():
    import CommandGenerator as CG
    from system_prompts import VALID_WORKFLOW_NODE_TYPES
    missing = [t for t in VALID_WORKFLOW_NODE_TYPES if t not in CG.COMMAND_GENERATOR_SYSTEM_PROMPT]
    assert not missing, f"CommandGenerator prompt is missing canonical node types: {missing}"
