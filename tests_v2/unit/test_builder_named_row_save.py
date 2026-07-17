"""
AIHUB-0041 — builder saves land in THE named row; no shared scratch row; no
empty shells vouched as success.

Live failure (test pack 09): requirements.process_name was missing, so every
compile fell back to the literal name "New Workflow" — save_compiled_workflow's
MERGE-by-name matched the stale shared row 388 of that name on EVERY save.
Compiled nodes landed in scratch; the user's named row (truth-test-upload 1255)
persisted EMPTY; the workflow_saved read-back vouched id=388; and a zero-node
compile was saved as junk row 1257 and reported "created".

Three fixes, tested here:
  1. _workflow_name_from_conversation recovers the user's own name
     ("...named truth-test: ...") from the delegation conversation.
  2. The fallback name is UNIQUE (never the reusable "New Workflow" literal),
     so it can never MERGE-match a shared scratch row.
  3. compile_workflow refuses to save a plan that materialized to ZERO nodes.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]


def _import_builder_nodes():
    """Import the BUILDER graph.nodes (command_center_service/graph shadows it)."""
    saved_path = list(sys.path)
    saved_graph = {k: v for k, v in sys.modules.items() if k == "graph" or k.startswith("graph.")}
    try:
        for k in list(saved_graph):
            del sys.modules[k]
        sys.path.insert(0, str(_ROOT / "builder_service"))
        import graph.nodes as b_nodes  # noqa: PLC0415
        assert "builder_service" in b_nodes.__file__.replace("\\", "/"), \
            f"resolved the wrong graph package: {b_nodes.__file__}"
        return b_nodes
    finally:
        sys.path[:] = saved_path
        # restore whatever graph modules were loaded before, so later tests that
        # import the CC-side graph package are unaffected
        for k in list(sys.modules):
            if k == "graph" or k.startswith("graph."):
                del sys.modules[k]
        sys.modules.update(saved_graph)


nodes = None
try:
    nodes = _import_builder_nodes()
except Exception as e:  # pragma: no cover
    pytest.skip(f"builder graph.nodes not importable here: {e}", allow_module_level=True)


class _Msg:
    def __init__(self, role, content):
        self.role = role
        self.content = content


class _Conv:
    def __init__(self, messages):
        self.messages = messages


def _with_conversation(monkeypatch, user_text):
    mgr = MagicMock()
    mgr.get_conversation.return_value = _Conv([_Msg("user", user_text)])
    fake_mod = types.ModuleType("agent_communication.manager")
    fake_mod.get_communication_manager = lambda: mgr
    # the helper imports it lazily by module path
    import agent_communication.manager as real_mod  # noqa: F401
    monkeypatch.setattr(real_mod, "get_communication_manager", lambda: mgr)


class TestNameFromConversation:
    @pytest.mark.parametrize("text,expected", [
        ("Use the workflow builder to create a workflow named truth-test: query AIRDB and save a CSV.",
         "truth-test"),
        ("Create a standalone workflow named truth-test-upload that uploads the CSV.",
         "truth-test-upload"),
        ('Please build a workflow called "Nightly Sales Export" for me.',
         "Nightly Sales Export"),
        ("Build one titled 'store-headcount-v3' now.", "store-headcount-v3"),
        ("Create a workflow named **expense-audit** with three steps.", "expense-audit"),
    ])
    def test_extracts_user_given_name(self, monkeypatch, text, expected):
        _with_conversation(monkeypatch, text)
        assert nodes._workflow_name_from_conversation("c1") == expected

    def test_no_name_returns_none(self, monkeypatch):
        _with_conversation(monkeypatch, "Build a workflow that queries AIRDB and exports to Excel.")
        assert nodes._workflow_name_from_conversation("c1") is None

    def test_manager_error_returns_none(self, monkeypatch):
        import agent_communication.manager as real_mod
        monkeypatch.setattr(real_mod, "get_communication_manager",
                            lambda: (_ for _ in ()).throw(RuntimeError("down")))
        assert nodes._workflow_name_from_conversation("c1") is None


class TestZeroNodeCompileGuard:
    def test_zero_node_materialization_refused(self, monkeypatch):
        import workflow_compiler as wc
        monkeypatch.setattr(wc, "generate_commands_from_plan",
                            lambda **k: (True, {"commands": []}, ""))
        monkeypatch.setattr(wc, "resolve_command_ids", lambda cmds: cmds)
        monkeypatch.setattr(wc, "materialize_commands",
                            lambda cj, base_workflow=None: {"nodes": [], "connections": []})
        saved = []
        monkeypatch.setattr(wc, "save_compiled_workflow",
                            lambda *a, **k: saved.append(1) or (True, 999, ""))

        result = wc.compile_workflow("1. do the thing", "junk-flow")

        assert result["success"] is False
        assert "zero nodes" in (result["error"] or "")
        assert "nothing was created" in (result["error"] or "")
        assert saved == []          # never reached the save

    def test_nonempty_materialization_proceeds_past_guard(self, monkeypatch):
        import workflow_compiler as wc
        monkeypatch.setattr(wc, "generate_commands_from_plan",
                            lambda **k: (True, {"commands": [{"c": 1}]}, ""))
        monkeypatch.setattr(wc, "resolve_command_ids", lambda cmds: cmds)
        monkeypatch.setattr(wc, "materialize_commands",
                            lambda cj, base_workflow=None: {"nodes": [{"id": "n1", "type": "Database"}],
                                                            "connections": []})
        # stop the pipeline right after the guard (validation) — we only care
        # that the zero-node guard let a real workflow through
        sentinel = RuntimeError("reached validation")
        monkeypatch.setattr(wc, "_to_validation_format",
                            lambda wd: (_ for _ in ()).throw(sentinel))
        with pytest.raises(RuntimeError, match="reached validation"):
            wc.compile_workflow("1. query AIRDB", "real-flow")


class TestUniqueFallbackContract:
    def test_fallback_is_never_the_shared_literal(self):
        """The call site's fallback must be unique per compile. We assert the
        contract on the source: the literal fallback 'New Workflow' (bare) is
        gone from the name-resolution site in favor of a uuid-suffixed one."""
        src = (Path(_ROOT) / "builder_service" / "graph" / "nodes.py").read_text(encoding="utf-8")
        assert 'or "New Workflow"' not in src
        assert 'f"New Workflow {_uuid.uuid4().hex[:8]}"' in src
