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


# ---------------------------------------------------------------------------
# Generalized resolver: load_text_file + send_email_message attachments
# (audit follow-up 2026-08-25 — same dead-path class as process_csv/show_csv)
# ---------------------------------------------------------------------------

@pytest.fixture
def mixed_uploads(tmp_path, monkeypatch):
    """Agent with TWO uploads: a CSV and a TXT — exercises extension filters."""
    csv_path = tmp_path / "data.csv"
    pd.DataFrame({"a": [1]}).to_csv(csv_path, index=False)
    txt_path = tmp_path / "notes.txt"
    txt_path.write_text("hello uploaded world", encoding="utf-8")
    docs = [
        {"document_id": "d-csv", "filename": "u1_data.csv",
         "original_path": str(csv_path)},
        {"document_id": "d-txt", "filename": "u2_notes.txt",
         "original_path": str(txt_path)},
    ]
    monkeypatch.setattr(aki, "get_agent_knowledge_documents",
                        lambda agent_id, user_id=None: docs)
    ga._current_agent_context.agent_id = 7007
    ga._current_agent_context.user_id = 1
    yield csv_path, txt_path
    for attr in ("agent_id", "user_id"):
        if hasattr(ga._current_agent_context, attr):
            delattr(ga._current_agent_context, attr)


class TestGeneralizedResolver:
    def test_csv_auto_pick_ignores_txt(self, mixed_uploads):
        csv_path, _ = mixed_uploads
        path, err = ga._resolve_uploaded_csv_path("wrong_name.csv")
        assert err is None and path == str(csv_path)

    def test_text_auto_pick_ignores_nothing_wrongly(self, mixed_uploads):
        # extensions=_TEXT_FILE_EXTS covers BOTH .txt and .csv → two matches,
        # so no auto-pick: the error must list both by name.
        _, _ = mixed_uploads
        path, err = ga._resolve_uploaded_file_path(
            "ghost.txt", extensions=ga._TEXT_FILE_EXTS, kind="text file")
        assert path is None
        assert "data.csv" in err and "notes.txt" in err

    def test_load_text_file_by_display_name(self, mixed_uploads):
        out = ga.load_text_file.func(file_path="notes.txt")
        assert "hello uploaded world" in out

    def test_load_text_file_literal_path_still_works(self, mixed_uploads, tmp_path):
        p = tmp_path / "direct.txt"
        p.write_text("direct read", encoding="utf-8")
        out = ga.load_text_file.func(file_path=str(p))
        assert "direct read" in out

    def test_send_email_resolves_uploaded_attachment(self, mixed_uploads, monkeypatch):
        _, txt_path = mixed_uploads
        sent = {}
        monkeypatch.setattr(ga, "send_email",
                            lambda to, subj, msg, attach, is_html: sent.update(
                                attach=attach) or True)
        out = ga.send_email_message.func(
            email_to="a@b.com", subject="s", message="m",
            attachment_file_path="notes.txt")
        assert "Successfully sent" in out
        assert sent["attach"] == str(txt_path)

    def test_send_email_fails_closed_on_missing_attachment(self, mixed_uploads, monkeypatch):
        called = {}
        monkeypatch.setattr(ga, "send_email",
                            lambda *a, **k: called.update(sent=True) or True)
        monkeypatch.setattr(aki, "get_agent_knowledge_documents",
                            lambda agent_id, user_id=None: [])
        out = ga.send_email_message.func(
            email_to="a@b.com", subject="s", message="m",
            attachment_file_path=r"C:\nope\ghost.pdf")
        assert "NOT sent" in out
        assert "sent" not in called   # never silently sent without attachment

    def test_send_email_without_attachment_unchanged(self, monkeypatch):
        monkeypatch.setattr(ga, "send_email", lambda *a, **k: True)
        out = ga.send_email_message.func(email_to="a@b.com", subject="s",
                                         message="m")
        assert "Successfully sent" in out


# ---------------------------------------------------------------------------
# Catalog hygiene + binding-loop type guard (audit fixes 3 + 4)
# ---------------------------------------------------------------------------

class TestCatalogAndBindingGuard:
    def test_get_document_fields_removed_from_catalog(self):
        import yaml
        with open(_ROOT / "core_tools.yaml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        names = []
        def walk(node):
            if isinstance(node, dict):
                if isinstance(node.get("name"), str):
                    names.append(node["name"])
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        walk(cfg)
        assert "get_document_fields" not in names
        assert "list_document_fields" in names   # the real tool stays

    def test_every_catalog_tool_is_a_real_tool_or_special_cased(self):
        """The audit that found the drift, now standing guard: every catalog
        name must resolve to a BaseTool in GeneralAgent globals, except the
        names bound by dedicated loaders."""
        import yaml
        from langchain_core.tools import BaseTool
        SPECIAL = {"manage_knowledge", "list_integrations",
                   "get_integration_operations", "execute_integration"}
        with open(_ROOT / "core_tools.yaml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        names = set()
        def walk(node):
            if isinstance(node, dict):
                if isinstance(node.get("name"), str):
                    names.add(node["name"])
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        walk(cfg)
        bad = []
        for n in sorted(names - SPECIAL):
            obj = getattr(ga, n, None)
            if not isinstance(obj, BaseTool):
                bad.append(f"{n} -> {type(obj).__name__}")
        assert not bad, f"catalog names not resolving to tools: {bad}"

    def test_binding_loop_has_type_guard(self):
        src = (_ROOT / "GeneralAgent.py").read_text(encoding="utf-8")
        assert "not isinstance(tool_func, _LCBaseTool)" in src
        assert "SKIPPED" in src
