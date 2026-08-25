"""
Admit-or-deny policy, P1 (2026-08-25) — General Agent surface.

Covers:
- chat_upload_admission.check_admission: admit / per-file deny / budget deny /
  tabular bypass / soft warn, with real numbers in every user-facing message
- DocUtils budgeted page readers (mocked pyodbc): FULL page text, whole pages
  only, page-boundary stop with continuation, start_page continuation
- agent_knowledge_integration._format_knowledge_response: pages never sliced,
  budget stops between pages and enumerates omissions
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import chat_upload_admission as adm  # noqa: E402


# ---------------------------------------------------------------------------
# Policy decisions
# ---------------------------------------------------------------------------

class TestCheckAdmission:
    def test_small_file_admitted_clean(self):
        r = adm.check_admission("report.pdf", 40_000)  # ~10K tokens
        assert r["admit"] and r["reason"] == "ok"
        assert r["warning"] is None and r["message"] is None

    def test_per_file_deny_has_numbers_and_alternatives(self):
        r = adm.check_admission("huge.pdf", 2_000_000)  # ~500K tokens > 300K
        assert not r["admit"] and r["reason"] == "per_file"
        assert "500,000" in r["message"]
        assert "300,000" in r["message"]
        assert "NOT added" in r["message"]
        assert "document repository" in r["message"]

    def test_budget_deny_has_numbers(self):
        # existing 2.3M chars (~575K tokens) + 200K chars (~50K tokens) > 600K
        r = adm.check_admission("more.pdf", 200_000,
                                existing_conversation_chars=2_300_000)
        assert not r["admit"] and r["reason"] == "budget"
        assert "575,000" in r["message"]
        assert "600,000" in r["message"]
        assert "new conversation" in r["message"]

    def test_tabular_bypasses_all_ceilings(self):
        r = adm.check_admission("giant.csv", 50_000_000,
                                existing_conversation_chars=50_000_000)
        assert r["admit"] and r["reason"] == "tabular_lane"
        for ext in (".tsv", ".xlsx", ".xls"):
            assert adm.check_admission(f"f{ext}", 10_000_000)["admit"]

    def test_soft_warn_between_warn_and_limit(self):
        r = adm.check_admission("big.pdf", 800_000)  # ~200K tokens
        assert r["admit"] and r["warning"] is not None
        assert "200,000" in r["warning"]

    def test_at_exact_limit_admitted(self):
        r = adm.check_admission("edge.pdf", adm.per_file_limit_tokens() * 4)
        assert r["admit"]


# ---------------------------------------------------------------------------
# DocUtils budgeted readers (pyodbc mocked)
# ---------------------------------------------------------------------------

import DocUtils  # noqa: E402


class _FakeCursor:
    def __init__(self, fetchone_seq, fetchall_seq):
        self._one = list(fetchone_seq)
        self._all = list(fetchall_seq)

    def execute(self, *a, **k):
        return self

    def fetchone(self):
        return self._one.pop(0) if self._one else None

    def fetchall(self):
        return self._all.pop(0) if self._all else []

    def close(self):
        pass


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def close(self):
        pass


def _patch_connect(monkeypatch, cursor):
    monkeypatch.setattr(DocUtils.pyodbc, "connect",
                        lambda *a, **k: _FakeConn(cursor))


def _pages(doc_id, texts, filename="f.pdf"):
    total = len(texts)
    return [(f"pg{i+1}", i + 1, t, doc_id, filename, "doc", total)
            for i, t in enumerate(texts)]


class TestGetDocumentByIdBudget:
    def test_under_budget_full_text_no_continuation(self, monkeypatch):
        import config as cfg
        monkeypatch.setattr(cfg, "AGENT_PAGE_READ_BUDGET_TOKENS", 1000)  # 4000 chars
        texts = ["A" * 900, "B" * 900, "C" * 900]
        cur = _FakeCursor([(3,)], [_pages("d1", texts)])
        _patch_connect(monkeypatch, cur)
        out = json.loads(DocUtils.get_document_by_id("cs", "d1"))
        assert out["error"] is None
        assert [p["text"] for p in out["pages"]] == texts  # FULL, unsliced
        assert "continuation" not in out

    def test_over_budget_stops_at_page_boundary(self, monkeypatch):
        import config as cfg
        monkeypatch.setattr(cfg, "AGENT_PAGE_READ_BUDGET_TOKENS", 500)  # 2000 chars
        texts = ["A" * 900, "B" * 900, "C" * 900, "D" * 900]
        cur = _FakeCursor([(4,)], [_pages("d1", texts)])
        _patch_connect(monkeypatch, cur)
        out = json.loads(DocUtils.get_document_by_id("cs", "d1"))
        assert len(out["pages"]) == 2                      # 1800 fits, 2700 doesn't
        assert out["pages"][0]["text"] == "A" * 900        # whole page, no slice
        cont = out["continuation"]
        assert cont["next_start_page"] == 3
        assert "IN FULL" in cont["note"]

    def test_start_page_continuation(self, monkeypatch):
        import config as cfg
        monkeypatch.setattr(cfg, "AGENT_PAGE_READ_BUDGET_TOKENS", 500)
        texts = ["A" * 900, "B" * 900, "C" * 900, "D" * 900]
        cur = _FakeCursor([(4,)], [_pages("d1", texts)])
        _patch_connect(monkeypatch, cur)
        out = json.loads(DocUtils.get_document_by_id("cs", "d1", start_page=3))
        assert [p["page_number"] for p in out["pages"]] == [3, 4]
        assert "continuation" not in out

    def test_single_oversized_page_served_whole(self, monkeypatch):
        import config as cfg
        monkeypatch.setattr(cfg, "AGENT_PAGE_READ_BUDGET_TOKENS", 100)  # 400 chars
        texts = ["X" * 5000, "Y" * 100]
        cur = _FakeCursor([(2,)], [_pages("d1", texts)])
        _patch_connect(monkeypatch, cur)
        out = json.loads(DocUtils.get_document_by_id("cs", "d1"))
        # First page exceeds the whole budget but is served WHOLE (never sliced)
        assert out["pages"][0]["text"] == "X" * 5000
        assert out["continuation"]["next_start_page"] == 2


class TestGetDocumentsByIdsBudget:
    def _rows(self, doc_id, texts, filename):
        total = len(texts)
        return [(f"{doc_id}pg{i+1}", i + 1, t, doc_id, filename, "doc", total, "")
                for i, t in enumerate(texts)]

    def test_budget_across_documents_reports_unserved(self, monkeypatch):
        import config as cfg
        monkeypatch.setattr(cfg, "AGENT_PAGE_READ_BUDGET_TOKENS", 500)  # 2000 chars
        rows = (self._rows("d1", ["A" * 900, "B" * 900, "C" * 900], "one.pdf")
                + self._rows("d2", ["Z" * 900], "two.pdf"))
        cur = _FakeCursor([], [[("d1", 3), ("d2", 1)], rows])
        _patch_connect(monkeypatch, cur)
        out = json.loads(DocUtils.get_documents_by_ids("cs", ["d1", "d2"]))
        assert len(out["documents"]["d1"]["pages"]) == 2   # stopped before page 3
        cont = out["continuation"]
        assert cont["stopped_at_document_id"] == "d1"
        assert cont["next_start_page"] == 3
        assert "d2" in cont["unserved_document_ids"]
        assert "Not served" in out["documents"]["d2"]["error"]

    def test_under_budget_all_docs_full(self, monkeypatch):
        import config as cfg
        monkeypatch.setattr(cfg, "AGENT_PAGE_READ_BUDGET_TOKENS", 10_000)
        rows = (self._rows("d1", ["A" * 900], "one.pdf")
                + self._rows("d2", ["Z" * 900], "two.pdf"))
        cur = _FakeCursor([], [[("d1", 1), ("d2", 1)], rows])
        _patch_connect(monkeypatch, cur)
        out = json.loads(DocUtils.get_documents_by_ids("cs", ["d1", "d2"]))
        assert "continuation" not in out
        assert out["documents"]["d1"]["pages"][0]["text"] == "A" * 900
        assert out["documents"]["d2"]["pages"][0]["text"] == "Z" * 900


# ---------------------------------------------------------------------------
# _format_knowledge_response — whole pages + enumerated omissions
# ---------------------------------------------------------------------------

class TestFormatKnowledgeResponse:
    def _fmt(self, contents, apply_caps=True):
        from agent_knowledge_integration import _format_knowledge_response
        return _format_knowledge_response(contents, apply_caps=apply_caps)

    def test_large_page_not_sliced(self):
        big = "R" * 120_000  # would have been cut at 50K by the old code
        out = self._fmt({"d1": {"filename": "f.csv", "document_type": "t",
                                "pages": {1: big}}})
        assert big in out
        assert "truncated" not in out.lower()

    def test_budget_stops_between_pages_and_enumerates(self):
        p = "A" * 250_000
        contents = {
            "d1": {"filename": "one.pdf", "document_type": "t",
                   "pages": {1: p, 2: p}},          # page 2 blows the 400K budget
            "d2": {"filename": "two.pdf", "document_type": "t",
                   "pages": {1: "small"}},
        }
        out = self._fmt(contents)
        assert out.count("A" * 1000) >= 1
        assert "NOT INCLUDED" in out
        assert "one.pdf page 2" in out
        assert "COMPLETE" in out            # what IS shown is whole
        assert "get_document_pages" in out  # escape hatch named

    def test_no_caps_mode_unchanged(self):
        p = "B" * 500_000
        out = self._fmt({"d1": {"filename": "f.pdf", "document_type": "t",
                                "pages": {1: p}}}, apply_caps=False)
        assert p in out and "NOT INCLUDED" not in out
