"""
Legacy CSV tools (process_csv / show_csv) path resolution (2026-08-25).

Bug: both tools take a literal server path and pd.read_csv it — but chat
uploads are DELETED from the temp uploads dir after ingestion, so every
uploaded CSV produced "Error: File not found at ..." → the agent apologized
"the direct CSV-processing tool could not open the file path" (reported from
a client machine, S:\\AIHub_Installer\\csv_issue logs).

Fix under test: _resolve_uploaded_csv_path — literal path passthrough, then
uploaded-file resolution by display name / stored uuid_name / document_id via
Documents.original_path with agent_files-tee fallback, then single-CSV
auto-pick, then an honest error listing resolvable files.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

pd = pytest.importorskip("pandas")

try:
    import GeneralAgent as ga
except Exception as e:  # pragma: no cover - env-dependent
    pytest.skip(f"GeneralAgent not importable here: {e}", allow_module_level=True)

import agent_knowledge_integration as aki  # noqa: E402
import chat_file_manager as cfm  # noqa: E402


@pytest.fixture
def agent_ctx(tmp_path, monkeypatch):
    """Thread-local agent context + two knowledge docs: one CSV whose
    original_path exists, one PDF. Returns (tmp_path, csv_path)."""
    csv_path = tmp_path / "invoice_900112233.csv"
    pd.DataFrame({"a": range(5), "b": range(5)}).to_csv(csv_path, index=False)

    docs = [
        {"document_id": "doc-csv-42",
         "filename": "f028ff7b-66be-4458-8aee-1a0aa74ff3a3_invoice_900112233.csv",
         "original_path": str(csv_path)},
        {"document_id": "doc-pdf-7",
         "filename": "aaaa_report.pdf",
         "original_path": None},
    ]
    monkeypatch.setattr(aki, "get_agent_knowledge_documents",
                        lambda agent_id, user_id=None: docs)
    ga._current_agent_context.agent_id = 7007
    ga._current_agent_context.user_id = 1
    yield tmp_path, csv_path
    for attr in ("agent_id", "user_id"):
        if hasattr(ga._current_agent_context, attr):
            delattr(ga._current_agent_context, attr)


class TestResolver:
    def test_literal_existing_path_passthrough(self, agent_ctx):
        _, csv_path = agent_ctx
        path, err = ga._resolve_uploaded_csv_path(str(csv_path))
        assert err is None and path == str(csv_path)

    def test_display_filename_resolves(self, agent_ctx):
        _, csv_path = agent_ctx
        path, err = ga._resolve_uploaded_csv_path("invoice_900112233.csv")
        assert err is None and path == str(csv_path)

    def test_stored_uuid_name_resolves(self, agent_ctx):
        _, csv_path = agent_ctx
        path, err = ga._resolve_uploaded_csv_path(
            "f028ff7b-66be-4458-8aee-1a0aa74ff3a3_invoice_900112233.csv")
        assert err is None and path == str(csv_path)

    def test_document_id_resolves(self, agent_ctx):
        _, csv_path = agent_ctx
        path, err = ga._resolve_uploaded_csv_path("doc-csv-42")
        assert err is None and path == str(csv_path)

    def test_guessed_uploads_temp_path_resolves_by_basename(self, agent_ctx):
        # The exact failure mode from the client log: model passes the dead
        # temp path "...\uploads\<uuid>_name.csv"
        _, csv_path = agent_ctx
        dead = r"C:\Program Files\AIHub\uploads\f028ff7b-66be-4458-8aee-1a0aa74ff3a3_invoice_900112233.csv"
        path, err = ga._resolve_uploaded_csv_path(dead)
        assert err is None and path == str(csv_path)

    def test_wrong_name_single_csv_auto_picks(self, agent_ctx):
        _, csv_path = agent_ctx
        path, err = ga._resolve_uploaded_csv_path("the_invoice_file.csv")
        assert err is None and path == str(csv_path)

    def test_tee_fallback_when_original_path_dead(self, agent_ctx, monkeypatch):
        tmp, csv_path = agent_ctx
        tee_path = tmp / "tee_copy.csv"
        tee_path.write_bytes(csv_path.read_bytes())
        docs = [{"document_id": "doc-csv-42",
                 "filename": "f028_invoice_900112233.csv",
                 "original_path": str(tmp / "deleted-temp.csv")}]  # dead
        monkeypatch.setattr(aki, "get_agent_knowledge_documents",
                            lambda agent_id, user_id=None: docs)
        monkeypatch.setattr(cfm, "get_agent_input_path",
                            lambda agent_id, user_id, file_id: tee_path)
        path, err = ga._resolve_uploaded_csv_path("invoice_900112233.csv")
        assert err is None and path == str(tee_path)

    def test_no_csvs_gives_honest_error(self, agent_ctx, monkeypatch):
        monkeypatch.setattr(aki, "get_agent_knowledge_documents",
                            lambda agent_id, user_id=None: [])
        path, err = ga._resolve_uploaded_csv_path("ghost.csv")
        assert path is None
        assert "no" in err.lower() and "uploaded CSV" in err


class TestToolsEndToEnd:
    def test_show_csv_by_display_name(self, agent_ctx):
        out = ga.show_csv.func(file_path="invoice_900112233.csv", rows=3)
        assert "Total Rows: 5" in out

    def test_process_csv_summarize_by_display_name(self, agent_ctx):
        out = ga.process_csv.func(file_path="invoice_900112233.csv",
                                  operation="summarize")
        assert "Total Rows: 5" in out

    def test_process_csv_honest_error_when_nothing_matches(self, agent_ctx, monkeypatch):
        monkeypatch.setattr(aki, "get_agent_knowledge_documents",
                            lambda agent_id, user_id=None: [])
        out = ga.process_csv.func(file_path=r"C:\nope\ghost.csv",
                                  operation="summarize")
        assert "Error: File not found" in out
