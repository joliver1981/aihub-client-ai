"""Unit pack for pass 3 (2026-09-02): export_data + manipulate_pdf
(agent_service/export_tools.py over code_tools.execute_python) and read_file's
image (vision) path in document_tools.

The sandbox is monkeypatched (no interpreter is launched); the pure helpers —
SQL gate, rows_json parsing, page specs, script composition — are exercised
directly. Runs standalone or under pytest; self-skips without the SDK.
"""
import asyncio
import base64
import os
import sys
import tempfile
from unittest import mock

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

try:
    import export_tools as X                   # noqa: E402
    import document_tools as D                 # noqa: E402
    import code_tools as C                     # noqa: E402
    from platform_tools import CURRENT_USER    # noqa: E402
    HAVE_SDK = True
except ImportError as e:
    HAVE_SDK = False
    _IMPORT_ERR = e

if not HAVE_SDK:
    try:
        import pytest
        pytestmark = pytest.mark.skip(
            reason=f"needs the aihub-agent env (claude_agent_sdk): {_IMPORT_ERR}")
    except ImportError:
        pass

# A 1x1 transparent PNG.
PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def _run(coro):
    return asyncio.run(coro)


def _txt(res):
    return res["content"][0]["text"]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_safe_name_and_sql_gate():
    assert X.safe_name("Vendors list / Q3", "xlsx") == "Vendors_list_Q3.xlsx"
    assert X.safe_name("..\\..\\evil.pdf", "csv") == "evil.csv"
    assert X.safe_name("", "json") == "export.json"
    assert X.safe_name("report.xlsx", "xlsx") == "report.xlsx"
    ok, _ = X.sql_is_select_only("SELECT a, b FROM t WHERE x = 1 ORDER BY a")
    assert ok
    ok, _ = X.sql_is_select_only("  with c as (select 1 as v) select * from c; ")
    assert ok
    for bad in ("DELETE FROM t", "select * from t; drop table t", "SELECT * INTO t2 FROM t",
                "EXEC sp_who", "", "select 1 -- x\n; select 2", "update t set a=1"):
        ok, why = X.sql_is_select_only(bad)
        assert not ok, bad
    ok, _ = X.sql_is_select_only("/* comment */ SELECT 1")
    assert ok


def test_parse_rows_json_shapes():
    cols, rows, err = X.parse_rows_json('[{"a": 1, "b": "x"}, {"b": "y", "c": 3}]')
    assert err is None and cols == ["a", "b", "c"] and rows == [[1, "x", None], [None, "y", 3]]
    cols, rows, err = X.parse_rows_json('{"columns": ["a", "b"], "rows": [[1, 2], [3]]}')
    assert err is None and rows == [[1, 2], [3, None]]
    cols, rows, err = X.parse_rows_json('{"rows": [[1]]}')
    assert err and "columns" in err
    _c, _r, err = X.parse_rows_json("[]")
    assert err and "empty" in err
    _c, _r, err = X.parse_rows_json("[[1, 2]]")
    assert err and "list of objects" in err
    _c, _r, err = X.parse_rows_json("not json")
    assert err and "not valid JSON" in err


def test_parse_pages():
    assert X.parse_pages("1-3,7, 10-11") == ([1, 2, 3, 7, 10, 11], None)
    assert X.parse_pages("") == ([], None)
    assert X.parse_pages("3,3,2") == ([3, 2], None)
    assert X.parse_pages("5-2")[1]
    assert X.parse_pages("0")[1]
    assert X.parse_pages("a-b")[1]


def test_compose_export_code_embeds_data_safely_and_picks_writer():
    code = X.compose_export_code("xlsx", "out.xlsx", rows_json_text='[[1, "it\'s \\"q\\""]]',
                                 columns=["n", "s"], sheet_name="Data")
    assert "to_excel" in code and "freeze_panes" in code and "json.loads" in code
    assert "aihub_runtime" not in code
    compile(code, "<export>", "exec")                       # the script must at least parse
    code = X.compose_export_code("csv", "o.csv", connection="ERPDB", sql="SELECT 1 AS x")
    assert "aihub.query(\"ERPDB\", \"SELECT 1 AS x\")" in code and "to_csv" in code
    assert f"_rows[:{X.MAX_ROWS}]" in code
    compile(code, "<export>", "exec")
    code = X.compose_export_code("pdf", "o.pdf", connection="ERPDB", sql="SELECT 1", title="T")
    assert "SimpleDocTemplate" in code and "repeatRows=1" in code
    compile(code, "<export>", "exec")
    code = X.compose_export_code("json", "o.json", connection="ERPDB", sql="SELECT 1")
    assert "orient='records'" in code
    compile(code, "<export>", "exec")


