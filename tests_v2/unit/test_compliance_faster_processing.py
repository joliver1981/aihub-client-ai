"""Compliance pipeline: two steps that only cost time are left out by default.

"Faster processing": the page-by-page field extraction is left out.

The document service makes one AI call per page to pull named fields out of it: about ten
seconds a page, the largest single part of the time a long manual takes in the Compliance
Process node. The agent answers from the page text, and searching the pages returned the
same passages with and without those fields (measured on vendor manuals), so the step is
left out by default. Everything else the node does is unchanged.

"Agent knowledge only": a document that goes to an agent is not also written to the
document library's search index. The moment a document is given to an agent it is flagged
as agent knowledge, and the library search is built for documents that are not: its pages
were reachable from there only by accident, while writing them was the largest remaining
part of the time. Without an agent the library index is the only place the document can
be searched, and it is always written.

Each is a setting: on by default, switched off per node (its checkbox) or platform wide
(COMPLIANCE_SKIP_PAGE_FIELDS, COMPLIANCE_SKIP_LIBRARY_INDEX). A node saved before an
option existed has no value for it and gets the platform setting.

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
        assert sent["data"]["is_knowledge_document"] == "false"

    def test_agent_knowledge_only_stores_it_as_the_agents_document_from_the_start(self, engine, sent):
        # the document service writes no library index for a document flagged this way
        engine._ingest_document("C:\\in\\guide.pdf", extract_fields=False, knowledge_only=True)
        assert sent["data"] == {
            "filePath": "C:\\in\\guide.pdf",
            "is_knowledge_document": "true",
            "extract_fields": "false",
            "force_ai_extraction": "true",
            "detect_document_type": "true",
        }


# ---------------------------------------------------------------------------
# The pipeline: on by default, off when asked, platform setting when not said
# ---------------------------------------------------------------------------

@pytest.fixture
def pipeline(engine, monkeypatch, tmp_path):
    f = tmp_path / "x.pdf"
    f.write_bytes(b"%PDF-1.4 fake")
    log = {"ingests": [], "knowledge_only": [], "indexed": [], "requirements_from": []}

    def fake_ingest(file_path, extract_fields=True, knowledge_only=False):
        log["ingests"].append(extract_fields)
        log["knowledge_only"].append(knowledge_only)
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
    monkeypatch.setattr(ce.cfg, "COMPLIANCE_SKIP_LIBRARY_INDEX", True, raising=False)

    def run(**kwargs):
        kwargs.setdefault("agent_id", 9)
        return engine.process_compliance_document(file_path=str(f), retailer_id=1, set_id=2,
                                                  uploaded_by=3, **kwargs)
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

        def boom(file_path, extract_fields=True, knowledge_only=False):
            raise RuntimeError("document service timed out")
        monkeypatch.setattr(engine, "_ingest_document", boom)
        out = run()
        assert out.error == "document service timed out"
        assert out.page_fields_extracted is False
        assert out.library_indexed is False

    def test_a_duplicate_ingests_nothing_and_says_nothing_about_fields(self, pipeline, engine, monkeypatch):
        log, run, _path = pipeline
        monkeypatch.setattr(engine, "_check_duplicate", lambda *_: {
            "version_id": 5, "version_number": 2, "document_id": "doc-old"})
        monkeypatch.setattr(engine, "_duplicate_knowledge_note", lambda *a: "held")
        out = run()
        assert out.is_duplicate and log["ingests"] == []
        assert out.page_fields_extracted is None
        assert out.library_indexed is None


class TestAgentKnowledgeOnly:
    """A document that goes to an agent is not also written to the document library's index."""

    def test_on_by_default_for_a_document_that_goes_to_an_agent(self, pipeline):
        log, run, _path = pipeline
        out = run()
        assert log["knowledge_only"] == [True]
        assert out.library_indexed is False
        assert log["indexed"] == [(9, "doc-new")]              # the agent's own index is still built
        assert out.error is None and len(out.requirements) == 1

    def test_without_an_agent_the_library_index_is_always_written(self, pipeline, monkeypatch):
        # with no agent, the library is the only place the document can be searched
        log, run, _path = pipeline
        out = run(agent_id=None)
        assert log["knowledge_only"] == [False]
        assert out.library_indexed is True
        assert run(agent_id=None, skip_library_index=True).library_indexed is True
        assert run(agent_id=0, skip_library_index=True).library_indexed is True
        assert log["knowledge_only"] == [False, False, False]
        assert log["indexed"] == []

    def test_switched_off_for_the_call_the_library_index_is_written_as_before(self, pipeline):
        log, run, _path = pipeline
        out = run(skip_library_index=False)
        assert log["knowledge_only"] == [False]
        assert out.library_indexed is True
        assert log["indexed"] == [(9, "doc-new")]

    def test_the_platform_setting_decides_when_the_call_does_not_say(self, pipeline, monkeypatch):
        log, run, _path = pipeline
        monkeypatch.setattr(ce.cfg, "COMPLIANCE_SKIP_LIBRARY_INDEX", False, raising=False)
        assert run().library_indexed is True
        assert run(skip_library_index=True).library_indexed is False       # the call's own choice wins
        monkeypatch.delattr(ce.cfg, "COMPLIANCE_SKIP_LIBRARY_INDEX", raising=False)
        assert run().library_indexed is False                              # a platform without the setting
        assert log["knowledge_only"] == [False, True, True]

    def test_it_is_independent_of_faster_processing(self, pipeline):
        log, run, _path = pipeline
        run(skip_page_fields=False)                        # fields extracted, still the agent's document only
        run(skip_library_index=False)                      # library index written, still no page fields
        assert log["ingests"] == [True, False]
        assert log["knowledge_only"] == [True, False]

    def test_a_document_the_agent_does_not_take_is_not_put_in_the_library_instead(self, pipeline, engine, monkeypatch):
        # an older version than the one the agent holds is recorded but not added to its knowledge:
        # it must not turn up in the document library either
        log, run, _path = pipeline
        monkeypatch.setattr(engine, "_check_manual_versions", lambda *a: {
            "older_existing": [], "newer_existing": {"document_id": "doc-2026", "filename": "Manual 2026.pdf"},
            "note": "version check: NOT added"})
        monkeypatch.setattr(engine.__class__, "_record_not_added", staticmethod(lambda *a: True))
        out = run()
        assert log["knowledge_only"] == [True] and out.library_indexed is False
        assert log["indexed"] == [] and out.superseded_by["filename"] == "Manual 2026.pdf"


