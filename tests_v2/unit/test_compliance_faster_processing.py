"""Compliance pipeline, "Faster processing": the page-by-page field extraction is left out.

The document service makes one AI call per page to pull named fields out of it: about ten
seconds a page, the largest single part of the time a long manual takes in the Compliance
Process node. The agent answers from the page text, and searching the pages returned the
same passages with and without those fields (measured on vendor manuals), so the step is
left out by default. Everything else the node does is unchanged.

It is a setting: on by default, switched off per node (the checkbox "Faster processing")
or platform wide (COMPLIANCE_SKIP_PAGE_FIELDS). A node saved before the option existed has
no value for it and gets the platform setting.

All boundaries are mocked: the document service, the database, knowledge indexing.
"""
from __future__ import annotations

import os
import re
from unittest.mock import MagicMock

import pytest

import compliance_engine as ce
from compliance_engine import ComplianceEngine, ExtractedRequirement

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.fixture
def engine():
    return ComplianceEngine()


# ---------------------------------------------------------------------------
# What is sent to the document service
# ---------------------------------------------------------------------------

@pytest.fixture
def sent(monkeypatch):
    """Capture the form the pipeline posts to the document service."""
    seen = {}

    def fake_post(url, data=None, timeout=None, **_):
        seen["url"], seen["data"], seen["timeout"] = url, dict(data or {}), timeout
        resp = MagicMock()
        resp.raise_for_status = lambda: None
        resp.json.return_value = {"status": "success", "document_id": "doc-1", "page_count": 231}
        return resp

    monkeypatch.setattr("requests.post", fake_post)
    return seen


class TestWhatTheDocumentServiceIsAskedToDo:

    def test_faster_processing_asks_for_no_page_fields_and_nothing_else_changes(self, engine, sent):
        assert engine._ingest_document("C:\\in\\guide.pdf", extract_fields=False) == "doc-1"
        assert sent["url"].endswith("/document/process")
        assert sent["data"] == {
            "filePath": "C:\\in\\guide.pdf",
            "extract_fields": "false",                 # the one thing that differs
            "force_ai_extraction": "true",             # picture and table pages are still read by AI
            "is_knowledge_document": "false",          # ingested the same way as before
            "detect_document_type": "true",
        }

    def test_with_the_option_off_the_request_is_what_it_always_was(self, engine, sent):
        engine._ingest_document("C:\\in\\guide.pdf", extract_fields=True)
        assert sent["data"] == {
            "filePath": "C:\\in\\guide.pdf",
            "extract_fields": "true",
            "force_ai_extraction": "true",
            "is_knowledge_document": "false",
            "detect_document_type": "true",
        }

    def test_called_the_old_way_it_still_extracts_fields(self, engine, sent):
        engine._ingest_document("C:\\in\\guide.pdf")
        assert sent["data"]["extract_fields"] == "true"


# ---------------------------------------------------------------------------
# The pipeline: on by default, off when asked, platform setting when not said
# ---------------------------------------------------------------------------

@pytest.fixture
def pipeline(engine, monkeypatch, tmp_path):
    f = tmp_path / "x.pdf"
    f.write_bytes(b"%PDF-1.4 fake")
    log = {"ingests": [], "indexed": [], "requirements_from": []}

    def fake_ingest(file_path, extract_fields=True):
        log["ingests"].append(extract_fields)
        return "doc-new"

    def fake_requirements(file_path, set_info):
        log["requirements_from"].append(file_path)
        return [ExtractedRequirement(category="shipping", subcategory="x", requirement_text="t")]

    monkeypatch.setattr(engine, "_check_duplicate", lambda *_: None)
    monkeypatch.setattr(engine, "_ingest_document", fake_ingest)
    monkeypatch.setattr(engine, "_store_document_hash", lambda doc_id, doc_hash: True)
    monkeypatch.setattr(engine, "_attach_attributes", lambda *a, **k: None)
    monkeypatch.setattr(engine, "_create_version", lambda *_: (200, 1))
    monkeypatch.setattr(engine.__class__, "get_document_set",
                        staticmethod(lambda set_id: {"set_id": set_id, "retailer_id": 1}))
    monkeypatch.setattr(engine, "_extract_requirements", fake_requirements)
    monkeypatch.setattr(engine, "_store_requirements", lambda vid, reqs: None)
    monkeypatch.setattr(engine, "_check_manual_versions", lambda *a: {
        "older_existing": [], "newer_existing": None, "note": "version check: nothing to replace"})
    monkeypatch.setattr(engine, "_index_to_knowledge",
                        lambda agent_id, doc_id, uploaded_by: log["indexed"].append((agent_id, doc_id)))
    monkeypatch.setattr(ce.cfg, "COMPLIANCE_SKIP_PAGE_FIELDS", True, raising=False)

    def run(**kwargs):
        return engine.process_compliance_document(file_path=str(f), retailer_id=1, set_id=2,
                                                  uploaded_by=3, agent_id=9, **kwargs)
    return log, run, str(f)


