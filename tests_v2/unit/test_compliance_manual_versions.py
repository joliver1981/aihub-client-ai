"""Compliance pipeline: an agent answers from the CURRENT edition of a manual.

When a document arrives that is a newer version of a manual the agent already holds
for the retailer, the older version leaves the agent's knowledge; a document that is
OLDER than the one the agent holds is recorded but not added. The option is on by
default and can be switched off per call (the workflow node's checkbox) or platform
wide (COMPLIANCE_RETIRE_SUPERSEDED_VERSIONS).

All boundaries are mocked: DB cursor, the small model, knowledge indexing.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

import compliance_engine as ce
from compliance_engine import ComplianceEngine, ExtractedRequirement


def _doc(doc_id, filename, first_pages="[page 1] title"):
    return {"document_id": doc_id, "filename": filename, "page_count": 30, "first_pages": first_pages}


NEW = _doc("new", "Acme Vendor Guide - Supply Chain (November 2026).pdf", "[page 1] 2026 Vendor Guide Supply Chain")
OLD = _doc("old", "Vendor Guide Supply Chain 2025.pdf", "[page 1] 2025 Vendor Guide Supply Chain")
LEGAL = _doc("legal", "Vendor Guide Legal 2025.pdf", "[page 1] 2025 Vendor Guide Legal")


def _model(same_manual: dict, order: str = "by_year"):
    """A stand-in for the small model.

    same_manual: {existing filename: (same_manual, confidence)} for the first question.
    order: how it answers 'which edition is more recent' —
      'by_year'  the document whose text says 2026 wins, wherever it is placed
      'always_a' whichever document is shown first (an answer that does not survive the swap)
      'unknown'  nothing shows a date
    """
    calls = {"same": 0, "order": 0}

    def call(prompt, system, max_tokens=512, temp=0.0):
        if "DOCUMENT A" in prompt:
            calls["order"] += 1
            if order == "unknown":
                answer = "unknown"
            elif order == "always_a":
                answer = "A"
            else:
                a_text = prompt.split("DOCUMENT B")[0]
                answer = "A" if "2026" in a_text else "B"
            return json.dumps({"a_edition": "x", "b_edition": "y", "more_recent": answer})
        calls["same"] += 1
        results = []
        for line in prompt.splitlines():
            if line.startswith("[D"):
                label = line[1:line.index("]")]
                name = line.split("file name: ")[1].split(" | pages")[0]
                same, conf = same_manual.get(name, (False, "high"))
                results.append({"id": label, "reason": "r", "same_manual": same, "confidence": conf})
        return "```json\n" + json.dumps({"results": results}) + "\n```"

    call.calls = calls
    return call


@pytest.fixture
def engine():
    return ComplianceEngine()


@pytest.fixture
def model(monkeypatch):
    def install(fn):
        import agent_knowledge_integration as aki
        monkeypatch.setattr(aki, "_haiku_call_with_fallback", fn)
        return fn
    return install


# ---------------------------------------------------------------------------
# Which documents are other editions of the manual that arrived
# ---------------------------------------------------------------------------

class TestJudgeManualVersions:

    def test_newer_edition_replaces_the_older_one_and_leaves_other_manuals(self, engine, model):
        fn = model(_model({OLD["filename"]: (True, "high")}))
        out = engine._judge_manual_versions(NEW, [OLD, LEGAL])
        assert out["older_existing"] == [{"document_id": "old", "filename": OLD["filename"]}]
        assert out["newer_existing"] is None
        assert "taken out of the agent's knowledge" in out["note"]
        assert fn.calls["order"] == 2          # asked twice, positions swapped

    def test_an_older_manual_arriving_late_does_not_replace_the_current_one(self, engine, model):
        model(_model({NEW["filename"]: (True, "high")}))
        out = engine._judge_manual_versions(OLD, [NEW, LEGAL])     # the 2025 file comes in second
        assert out["older_existing"] == []
        assert out["newer_existing"] == {"document_id": "new", "filename": NEW["filename"]}
        assert "NOT added" in out["note"]

    def test_a_different_manual_of_the_same_retailer_is_never_replaced(self, engine, model):
        fn = model(_model({}))
        out = engine._judge_manual_versions(NEW, [LEGAL])
        assert out["older_existing"] == [] and out["newer_existing"] is None
        assert "not another version" in out["note"]
        assert fn.calls["order"] == 0

    def test_low_confidence_is_not_acted_on(self, engine, model):
        model(_model({OLD["filename"]: (True, "low")}))
        out = engine._judge_manual_versions(NEW, [OLD])
        assert out["older_existing"] == [] and out["newer_existing"] is None

    def test_an_order_answer_that_does_not_survive_the_swap_changes_nothing(self, engine, model):
        model(_model({OLD["filename"]: (True, "high")}, order="always_a"))
        out = engine._judge_manual_versions(NEW, [OLD])
        assert out["older_existing"] == [] and out["newer_existing"] is None
        assert "could not run" in out["note"] and "by hand" in out["note"]

    def test_no_date_in_either_document_takes_the_later_arrival_as_current(self, engine, model):
        model(_model({OLD["filename"]: (True, "high")}, order="unknown"))
        out = engine._judge_manual_versions(NEW, [OLD])
        assert [o["document_id"] for o in out["older_existing"]] == ["old"]
        assert "the one that arrived later was taken as current" in out["note"]

    def test_no_reply_from_the_model_replaces_nothing(self, engine, model):
        model(lambda prompt, system, max_tokens=512, temp=0.0: None)
        out = engine._judge_manual_versions(NEW, [OLD])
        assert out["older_existing"] == [] and out["newer_existing"] is None
        assert "could not run" in out["note"]

    def test_a_reply_that_is_not_json_replaces_nothing(self, engine, model):
        model(lambda prompt, system, max_tokens=512, temp=0.0: "I think they are the same manual.")
        out = engine._judge_manual_versions(NEW, [OLD])
        assert out["older_existing"] == [] and out["newer_existing"] is None

    def test_many_existing_documents_are_asked_about_in_batches(self, engine, model, monkeypatch):
        monkeypatch.setattr(ComplianceEngine, "_VERSION_PROMPT_BATCH_CHARS", 400)
        others = [_doc(f"d{i}", f"Other manual {i}.pdf", "[page 1] " + "x" * 200) for i in range(5)]
        fn = model(_model({OLD["filename"]: (True, "high")}))
        out = engine._judge_manual_versions(NEW, others + [OLD])
        assert fn.calls["same"] > 1                                # several calls
        assert [o["document_id"] for o in out["older_existing"]] == ["old"]   # none lost between batches


# ---------------------------------------------------------------------------
# Candidates come from the same agent AND the same retailer only
# ---------------------------------------------------------------------------

class TestCheckManualVersions:

    def _conn(self, candidate_ids):
        conn, cursor = MagicMock(), MagicMock()
        conn.cursor.return_value = cursor
        cursor.fetchall.return_value = [(c,) for c in candidate_ids]
        return conn, cursor

    def test_query_is_scoped_to_the_agent_and_the_retailer(self, engine, monkeypatch):
        conn, cursor = self._conn([])
        monkeypatch.setattr(ce, "get_db_connection", lambda: conn)
        out = engine._check_manual_versions(agent_id=7, retailer_id=3, new_document_id="new")
        sql, *params = cursor.execute.call_args_list[1].args
        assert "k.agent_id = ?" in sql and "s.retailer_id = ?" in sql and "k.is_active = 1" in sql
        assert params == [7, 3, "new"]
        assert out["older_existing"] == [] and "no other document" in out["note"]

    def test_profiles_are_judged(self, engine, monkeypatch):
        conn, _cursor = self._conn(["old"])
        monkeypatch.setattr(ce, "get_db_connection", lambda: conn)
        profiles = {"new": NEW, "old": OLD}
        monkeypatch.setattr(engine, "_document_profile", lambda cursor, doc_id: profiles[doc_id])
        seen = {}

        def fake_judge(new_doc, candidates):
            seen["new"], seen["candidates"] = new_doc, candidates
            return {"older_existing": [{"document_id": "old", "filename": "o.pdf"}],
                    "newer_existing": None, "note": "n"}

        monkeypatch.setattr(engine, "_judge_manual_versions", fake_judge)
        out = engine._check_manual_versions(7, 3, "new")
        assert seen == {"new": NEW, "candidates": [OLD]}
        assert out["older_existing"][0]["document_id"] == "old"

    def test_a_database_error_replaces_nothing(self, engine, monkeypatch):
        def boom():
            raise RuntimeError("db down")
        monkeypatch.setattr(ce, "get_db_connection", boom)
        out = engine._check_manual_versions(7, 3, "new")
        assert out["older_existing"] == [] and out["newer_existing"] is None
        assert "could not run" in out["note"]


# ---------------------------------------------------------------------------
# The pipeline: option on by default, off when asked, old document not added
# ---------------------------------------------------------------------------

@pytest.fixture
def pipeline(engine, monkeypatch, tmp_path):
    f = tmp_path / "x.pdf"
    f.write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(engine, "_check_duplicate", lambda *_: None)
    monkeypatch.setattr(engine, "_ingest_document", lambda fp, **kw: "doc-new")
    monkeypatch.setattr(engine, "_store_document_hash", lambda doc_id, doc_hash: True)
    monkeypatch.setattr(engine, "_create_version", lambda *_: (200, 1))
    monkeypatch.setattr(engine.__class__, "get_document_set",
                        staticmethod(lambda set_id: {"set_id": set_id, "retailer_id": 1}))
    monkeypatch.setattr(engine, "_extract_requirements", lambda fp, info: [
        ExtractedRequirement(category="shipping", subcategory="x", requirement_text="t")])
    monkeypatch.setattr(engine, "_store_requirements", lambda vid, reqs: None)
    log = {"indexed": [], "retired": [], "checked": 0}
    monkeypatch.setattr(engine, "_index_to_knowledge",
                        lambda agent_id, doc_id, uploaded_by: log["indexed"].append((agent_id, doc_id)))
    def fake_retire(agent_id, doc_id, replaced_by=None):
        log["retired"].append((agent_id, doc_id))
        log["replaced_by"] = replaced_by
        return True

    monkeypatch.setattr(engine.__class__, "_retire_knowledge_for_document", staticmethod(fake_retire))
    log["not_added"] = []
    monkeypatch.setattr(engine.__class__, "_record_not_added", staticmethod(
        lambda agent_id, doc_id, newer: log["not_added"].append((agent_id, doc_id, newer)) or True))
    monkeypatch.setattr(ce.cfg, "COMPLIANCE_RETIRE_SUPERSEDED_VERSIONS", True, raising=False)

    def set_check(result):
        def check(agent_id, retailer_id, doc_id):
            log["checked"] += 1
            return result
        monkeypatch.setattr(engine, "_check_manual_versions", check)

    def run(**kwargs):
        return engine.process_compliance_document(file_path=str(f), retailer_id=1, set_id=2,
                                                  uploaded_by=3, **kwargs)
    return engine, log, set_check, run


class TestPipelineRetiresSupersededVersions:
    OLDER = {"older_existing": [{"document_id": "doc-old", "filename": "Manual 2025.pdf"}],
             "newer_existing": None, "note": "version check: older version(s) taken out",
             "new_filename": "Manual 2026.pdf"}
    NEWER_HELD = {"older_existing": [],
                  "newer_existing": {"document_id": "doc-2026", "filename": "Manual 2026.pdf"},
                  "note": "version check: NOT added"}

    def test_on_by_default_the_older_version_leaves_the_agents_knowledge(self, pipeline):
        _engine, log, set_check, run = pipeline
        set_check(self.OLDER)
        out = run(agent_id=9)
        assert log["indexed"] == [(9, "doc-new")]
        assert log["retired"] == [(9, "doc-old")]
        assert log["replaced_by"] == "Manual 2026.pdf"      # the knowledge row says what replaced it
        assert out.retired_documents == self.OLDER["older_existing"]
        assert out.superseded_by is None
        assert out.version_check_note == self.OLDER["note"]
        assert out.error is None

    def test_switched_off_for_the_call_nothing_is_checked_or_retired(self, pipeline):
        _engine, log, set_check, run = pipeline
        set_check(self.OLDER)
        out = run(agent_id=9, retire_superseded=False)
        assert (log["indexed"], log["retired"], log["checked"]) == ([(9, "doc-new")], [], 0)
        assert out.retired_documents == [] and out.version_check_note is None

    def test_platform_setting_off_is_the_default_when_the_call_does_not_say(self, pipeline, monkeypatch):
        _engine, log, set_check, run = pipeline
        set_check(self.OLDER)
        monkeypatch.setattr(ce.cfg, "COMPLIANCE_RETIRE_SUPERSEDED_VERSIONS", False, raising=False)
        run(agent_id=9)
        assert log["checked"] == 0 and log["retired"] == []
        run(agent_id=9, retire_superseded=True)          # the call's own choice wins
        assert log["checked"] == 1 and log["retired"] == [(9, "doc-old")]

    def test_a_document_older_than_the_one_held_is_recorded_but_not_added(self, pipeline):
        _engine, log, set_check, run = pipeline
        set_check(self.NEWER_HELD)
        out = run(agent_id=9)
        assert log["indexed"] == [] and log["retired"] == []
        assert out.superseded_by == self.NEWER_HELD["newer_existing"]
        # ... and a trace says why, for the next time the same file arrives
        assert log["not_added"] == [(9, "doc-new", "Manual 2026.pdf")]
        assert out.version_id == 200 and len(out.requirements) == 1      # the version record is still made
        assert out.error is None

    def test_no_agent_means_no_check(self, pipeline):
        _engine, log, set_check, run = pipeline
        set_check(self.OLDER)
        out = run()
        assert (log["indexed"], log["retired"], log["checked"]) == ([], [], 0)
        assert out.retired_documents == []

    def test_a_check_that_could_not_run_still_adds_the_document(self, pipeline):
        _engine, log, set_check, run = pipeline
        set_check({"older_existing": [], "newer_existing": None,
                   "note": "version check could not run (x) — nothing was replaced"})
        out = run(agent_id=9)
        assert log["indexed"] == [(9, "doc-new")] and log["retired"] == []
        assert "could not run" in out.version_check_note


# ---------------------------------------------------------------------------
# Retiring = this agent only; vectors go only when no agent holds the document
# ---------------------------------------------------------------------------

class TestRecordNotAdded:

    def test_writes_an_inactive_row_that_says_why(self, monkeypatch):
        conn, cursor = MagicMock(), MagicMock()
        conn.cursor.return_value = cursor
        monkeypatch.setattr(ce, "get_db_connection", lambda: conn)
        assert ComplianceEngine._record_not_added(9, "doc-old", "Manual 2026.pdf") is True
        sql, *params = cursor.execute.call_args_list[1].args
        assert "INSERT INTO AgentKnowledge" in sql
        assert "getutcdate(), 0)" in sql                 # inactive: never searched, never listed
        agent_id, doc_id, note = params
        assert (agent_id, doc_id) == (9, "doc-old")
        assert note.startswith("Not added 20") and "newer version (Manual 2026.pdf)" in note
        conn.commit.assert_called()

    def test_a_database_error_is_not_fatal(self, monkeypatch):
        def boom():
            raise RuntimeError("db down")
        monkeypatch.setattr(ce, "get_db_connection", boom)
        assert ComplianceEngine._record_not_added(9, "doc-old", "Manual 2026.pdf") is False


class TestRetireKnowledgeForDocument:

    def _db(self, monkeypatch, rowcount, still_active):
        conn, cursor = MagicMock(), MagicMock()
        conn.cursor.return_value = cursor
        cursor.rowcount = rowcount
        cursor.fetchone.return_value = (still_active,)
        monkeypatch.setattr(ce, "get_db_connection", lambda: conn)
        queued = []
        import agent_knowledge_integration as aki
        monkeypatch.setattr(aki, "queue_knowledge_vector_delete", lambda doc_id: queued.append(doc_id))
        return cursor, queued

    def test_soft_deletes_for_this_agent_and_removes_the_vectors(self, monkeypatch):
        cursor, queued = self._db(monkeypatch, rowcount=1, still_active=0)
        assert ComplianceEngine._retire_knowledge_for_document(
            9, "doc-old", replaced_by="Manual 2026.pdf") is True
        sql, *params = cursor.execute.call_args_list[1].args
        assert "SET is_active = 0" in sql and "agent_id = ?" in sql
        note, doc_id, agent_id = params
        assert (doc_id, agent_id) == ("doc-old", 9)
        assert note.startswith("Superseded 20") and note.endswith("replaced by Manual 2026.pdf")
        assert queued == ["doc-old"]

    def test_a_note_the_column_cannot_take_does_not_stop_the_retirement(self, monkeypatch):
        cursor, queued = self._db(monkeypatch, rowcount=1, still_active=0)
        real = {"n": 0}

        def execute(sql, *params):
            real["n"] += 1
            if "description = ?" in sql:
                raise RuntimeError("String or binary data would be truncated")

        cursor.execute.side_effect = execute
        assert ComplianceEngine._retire_knowledge_for_document(9, "doc-old", replaced_by="x" * 400) is True
        plain = [c.args for c in cursor.execute.call_args_list
                 if "SET is_active = 0" in c.args[0] and "description" not in c.args[0]]
        assert plain and plain[0][1:] == ("doc-old", 9)
        assert queued == ["doc-old"]

    def test_vectors_stay_while_another_agent_still_holds_the_document(self, monkeypatch):
        _cursor, queued = self._db(monkeypatch, rowcount=1, still_active=1)
        assert ComplianceEngine._retire_knowledge_for_document(9, "doc-old") is True
        assert queued == []

    def test_nothing_to_retire(self, monkeypatch):
        _cursor, queued = self._db(monkeypatch, rowcount=0, still_active=0)
        assert ComplianceEngine._retire_knowledge_for_document(9, "doc-old") is False
        assert queued == []
