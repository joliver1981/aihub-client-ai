"""
Regression tests for the CSV tabular lane (FedEx-invoice bug, 2026-08-25).

Root cause under test: CSV files uploaded as agent knowledge got NO structured
query lane — only Excel (.xlsx/.xls) did — so agents answered row-count and
arithmetic questions from chunked/truncated page text and invented numbers
(reported "41 rows" for a 1000+ row file because page_count was 41).

The fix extends agent_excel_tools to CSV/TSV:
  - load_tabular_dataframe() dispatches read_csv vs read_excel
  - generate_excel_metadata() profiles CSVs as a single sheet named "data"
    with EXACT row counts
  - the query tools (read/aggregate/summary/update) work on CSV paths
  - the system prompt tells the model these tools cover CSV and that page
    count is NOT row count
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
    import agent_excel_tools as aet
except Exception as e:  # pragma: no cover - env-dependent
    pytest.skip(f"agent_excel_tools not importable here: {e}", allow_module_level=True)


# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------

def _write_csv(tmp_path: Path, rows: int = 1047, name: str = "invoice.csv") -> Path:
    """A CSV echoing the bug's scale: 1000+ data rows."""
    df = pd.DataFrame({
        "Invoice Number": ["889441998"] * rows,
        "Tracking ID": [f"8811496245{i:04d}" for i in range(rows)],
        "Number of Pieces": [1] * rows,
        "Net Charge Amount": [10.5] * rows,
        "Banner": (["RU", "UP", "CR"] * rows)[:rows],
    })
    p = tmp_path / name
    df.to_csv(p, index=False)
    return p


# ---------------------------------------------------------------------------
# Loader dispatch
# ---------------------------------------------------------------------------

class TestLoadTabularDataframe:
    def test_is_tabular_data_file(self):
        assert aet.is_tabular_data_file("report.CSV")
        assert aet.is_tabular_data_file("report.tsv")
        assert aet.is_tabular_data_file("report.xlsx")
        assert aet.is_tabular_data_file("report.xls")
        assert not aet.is_tabular_data_file("report.pdf")
        assert not aet.is_tabular_data_file("")
        assert not aet.is_tabular_data_file(None)

    def test_csv_loads_all_rows(self, tmp_path):
        p = _write_csv(tmp_path, rows=1047)
        df = aet.load_tabular_dataframe(str(p))
        assert len(df) == 1047
        assert "Tracking ID" in df.columns

    def test_csv_ignores_sheet_name(self, tmp_path):
        p = _write_csv(tmp_path, rows=10)
        df = aet.load_tabular_dataframe(str(p), sheet_name="Sheet1")
        assert len(df) == 10

    def test_tsv_uses_tab_separator(self, tmp_path):
        p = tmp_path / "data.tsv"
        pd.DataFrame({"a": [1, 2], "b": [3, 4]}).to_csv(p, sep="\t", index=False)
        df = aet.load_tabular_dataframe(str(p))
        assert list(df.columns) == ["a", "b"]
        assert len(df) == 2

    def test_xlsx_still_loads(self, tmp_path):
        pytest.importorskip("openpyxl")
        p = tmp_path / "data.xlsx"
        pd.DataFrame({"x": [1, 2, 3]}).to_excel(p, index=False)
        df = aet.load_tabular_dataframe(str(p))
        assert len(df) == 3

    def test_cp1252_csv_does_not_crash(self, tmp_path):
        p = tmp_path / "win.csv"
        # 0x92 = cp1252 right single quote — invalid as UTF-8
        p.write_bytes(b"name,amount\r\nO\x92Brien,10\r\nSmith,20\r\n")
        df = aet.load_tabular_dataframe(str(p))
        assert len(df) == 2
        assert df["amount"].sum() == 30


# ---------------------------------------------------------------------------
# Metadata profile (feeds get_excel_summary + the system prompt row counts)
# ---------------------------------------------------------------------------

class TestCsvMetadata:
    def test_exact_row_count_and_single_data_sheet(self, tmp_path):
        p = _write_csv(tmp_path, rows=1047)
        meta = aet.generate_excel_metadata(str(p))
        assert meta["total_rows"] == 1047
        assert len(meta["sheets"]) == 1
        sheet = meta["sheets"][0]
        assert sheet["name"] == aet.CSV_SHEET_NAME
        assert sheet["row_count"] == 1047
        assert sheet["column_count"] == 5
        col_names = [c["name"] for c in sheet["columns"]]
        assert "Number of Pieces" in col_names

    def test_numeric_stats_present(self, tmp_path):
        p = _write_csv(tmp_path, rows=50)
        meta = aet.generate_excel_metadata(str(p))
        cols = {c["name"]: c for c in meta["sheets"][0]["columns"]}
        assert cols["Net Charge Amount"]["dtype"] == "number"
        assert cols["Net Charge Amount"]["min"] == 10.5
        assert cols["Net Charge Amount"]["max"] == 10.5

    def test_excel_metadata_path_unchanged(self, tmp_path):
        pytest.importorskip("openpyxl")
        p = tmp_path / "book.xlsx"
        pd.DataFrame({"x": [1, 2, 3], "y": ["a", "b", "c"]}).to_excel(
            p, index=False, sheet_name="MySheet")
        meta = aet.generate_excel_metadata(str(p))
        assert meta["total_rows"] == 3
        assert meta["sheets"][0]["name"] == "MySheet"