class TestPipeline:

    def test_on_by_default_no_page_fields_and_everything_else_still_happens(self, pipeline):
        log, run, path = pipeline
        out = run()
        assert log["ingests"] == [False]                       # extract_fields=False
        assert out.page_fields_extracted is False
        # the rest of the pipeline does not depend on those fields
        assert log["requirements_from"] == [path]              # the requirement list is read from the file
        assert log["indexed"] == [(9, "doc-new")]              # the agent still gets the document
        assert (out.version_id, out.document_id, len(out.requirements)) == (200, "doc-new", 1)
        assert out.error is None

    def test_switched_off_for_the_call_the_fields_are_extracted(self, pipeline):
        log, run, _path = pipeline
        out = run(skip_page_fields=False)
        assert log["ingests"] == [True]
        assert out.page_fields_extracted is True

    def test_the_platform_setting_decides_when_the_call_does_not_say(self, pipeline, monkeypatch):
        log, run, _path = pipeline
        monkeypatch.setattr(ce.cfg, "COMPLIANCE_SKIP_PAGE_FIELDS", False, raising=False)
        assert run().page_fields_extracted is True
        assert run(skip_page_fields=True).page_fields_extracted is False   # the call's own choice wins
        assert log["ingests"] == [True, False]

    def test_a_platform_without_the_setting_gets_the_faster_processing(self, pipeline, monkeypatch):
        log, run, _path = pipeline
        monkeypatch.delattr(ce.cfg, "COMPLIANCE_SKIP_PAGE_FIELDS", raising=False)
        run()
        assert log["ingests"] == [False]

    def test_a_failed_ingest_still_says_which_way_it_was_asked(self, pipeline, engine, monkeypatch):
        _log, run, _path = pipeline

        def boom(file_path, extract_fields=True):
            raise RuntimeError("document service timed out")
        monkeypatch.setattr(engine, "_ingest_document", boom)
        out = run()
        assert out.error == "document service timed out"
        assert out.page_fields_extracted is False

    def test_a_duplicate_ingests_nothing_and_says_nothing_about_fields(self, pipeline, engine, monkeypatch):
        log, run, _path = pipeline
        monkeypatch.setattr(engine, "_check_duplicate", lambda *_: {
            "version_id": 5, "version_number": 2, "document_id": "doc-old"})
        monkeypatch.setattr(engine, "_duplicate_knowledge_note", lambda *a: "held")
        out = run()
        assert out.is_duplicate and log["ingests"] == []
        assert out.page_fields_extracted is None


class TestPlatformSetting:

    def test_it_is_on_unless_the_environment_says_false(self):
        src = open(os.path.join(APP_ROOT, "config.py"), encoding="utf-8").read()
        line = next(ln for ln in src.splitlines() if ln.startswith("COMPLIANCE_SKIP_PAGE_FIELDS"))
        expr = line.split("=", 1)[1].split("#", 1)[0].strip()
        assert expr == "os.getenv('COMPLIANCE_SKIP_PAGE_FIELDS', 'True').lower() == 'true'"
        for value, want in ((None, True), ("true", True), ("True", True), ("false", False), ("False", False)):
            env = {} if value is None else {"COMPLIANCE_SKIP_PAGE_FIELDS": value}
            assert eval(expr, {"os": MagicMock(getenv=lambda k, d=None, _e=env: _e.get(k, d))}) is want


# ---------------------------------------------------------------------------
# The workflow node: its checkbox, and what a node saved before the option gets
# ---------------------------------------------------------------------------

class _FakeResult:
    version_id, version_number, retailer_id, set_id, document_id = 200, 1, 1, 2, "doc-new"
    requirements = [object()]
    is_duplicate, change_summary, excel_path, error = False, None, None, None
    retired_documents, superseded_by, version_check_note = [], None, None

    def __init__(self, page_fields_extracted):
        self.page_fields_extracted = page_fields_extracted


