"""P1 tests — structured workflow command generation (Option A hardening).

Hermetic: quickPrompt is mocked, so no network. Verifies JSON mode is requested,
that JSON-mode output parses first-try, that the legacy free-text fallbacks still
work, and that the WorkflowAgent wires the CommandGenerator on by default.

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
import CommandGenerator as CG


_JSON_MODE_RESPONSE = '{"action": "build_workflow", "commands": [{"type": "add_node", "node_type": "Alert"}, {"type": "set_start_node", "node_id": "node-0"}]}'


def test_json_mode_requests_response_format(monkeypatch):
    monkeypatch.setattr(cfg, "WORKFLOW_STRUCTURED_COMMANDS", True, raising=False)
    captured = {}

    def fake_quick(*args, **kwargs):
        captured.update(kwargs)
        return _JSON_MODE_RESPONSE
    monkeypatch.setattr(CG, "quickPrompt", fake_quick)

    result = CG.CommandGenerator().generate_commands("1. Alert node: email someone.")
    assert captured.get("response_format") == {"type": "json_object"}
    assert result["action"] == "build_workflow"
    assert len(result["commands"]) == 2


def test_flag_off_sends_no_response_format(monkeypatch):
    monkeypatch.setattr(cfg, "WORKFLOW_STRUCTURED_COMMANDS", False, raising=False)
    captured = {}

    def fake_quick(*args, **kwargs):
        captured.update(kwargs)
        return "```json\n" + _JSON_MODE_RESPONSE + "\n```"
    monkeypatch.setattr(CG, "quickPrompt", fake_quick)

    result = CG.CommandGenerator().generate_commands("1. Alert node.")
    assert captured.get("response_format") is None
    assert result and len(result["commands"]) == 2


# ── _extract_commands robustness ─────────────────────────────────────────

def test_extract_parses_pure_json_object():
    out = CG.CommandGenerator()._extract_commands(_JSON_MODE_RESPONSE)
    assert out and out["commands"][0]["node_type"] == "Alert"


def test_extract_parses_markdown_block():
    out = CG.CommandGenerator()._extract_commands("Here you go:\n```json\n" + _JSON_MODE_RESPONSE + "\n```")
    assert out and len(out["commands"]) == 2


def test_extract_returns_none_on_garbage():
    assert CG.CommandGenerator()._extract_commands("sorry, I can't do that") is None


def test_generate_commands_returns_none_on_unparseable(monkeypatch):
    monkeypatch.setattr(CG, "quickPrompt", lambda *a, **k: "not json at all")
    assert CG.CommandGenerator().generate_commands("1. Alert node.") is None


# ── WorkflowAgent wires the CommandGenerator on by default ───────────────

@pytest.fixture
def wf(monkeypatch):
    import WorkflowAgent as WA
    monkeypatch.setattr(WA.WorkflowAgent, "_initialize_llm",
                        lambda self: setattr(self, "llm", MagicMock()))
    monkeypatch.setattr(WA.WorkflowAgent, "_build_agent_executor",
                        lambda self: setattr(self, "agent_executor", MagicMock()))
    return WA


def test_workflowagent_has_command_generator_by_default(wf, monkeypatch):
    monkeypatch.setattr(cfg, "WORKFLOW_STRUCTURED_COMMANDS", True, raising=False)
    agent = wf.WorkflowAgent(session_id="s1")
    assert agent.command_generator is not None
    assert agent.use_structured_commands is True


def test_workflowagent_no_command_generator_when_disabled(wf, monkeypatch):
    monkeypatch.setattr(cfg, "WORKFLOW_STRUCTURED_COMMANDS", False, raising=False)
    monkeypatch.setattr(cfg, "USE_TWO_STAGE_ARCHITECTURE", False, raising=False)
    agent = wf.WorkflowAgent(session_id="s2")
    assert agent.command_generator is None