def test_compose_pdf_code_per_operation():
    for op in X.PDF_OPS:
        code = X.compose_pdf_code(op, ["in_0.pdf", "in_1.pdf"], [1, 2], 90, "out.pdf", "doc")
        compile(code, "<pdf>", "exec")
        assert "PdfReader" in code
    assert "page.rotate(deg)" in X.compose_pdf_code("rotate", ["in_0.pdf"], [], 180, "o.pdf", "d")
    assert "zipfile.ZipFile" in X.compose_pdf_code("split", ["in_0.pdf"], [], 0, "", "d")
    assert "for f in inputs" in X.compose_pdf_code("merge", ["a", "b"], [], 0, "m.pdf", "d")


# ---------------------------------------------------------------------------
# export_data tool (sandbox mocked)
# ---------------------------------------------------------------------------

def _fake_exec(calls, output, links, ok=True):
    async def fake(uid, code, **kw):
        calls.append({"uid": uid, "code": code, **kw})
        return {"configured": True, "ok": ok, "timed_out": False, "returncode": 0 if ok else 1,
                "output": output, "links": links, "produced": [], "manifest": "", "error": None}
    return fake


def test_export_data_source_rules_and_success():
    tok = CURRENT_USER.set({"user_id": 7, "role": 2, "username": "dev"})
    calls = []
    link = "[⤓ vendors.xlsx (12.0 KB)](/api/files/0f1e2d3c-1111-2222-3333-444455556666)"

    async def fake_index():
        return [{"id": "3", "name": "ERPDB", "type": "mssql", "database": "ERPDB"}]

    try:
        with mock.patch.object(X, "execute_python", _fake_exec(calls, "ROWS=17\nCOLS=2\nTRUNCATED=0\nFILE=vendors.xlsx", [link])), \
             mock.patch("platform_tools._connections_index", fake_index):
            res = _run(X.export_data.handler({"name": "vendors", "format": "xlsx"}))
            assert res.get("is_error") and "exactly ONE source" in _txt(res)
            res = _run(X.export_data.handler({"name": "vendors", "format": "xlsx",
                                              "rows_json": "[{\"a\":1}]", "connection": "ERPDB", "sql": "select 1"}))
            assert res.get("is_error") and "exactly ONE source" in _txt(res)
            res = _run(X.export_data.handler({"name": "vendors", "format": "xlsx",
                                              "connection": "ERPDB", "sql": "DELETE FROM x"}))
            assert res.get("is_error") and "Nothing exported" in _txt(res) and not calls
            res = _run(X.export_data.handler({"name": "vendors", "format": "xlsx",
                                              "connection": "ERPDB", "sql": "SELECT * INTO t2 FROM t"}))
            assert res.get("is_error") and "read-only" in _txt(res) and not calls
            res = _run(X.export_data.handler({"name": "vendors", "format": "docx", "rows_json": "[{\"a\":1}]"}))
            assert res.get("is_error") and "format must be" in _txt(res)
            res = _run(X.export_data.handler({"name": "vendors", "format": "xlsx",
                                              "connection": "3", "sql": "SELECT name, status FROM LFA1"}))
            assert not res.get("is_error"), _txt(res)
            assert "Exported 17 row(s)" in _txt(res) and link in _txt(res) and "connection 'ERPDB'" in _txt(res)
            assert calls[-1]["lane"] == "export_data" and calls[-1]["stage_uploads"] is False
            assert 'aihub.query("ERPDB"' in calls[-1]["code"]
            res = _run(X.export_data.handler({"name": "nope", "format": "csv", "connection": "Nope", "sql": "SELECT 1"}))
            assert res.get("is_error") and "No connection named" in _txt(res)
    finally:
        CURRENT_USER.reset(tok)


def test_export_data_reports_zero_rows_failures_and_truncation():
    tok = CURRENT_USER.set({"user_id": 7, "role": 2, "username": "dev"})
    calls = []
    try:
        with mock.patch.object(X, "execute_python", _fake_exec(calls, "ROWS=0\nCOLS=0\nTRUNCATED=0", [])):
            res = _run(X.export_data.handler({"name": "x", "format": "csv", "rows_json": "[{\"a\": 1}]"}))
            assert "0 rows" in _txt(res) and not res.get("is_error")
        with mock.patch.object(X, "execute_python", _fake_exec(calls, "Error (exit 1): boom", [], ok=False)):
            res = _run(X.export_data.handler({"name": "x", "format": "csv", "rows_json": "[{\"a\": 1}]"}))
            assert res.get("is_error") and "FAILED" in _txt(res) and "boom" in _txt(res)
        link = "[⤓ x.pdf (1.0 KB)](/api/files/0f1e2d3c-1111-2222-3333-444455556666)"
        with mock.patch.object(X, "execute_python",
                               _fake_exec(calls, "ROWS=5000\nCOLS=3\nTRUNCATED=1\nPDF_ROWS_SHOWN=2000\nFILE=x.pdf", [link])):
            res = _run(X.export_data.handler({"name": "x", "format": "pdf", "rows_json": "[{\"a\": 1}]"}))
            assert "cut at that cap" in _txt(res) and "first 2000 rows" in _txt(res)
    finally:
        CURRENT_USER.reset(tok)


