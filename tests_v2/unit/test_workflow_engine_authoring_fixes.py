"""Engine fixes from the 2026-10-09 "build it all in the platform" test.

The AI Workflow Builder built a commercial-invoice workflow that ran but
misrouted work; each defect below was hit live:
  * a Conditional expression over an extraction result went silently FALSE
    whenever the result contained a null (JSON text substituted into Python);
  * a Folder Selector with "*.pdf;*.xlsx" (or an empty pattern) found nothing;
  * Excel Export ignored outputVariable / continueOnError, AI Extract ignored
    continueOnError — keys every comparable node honours.
Every change is additive: workflows that do not use the new keys behave as
before (pinned here too).
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def engine(monkeypatch):
    from workflow_execution import WorkflowExecutionEngine

    e = WorkflowExecutionEngine(connection_string="fake-dsn")
    e.logged = []
    monkeypatch.setattr(e, "log_execution", lambda *a, **kw: e.logged.append(a))
    monkeypatch.setattr(e, "_create_step_execution", lambda *a, **kw: "fake-step-id")
    monkeypatch.setattr(e, "_update_step_status", lambda *a, **kw: None)
    monkeypatch.setattr(e, "_update_workflow_status", lambda *a, **kw: None)
    monkeypatch.setattr(e, "_update_workflow_variable", lambda *a, **kw: None)
    monkeypatch.setattr(e, "get_db_connection", lambda: MagicMock())
    return e


def _warnings(engine):
    return [a[3] for a in engine.logged if len(a) >= 4 and a[2] == "warning"]


def _cond(**config):
    return {"id": "c1", "type": "Conditional", "config": config}


# --------------------------------------------------------------- Conditional

class TestConditionalJsonValues:
    EXTRACTION = {"Lines": [{"PONumber": "265748", "YMCVS": None, "Material": "100% COTTON"}]}

    def test_ai_builder_expression_over_result_with_null_is_true(self, engine):
        # The exact expression the AI Builder wrote; 11 of 20 invoices went the
        # fail way because their extraction carried a null field.
        r = engine._execute_conditional_node(
            "e1", _cond(conditionType="expression",
                        expression="'Lines' in ${invoiceExtraction} and bool(${invoiceExtraction}['Lines'])"),
            {"invoiceExtraction": self.EXTRACTION})
        assert r["success"] is True

    def test_empty_lines_is_false(self, engine):
        r = engine._execute_conditional_node(
            "e1", _cond(conditionType="expression",
                        expression="bool(${invoiceExtraction}['Lines'])"),
            {"invoiceExtraction": {"Lines": [], "note": None}})
        assert r["success"] is False

    def test_json_booleans_evaluate(self, engine):
        r = engine._execute_conditional_node(
            "e1", _cond(conditionType="expression", expression="${flags}['ok'] == True"),
            {"flags": {"ok": True, "other": False}})
        assert r["success"] is True

    def test_quoted_placeholder_style_unchanged(self, engine):
        r = engine._execute_conditional_node(
            "e1", _cond(conditionType="expression", expression="'${status}' == 'active'"),
            {"status": "active"})
        assert r["success"] is True

    def test_unevaluable_expression_says_why_in_run_log(self, engine):
        r = engine._execute_conditional_node(
            "e1", _cond(conditionType="expression", expression="this is not python &&"), {})
        assert r["success"] is False
        assert any("could not be evaluated" in w for w in _warnings(engine))

    def test_unresolved_reference_in_comparison_is_reported(self, engine):
        r = engine._execute_conditional_node(
            "e1", _cond(conditionType="comparison", leftValue="${excelAppendResult.status}",
                        operator="==", rightValue="success"), {})
        assert r["success"] is False
        assert any("could not be resolved" in w and "excelAppendResult" in w for w in _warnings(engine))

    def test_resolved_comparison_logs_no_warning(self, engine):
        r = engine._execute_conditional_node(
            "e1", _cond(conditionType="comparison", leftValue="${a}", operator="==", rightValue="5"),
            {"a": 5})
        assert r["success"] is True
        assert not _warnings(engine)


# ----------------------------------------------------------- Folder Selector

class TestFolderSelectorPatterns:
    @pytest.fixture
    def folder(self, tmp_path):
        for name in ("a.pdf", "b.xlsx", "c.doc", "d.PDF"):
            (tmp_path / name).write_text("x")
        (tmp_path / "sub").mkdir()
        return str(tmp_path)

    @pytest.mark.parametrize("pattern", ["*.pdf;*.xlsx", "*.pdf|*.xlsx", "*.pdf,*.xlsx", "*.pdf ; *.xlsx"])
    def test_separators(self, engine, folder, pattern):
        names = sorted(os.path.basename(f).lower() for f in engine._list_files_in_folder(folder, pattern, "all"))
        assert "b.xlsx" in names and "a.pdf" in names and "c.doc" not in names

    def test_empty_pattern_means_all_files_not_the_folder(self, engine, folder):
        names = sorted(os.path.basename(f) for f in engine._list_files_in_folder(folder, "", "all"))
        assert names == ["a.pdf", "b.xlsx", "c.doc", "d.PDF"]  # files only, no 'sub'

    def test_single_pattern_unchanged(self, engine, folder):
        names = [os.path.basename(f) for f in engine._list_files_in_folder(folder, "*.xlsx", "all")]
        assert names == ["b.xlsx"]

    def test_no_match_names_the_pattern(self, engine, folder):
        node = {"id": "fs", "type": "Folder Selector",
                "config": {"folderPath": folder, "filePattern": "*.csv", "selectionMode": "all",
                           "failIfEmpty": False, "outputVariable": "files"}}
        r = engine._execute_folder_selector_node("e1", node, {})
        assert r["success"] is True and r["data"]["filesFound"] is False
        assert any("*.csv" in w for w in _warnings(engine))

    def test_all_mode_with_no_match_yields_an_empty_list(self, engine, folder):
        # A Loop over it sees zero items (not "Loop source is not an array: str")
        # and len(${files}) evaluates; single-file modes keep the empty string.
        variables = {}
        node = {"id": "fs", "type": "Folder Selector",
                "config": {"folderPath": folder, "filePattern": "*.csv", "selectionMode": "all",
                           "failIfEmpty": False, "outputVariable": "files"}}
        r = engine._execute_folder_selector_node("e1", node, variables)
        assert variables["files"] == [] and r["data"]["allFiles"] == []
        c = engine._execute_conditional_node(
            "e1", _cond(conditionType="expression", expression="len(${files}) > 0"), variables)
        assert c["success"] is False and not any("could not be evaluated" in w for w in _warnings(engine))
        node["config"]["selectionMode"] = "first"
        engine._execute_folder_selector_node("e1", node, variables)
        assert variables["files"] == ""


# -------------------------------------------------------------- Excel Export

class TestExcelExportOutputAndContinue:
    def test_passthrough_without_new_keys(self, engine):
        res = {"success": False, "error": "boom", "data": {}}
        assert engine._finish_excel_export("e1", "x1", {}, {}, dict(res)) == res

    def test_output_variable_on_success(self, engine):
        variables = {}
        r = engine._finish_excel_export(
            "e1", "x1", {"outputVariable": "excelAppendResult"}, variables,
            {"success": True, "data": {"file_path": "o.xlsx", "rows_written": 3, "sheet_name": "Lines"}})
        assert r["success"] is True
        assert variables["excelAppendResult"] == {"status": "success", "file_path": "o.xlsx",
                                                  "rows_written": 3, "sheet_name": "Lines"}

    def test_output_variable_and_continue_on_failure(self, engine):
        variables = {}
        r = engine._finish_excel_export(
            "e1", "x1", {"outputVariable": "res", "continueOnError": True}, variables,
            {"success": False, "error": "Excel Export failed: locked", "data": {}})
        assert r["success"] is True and r["data"]["continued"] is True
        assert variables["res"]["status"] == "failed" and "locked" in variables["res"]["error"]

    def test_failure_without_continue_still_fails(self, engine):
        variables = {}
        r = engine._finish_excel_export(
            "e1", "x1", {"outputVariable": "res"}, variables,
            {"success": False, "error": "nope", "data": {}})
        assert r["success"] is False and variables["res"]["status"] == "failed"

    def test_end_to_end_failure_honours_continue(self, engine, tmp_path):
        node = {"id": "x1", "type": "Excel Export",
                "config": {"inputVariable": "${nothing_here}", "excelOutputPath": str(tmp_path / "o.xlsx"),
                           "excelOperation": "append", "continueOnError": True, "outputVariable": "res"}}
        variables = {}
        r = engine._execute_excel_export_node("e1", node, variables)
        assert r["success"] is True
        assert variables["res"]["status"] == "failed"


# ---------------------------------------------------------------- AI Extract

class TestAIExtractContinueOnError:
    def _node(self, **cfg):
        base = {"inputVariable": "", "fields": [{"name": "x", "type": "text"}], "outputVariable": "inv"}
        base.update(cfg)
        return {"id": "ax", "type": "AI Extract", "config": base}

    def test_error_without_continue_fails(self, engine):
        r = engine._execute_ai_extract_node("e1", self._node(), {})
        assert r["success"] is False

    def test_error_with_continue_passes_and_records_failure(self, engine):
        variables = {}
        r = engine._execute_ai_extract_node("e1", self._node(continueOnError=True), variables)
        assert r["success"] is True and r["data"]["continued"] is True
        assert variables["inv"]["status"] == "failed"