# ---------------------------------------------------------------------------
# Query tools on CSV paths
# ---------------------------------------------------------------------------

@pytest.fixture
def csv_toolset(tmp_path, monkeypatch):
    """An ExcelTool wired to a real 1047-row CSV, DB calls patched out."""
    p = _write_csv(tmp_path, rows=1047)
    meta = aet.generate_excel_metadata(str(p))
    monkeypatch.setattr(aet, "get_excel_metadata", lambda doc_id: meta)
    monkeypatch.setattr(aet, "get_excel_file_path", lambda doc_id: str(p))
    monkeypatch.setattr(aet, "_update_excel_metadata", lambda doc_id, m: None)
    docs = [{"knowledge_id": 1, "agent_id": 7, "document_id": "doc-csv-1",
             "description": "FedEx invoice", "filename": "invoice.csv",
             "document_type": "invoice"}]
    toolset = aet.ExcelTool(agent_id=7, excel_docs=docs)
    tools = {t.name: t for t in toolset.get_tools()}
    return toolset, tools, p


class TestCsvQueryTools:
    def test_summary_reports_exact_rows(self, csv_toolset):
        _, tools, _ = csv_toolset
        out = tools["get_excel_summary"].func(document_id="doc-csv-1")
        assert "1047" in out
        assert aet.CSV_SHEET_NAME in out

    def test_read_returns_rows_and_true_total(self, csv_toolset):
        _, tools, _ = csv_toolset
        out = tools["read_excel_data"].func(
            document_id="doc-csv-1", start_row=1, end_row=5)
        assert "(total: 1047)" in out
        assert "8811496245" in out  # actual data made it through

    def test_read_filter_condition(self, csv_toolset):
        _, tools, _ = csv_toolset
        out = tools["read_excel_data"].func(
            document_id="doc-csv-1", filter_condition="Banner == 'RU'")
        assert "of 349 rows" in out  # 1047/3 = 349 per banner

    def test_aggregate_sums_correctly(self, csv_toolset):
        _, tools, _ = csv_toolset
        out = tools["aggregate_excel_data"].func(
            document_id="doc-csv-1",
            aggregations='{"Number of Pieces": "sum"}')
        assert "1047" in out  # 1047 rows x 1 piece

    def test_aggregate_group_by_banner(self, csv_toolset):
        _, tools, _ = csv_toolset
        out = tools["aggregate_excel_data"].func(
            document_id="doc-csv-1", group_by="Banner",
            aggregations='{"Number of Pieces": "count"}')
        assert "RU" in out and "UP" in out and "CR" in out
        assert "349" in out

    def test_system_prompt_covers_csv_and_forbids_page_math(self, csv_toolset):
        toolset, _, _ = csv_toolset
        prompt = toolset.get_system_prompt_addition()
        assert "CSV" in prompt
        assert "invoice.csv" in prompt
        assert "1047 total rows" in prompt
        assert "page count is NOT its row count" in prompt


class TestCsvUpdateTool:
    def test_cells_refused_for_csv(self, csv_toolset):
        _, tools, _ = csv_toolset
        out = tools["update_excel_data"].func(
            document_id="doc-csv-1", updates='{"cells": {"A1": "x"}}')
        assert "not" in out.lower() and "csv" in out.lower()

    def test_row_update_and_add_rows_persist(self, csv_toolset, monkeypatch):
        _, tools, p = csv_toolset
        out = tools["update_excel_data"].func(
            document_id="doc-csv-1",
            updates='{"rows": {"2": {"Banner": "MD"}}, '
                    '"add_rows": [{"Invoice Number": "999", "Number of Pieces": 2}]}')
        assert "Successfully applied" in out
        df = pd.read_csv(p)
        assert df.iloc[0]["Banner"] == "MD"       # row 2 = first data row
        assert len(df) == 1048                     # one appended row
        assert df.iloc[-1]["Number of Pieces"] == 2


# ---------------------------------------------------------------------------
# run_tabular_query — path-based entry point behind /api/internal/tabular/query
# (serves The Agent's query_tabular_file tool)
# ---------------------------------------------------------------------------