# ---------------------------------------------------------------------------
# manipulate_pdf tool (sandbox mocked, path resolver mocked)
# ---------------------------------------------------------------------------

def test_manipulate_pdf_validation_and_operations():
    tok = CURRENT_USER.set({"user_id": 7, "role": 2, "username": "dev"})
    calls = []
    d = tempfile.mkdtemp()
    pdf = os.path.join(d, "Annual SOP.pdf")
    open(pdf, "wb").write(b"%PDF-1.4 fake")
    txt = os.path.join(d, "notes.txt")
    open(txt, "wb").write(b"x")
    link = "[⤓ Annual_SOP_pages_1-2.pdf (3.0 KB)](/api/files/0f1e2d3c-1111-2222-3333-444455556666)"
    try:
        with mock.patch.object(X, "execute_python", _fake_exec(calls, "PAGES=4\nOUT=Annual_SOP_pages_1-2.pdf\nPAGES_OUT=2", [link])):
            res = _run(X.manipulate_pdf.handler({"operation": "extract", "path": pdf}))
            assert res.get("is_error") and "needs `pages`" in _txt(res)
            res = _run(X.manipulate_pdf.handler({"operation": "rotate", "path": pdf, "degrees": 45}))
            assert res.get("is_error") and "90, 180 or 270" in _txt(res)
            res = _run(X.manipulate_pdf.handler({"operation": "merge", "paths": [pdf]}))
            assert res.get("is_error") and "at least two" in _txt(res)
            res = _run(X.manipulate_pdf.handler({"operation": "extract", "path": txt, "pages": "1"}))
            assert res.get("is_error") and "not a PDF" in _txt(res)
            res = _run(X.manipulate_pdf.handler({"operation": "extract", "path": pdf, "pages": "1-2"}))
            assert not res.get("is_error"), _txt(res)
            assert "extracted 2 page(s) of 4" in _txt(res) and link in _txt(res)
            assert calls[-1]["trusted_files"] == [(pdf, "in_0.pdf")] and calls[-1]["lane"] == "manipulate_pdf"
            assert "sel = [1, 2]" in calls[-1]["code"]
        with mock.patch.object(X, "execute_python", _fake_exec(calls, "PAGES=4\nERROR=page(s) [9] do not exist (the document has 4 pages)", [], ok=False)):
            res = _run(X.manipulate_pdf.handler({"operation": "extract", "path": pdf, "pages": "9"}))
            assert res.get("is_error") and "do not exist" in _txt(res)
        with mock.patch.object(X, "execute_python", _fake_exec(calls, "PAGES=4\nMETA Title=SOP\nENCRYPTED=False\nPAGE_SIZE=612x792pt", [])):
            res = _run(X.manipulate_pdf.handler({"operation": "info", "path": pdf}))
            assert "4 page(s)" in _txt(res) and "Title=SOP" in _txt(res)
        # a non-existent input fails closed before any code runs
        n = len(calls)
        res = _run(X.manipulate_pdf.handler({"operation": "info", "path": os.path.join(d, "missing.pdf")}))
        assert res.get("is_error") and len(calls) == n
    finally:
        CURRENT_USER.reset(tok)


# ---------------------------------------------------------------------------
# Vision: read_file returns an image block
# ---------------------------------------------------------------------------

def test_read_file_returns_an_image_block_for_pictures():
    tok = CURRENT_USER.set({"user_id": 7, "role": 2, "username": "dev"})
    d = tempfile.mkdtemp()
    png = os.path.join(d, "shot.png")
    open(png, "wb").write(PNG_1x1)
    try:
        res = _run(D.read_file.handler({"path": png}))
        assert not res.get("is_error"), res
        items = res["content"]
        assert items[0]["type"] == "image" and items[0]["mimeType"] == "image/png"
        assert base64.b64decode(items[0]["data"]) == PNG_1x1
        assert items[1]["type"] == "text" and "you can SEE it" in items[1]["text"]
        # over the inline cap -> no image block (falls back to extraction)
        with mock.patch.object(D, "_IMAGE_INLINE_MAX_MB", 0.0):
            assert D.image_block(png, "png", len(PNG_1x1)) is None
        assert D.image_block(png, "pdf", 10) is None
    finally:
        CURRENT_USER.reset(tok)


def test_execute_python_stages_trusted_files_and_reports_not_configured():
    # No interpreter -> honest not-configured result, never an exception.
    with mock.patch("code_exec.resolve_interpreter", lambda *a, **k: None):
        r = _run(C.execute_python(7, "print(1)", lane="t"))
        assert r["configured"] is False and r["error"]


if __name__ == "__main__":
    if not HAVE_SDK:
        print(f"SKIP-ALL: {_IMPORT_ERR}")
        sys.exit(0)
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in fns:
        try:
            f()
            print(f"PASS  {n}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {n}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {n}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
