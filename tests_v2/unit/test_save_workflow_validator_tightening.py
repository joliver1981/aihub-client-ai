"""
AIHUB-0054 — /save/workflow validator tightening + auto-fix disclosure.

Before this fix the platform verdict on /save/workflow was over-optimistic:
a Database node with EMPTY connection AND EMPTY query saved as "runnable"
(live: n49-badcfg, id 1286, from the 0049 evidence round), so the
saved_as_draft honesty affordance was dead code on the native path. The
native editing layer also silently auto-promoted a start and auto-cleaned
edges with no disclosure in the save result.

These tests pin:
  1. detect_database_config_errors flags the execution-impossible shapes the
     engine itself refuses at runtime (workflow_execution._execute_database_node
     raises ValueError for each):
       - empty/missing connection            -> DATABASE_NODE_MISSING_CONNECTION
       - empty payload for declared op       -> DATABASE_NODE_MISSING_OPERATION_CONFIG
         (query/procedure/select/insert/update/delete)
     while any non-empty value — including a ${variable} reference — counts as
     configured (high-precision: no false positives on runtime-substituted fields).
  2. run() end-to-end: the new codes have no fixer -> unfixable -> errors
     present -> the save route's is_valid=False -> saved_as_draft=True.
  3. Control shapes (fully configured Database nodes, other node types) are
     untouched by the new checks.
  4. app.py /save/workflow response carries validation_warnings (additive).
  5. workflow_tools disclosures: add_node silent auto-start, remove_node
     edge auto-clean + start auto-promotion are recorded on the definition,
     stripped from the save POST body, drained on successful save, returned
     in the result, and rendered by summarize_save.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import workflow_deterministic_validator as det_val  # noqa: E402


def _db_node(node_id="n1", **cfg):
    return {"id": node_id, "type": "Database", "label": "DB",
            "config": cfg, "isStart": True}


def _state(nodes):
    return {"nodes": nodes, "connections": []}


def _codes(issues):
    return {i.code for i in issues}


# ─── 1. execution-impossible Database shapes -> ERROR ─────────────────────

class TestDatabaseExecutionImpossible:
    def test_empty_connection_and_empty_query_flagged_both(self):
        """The n49-badcfg shape (live: saved 'runnable' before the fix)."""
        issues = det_val.detect_database_config_errors(
            _state([_db_node(connection="", query="")]))
        codes = _codes(issues)
        assert "DATABASE_NODE_MISSING_CONNECTION" in codes
        assert "DATABASE_NODE_MISSING_OPERATION_CONFIG" in codes
        assert all(i.severity == det_val.ERROR for i in issues)

    def test_missing_connection_key(self):
        issues = det_val.detect_database_config_errors(
            _state([_db_node(query="SELECT 1")]))
        assert "DATABASE_NODE_MISSING_CONNECTION" in _codes(issues)

    def test_connection_present_empty_query_default_op(self):
        issues = det_val.detect_database_config_errors(
            _state([_db_node(connection="58", query="")]))
        codes = _codes(issues)
        assert "DATABASE_NODE_MISSING_CONNECTION" not in codes
        assert "DATABASE_NODE_MISSING_OPERATION_CONFIG" in codes

    def test_missing_query_key_default_op(self):
        issues = det_val.detect_database_config_errors(
            _state([_db_node(connection="58")]))
        assert "DATABASE_NODE_MISSING_OPERATION_CONFIG" in _codes(issues)

    def test_procedure_op_requires_procedure(self):
        bad = det_val.detect_database_config_errors(
            _state([_db_node(connection="58", dbOperation="procedure")]))
        assert "DATABASE_NODE_MISSING_OPERATION_CONFIG" in _codes(bad)
        good = det_val.detect_database_config_errors(
            _state([_db_node(connection="58", dbOperation="procedure",
                             procedure="sp_report")]))
        assert "DATABASE_NODE_MISSING_OPERATION_CONFIG" not in _codes(good)

    def test_select_op_requires_table(self):
        bad = det_val.detect_database_config_errors(
            _state([_db_node(connection="58", dbOperation="select")]))
        assert "DATABASE_NODE_MISSING_OPERATION_CONFIG" in _codes(bad)
        good = det_val.detect_database_config_errors(
            _state([_db_node(connection="58", dbOperation="select",
                             tableName="TS.employee_data")]))
        assert "DATABASE_NODE_MISSING_OPERATION_CONFIG" not in _codes(good)

    @pytest.mark.parametrize("op", ["insert", "update", "delete"])
    def test_write_ops_require_table(self, op):
        issues = det_val.detect_database_config_errors(
            _state([_db_node(connection="58", dbOperation=op)]))
        assert "DATABASE_NODE_MISSING_OPERATION_CONFIG" in _codes(issues)


# ─── 2. high-precision: configured shapes stay clean ──────────────────────

class TestDatabaseConfiguredControl:
    def test_fully_configured_query_no_new_errors(self):
        issues = det_val.detect_database_config_errors(
            _state([_db_node(connection="58",
                             query="SELECT * FROM TS.employee_data")]))
        assert "DATABASE_NODE_MISSING_CONNECTION" not in _codes(issues)
        assert "DATABASE_NODE_MISSING_OPERATION_CONFIG" not in _codes(issues)

    def test_variable_only_query_counts_as_configured(self):
        """The engine substitutes ${...} at runtime — a raw non-empty query
        is NOT provably impossible, so it must not be flagged."""
        issues = det_val.detect_database_config_errors(
            _state([_db_node(connection="58", query="${runtime_sql}")]))
        assert "DATABASE_NODE_MISSING_OPERATION_CONFIG" not in _codes(issues)

    def test_int_connection_counts_as_configured(self):
        issues = det_val.detect_database_config_errors(
            _state([_db_node(connection=58, query="SELECT 1")]))
        assert "DATABASE_NODE_MISSING_CONNECTION" not in _codes(issues)

    def test_whitespace_only_connection_flagged(self):
        issues = det_val.detect_database_config_errors(
            _state([_db_node(connection="   ", query="SELECT 1")]))
        assert "DATABASE_NODE_MISSING_CONNECTION" in _codes(issues)

    def test_non_database_node_untouched(self):
        node = {"id": "n1", "type": "Alert", "config": {}, "isStart": True}
        assert det_val.detect_database_config_errors(_state([node])) == []

    def test_existing_numeric_and_placeholder_checks_still_fire(self):
        """The pre-existing detector behavior must not regress."""
        issues = det_val.detect_database_config_errors(
            _state([_db_node(connection="AIRDB", query="SELECT * FROM t WHERE id = ?")]))
        codes = _codes(issues)
        assert "DATABASE_CONNECTION_NOT_NUMERIC" in codes
        assert "DATABASE_QUERY_HAS_QUESTIONMARK_PLACEHOLDERS" in codes


# ─── 3. run() end-to-end -> unfixable -> draft verdict on the save path ───

class TestRunEndToEnd:
    def test_unconfigured_database_is_unfixable_error(self):
        res = det_val.run(_state([_db_node(connection="", query="")]))
        codes = _codes(res.errors)
        assert "DATABASE_NODE_MISSING_CONNECTION" in codes
        assert "DATABASE_NODE_MISSING_OPERATION_CONFIG" in codes
        # No fixer registered for either code -> both land in unfixable_errors,
        # which is what makes the save route's is_valid False -> draft.
        unfixable = _codes(res.unfixable_errors)
        assert "DATABASE_NODE_MISSING_CONNECTION" in unfixable
        assert "DATABASE_NODE_MISSING_OPERATION_CONFIG" in unfixable
        assert "DATABASE_NODE_MISSING_CONNECTION" not in det_val.FIXERS
        assert "DATABASE_NODE_MISSING_OPERATION_CONFIG" not in det_val.FIXERS

    def test_configured_workflow_still_valid(self):
        node = _db_node(connection="58", query="SELECT 1")
        res = det_val.run(_state([node]))
        assert "DATABASE_NODE_MISSING_CONNECTION" not in _codes(res.errors)
        assert "DATABASE_NODE_MISSING_OPERATION_CONFIG" not in _codes(res.errors)

    def test_detector_registered(self):
        assert det_val.detect_database_config_errors in det_val.DETECTORS


# ─── 4. app.py route wiring: validation_warnings in the response ──────────

class TestSaveRouteWarningsWiring:
    def test_route_response_includes_validation_warnings(self):
        src = (_REPO / "app.py").read_text(encoding="utf-8")
        assert '"validation_warnings": validation_warnings' in src
        assert "validation_warnings = [i.message for i in _det.warnings]" in src


# ─── 5. native-layer auto-fix disclosures ─────────────────────────────────

def _load_workflow_tools():
    """Load workflow_tools.py by file (its package import chain pulls the CC
    service config; the pure graph-surgery functions don't need it)."""
    import importlib.util
    path = _REPO / "command_center_service" / "graph" / "workflow_tools.py"
    spec = importlib.util.spec_from_file_location("_wt_0054", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture()
def wt():
    return _load_workflow_tools()


class TestAutoFixDisclosures:
    def test_first_node_auto_start_not_disclosed(self, wt):
        """Starting the very first node is natural, not a silent rewire."""
        d = {"nodes": [], "connections": []}
        wt.add_node(d, "Alert", "First", {})
        assert "_build_disclosures" not in d

    def test_later_node_silent_auto_start_disclosed(self, wt):
        d = {"nodes": [], "connections": []}
        wt.add_node(d, "Alert", "First", {})
        # Manually clear start so the second add silently auto-promotes
        for n in d["nodes"]:
            n["isStart"] = False
        wt.add_node(d, "Database", "Second", {})
        assert any("auto-promoted to start" in m
                   for m in d.get("_build_disclosures", []))

    def test_explicit_make_start_not_disclosed(self, wt):
        d = {"nodes": [], "connections": []}
        wt.add_node(d, "Alert", "First", {})
        for n in d["nodes"]:
            n["isStart"] = False
        wt.add_node(d, "Database", "Second", {}, make_start=True)
        assert "_build_disclosures" not in d

    def test_remove_node_discloses_edge_cleanup_and_start_promote(self, wt):
        d = {"nodes": [], "connections": []}
        r1 = wt.add_node(d, "Alert", "StartNode", {})
        r2 = wt.add_node(d, "Database", "Second", {})
        wt.wire(d, r1["node_id"], r2["node_id"], on="pass")
        # r1 is start; removing it drops 1 edge AND auto-promotes r2
        res = wt.remove_node(d, r1["node_id"])
        assert res["ok"] and res["removed_edges"] == 1
        msgs = d.get("_build_disclosures", [])
        assert any("edge(s)" in m for m in msgs)
        assert any("auto-promoted to start" in m for m in msgs)

    def test_remove_non_start_node_no_promote_disclosure(self, wt):
        d = {"nodes": [], "connections": []}
        wt.add_node(d, "Alert", "First", {})
        r2 = wt.add_node(d, "Database", "Second", {})
        wt.remove_node(d, r2["node_id"])
        msgs = d.get("_build_disclosures", [])
        assert not any("auto-promoted to start" in m for m in msgs)

    def test_save_definition_strips_and_drains_and_returns(self, wt, monkeypatch):
        posted = {}

        def fake_post(route, body, timeout=None):
            posted.update(body)
            return {"ok": True, "workflow_id": 4242, "is_valid": True,
                    "saved_as_draft": False, "validation_errors": [],
                    "validation_warnings": []}

        monkeypatch.setattr(wt, "_post", fake_post)
        monkeypatch.setattr(wt, "resolve", lambda name: {"ok": False})
        monkeypatch.setattr(wt, "_get", lambda route, timeout=None: {"ok": False})

        d = {"nodes": [], "connections": []}
        wt.add_node(d, "Alert", "First", {})
        for n in d["nodes"]:
            n["isStart"] = False
        wt.add_node(d, "Database", "Second", {})  # silent auto-start -> disclosure
        assert d.get("_build_disclosures")

        res = wt.save_definition("wf-0054-test", d)
        assert res["ok"] and res["workflow_id"] == 4242
        # stripped from the POST body (never persists into the row)
        assert "_build_disclosures" not in posted["workflow"]
        # returned on the result
        assert any("auto-promoted" in m for m in res["disclosures"])
        # drained so it is reported exactly once
        assert "_build_disclosures" not in d

    def test_save_definition_failed_save_keeps_disclosures(self, wt, monkeypatch):
        monkeypatch.setattr(wt, "_post",
                            lambda route, body, timeout=None:
                            {"ok": False, "message": "refused"})
        d = {"nodes": [], "connections": [],
             "_build_disclosures": ["auto-promoted X to start"]}
        res = wt.save_definition("wf-0054-test", d)
        assert not res["ok"]
        assert d.get("_build_disclosures") == ["auto-promoted X to start"]

    def test_summarize_save_renders_disclosures(self, wt):
        result = {"ok": True, "workflow_id": 1, "is_valid": True,
                  "saved_as_draft": False, "validation_errors": [],
                  "disclosures": ["No start node was set, so 'DB' (n_x) was auto-promoted to start."],
                  "readback": {"id": 1, "node_count": 1, "node_types": ["Database"],
                               "labels": ["DB"], "mismatch": False}}
        text = wt.summarize_save("wf", result)
        assert "Auto-fixes during this build" in text
        assert "auto-promoted to start" in text

    def test_summarize_save_no_disclosures_unchanged(self, wt):
        result = {"ok": True, "workflow_id": 1, "is_valid": True,
                  "saved_as_draft": False, "validation_errors": [],
                  "readback": {"id": 1, "node_count": 1, "node_types": ["Alert"],
                               "labels": ["A"], "mismatch": False}}
        text = wt.summarize_save("wf", result)
        assert "Auto-fixes" not in text
        assert "Validation: passed (runnable)." in text