class TestRunTabularQuery:
    def test_summary_has_exact_rows(self, tmp_path):
        p = _write_csv(tmp_path, rows=1047)
        r = aet.run_tabular_query(str(p), "summary")
        assert r["ok"] is True
        assert "Total rows across all sheets: 1047" in r["text"]
        assert "Tracking ID" in r["text"]

    def test_read_slice(self, tmp_path):
        p = _write_csv(tmp_path, rows=30)
        r = aet.run_tabular_query(str(p), "read",
                                  {"start_row": 1, "end_row": 5,
                                   "columns": "Invoice Number,Banner"})
        assert r["ok"] is True
        assert "(total: 30)" in r["text"]

    def test_aggregate_group_by(self, tmp_path):
        p = _write_csv(tmp_path, rows=30)
        r = aet.run_tabular_query(str(p), "aggregate",
                                  {"group_by": "Banner",
                                   "aggregations": '{"Number of Pieces": "sum"}'})
        assert r["ok"] is True
        assert "grouped by: Banner" in r["text"]

    def test_aggregate_sum_keeps_full_precision(self, tmp_path):
        """Handoff B (2026-08-28): to_markdown's default 6-sig-fig 'g' float
        format rendered sums over ~1e6 as scientific notation (1.79628e+06),
        so the agent reported '$1,796,280 (approximately)' where the file
        holds an exact figure. floatfmt='.15g' must keep the cents."""
        p = tmp_path / "revenue.csv"
        p.write_text("region,net_revenue\n"
                     "North,896141.58\nSouth,900141.59\n", encoding="utf-8")
        r = aet.run_tabular_query(str(p), "aggregate",
                                  {"aggregations": '{"net_revenue": "sum"}'})
        assert r["ok"] is True
        assert "1796283.17" in r["text"]
        assert "e+06" not in r["text"]

    def test_read_slice_keeps_full_precision(self, tmp_path):
        p = tmp_path / "revenue.csv"
        p.write_text("region,net_revenue\nNorth,1796283.17\n", encoding="utf-8")
        r = aet.run_tabular_query(str(p), "read", {})
        assert r["ok"] is True
        assert "1796283.17" in r["text"]
        assert "e+06" not in r["text"]

    def test_missing_file(self, tmp_path):
        r = aet.run_tabular_query(str(tmp_path / "ghost.csv"), "summary")
        assert r["ok"] is False
        assert "No such file" in r["error"]

    def test_non_tabular_extension(self, tmp_path):
        p = tmp_path / "doc.pdf"
        p.write_bytes(b"%PDF-1.4 fake")
        r = aet.run_tabular_query(str(p), "summary")
        assert r["ok"] is False
        assert "not a tabular data file" in r["error"]

    def test_unknown_operation(self, tmp_path):
        p = _write_csv(tmp_path, rows=3)
        r = aet.run_tabular_query(str(p), "delete_everything")
        assert r["ok"] is False
        assert "Unknown operation" in r["error"]


# ---------------------------------------------------------------------------
# CC attachment extractor — exact-count fact lines (honest previews)
# ---------------------------------------------------------------------------

class TestAttachmentExtractorFacts:
    def _extract_csv(self, rows: int) -> str:
        import attachment_text_extractor as ate
        lines = ["col_a,col_b"] + [f"v{i},{i}" for i in range(rows)]
        return ate.extract_csv_text("\n".join(lines).encode("utf-8"), "t.csv")

    def test_small_csv_gets_fact_line_no_warning(self):
        out = self._extract_csv(40)
        assert "[CSV file facts: 40 data rows + 1 header row, 2 columns.]" in out
        assert "PREVIEW" not in out

    def test_large_csv_fact_line_and_truncation_totals(self):
        out = self._extract_csv(1047)
        assert "1047 data rows" in out
        assert "PREVIEW of the first 500 rows" in out
        assert "run code against the original file" in out
        # Footer restates the true total next to what was cut
        assert "548 more rows" in out  # 1048 total lines - 500 shown
        assert "1047" in out.rsplit("...", 1)[-1]

    def test_large_xlsx_sheet_header_totals(self, tmp_path):
        openpyxl = pytest.importorskip("openpyxl")
        import attachment_text_extractor as ate
        p = tmp_path / "big.xlsx"
        df = pd.DataFrame({"a": range(700), "b": range(700)})
        df.to_excel(p, index=False, sheet_name="Data")
        out = ate.extract_xlsx_text(p.read_bytes(), "big.xlsx")
        assert "700 data rows + 1 header row" in out
        assert "PREVIEW below shows only the first 500 rows" in out
        assert "201 more" in out  # 701 total rows - 500 shown
