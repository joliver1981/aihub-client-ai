"""Compliance pipeline: a file that was already processed is recognised as a duplicate.

The duplicate check compares the file's hash with Documents.hash_value, and nothing
wrote that column: no file was ever recognised, and every re-sent manual became a new
version (a second ingestion, a second requirements extraction, a second copy in the
agent's knowledge). The pipeline now records the hash on the document it ingests, and a
duplicate run says what the targeted agent holds of that file.

All boundaries are mocked (DB cursor).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def patch_db(monkeypatch):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchone.return_value = None
    cursor.fetchall.return_value = []
    cursor.rowcount = 0
    monkeypatch.setattr("compliance_engine.get_db_connection", lambda: conn)
    return conn, cursor


@pytest.fixture
def engine():
    from compliance_engine import ComplianceEngine
    return ComplianceEngine()


class TestStoreDocumentHash:
    """_check_duplicate compares against Documents.hash_value, and nothing wrote it:
    no file was ever recognised as a duplicate. The pipeline now records the hash."""

    def test_stores_the_hash_only_where_there_is_none(self, engine, patch_db):
        conn, cursor = patch_db
        cursor.rowcount = 1
        assert engine._store_document_hash("doc-1", "abc123") is True
        sql, *params = cursor.execute.call_args_list[-1].args
        assert "UPDATE Documents SET hash_value = ?" in sql
        assert "hash_value IS NULL" in sql
        assert params == ["abc123", "doc-1"]
        conn.commit.assert_called()

    def test_row_not_visible_yet_is_retried_then_reported(self, engine, patch_db, monkeypatch, caplog):
        conn, cursor = patch_db
        cursor.rowcount = 0
        import time
        monkeypatch.setattr(time, "sleep", lambda s: None)
        with caplog.at_level("WARNING"):
            assert engine._store_document_hash("doc-1", "abc123") is False
        updates = [c for c in cursor.execute.call_args_list if "UPDATE Documents" in c.args[0]]
        assert len(updates) == 3
        assert "will not be recognised as a duplicate" in caplog.text

    def test_row_appearing_on_the_second_try_is_stored(self, engine, patch_db, monkeypatch):
        conn, cursor = patch_db
        import time
        monkeypatch.setattr(time, "sleep", lambda s: None)
        counts = iter([0, 1])

        def execute(sql, *params):
            if "UPDATE Documents" in sql:
                cursor.rowcount = next(counts)

        cursor.execute.side_effect = execute
        assert engine._store_document_hash("doc-1", "abc123") is True

    def test_a_database_error_does_not_stop_processing(self, engine, monkeypatch):
        import time
        monkeypatch.setattr(time, "sleep", lambda s: None)

        def boom():
            raise RuntimeError("db down")
        monkeypatch.setattr("compliance_engine.get_db_connection", boom)
        assert engine._store_document_hash("doc-1", "abc123") is False

    def test_nothing_to_store(self, engine):
        assert engine._store_document_hash("", "abc") is False
        assert engine._store_document_hash("doc-1", "") is False


class TestDuplicateKnowledgeNote:
    """A duplicate adds nothing — the run must say whether the agent it targets
    actually holds the file."""

    def _rows(self, patch_db, rows):
        _conn, cursor = patch_db
        cursor.fetchall.return_value = rows

    def test_already_in_the_agents_knowledge(self, engine, patch_db):
        self._rows(patch_db, [(9, True, "Retailer compliance document")])
        note = engine._duplicate_knowledge_note(9, "doc-1")
        assert "already in the agent's knowledge" in note

    def test_left_out_earlier_is_not_added_again(self, engine, patch_db):
        self._rows(patch_db, [(9, False, "Superseded 2026-11-21: replaced by Manual 2027.pdf")])
        note = engine._duplicate_knowledge_note(9, "doc-1")
        assert "left out of the agent's knowledge earlier" in note
        assert "replaced by Manual 2027.pdf" in note and "not added again" in note
        assert "NOT in this agent's knowledge" not in note     # deliberate, so not a warning

    def test_held_by_another_agent_only(self, engine, patch_db):
        self._rows(patch_db, [(4, True, "x")])
        note = engine._duplicate_knowledge_note(9, "doc-1")
        assert "NOT in this agent's knowledge" in note and "agent 4 holds it" in note

    def test_in_no_agents_knowledge(self, engine, patch_db):
        self._rows(patch_db, [])
        note = engine._duplicate_knowledge_note(9, "doc-1")
        assert "NOT in this agent's knowledge" in note and "holds it" not in note

    def test_database_error_is_reported_not_raised(self, engine, monkeypatch):
        def boom():
            raise RuntimeError("db down")
        monkeypatch.setattr("compliance_engine.get_db_connection", boom)
        assert "could not read" in engine._duplicate_knowledge_note(9, "doc-1")

    def test_duplicate_result_carries_the_note_for_the_targeted_agent(self, engine, monkeypatch, tmp_path):
        f = tmp_path / "x.pdf"
        f.write_bytes(b"%PDF-1.4 fake")
        monkeypatch.setattr(engine, "_check_duplicate", lambda set_id, doc_hash: {
            "version_id": 9, "version_number": 2, "document_id": "doc-9"})
        monkeypatch.setattr(engine, "_duplicate_knowledge_note",
                            lambda agent_id, doc_id: f"note for agent {agent_id} doc {doc_id}")
        ingested = []
        monkeypatch.setattr(engine, "_ingest_document", lambda fp: ingested.append(fp) or "x")
        out = engine.process_compliance_document(file_path=str(f), retailer_id=1, set_id=2,
                                                 uploaded_by=3, agent_id=7)
        assert out.is_duplicate and out.version_number == 2
        assert out.version_check_note == "note for agent 7 doc doc-9"
        assert ingested == []            # a duplicate is not ingested again
        # No agent targeted → no note
        out2 = engine.process_compliance_document(file_path=str(f), retailer_id=1, set_id=2, uploaded_by=3)
        assert out2.is_duplicate and out2.version_check_note is None