@pytest.fixture
def node(monkeypatch, tmp_path):
    """Run the Compliance Process node's own code with the engine replaced."""
    import workflow_execution as we

    f = tmp_path / "guide.pdf"
    f.write_bytes(b"%PDF-1.4 fake")
    seen = {"calls": [], "logs": [], "vars": {}}

    class FakeEngine:
        @staticmethod
        def get_document_set(set_id):
            return {"set_id": set_id, "retailer_id": 1, "agent_id": 9}

        def process_compliance_document(self, **kwargs):
            seen["calls"].append(kwargs)
            skip = kwargs.get("skip_page_fields")
            return _FakeResult(page_fields_extracted=not (True if skip is None else skip))

    monkeypatch.setattr(ce, "ComplianceEngine", FakeEngine)

    engine_cls = next(c for c in vars(we).values()
                      if isinstance(c, type) and hasattr(c, "_execute_compliance_process_node"))
    runner = object.__new__(engine_cls)
    runner.log_execution = lambda execution_id, node_id, level, message, *a, **k: seen["logs"].append((level, message))
    runner._replace_variable_references = lambda text, variables: variables.get(text, text)
    runner._extract_variable_name = lambda name: name
    runner._update_workflow_variable = lambda execution_id, name, kind, value: seen["vars"].update({name: value})

    def run(**config):
        cfg = {"inputVariable": "file", "routingMode": "fixed", "setId": 2, **config}
        out = runner._execute_compliance_process_node("exec-1", {"id": "n1", "config": cfg}, {"file": str(f)})
        return out, seen
    return run


class TestWorkflowNode:

    def test_a_node_saved_before_the_option_leaves_it_to_the_platform_setting(self, node):
        _out, seen = node()
        assert seen["calls"][0]["skip_page_fields"] is None

    @pytest.mark.parametrize("saved, passed", [
        (True, True), ("true", True), ("on", True),
        (False, False), ("false", False), ("False", False), ("0", False), ("off", False),
        ("", None), (None, None),
    ])
    def test_the_checkbox_value_is_what_the_pipeline_gets(self, node, saved, passed):
        _out, seen = node(skipPageFields=saved)
        assert seen["calls"][0]["skip_page_fields"] is passed

    def test_the_run_log_and_the_result_say_which_way_it_went(self, node):
        _out, seen = node(skipPageFields=True)
        assert seen["vars"]["complianceResult"]["page_fields"] == "skipped"
        assert any("Page fields: not extracted (Faster processing is on)" in m for _lvl, m in seen["logs"])
        # the line must not promise anything the option does not decide (where the document is searchable)
        assert not any("library search" in m for _lvl, m in seen["logs"])

        _out, seen = node(skipPageFields=False)
        assert seen["vars"]["complianceResult"]["page_fields"] == "extracted"
        assert any("Page fields: extracted from every page" in m for _lvl, m in seen["logs"])

    def test_the_other_options_are_passed_as_before(self, node):
        _out, seen = node(retireSupersededVersions=False, skipPageFields=False)
        call = seen["calls"][0]
        assert call["retire_superseded"] is False and call["agent_id"] == 9
        assert call["retailer_id"] == 1 and call["set_id"] == 2


class TestDesigner:
    """The node's panel in the Workflow Designer (static files, read as text)."""

    def _read(self, *parts):
        with open(os.path.join(APP_ROOT, *parts), encoding="utf-8") as fh:
            return fh.read()

    def test_the_checkbox_is_there_and_ticked_for_a_new_node(self):
        js = self._read("static", "js", "workflow.js")
        box = re.search(r'<input[^>]*name="skipPageFields"[^>]*>', js)
        assert box, "the Faster processing checkbox is missing from the Compliance Process panel"
        assert 'type="checkbox"' in box.group(0) and "checked" in box.group(0)
        assert 'id="cp-skip-page-fields"' in box.group(0)
        assert "skipPageFields: true," in js                       # a new node's saved value

    def test_a_saved_false_is_shown_unticked_and_an_old_node_ticked(self):
        js = self._read("static", "js", "compliance_process_node.js")
        assert "getElementById('cp-skip-page-fields')" in js
        assert re.search(r"const saved = currentConfig\.skipPageFields;\s*"
                         r"fastChk\.checked = !\(saved === false \|\| saved === 'false'\);", js)

    def test_the_page_loads_the_changed_scripts_under_a_new_address(self):
        html = self._read("templates", "workflow_tool.html")
        assert re.search(r"filename='js/compliance_process_node\.js', v=\d+", html)
