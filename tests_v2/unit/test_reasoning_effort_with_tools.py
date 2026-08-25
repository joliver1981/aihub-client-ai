"""
gpt-5.6-terra tools + reasoning_effort 400 (2026-08-25).

Bug: since the default-model switch to gpt-5.6-terra (2026-08-20), EVERY
General Agent chat turn 400'd — "Function tools with reasoning_effort are not
supported for gpt-5.6-terra in /v1/chat/completions ... set reasoning_effort
to 'none'" — because GeneralAgent/WorkflowAgent always bind function tools
and passed the configured effort ('medium'/'low'). The friendly-error layer
made it look like "the file-reading tool was unavailable."

Fix under test: api_keys_config.reasoning_effort_for_tools() — tool-binding
agents use OPENAI_REASONING_EFFORT_WITH_TOOLS (default 'none'); non-reasoning
models keep None. Drift tripwires assert both agents actually route their
effort through the helper.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import api_keys_config as akc  # noqa: E402
import config as cfg  # noqa: E402


class TestHelper:
    def test_reasoning_model_effort_becomes_none_by_default(self):
        assert akc.reasoning_effort_for_tools("medium") == "none"
        assert akc.reasoning_effort_for_tools("low") == "none"

    def test_non_reasoning_model_stays_none(self):
        assert akc.reasoning_effort_for_tools(None) is None
        assert akc.reasoning_effort_for_tools("") is None

    def test_knob_override_respected(self, monkeypatch):
        monkeypatch.setattr(cfg, "OPENAI_REASONING_EFFORT_WITH_TOOLS", "low")
        assert akc.reasoning_effort_for_tools("medium") == "low"

    def test_config_default_is_none(self):
        assert getattr(cfg, "OPENAI_REASONING_EFFORT_WITH_TOOLS") == "none"


class TestCallSitesRouteThroughHelper:
    """Source-level tripwires: the two tool-binding agents must derive their
    effort via reasoning_effort_for_tools. Instantiating them needs a DB, so
    inspect the source of the exact factory methods instead."""

    def _source(self, path: str, method: str) -> str:
        text = (_ROOT / path).read_text(encoding="utf-8")
        m = re.search(rf"def {method}\(self.*?(?=\n    def )", text, re.DOTALL)
        assert m, f"{method} not found in {path}"
        return m.group(0)

    def test_general_agent_create_llm(self):
        src = self._source("GeneralAgent.py", "_create_llm")
        assert "reasoning_effort_for_tools(" in src

    def test_workflow_agent_initialize_llm(self):
        src = self._source("WorkflowAgent.py", "_initialize_llm")
        assert "reasoning_effort_for_tools(" in src
