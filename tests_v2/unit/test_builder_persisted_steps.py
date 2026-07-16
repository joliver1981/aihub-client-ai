"""
AIHUB-0034 (arbiter directive) — the build reply's step list is derived from the
ACTUALLY-PERSISTED nodes, so a step the builder never compiled (e.g. an SFTP
upload it has no node for) can never be listed as built.

This is the regression the earlier unit tests MISSED: they tested an isolated
builder-side helper, but the live reply is composed on the CC side from the
delegator. build_reply.persisted_steps_block IS the code the delegator uses
(delegator.py) to override the confabulated narration — tested here with the
tester's exact live scenario (id 1252 = Database/Set Variable/File, plan asked
for SFTP).
"""
from __future__ import annotations

import importlib.util
import os

import pytest

pytestmark = pytest.mark.unit


def _mod():
    path = os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "..",
        "command_center", "orchestration", "build_reply.py"))
    spec = importlib.util.spec_from_file_location("_cc_build_reply", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# the tester's live repro
PERSISTED = ["Database", "Set Variable", "File"]     # id 1252 — NO SFTP node
PLAN_WITH_SFTP = "Step 1: query AIRDB. Step 2: write CSV. Step 3: SFTP upload to /outgoing/."


class TestPersistedStepsBlock:
    def test_lists_only_persisted_nodes_not_the_requested_sftp(self):
        block = _mod().persisted_steps_block(1252, "success", PERSISTED, PLAN_WITH_SFTP)
        assert "Database" in block and "Set Variable" in block and "File" in block
        # the dropped SFTP step is NOT listed as a built step
        assert "- SFTP" not in block and "SFTP upload to /outgoing" not in block
        # it IS disclosed as not-built + steered to a Code Flow
        assert "NOT in this workflow" in block and "Code Flow" in block
        assert "SFTP/FTP file transfer" in block
        # authoritative instruction to relay only these steps
        assert "Report ONLY the steps listed above" in block
        assert "3 step(s)" in block

    def test_fully_supported_build_has_no_dropped_disclosure(self):
        block = _mod().persisted_steps_block(
            1300, "success", ["Database", "Excel Export"], "query AIRDB and export to Excel")
        assert "Database" in block and "Excel Export" in block
        assert "NOT in this workflow" not in block and "Code Flow" not in block

    def test_draft_status_wording(self):
        block = _mod().persisted_steps_block(1301, "draft", ["Database"], "query AIRDB")
        assert "DRAFT" in block and "not yet runnable" in block


class TestDroppedCapability:
    def test_sftp_requested_and_absent_is_dropped(self):
        assert _mod().dropped_capability(PERSISTED, PLAN_WITH_SFTP) == "SFTP/FTP file transfer"

    def test_no_unsupported_request_is_none(self):
        assert _mod().dropped_capability(PERSISTED, "query AIRDB and export to Excel") is None

    @pytest.mark.parametrize("nodes", [["Code Step"], ["Automation"], ["SFTP"]])
    def test_covered_by_a_transfer_node_is_not_dropped(self, nodes):
        # future-proof: if a transfer/code-capable node ever exists, don't false-flag
        assert _mod().dropped_capability(nodes, PLAN_WITH_SFTP) is None


class TestUnsupportedCapability:
    @pytest.mark.parametrize("text,label", [
        ("SFTP upload to /outgoing/", "SFTP/FTP file transfer"),
        ("push to our ftp server", "SFTP/FTP file transfer"),
        ("upload the file to the remote host", "remote upload/transfer"),
        ("run this python to reconcile", "custom code execution"),
    ])
    def test_detects(self, text, label):
        assert _mod().unsupported_capability(text) == label

    def test_supported_is_none(self):
        assert _mod().unsupported_capability("query the database and export to a table") is None