class TestPlatformSetting:

    def test_it_is_on_unless_the_environment_says_false(self):
        src = open(os.path.join(APP_ROOT, "config.py"), encoding="utf-8").read()
        line = next(ln for ln in src.splitlines() if ln.startswith("COMPLIANCE_SKIP_PAGE_FIELDS"))
        expr = line.split("=", 1)[1].split("#", 1)[0].strip()
        assert expr == "os.getenv('COMPLIANCE_SKIP_PAGE_FIELDS', 'True').lower() == 'true'"
        for value, want in ((None, True), ("true", True), ("True", True), ("false", False), ("False", False)):
            env = {} if value is None else {"COMPLIANCE_SKIP_PAGE_FIELDS": value}
            assert eval(expr, {"os": MagicMock(getenv=lambda k, d=None, _e=env: _e.get(k, d))}) is want

    def test_agent_knowledge_only_is_on_unless_the_environment_says_false(self):
        src = open(os.path.join(APP_ROOT, "config.py"), encoding="utf-8").read()
        line = next(ln for ln in src.splitlines() if ln.startswith("COMPLIANCE_SKIP_LIBRARY_INDEX"))
        expr = line.split("=", 1)[1].split("#", 1)[0].strip()
        assert expr == "os.getenv('COMPLIANCE_SKIP_LIBRARY_INDEX', 'True').lower() == 'true'"
        for value, want in ((None, True), ("true", True), ("false", False), ("False", False)):
            env = {} if value is None else {"COMPLIANCE_SKIP_LIBRARY_INDEX": value}
            assert eval(expr, {"os": MagicMock(getenv=lambda k, d=None, _e=env: _e.get(k, d))}) is want


# ---------------------------------------------------------------------------
# The workflow node: its checkbox, and what a node saved before the option gets
# ---------------------------------------------------------------------------

class _FakeResult:
    version_id, version_number, retailer_id, set_id, document_id = 200, 1, 1, 2, "doc-new"
    requirements = [object()]
    is_duplicate, change_summary, excel_path, error = False, None, None, None
    retired_documents, superseded_by, version_check_note = [], None, None

    def __init__(self, page_fields_extracted, library_indexed=None):
        self.page_fields_extracted = page_fields_extracted
        self.library_indexed = library_indexed


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
            index = kwargs.get("skip_library_index")
            return _FakeResult(page_fields_extracted=not (True if skip is None else skip),
                               library_indexed=not ((True if index is None else index) and kwargs.get("agent_id")))

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

    def test_a_node_saved_before_agent_knowledge_only_leaves_it_to_the_platform_setting(self, node):
        _out, seen = node()
        assert seen["calls"][0]["skip_library_index"] is None

    @pytest.mark.parametrize("saved, passed", [
        (True, True), ("true", True), (False, False), ("false", False), ("off", False), ("", None), (None, None),
    ])
    def test_the_agent_knowledge_only_checkbox_is_what_the_pipeline_gets(self, node, saved, passed):
        _out, seen = node(skipLibraryIndex=saved)
        assert seen["calls"][0]["skip_library_index"] is passed

    def test_the_run_log_and_the_result_say_whether_the_library_index_was_written(self, node):
        _out, seen = node(skipLibraryIndex=True)
        assert seen["vars"]["complianceResult"]["library_index"] == "skipped"
        assert any("Document library index: not written (Agent knowledge only is on)" in m for _l, m in seen["logs"])

        _out, seen = node(skipLibraryIndex=False)
        assert seen["vars"]["complianceResult"]["library_index"] == "written"
        assert any(m == "Document library index: written (Agent knowledge only is off)" for _l, m in seen["logs"])

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

    def test_the_agent_knowledge_only_checkbox_is_there_and_ticked_for_a_new_node(self):
        js = self._read("static", "js", "workflow.js")
        box = re.search(r'<input[^>]*name="skipLibraryIndex"[^>]*>', js)
        assert box, "the Agent knowledge only checkbox is missing from the Compliance Process panel"
        assert 'type="checkbox"' in box.group(0) and "checked" in box.group(0)
        assert 'id="cp-skip-library-index"' in box.group(0)
        assert "skipLibraryIndex: true," in js
        loader = self._read("static", "js", "compliance_process_node.js")
        assert re.search(r"const saved = currentConfig\.skipLibraryIndex;\s*"
                         r"agentOnlyChk\.checked = !\(saved === false \|\| saved === 'false'\);", loader)

    def test_a_saved_false_is_shown_unticked_and_an_old_node_ticked(self):
        js = self._read("static", "js", "compliance_process_node.js")
        assert "getElementById('cp-skip-page-fields')" in js
        assert re.search(r"const saved = currentConfig\.skipPageFields;\s*"
                         r"fastChk\.checked = !\(saved === false \|\| saved === 'false'\);", js)

    def test_the_page_loads_the_changed_scripts_under_a_new_address(self):
        html = self._read("templates", "workflow_tool.html")
        assert re.search(r"filename='js/compliance_process_node\.js', v=\d+", html)
