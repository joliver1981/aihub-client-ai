"""
AIHUB-0039 R2 — the GENERIC save path refuses to strip kind='code_flow'.

Round-1 live retest: the compile-side guards (ddc78b5) fired but were
NON-FATAL — the builder executor then persisted its materialized visual
definition through POST /save/workflow → app.save_workflow_to_database, an
unguarded MERGE-by-name that overwrote code flow wf 1259 ('store-headcount-v3',
kind='code_flow' + 5 code steps → kind=None + 1 broken node, gone from the
/codeflows registry) while chat said "✅ Updated".

These tests drive the REAL save_workflow_to_database source (AST-extracted from
app.py — the module itself is too heavy to import in unit tests) with a mocked
DB, asserting:
  - a save that would STRIP kind='code_flow' from an existing row raises
    ValueError and never reaches the MERGE (the live destruction signature);
  - the code-flow layer's own saves (incoming kind='code_flow') pass through;
  - normal workflows and brand-new names are unaffected.
"""
from __future__ import annotations

import ast
import json as _json
import logging
import os
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APP_PY = os.path.join(_REPO, "app.py")


def _load_save_fn(conn):
    """AST-extract save_workflow_to_database from app.py and exec it with
    mocked module globals."""
    with open(APP_PY, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    fn_node = next(n for n in tree.body
                   if isinstance(n, ast.FunctionDef) and n.name == "save_workflow_to_database")
    module = ast.Module(body=[fn_node], type_ignores=[])
    ns = {
        "get_db_connection": lambda: conn,
        "json": _json,
        "os": os,
        "logger": logging.getLogger("test"),
        "capture_exception": lambda e: None,
    }
    exec(compile(module, APP_PY, "exec"), ns)  # noqa: S102 — testing real source
    return ns["save_workflow_to_database"]


def _conn(fetchone_results):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchone.side_effect = list(fetchone_results)
    return conn, cursor


VISUAL_DATA = {"nodes": [{"id": "n1", "type": "Excel Export"}], "connections": []}
CODEFLOW_DATA = {"kind": "code_flow", "nodes": [{"id": "s1", "type": "Code Step"}],
                 "connections": [], "definition": {"steps": []}}


class TestKindStripGuard:
    def test_visual_save_over_code_flow_row_refused_before_merge(self):
        # the live destruction signature: existing row kind='code_flow',
        # incoming data kind-less (the builder's materialized visual defn)
        conn, cursor = _conn([(1259, "code_flow")])
        fn = _load_save_fn(conn)

        with pytest.raises(ValueError) as ei:
            fn("store-headcount-v3", dict(VISUAL_DATA))

        msg = str(ei.value)
        assert "Refusing to overwrite" in msg and "Code Flow" in msg
        assert "nothing was changed" in msg and "code-flow tools" in msg
        executed = " ".join(str(c.args[0]) for c in cursor.execute.call_args_list)
        assert "MERGE INTO Workflows" not in executed
        conn.commit.assert_not_called()

    def test_codeflow_layer_save_passes_through(self):
        # incoming data IS a code flow — same-kind overwrite is the code-flow
        # layer's own save; must not be blocked (no kind-check SELECT even needed)
        conn, cursor = _conn([(1259,)])          # id lookup after MERGE
        fn = _load_save_fn(conn)

        wf_id = fn("store-headcount-v3", dict(CODEFLOW_DATA))

        assert wf_id == 1259
        executed = " ".join(str(c.args[0]) for c in cursor.execute.call_args_list)
        assert "MERGE INTO Workflows" in executed
        conn.commit.assert_called_once()

    def test_normal_workflow_save_unaffected(self):
        # existing row is a plain workflow (kind NULL) → kind check passes,
        # then MERGE + id lookup
        conn, cursor = _conn([(1254, None), (1254,)])
        fn = _load_save_fn(conn)

        wf_id = fn("truth-test", dict(VISUAL_DATA))

        assert wf_id == 1254
        executed = " ".join(str(c.args[0]) for c in cursor.execute.call_args_list)
        assert "MERGE INTO Workflows" in executed

    def test_brand_new_name_save_unaffected(self):
        # no existing row → kind check returns None → MERGE inserts
        conn, cursor = _conn([None, (1300,)])
        fn = _load_save_fn(conn)

        wf_id = fn("brand-new-wf", dict(VISUAL_DATA))

        assert wf_id == 1300
        executed = " ".join(str(c.args[0]) for c in cursor.execute.call_args_list)
        assert "MERGE INTO Workflows" in executed
