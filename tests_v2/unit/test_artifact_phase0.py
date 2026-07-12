"""Unit tests — artifact-sharing Phase 0 (docs/agent-artifact-sharing-plan.md).

Covers:
  * sql_row_cap.read_sql_query_row_capped / fetch_rows_capped — the OOM guard
    on legacy SQL reads (behavioral, hermetic — no DB, no AppUtils import).
  * The SMART_RENDER_HYBRID_MAX_TABLE_DISPLAY_ROWS config-name bug fix
    (source-level: the bare-name getattr is gone, the real name is read).
  * Auth decorators on /download and both /document/serve routes (AST-level,
    so we don't have to import the full Flask app / live DB).
  * CC converse: delegated table rows inlined to the LLM are capped.

NOTE: repo .gitignore ignores test*.py — commit with `git add -f`.
"""
import ast
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import sql_row_cap


# ── helpers ──────────────────────────────────────────────────────────────

def _df(n, start=0):
    return pd.DataFrame({"id": range(start, start + n), "val": ["x"] * n})


def _fake_read_sql(total_rows, chunk=None):
    """Stand-in for pd.read_sql_query honoring the chunksize kwarg."""
    def fake(query, conn, chunksize=None):
        if chunksize is None:
            return _df(total_rows)
        def gen():
            emitted = 0
            while emitted < total_rows:
                n = min(chunksize, total_rows - emitted)
                yield _df(n, start=emitted)
                emitted += n
        return gen()
    return fake


class _FakeCursor:
    def __init__(self, total_rows):
        self._rows = [(i, "x") for i in range(total_rows)]
        self._pos = 0
        self.description = [("id",), ("val",)]

    def fetchmany(self, n):
        batch = self._rows[self._pos:self._pos + n]
        self._pos += len(batch)
        return batch

    def fetchall(self):
        batch = self._rows[self._pos:]
        self._pos = len(self._rows)
        return batch


# ── read_sql_query_row_capped ────────────────────────────────────────────

def test_read_under_cap_not_capped(monkeypatch):
    monkeypatch.setattr(sql_row_cap.pd, "read_sql_query", _fake_read_sql(120))
    df, capped = sql_row_cap.read_sql_query_row_capped("SELECT 1", None, cap=1000)
    assert len(df) == 120 and capped is False


def test_read_over_cap_truncates(monkeypatch):
    monkeypatch.setattr(sql_row_cap.pd, "read_sql_query", _fake_read_sql(250_000))
    df, capped = sql_row_cap.read_sql_query_row_capped("SELECT 1", None, cap=100_000)
    assert len(df) == 100_000 and capped is True
    assert df.attrs.get("row_cap_applied") == 100_000
    # rows kept must be the FIRST cap rows, contiguous
    assert df["id"].iloc[0] == 0 and df["id"].iloc[-1] == 99_999


def test_read_exactly_cap_not_flagged(monkeypatch):
    monkeypatch.setattr(sql_row_cap.pd, "read_sql_query", _fake_read_sql(1000))
    df, capped = sql_row_cap.read_sql_query_row_capped("SELECT 1", None, cap=1000)
    assert len(df) == 1000 and capped is False


def test_read_zero_rows_keeps_columns(monkeypatch):
    monkeypatch.setattr(sql_row_cap.pd, "read_sql_query", _fake_read_sql(0))
    df, capped = sql_row_cap.read_sql_query_row_capped("SELECT 1", None, cap=1000)
    assert len(df) == 0 and list(df.columns) == ["id", "val"] and capped is False


def test_read_cap_disabled(monkeypatch):
    monkeypatch.setattr(sql_row_cap.pd, "read_sql_query", _fake_read_sql(500))
    df, capped = sql_row_cap.read_sql_query_row_capped("SELECT 1", None, cap=0)
    assert len(df) == 500 and capped is False


