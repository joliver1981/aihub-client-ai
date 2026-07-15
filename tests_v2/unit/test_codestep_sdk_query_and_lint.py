"""
AIHUB-0037 — reliable data-step primitives:
  * aihub.query('CONN', sql, params) SDK helper (so the agent doesn't hand-roll
    pyodbc/SQLAlchemy) — tested with a fake pyodbc module.
  * compiler.lint_input_names — a step whose code reads aihub.input('X') for an
    undeclared X is rejected at author time (the src_csv-vs-parsed_csv bug).
"""
from __future__ import annotations

import os
import sys
import types

import pytest

pytestmark = pytest.mark.unit

# import the real SDK module (automations/sdk/aihub_runtime)
_SDK = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "automations", "sdk"))
if _SDK not in sys.path:
    sys.path.insert(0, _SDK)
import aihub_runtime as aihub  # noqa: E402


# --------------------------------------------------------------- aihub.query

class _FakeCursor:
    def __init__(self, rows, cols):
        self._rows = rows
        self.description = [(c,) for c in cols] if cols is not None else None
        self.executed = None
    def execute(self, sql, params=None):
        self.executed = (sql, params)
    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows, cols):
        self.cur = _FakeCursor(rows, cols)
        self.committed = False
        self.closed = False
    def cursor(self):
        return self.cur
    def commit(self):
        self.committed = True
    def close(self):
        self.closed = True


def _fake_pyodbc(monkeypatch, conn):
    mod = types.ModuleType("pyodbc")
    mod.connect = lambda cs: conn
    monkeypatch.setitem(sys.modules, "pyodbc", mod)


class TestAihubQuery:
    def test_select_returns_list_of_dicts(self, monkeypatch):
        monkeypatch.setattr(aihub, "connection", lambda n: "Driver=X;Server=s;")
        conn = _FakeConn(rows=[(1, 834.60), (2, 1140.44)], cols=["emp_id", "amount"])
        _fake_pyodbc(monkeypatch, conn)
        rows = aihub.query("AIRDB", "SELECT emp_id, amount FROM x WHERE d = ?", ["Sales"])
        assert rows == [{"emp_id": 1, "amount": 834.60}, {"emp_id": 2, "amount": 1140.44}]
        assert conn.cur.executed == ("SELECT emp_id, amount FROM x WHERE d = ?", ["Sales"])
        assert conn.closed is True

    def test_non_select_commits_and_returns_empty(self, monkeypatch):
        monkeypatch.setattr(aihub, "connection", lambda n: "Driver=X;")
        conn = _FakeConn(rows=[], cols=None)   # description None => non-SELECT
        _fake_pyodbc(monkeypatch, conn)
        out = aihub.query("AIRDB", "UPDATE x SET y=1")
        assert out == [] and conn.committed is True

    def test_resolves_connection_by_name_enforcing_allowlist(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(aihub, "connection", lambda n: seen.setdefault("name", n) or "Driver=X;")
        _fake_pyodbc(monkeypatch, _FakeConn(rows=[], cols=["a"]))
        aihub.query("AIRDB", "SELECT 1 a")
        assert seen["name"] == "AIRDB"   # goes through connection() (which enforces the manifest)

    def test_missing_pyodbc_is_a_clear_error(self, monkeypatch):
        monkeypatch.setattr(aihub, "connection", lambda n: "Driver=X;")
        monkeypatch.setitem(sys.modules, "pyodbc", None)   # -> `import pyodbc` raises ImportError
        with pytest.raises(aihub.AutomationRuntimeError) as ei:
            aihub.query("AIRDB", "SELECT 1")
        assert "pyodbc" in str(ei.value)


# --------------------------------------------------- compiler.lint_input_names

from codeflows import compiler  # noqa: E402


class TestInputNameLint:
    def test_undeclared_input_is_rejected(self):
        # the AIHUB-0037 bug: code reads 'src_csv', step declares 'parsed_csv'
        err = compiler.lint_input_names(
            "import aihub_runtime as aihub\nsrc = aihub.input('src_csv')\n",
            [{"name": "parsed_csv", "type": "string"}])
        assert err and "src_csv" in err and "parsed_csv" in err

    def test_matching_input_is_ok(self):
        assert compiler.lint_input_names(
            "src = aihub.input('parsed_csv')\n",
            [{"name": "parsed_csv"}]) is None

    def test_no_input_reads_is_ok(self):
        assert compiler.lint_input_names("print('hi')\n", []) is None

    def test_multiple_and_reports_only_missing(self):
        err = compiler.lint_input_names(
            "a = aihub.input('good')\nb = aihub.input('bad')\n",
            [{"name": "good"}])
        assert err and "bad" in err and "good" not in err.split("undeclared input(s) via aihub.input():")[1].split("—")[0]


# ------------------------------------------- lint wired into the manager CRUD

from tests_v2.unit.test_code_flows_manager import _MemManager  # noqa: E402


def test_add_step_rejects_input_name_mismatch():
    mgr = _MemManager()
    mgr.create_code_flow("flow")
    ok, _sid, err = mgr.add_step(
        "flow", "reconcile",
        "import aihub_runtime as aihub\nsrc = aihub.input('src_csv')\n",
        inputs=[{"name": "parsed_csv", "type": "string", "default": "${s1_files[0]}"}])
    assert not ok and "src_csv" in err


def test_update_step_code_rejects_input_name_mismatch():
    mgr = _MemManager()
    mgr.create_code_flow("flow")
    ok, sid, _ = mgr.add_step("flow", "s", "print(1)",
                              inputs=[{"name": "parsed_csv", "type": "string"}])
    assert ok
    ok2, err = mgr.update_step_code("flow", sid,
                                    "import aihub_runtime as aihub\nx = aihub.input('wrong_name')\n")
    assert not ok2 and "wrong_name" in err
