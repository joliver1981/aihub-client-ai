"""
AIHUB-0034 F2 — the Workflow Builder must never headline a false success. When a
build lands as a DRAFT (failed validation) or errors, the user-facing reply must
LEAD with the honest verdict, even though the agent's speculative preamble says
"✅ Workflow created / Verified configuration".

build_outcome.py is dependency-free; loaded by file path (builder_service/graph
is shadowed on sys.path).
"""
from __future__ import annotations

import importlib.util
import os

import pytest

pytestmark = pytest.mark.unit


def _mod():
    path = os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "..",
        "builder_service", "graph", "build_outcome.py"))
    spec = importlib.util.spec_from_file_location("_bld_outcome", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# the exact kind of optimistic preamble the LLM produced (AIHUB-0033 F2 evidence)
SPECULATIVE = ("✅ Workflow created: \"Nightly Expense Reconciliation\"\n"
               "Verified configuration: Multi-step flow includes: PDF discovery, "
               "AIRDB enrichment, CSV generation, SFTP upload.")


class TestDraftMessage:
    def test_leads_with_honest_draft_verdict_not_the_speculative_success(self):
        msg = _mod().draft_message("Nightly Expense Reconciliation", 1238,
                                   errors=["Connection node-1 -> node-2 references missing target node-2"],
                                   agent_notes=SPECULATIVE)
        first = msg.lstrip().splitlines()[0]
        assert first.startswith("**⚠️")          # honest headline, not "✅"
        assert "DRAFT" in first and "NOT created" in first
        assert not msg.lstrip().startswith("✅")
        # the speculative text is still present but DEMOTED under the authoritative note
        assert "authoritative" in msg
        idx_verdict = msg.index("DRAFT")
        idx_speculative = msg.index("✅ Workflow created")
        assert idx_verdict < idx_speculative        # verdict comes first
        assert "missing target node-2" in msg       # the validation error is shown

    def test_edit_says_not_updated(self):
        msg = _mod().draft_message("W", 5, errors=["e"], is_edit=True)
        assert "NOT updated" in msg.splitlines()[0]

    def test_no_agent_notes_has_no_authoritative_section(self):
        msg = _mod().draft_message("W", 5, errors=["e"], agent_notes="")
        assert "authoritative" not in msg and "Agent notes" not in msg


class TestErrorMessage:
    def test_leads_with_failure(self):
        msg = _mod().error_message("Could not parse SQLAlchemy URL", agent_notes=SPECULATIVE)
        first = msg.lstrip().splitlines()[0]
        assert first.startswith("**❌") and "NOT created" in first
        assert not msg.lstrip().startswith("✅")
        assert "Could not parse SQLAlchemy URL" in msg
        assert msg.index("build failed") < msg.index("✅ Workflow created")

    def test_edit_error_says_not_updated(self):
        msg = _mod().error_message("boom", is_edit=True)
        assert "NOT updated" in msg.splitlines()[0]
