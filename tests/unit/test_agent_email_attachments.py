"""
Tests for agent_email_attachments.py
====================================
The shared cloud-DB attachment-text helper used by the email dispatcher to feed
attachment CONTENT into email-triggered workflows and auto-reply drafts.

The bytes live in the CLOUD DB, read via get_cloud_db_connection — here we patch
that connection and inject a fake attachment_text_extractor so the tests are pure.
"""

import sys
import types
import pytest
from unittest.mock import patch

import agent_email_attachments as aea


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeCursor:
    def __init__(self, rows=None, one=None):
        self.rows = rows if rows is not None else []
        self.one = one
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.one

    def close(self):
        pass


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


def _fake_extractor(raise_for=None, result_map=None):
    mod = types.ModuleType("attachment_text_extractor")

    def _extract(file_bytes, filename, content_type=None, max_chars=None):
        if raise_for and filename in raise_for:
            raise RuntimeError("extractor boom")
        if result_map and filename in result_map:
            return result_map[filename]
        return {"success": True, "text": f"TEXT:{filename}"}

    mod.extract_text_from_attachment = _extract
    return mod


def _patch_conn(rows=None, one=None, raise_conn=False):
    if raise_conn:
        return patch.object(aea, "get_cloud_db_connection", side_effect=Exception("DB down"))
    conn = FakeConn(FakeCursor(rows=rows, one=one))
    return patch.object(aea, "get_cloud_db_connection", return_value=conn)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestGetAttachmentTextsForEvent:
    def test_extracts_all_attachments(self):
        rows = [
            (1, "a.pdf", "application/pdf", 100, b"PDFBYTES"),
            (2, "b.csv", "text/csv", 50, b"x,y"),
        ]
        with _patch_conn(rows=rows), \
             patch.dict(sys.modules, {"attachment_text_extractor": _fake_extractor()}):
            items = aea.get_attachment_texts_for_event(123)

        assert len(items) == 2
        assert items[0]["text"] == "TEXT:a.pdf"
        assert items[1]["text"] == "TEXT:b.csv"
        assert all(i["error"] is None for i in items)

    def test_missing_content_is_non_fatal(self):
        rows = [(3, "c.pdf", "application/pdf", 0, None)]
        with _patch_conn(rows=rows), \
             patch.dict(sys.modules, {"attachment_text_extractor": _fake_extractor()}):
            items = aea.get_attachment_texts_for_event(9)

        assert len(items) == 1
        assert items[0]["text"] == ""
        assert "content not available" in items[0]["error"]

    def test_extractor_error_is_non_fatal(self):
        rows = [(4, "d.pdf", "application/pdf", 10, b"x")]
        with _patch_conn(rows=rows), \
             patch.dict(sys.modules, {"attachment_text_extractor": _fake_extractor(raise_for={"d.pdf"})}):
            items = aea.get_attachment_texts_for_event(9)

        assert len(items) == 1
        assert items[0]["text"] == ""
        assert items[0]["error"]

    def test_extraction_failure_result_captured(self):
        rows = [(5, "e.docx", "application/x", 10, b"x")]
        rmap = {"e.docx": {"success": False, "error": "bad format"}}
        with _patch_conn(rows=rows), \
             patch.dict(sys.modules, {"attachment_text_extractor": _fake_extractor(result_map=rmap)}):
            items = aea.get_attachment_texts_for_event(9)

        assert items[0]["text"] == ""
        assert items[0]["error"] == "bad format"

    def test_query_failure_returns_empty(self):
        with _patch_conn(raise_conn=True):
            items = aea.get_attachment_texts_for_event(9)
        assert items == []


@pytest.mark.unit
class TestFetchAttachmentBytes:
    def test_returns_tuple(self):
        with _patch_conn(one=("f.pdf", "application/pdf", 3, b"abc")):
            result = aea.fetch_attachment_bytes(7)
        assert result == ("f.pdf", "application/pdf", 3, b"abc")

    def test_missing_returns_none(self):
        with _patch_conn(one=None):
            assert aea.fetch_attachment_bytes(7) is None


@pytest.mark.unit
class TestBuildCombined:
    def test_joins_with_headers(self):
        items = [
            {"filename": "a.pdf", "text": "HELLO", "error": None},
            {"filename": "b.csv", "text": "WORLD", "error": None},
        ]
        out = aea.build_combined_attachment_text(items)
        assert "a.pdf" in out and "HELLO" in out
        assert "b.csv" in out and "WORLD" in out

    def test_notes_errors(self):
        items = [{"filename": "x.pdf", "text": "", "error": "content not available"}]
        out = aea.build_combined_attachment_text(items)
        assert "x.pdf" in out and "could not read" in out

    def test_caps_at_max_chars(self):
        items = [{"filename": "big.txt", "text": "A" * 1000, "error": None}]
        out = aea.build_combined_attachment_text(items, max_chars=100)
        assert len(out) <= 100 + 80  # cap + truncation marker
        assert "truncated" in out


class TestCapSeparation:
    """The combined cap is its own knob. It used to reuse the per-file cap, so an
    email with N attachments squeezed all of them into ONE file's budget."""

    def test_combined_is_not_bounded_by_the_per_file_cap(self):
        items = [{"filename": f"f{i}.txt", "text": "A" * 400, "error": None}
                 for i in range(5)]
        with patch.object(aea._cfg, "MAX_ATTACHMENT_CHARS", 500), \
             patch.object(aea._cfg, "MAX_ATTACHMENT_COMBINED_CHARS", 100000):
            out = aea.build_combined_attachment_text(items)

        assert "truncated" not in out
        for i in range(5):
            assert f"f{i}.txt" in out

    def test_combined_still_caps_at_its_own_setting(self):
        items = [{"filename": "big.txt", "text": "A" * 5000, "error": None}]
        with patch.object(aea._cfg, "MAX_ATTACHMENT_COMBINED_CHARS", 1000):
            out = aea.build_combined_attachment_text(items)
        assert "truncated" in out

    def test_per_file_cap_reads_its_own_setting(self):
        with patch.object(aea._cfg, "MAX_ATTACHMENT_CHARS", 1234):
            assert aea._default_max_chars(None) == 1234

    def test_explicit_argument_wins_over_config(self):
        assert aea._default_max_chars(88) == 88
        assert aea._default_combined_max_chars(77) == 77

    def test_garbage_setting_falls_back_to_default(self):
        with patch.object(aea._cfg, "MAX_ATTACHMENT_COMBINED_CHARS", "not-a-number"):
            assert aea._default_combined_max_chars(None) == 2000000
        with patch.object(aea._cfg, "MAX_ATTACHMENT_CHARS", None):
            assert aea._default_max_chars(None) == 500000
