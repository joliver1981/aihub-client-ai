"""
AIHUB-0039 — the builder edit/save path REFUSES to touch kind='code_flow' rows.

Live failure (test pack 09, Scenario C-as-scripted): a misrouted CC turn had the
WorkflowAgent "edit" code flow store-headcount (wf 1256); the edit replaced the
whole definition with one broken visual node, all code steps were lost, and the
flow vanished from the /codeflows registry — reported as "✅ updated".

Two guards, both tested here against the REAL workflow_compiler functions with
only the DB connection mocked:
  1. load_workflow_from_database refuses to load a code_flow for editing
     (the compile edit path dies at STEP 0 with an honest steer, before any
     command generation or save).
  2. save_compiled_workflow refuses to overwrite a code_flow row — by id AND
     via the MERGE-by-name collision path (a "create" reusing a code flow's
     name would clobber it just like an edit).
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit

import workflow_compiler as wc


CODE_FLOW_ROW = (
    1256,
    "store-headcount",
    json.dumps({
        "kind": "code_flow",
        "nodes": [{"id": "s1", "type": "Code Step"}, {"id": "s2", "type": "Code Step"},
                  {"id": "s3", "type": "Code Step"}],
        "connections": [],
    }),
)

VISUAL_ROW = (
    1254,
    "truth-test",
    json.dumps({
        "nodes": [{"id": "n1", "type": "Database"}, {"id": "n2", "type": "File"}],
        "connections": [],
    }),
)


def _mock_conn(fetchone_results):
    """A pyodbc-style connection whose cursor yields scripted fetchone() results
    and records every executed SQL statement."""
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchone.side_effect = list(fetchone_results)
    cursor.fetchall.return_value = []
    return conn, cursor


class TestLoadGuard:
    def test_load_refuses_code_flow(self, monkeypatch):
        conn, cursor = _mock_conn([CODE_FLOW_ROW])
        monkeypatch.setattr(wc, "get_db_connection", lambda: conn)

        ok, wf, err = wc.load_workflow_from_database(workflow_id=1256)

        assert ok is False and wf is None
        assert "Code Flow" in err and "cannot edit" in err
        assert "Nothing was changed" in err
        assert "code-flow tools" in err          # honest steer
        assert "store-headcount" in err

    def test_load_normal_workflow_still_works(self, monkeypatch):
        conn, cursor = _mock_conn([VISUAL_ROW])
        monkeypatch.setattr(wc, "get_db_connection", lambda: conn)

        ok, wf, err = wc.load_workflow_from_database(workflow_id=1254)

        assert ok is True and err == ""
        assert wf["id"] == 1254 and len(wf["nodes"]) == 2


class TestSaveGuard:
    def test_save_by_id_refuses_code_flow(self, monkeypatch):
        # scripted fetchones: the kind-check SELECT
        conn, cursor = _mock_conn([(1256, "code_flow")])
        monkeypatch.setattr(wc, "get_db_connection", lambda: conn)

        ok, wf_id, err = wc.save_compiled_workflow(
            "store-headcount", {"nodes": [], "connections": []}, workflow_id=1256)

        assert ok is False and wf_id is None
        assert "Refusing to overwrite" in err and "Code Flow" in err
        executed = " ".join(str(c.args[0]) for c in cursor.execute.call_args_list)
        assert "UPDATE Workflows" not in executed
        assert "MERGE INTO Workflows" not in executed

    def test_save_merge_by_name_refuses_code_flow_collision(self, monkeypatch):
        # create mode (no id) whose name collides with an existing code flow
        conn, cursor = _mock_conn([(1256, "code_flow")])
        monkeypatch.setattr(wc, "get_db_connection", lambda: conn)

        ok, wf_id, err = wc.save_compiled_workflow(
            "store-headcount", {"nodes": [], "connections": []})

        assert ok is False
        assert "Refusing to overwrite" in err
        executed = " ".join(str(c.args[0]) for c in cursor.execute.call_args_list)
        assert "MERGE INTO Workflows" not in executed

    def test_save_normal_workflow_proceeds(self, monkeypatch):
        # kind-check returns a plain row (kind NULL), then the id lookup after save
        conn, cursor = _mock_conn([(1254, None), (1254,)])
        monkeypatch.setattr(wc, "get_db_connection", lambda: conn)

        ok, wf_id, err = wc.save_compiled_workflow(
            "truth-test", {"nodes": [], "connections": []}, workflow_id=1254)

        assert ok is True and wf_id == 1254
        executed = " ".join(str(c.args[0]) for c in cursor.execute.call_args_list)
        assert "UPDATE Workflows" in executed

    def test_save_brand_new_name_proceeds(self, monkeypatch):
        # create mode, no existing row with that name → kind check returns None
        conn, cursor = _mock_conn([None, (1300,)])
        monkeypatch.setattr(wc, "get_db_connection", lambda: conn)

        ok, wf_id, err = wc.save_compiled_workflow(
            "brand-new-flow", {"nodes": [], "connections": []})

        assert ok is True and wf_id == 1300
        executed = " ".join(str(c.args[0]) for c in cursor.execute.call_args_list)
        assert "MERGE INTO Workflows" in executed


class TestCompileEditPath:
    def test_compile_edit_of_code_flow_fails_at_step0(self, monkeypatch):
        """End-to-end through the REAL compile_workflow: edit mode targeting a
        code_flow dies at STEP 0 with the honest error — no command generation,
        no materialization, no save."""
        conn, cursor = _mock_conn([CODE_FLOW_ROW])
        monkeypatch.setattr(wc, "get_db_connection", lambda: conn)

        called = []
        monkeypatch.setattr(wc, "generate_commands_from_plan",
                            lambda **k: called.append("gen") or (False, None, "should not run"))

        result = wc.compile_workflow(
            workflow_plan="1. add a pdfplumber step",
            workflow_name="store-headcount",
            workflow_id=1256,
        )

        assert result["success"] is False
        assert "Code Flow" in (result["error"] or "")
        assert "code-flow tools" in (result["error"] or "")
        assert called == []                       # never reached STEP 1
        assert result["workflow_data"] is None    # nothing materialized