def test_read_default_cap_comes_from_config(monkeypatch):
    import config as cfg
    monkeypatch.setattr(cfg, "SQL_QUERY_ROW_SAFETY_CAP", 50, raising=False)
    monkeypatch.setattr(sql_row_cap.pd, "read_sql_query", _fake_read_sql(200))
    df, capped = sql_row_cap.read_sql_query_row_capped("SELECT 1", None)
    assert len(df) == 50 and capped is True


# ── fetch_rows_capped ────────────────────────────────────────────────────

def test_fetch_under_cap():
    rows, capped = sql_row_cap.fetch_rows_capped(_FakeCursor(120), cap=1000)
    assert len(rows) == 120 and capped is False


def test_fetch_over_cap():
    rows, capped = sql_row_cap.fetch_rows_capped(_FakeCursor(5000), cap=1000, batch_size=300)
    assert len(rows) == 1000 and capped is True


def test_fetch_exactly_cap_not_flagged():
    rows, capped = sql_row_cap.fetch_rows_capped(_FakeCursor(1000), cap=1000, batch_size=250)
    assert len(rows) == 1000 and capped is False


def test_fetch_cap_disabled():
    rows, capped = sql_row_cap.fetch_rows_capped(_FakeCursor(300), cap=0)
    assert len(rows) == 300 and capped is False


# ── config values exist and are sane ─────────────────────────────────────

def test_config_exposes_caps():
    import config as cfg
    assert isinstance(cfg.SQL_QUERY_ROW_SAFETY_CAP, int)
    assert isinstance(cfg.SMART_RENDER_HYBRID_MAX_TABLE_DISPLAY_ROWS, int)
    assert cfg.SMART_RENDER_HYBRID_MAX_TABLE_DISPLAY_ROWS >= 1000


# ── the config-name bug fix (source-level: no heavy imports) ─────────────

def test_renderer_reads_prefixed_config_name():
    src = (REPO_ROOT / "SmartContentRenderer_hybrid.py").read_text(encoding="utf-8")
    # The bug: getattr(cfg, 'MAX_TABLE_DISPLAY_ROWS', 1000) — a name that does
    # not exist on the config module, silently capping every table at 1000.
    assert "getattr(cfg, 'MAX_TABLE_DISPLAY_ROWS'" not in src
    assert "SMART_RENDER_HYBRID_MAX_TABLE_DISPLAY_ROWS" in src


# ── auth decorators on the exposed download routes (AST-level) ───────────

def _decorators_of(funcname, tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == funcname:
            names = []
            for dec in node.decorator_list:
                if isinstance(dec, ast.Name):
                    names.append(dec.id)
                elif isinstance(dec, ast.Attribute):
                    names.append(dec.attr)
                elif isinstance(dec, ast.Call):
                    f = dec.func
                    names.append(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))
            return names
    raise AssertionError(f"function {funcname} not found in app.py")


@pytest.fixture(scope="module")
def app_tree():
    return ast.parse((REPO_ROOT / "app.py").read_text(encoding="utf-8"))


@pytest.mark.parametrize("route_fn", ["download_file", "serve_document", "serve_document_2"])
def test_download_routes_require_login(app_tree, route_fn):
    assert "login_required" in _decorators_of(route_fn, app_tree), (
        f"{route_fn} must carry @login_required — it serves files and was "
        f"shipped unauthenticated (see docs/agent-artifact-sharing-plan.md Phase 0)")


def test_download_file_validates_file_id():
    src = (REPO_ROOT / "app.py").read_text(encoding="utf-8")
    i = src.index("def download_file(")
    body = src[i:i + 1200]
    assert "fullmatch" in body, "download_file must validate file_id as a uuid (path-traversal guard)"


# ── CC converse: delegated table rows inlined to the LLM are capped ──────

def test_cc_converse_table_inlining_is_capped():
    src = (REPO_ROOT / "command_center_service" / "graph" / "nodes.py").read_text(encoding="utf-8")
    assert "_DELEGATED_TABLE_LLM_ROW_CAP" in src
    # the cap must actually slice the delegated table rows
    assert "table_data[:_DELEGATED_TABLE_LLM_ROW_CAP]" in src
    assert "table preview: showing first" in src
