"""
AIHUB-0034 LIVE-PATH regression (arbiter directive: "a LIVE regression — unit-green
missed the live path twice").

Earlier fixes were unit-green on isolated helpers but still let the live reply
report a "SFTP Upload" step that no persisted node backed (id 1252 =
Database/Set Variable/File). The reason: the honest step list is composed on the
CC side, inside `delegate_to_builder`, from the SSE stream — not in any helper a
unit test was exercising.

This test drives the REAL `command_center.orchestration.delegator.delegate_to_builder`
consumption loop with a simulated *confabulating* builder stream (tokens that
claim a verified SFTP upload) plus a `workflow_saved` read-back whose persisted
nodes are only Database/Set Variable/File. It asserts the composed reply the CC
receives:
  - lists ONLY the 3 persisted nodes,
  - never presents the SFTP step as built,
  - drops the builder's confabulated "verified SFTP" narration,
  - discloses SFTP was NOT built and steers it to a Code Flow.

Only the network (httpx) and cc_config are faked; the delegator's own parsing +
composition (the code under test) runs for real.
"""
from __future__ import annotations

import sys
import types

import pytest

pytestmark = pytest.mark.unit


# ---- simulated builder SSE stream: confabulates a verified SFTP upload -------
# id 1252 in the field report. The plan lists SFTP; the tokens claim it verified;
# but the persisted read-back (workflow_saved) has NO transfer node.
_SSE_LINES = [
    "event: status",
    'data: {"phase": "compile", "label": "Compiling workflow"}',
    "",
    "event: token",
    'data: {"text": "\\u2705 Created workflow \'Nightly AIRDB export\'. "}',
    "",
    "event: token",
    'data: {"text": "Verified configuration:\\n- Query AIRDB\\n- Write CSV\\n- SFTP upload to /outgoing/ (verified)\\n"}',
    "",
    "event: plan",
    'data: {"status": "success", "steps": ['
    '{"description": "Query AIRDB for monthly invoice totals", "status": "completed"}, '
    '{"description": "Write results to a CSV file", "status": "completed"}, '
    '{"description": "SFTP upload the CSV to /outgoing/", "status": "completed"}]}',
    "",
    "event: workflow_saved",
    'data: {"workflow_id": 1252, "status": "success", "node_types": ["Database", "Set Variable", "File"]}',
    "",
    "event: done",
    'data: {"session_id": "test-session"}',
    "",
]


class _FakeStreamCtx:
    def __init__(self, lines):
        self._lines = lines
        self.status_code = 200
        self.text = ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_lines(self):
        for ln in self._lines:
            yield ln

    async def aread(self):
        return b""


class _FakeAsyncClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url, json=None, headers=None):
        return _FakeStreamCtx(_SSE_LINES)


@pytest.fixture
def _delegator(monkeypatch):
    # cc_config is imported lazily inside the function; fake it so no real config
    # is needed and no live builder URL is contacted.
    fake_cfg = types.ModuleType("cc_config")
    fake_cfg.get_builder_api_base_url = lambda: "http://localhost:65535"
    fake_cfg.AI_HUB_API_KEY = "TEST-KEY"
    monkeypatch.setitem(sys.modules, "cc_config", fake_cfg)

    from command_center.orchestration import delegator
    monkeypatch.setattr(delegator.httpx, "AsyncClient", _FakeAsyncClient)
    return delegator


async def test_live_path_never_reports_the_dropped_sftp_step(_delegator):
    # builder_session_id given so the session-creation POST is skipped.
    result = await _delegator.delegate_to_builder(
        message="Build a nightly workflow that queries AIRDB, writes a CSV, and SFTP-uploads it to /outgoing/",
        builder_session_id="test-session",
    )
    text = result["text"]

    # the 3 persisted nodes ARE listed
    assert "Database" in text and "Set Variable" in text and "File" in text

    # the confabulated SFTP step is NOT presented as built anywhere in the reply
    assert "(verified)" not in text
    assert "Verified configuration" not in text
    assert "SFTP upload to /outgoing/ (verified)" not in text

    # it IS honestly disclosed as not-built + routed to a Code Flow
    assert "NOT in this workflow" in text
    assert "SFTP/FTP file transfer" in text
    assert "Code Flow" in text
    assert "Report ONLY the steps listed above" in text

    # the delegation itself succeeded (a saved workflow) — the honesty fix must
    # not turn a real save into a spurious failure.
    assert result["status"] == "completed"


async def test_live_path_fully_supported_build_keeps_builder_narration(_delegator, monkeypatch):
    # A build with NO dropped capability must PREPEND the honest block and keep
    # the builder's narration (regression guard against over-suppression).
    supported = [
        "event: token",
        'data: {"text": "All set \\u2014 the export is ready."}',
        "",
        "event: plan",
        'data: {"status": "success", "steps": [{"description": "Query AIRDB and export to Excel", "status": "completed"}]}',
        "",
        "event: workflow_saved",
        'data: {"workflow_id": 1300, "status": "success", "node_types": ["Database", "Excel Export"]}',
        "",
        "event: done",
        'data: {"session_id": "test-session"}',
        "",
    ]
    monkeypatch.setattr(_FakeAsyncClient, "stream",
                        lambda self, *a, **k: _FakeStreamCtx(supported))

    result = await _delegator.delegate_to_builder(
        message="Build a workflow that queries AIRDB and exports to Excel",
        builder_session_id="test-session",
    )
    text = result["text"]
    assert "Database" in text and "Excel Export" in text
    assert "NOT in this workflow" not in text          # nothing was dropped
    assert "All set" in text                            # builder narration retained
    assert result["status"] == "completed"
