"""
AIHUB-0038 ROUND 2 — evidence-based coverage + the read-back describes the row
the USER opens.

Live round-1 failure (tester retest 2026-07-17, evidence RETEST_..._scenario_F.md):
  F1: the compile materialized a HOLLOW 'Automation' node (empty data, no
      secret/host/path). Its type name alone made dropped_capability() treat
      the SFTP transfer as covered → the distiller bypass never fired → reply
      said "Verified … Upload it via SFTP".
  F2: the read-back vouched the compile's scratch row 1260 ('New Workflow
      abd26b5c') while the row the user opens is 1261 ('truth-test-2', 3 nodes,
      no transfer node) — created by the executor plan step (two writers).
Also: the 0041 name extraction missed live because the delegation request is
recorded with role='builder' (role='user' means ESCALATED input only).
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(str(_ROOT), *path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _build_reply():
    return _load(("command_center", "orchestration", "build_reply.py"), "_br_r2")


def _readback():
    return _load(("builder_service", "routes", "compile_readback.py"), "_rb_r2")


# the tester's exact live shapes
HOLLOW_1260 = [  # compile scratch row: 4 nodes incl. the hollow Automation
    {"type": "Database", "configured": False},
    {"type": "Excel Export", "configured": False},
    {"type": "Set Variable", "configured": False},
    {"type": "Automation", "configured": False},      # empty data → placeholder
]
NAMED_1261 = [  # the row the user opens: 3 nodes, no transfer node at all
    {"type": "Database", "configured": False},
    {"type": "Excel Export", "configured": False},
    {"type": "Set Variable", "configured": False},
]
PLAN_SFTP = "Query AIRDB, save CSV, SFTP-upload the CSV to /outgoing using AUTODEMO_SFTP."


class TestEvidenceBasedCoverage:
    def test_hollow_automation_no_longer_covers(self):
        br = _build_reply()
        types_ = [n["type"] for n in HOLLOW_1260]
        # legacy (type-name only) behavior — the round-1 hole:
        assert br.dropped_capability(types_, PLAN_SFTP) is None
        # evidence-based — the hollow node cannot suppress the disclosure:
        assert br.dropped_capability(types_, PLAN_SFTP, nodes=HOLLOW_1260) == "SFTP/FTP file transfer"

    def test_configured_transfer_node_still_covers(self):
        br = _build_reply()
        nodes = [dict(HOLLOW_1260[-1], configured=True)]
        assert br.dropped_capability(["Automation"], PLAN_SFTP, nodes=nodes) is None

    def test_named_row_shape_drops_regardless(self):
        br = _build_reply()
        assert br.dropped_capability(
            [n["type"] for n in NAMED_1261], PLAN_SFTP, nodes=NAMED_1261) == "SFTP/FTP file transfer"

    def test_block_marks_unconfigured_placeholder(self):
        br = _build_reply()
        block = br.persisted_steps_block(1260, "draft", None, PLAN_SFTP, nodes=HOLLOW_1260)
        assert "Automation (UNCONFIGURED placeholder" in block
        assert "will not perform any upload" in block
        assert "NOT in this workflow" in block          # drop disclosure fires
        assert "Code Flow" in block

    def test_block_without_nodes_unchanged(self):
        br = _build_reply()
        block = br.persisted_steps_block(1300, "success", ["Database", "Excel Export"],
                                         "query AIRDB and export")
        assert "- Database" in block and "UNCONFIGURED" not in block


class TestResolveUserFacingRow:
    def test_plan_step_saved_id_wins_over_compile_row(self):
        rb = _readback()
        final_state = {"current_plan": {"steps": [
            {"result": {"saved_workflow_id": 1261, "compile_result": {}}},
        ]}}
        assert rb.resolve_user_facing_workflow_id(final_state, {"workflow_id": 1260}) == 1261

    def test_created_resources_fallback(self):
        rb = _readback()
        final_state = {"created_resources": {"workflows": [{"id": 1261, "name": "truth-test-2"}]}}
        assert rb.resolve_user_facing_workflow_id(final_state, {"workflow_id": 1260}) == 1261

    def test_compile_row_when_nothing_tracked(self):
        rb = _readback()
        assert rb.resolve_user_facing_workflow_id({}, {"workflow_id": 1260}) == 1260


class TestTrueReadback:
    def test_fetch_shapes_nodes_and_configured(self, monkeypatch):
        rb = _readback()
        payload = {"nodes": [
            {"id": "n1", "type": "Database", "data": {}},
            {"id": "n2", "type": "Automation", "data": {"automation": "expense-audit"}},
        ], "connections": []}

        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        import urllib.request as _rq
        monkeypatch.setattr(_rq, "urlopen",
                            lambda req, timeout=0: _Resp(json.dumps(payload).encode()))
        out = rb.fetch_workflow_readback(1261, "http://localhost:5001", "KEY")
        assert out["source"] == "db_readback"
        assert out["node_types"] == ["Database", "Automation"]
        assert out["nodes"][0]["configured"] is False       # empty data
        assert out["nodes"][1]["configured"] is True        # has settings

    def test_fetch_failure_returns_none(self, monkeypatch):
        rb = _readback()
        import urllib.request as _rq
        def _boom(req, timeout=0):
            raise OSError("connection refused")
        monkeypatch.setattr(_rq, "urlopen", _boom)
        assert rb.fetch_workflow_readback(1, "http://localhost:5001", "KEY") is None


class TestNameExtractionRoles:
    def _nodes(self):
        saved_path = list(sys.path)
        saved = {k: v for k, v in sys.modules.items() if k == "graph" or k.startswith("graph.")}
        try:
            for k in list(saved):
                del sys.modules[k]
            sys.path.insert(0, str(_ROOT / "builder_service"))
            import graph.nodes as b_nodes  # noqa: PLC0415
            assert "builder_service" in b_nodes.__file__.replace("\\", "/")
            return b_nodes
        finally:
            sys.path[:] = saved_path
            for k in list(sys.modules):
                if k == "graph" or k.startswith("graph."):
                    del sys.modules[k]
            sys.modules.update(saved)

    def _with_conv(self, monkeypatch, messages):
        import agent_communication.manager as mgr_mod
        mgr = MagicMock()
        mgr.get_conversation.return_value = types.SimpleNamespace(messages=messages)
        monkeypatch.setattr(mgr_mod, "get_communication_manager", lambda: mgr)

    def test_builder_role_message_found(self, monkeypatch):
        """The LIVE shape: the delegation request rides role='builder'."""
        nodes = self._nodes()
        self._with_conv(monkeypatch, [types.SimpleNamespace(
            role="builder",
            content="Use the workflow builder to create a workflow named truth-test-2: query AIRDB...")])
        assert nodes._workflow_name_from_conversation("c1") == "truth-test-2"

    def test_dict_shaped_message_found(self, monkeypatch):
        nodes = self._nodes()
        self._with_conv(monkeypatch, [
            {"role": "builder", "content": "create a workflow named truth-test-2: ..."}])
        assert nodes._workflow_name_from_conversation("c1") == "truth-test-2"

    def test_agent_role_still_ignored(self, monkeypatch):
        nodes = self._nodes()
        self._with_conv(monkeypatch, [
            {"role": "agent", "content": "I will build a workflow named something-else now"}])
        assert nodes._workflow_name_from_conversation("c1") is None
