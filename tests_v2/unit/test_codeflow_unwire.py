"""
AIHUB-0045 — unwire/remove-step exist, and competing same-type edges are a hard
error instead of silent first-match-wins.

Live gap (test pack 09, Scenario C): inserting a step "before the upload" left
BOTH the old direct pass edge and the new route; the walk silently followed the
OLD path (the new step never ran), and with no unwire tool the agent had to
rebuild the whole flow (v2 → v3).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from codeflows import compiler as cfc  # noqa: E402
from codeflows.manager import CodeFlowManager  # noqa: E402


def _defn(edges):
    return {
        "kind": "code_flow", "name": "store-headcount-v2",
        "steps": [
            {"id": "s_csv", "name": "write-csv", "code": "print('csv')"},
            {"id": "s_pdf", "name": "extract-expense-total", "code": "print('pdf')"},
            {"id": "s_up", "name": "upload-sftp", "code": "print('up')"},
        ],
        "edges": edges, "start": "s_csv",
    }


class _Harness:
    def __init__(self, defn):
        self.mgr = CodeFlowManager.__new__(CodeFlowManager)
        self.mgr.tenant_id = "TESTTENANT"
        self.defn = defn
        self.mgr._load_defn = lambda name: (1258, self.defn)
        self.saved = []
        self.mgr._save_definition = lambda d: self.saved.append(d)


class TestDuplicateEdgeValidation:
    def test_competing_pass_edges_are_an_error(self):
        # the live v2 shape: old direct edge + the new route both present
        ok, errors = cfc.validate_definition(_defn([
            {"from": "s_csv", "to": "s_up", "on": "pass"},       # stale
            {"from": "s_csv", "to": "s_pdf", "on": "pass"},      # new
            {"from": "s_pdf", "to": "s_up", "on": "pass"},
        ]))
        assert ok is False
        msg = "; ".join(errors)
        assert "competing 'pass' edges" in msg
        assert "unwire" in msg
        assert "s_csv" in msg

    def test_distinct_edge_types_from_one_step_are_fine(self):
        ok, errors = cfc.validate_definition(_defn([
            {"from": "s_csv", "to": "s_pdf", "on": "pass"},
            {"from": "s_csv", "to": "s_up", "on": "fail"},
        ]))
        assert ok is True, errors

    def test_dry_run_walk_errors_instead_of_silently_following_first(self):
        class _NeverRunner:
            def run_code_step(self, *a, **k):
                raise AssertionError("walk must not execute an ambiguous flow")
        result = cfc.dry_run_walk(_defn([
            {"from": "s_csv", "to": "s_up", "on": "pass"},
            {"from": "s_csv", "to": "s_pdf", "on": "pass"},
            {"from": "s_pdf", "to": "s_up", "on": "pass"},
        ]), _NeverRunner(), base_workdir="X:/nope")
        assert result["status"] == "error"
        assert "competing 'pass' edges" in result["error"]


class TestUnwire:
    def test_unwire_removes_the_stale_edge(self):
        h = _Harness(_defn([
            {"from": "s_csv", "to": "s_up", "on": "pass"},
            {"from": "s_csv", "to": "s_pdf", "on": "pass"},
            {"from": "s_pdf", "to": "s_up", "on": "pass"},
        ]))
        ok, err = h.mgr.unwire("store-headcount-v2", "s_csv", "s_up")
        assert ok is True and err is None
        edges = h.saved[-1]["edges"]
        assert {"from": "s_csv", "to": "s_up", "on": "pass"} not in edges
        assert len(edges) == 2
        # and the definition is now valid
        ok2, errors = cfc.validate_definition(h.saved[-1])
        assert ok2 is True, errors

    def test_unwire_narrowed_by_on(self):
        h = _Harness(_defn([
            {"from": "s_csv", "to": "s_up", "on": "pass"},
            {"from": "s_csv", "to": "s_up", "on": "fail"},
        ]))
        ok, _ = h.mgr.unwire("f", "s_csv", "s_up", on="fail")
        assert ok is True
        edges = h.saved[-1]["edges"]
        assert edges == [{"from": "s_csv", "to": "s_up", "on": "pass"}]

    def test_unwire_nothing_matched_is_an_error_not_a_noop(self):
        h = _Harness(_defn([{"from": "s_csv", "to": "s_pdf", "on": "pass"}]))
        ok, err = h.mgr.unwire("f", "s_csv", "s_up")
        assert ok is False
        assert "no edge" in err and "current edges" in err
        assert h.saved == []

    def test_unwire_bad_on_rejected(self):
        h = _Harness(_defn([]))
        ok, err = h.mgr.unwire("f", "a", "b", on="sometimes")
        assert ok is False and "'on' must be" in err


class TestRemoveStep:
    def test_remove_step_drops_step_and_its_edges(self):
        h = _Harness(_defn([
            {"from": "s_csv", "to": "s_pdf", "on": "pass"},
            {"from": "s_pdf", "to": "s_up", "on": "pass"},
        ]))
        ok, err = h.mgr.remove_step("f", "s_pdf")
        assert ok is True and err is None
        d = h.saved[-1]
        assert [s["id"] for s in d["steps"]] == ["s_csv", "s_up"]
        assert d["edges"] == []                     # both touching edges gone

    def test_remove_start_step_moves_start(self):
        h = _Harness(_defn([{"from": "s_csv", "to": "s_pdf", "on": "pass"}]))
        ok, _ = h.mgr.remove_step("f", "s_csv")
        assert ok is True
        assert h.saved[-1]["start"] == "s_pdf"

    def test_remove_unknown_step_errors(self):
        h = _Harness(_defn([]))
        ok, err = h.mgr.remove_step("f", "s_nope")
        assert ok is False and "not found" in err
        assert h.saved == []
