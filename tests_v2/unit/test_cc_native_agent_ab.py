"""
CC_AGENT="native" A/B agent — the native visual-workflow build path.

Covers the three layers added for the A/B agent (2026-07 architecture
assessment, Option B), with the classic path's behavior pinned untouched:

1. graph.build_routing — the deterministic native routing signals
   (looks_like_visual_workflow_build / looks_like_workflow_followup) and a
   regression pin on the CLASSIC signals they sit beside.
2. graph.workflow_tools — deterministic node-graph surgery (slot rule /
   competing-edge hard error / start handling / catalog validation) and the
   save → TRUE-read-back honesty contract (draft verdicts, empty rows, the
   AIHUB-0041 row-mismatch alarm) with HTTP mocked.
3. graph.edges.route_by_intent — the single native gate: build-intent turns
   about a visual workflow go to converse ONLY on native turns; classic
   routing is byte-for-byte unchanged.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_CC = str(_ROOT / "command_center_service")


def _import_cc(module: str):
    saved_path = list(sys.path)
    saved_graph = {k: v for k, v in sys.modules.items() if k == "graph" or k.startswith("graph.")}
    try:
        for k in list(saved_graph):
            del sys.modules[k]
        sys.path.insert(0, _CC)
        mod = __import__(module, fromlist=["_"])
        assert "command_center_service" in mod.__file__.replace("\\", "/")
        return mod
    finally:
        sys.path[:] = saved_path


br = _import_cc("graph.build_routing")
wt = _import_cc("graph.workflow_tools")

try:
    edges = _import_cc("graph.edges")
    cc_state = _import_cc("graph")
    _EDGES_OK = True
except Exception:  # pragma: no cover — langgraph absent in this env
    _EDGES_OK = False


# ─── 1. Routing signals ──────────────────────────────────────────────────

class TestVisualWorkflowBuildSignal:
    def test_plain_workflow_build_matches(self):
        assert br.looks_like_visual_workflow_build(
            "build a workflow that pulls AIRDB sales and emails a summary")

    def test_canvas_phrasing_matches(self):
        assert br.looks_like_visual_workflow_build(
            "create a visual workflow on the canvas for invoice review")

    def test_agent_request_keeps_builder_path(self):
        assert not br.looks_like_visual_workflow_build(
            "build a workflow and a data agent that answers questions about it")

    def test_mcp_request_keeps_builder_path(self):
        assert not br.looks_like_visual_workflow_build(
            "create a workflow and register an MCP server for it")

    def test_connection_creation_keeps_builder_path(self):
        assert not br.looks_like_visual_workflow_build(
            "create a new connection to ERPDB and a workflow that uses it")

    def test_non_workflow_build_does_not_match(self):
        assert not br.looks_like_visual_workflow_build(
            "set up a nightly export that emails me a report")

    def test_referencing_existing_connection_still_matches(self):
        # AIHUB-0033 F1b-R2 discipline: REFERENCING an existing connection by
        # name is not a create-a-connection ask.
        assert br.looks_like_visual_workflow_build(
            "build a workflow using the existing AIRDB connection to count employees")


class TestWorkflowFollowup:
    def test_node_followup(self):
        assert br.looks_like_workflow_followup("add an Alert node after the database step")

    def test_wire_followup(self):
        assert br.looks_like_workflow_followup("wire the fail edge to the alert")

    def test_terse_continue(self):
        assert br.looks_like_workflow_followup("continue")

    def test_unrelated_long_message_does_not_match(self):
        assert not br.looks_like_workflow_followup(
            "what were total sales for the Atlanta region in May compared to April?")


class TestClassicSignalsUnchanged:
    """Pin the CLASSIC deterministic signals the native ones sit beside."""

    def test_code_process_still_matches(self):
        assert br.looks_like_code_process(
            "automate parsing the expense PDFs nightly and upload the CSV via SFTP")

    def test_object_build_still_vetoed(self):
        assert not br.looks_like_code_process("create a data agent for ERPDB")

    def test_code_flow_followup_unchanged(self):
        assert br.looks_like_code_flow_followup("now dry-run it")
        assert not br.looks_like_code_flow_followup(
            "please summarize the quarterly revenue trends for the board meeting deck")


# ─── 2. workflow_tools surgery ───────────────────────────────────────────

def _fresh():
    return {"nodes": [], "connections": [], "variables": {}}


class TestNodeSurgery:
    def test_add_node_canonicalizes_type_and_sets_start(self):
        d = _fresh()
        r = wt.add_node(d, "database", "Pull sales", {"connection": "1", "query": "SELECT 1"})
        assert r["ok"] and r["type"] == "Database"
        assert d["nodes"][0]["isStart"] is True
        assert d["nodes"][0]["position"]["left"].endswith("px")

    def test_add_node_rejects_unknown_type_with_catalog(self):
        d = _fresh()
        r = wt.add_node(d, "SFTP Upload", "up", {})
        assert not r["ok"]
        assert "not a valid node type" in r["error"]
        assert "Code Flow" in r["error"]

    def test_portal_node_stamps_owner(self):
        d = _fresh()
        r = wt.add_node(d, "Portal", "fetch", {}, user_context={"user_id": 13})
        assert r["ok"]
        assert d["nodes"][0]["config"]["ownerUserId"] == "13"

    def test_second_node_not_start(self):
        d = _fresh()
        wt.add_node(d, "Database", "a", {})
        wt.add_node(d, "Alert", "b", {})
        starts = [n for n in d["nodes"] if n["isStart"]]
        assert len(starts) == 1 and starts[0]["label"] == "a"

    def test_remove_node_cascades_edges_and_moves_start(self):
        d = _fresh()
        a = wt.add_node(d, "Database", "a", {})["node_id"]
        b = wt.add_node(d, "Alert", "b", {})["node_id"]
        wt.wire(d, a, b, "pass")
        r = wt.remove_node(d, a)
        assert r["ok"] and r["removed_edges"] == 1
        assert "start moved" in r["note"]
        assert d["nodes"][0]["isStart"] is True

    def test_set_start_exclusive(self):
        d = _fresh()
        a = wt.add_node(d, "Database", "a", {})["node_id"]
        b = wt.add_node(d, "Alert", "b", {})["node_id"]
        wt.set_start(d, b)
        assert [n["id"] for n in d["nodes"] if n["isStart"]] == [b]
        assert not wt.set_start(d, "n_missing")["ok"]


class TestSlotRule:
    def _pair(self):
        d = _fresh()
        a = wt.add_node(d, "Database", "a", {})["node_id"]
        b = wt.add_node(d, "Alert", "b", {})["node_id"]
        c = wt.add_node(d, "File", "c", {})["node_id"]
        return d, a, b, c

    def test_pass_then_second_pass_is_hard_error(self):
        d, a, b, c = self._pair()
        assert wt.wire(d, a, b, "pass")["ok"]
        r = wt.wire(d, a, c, "pass")
        assert not r["ok"] and "unwire" in r["error"]

    def test_complete_competes_with_pass(self):
        d, a, b, c = self._pair()
        assert wt.wire(d, a, b, "pass")["ok"]
        assert not wt.wire(d, a, c, "complete")["ok"]

    def test_fail_edge_allowed_alongside_pass(self):
        d, a, b, c = self._pair()
        assert wt.wire(d, a, b, "pass")["ok"]
        assert wt.wire(d, a, c, "fail")["ok"]
        assert not wt.wire(d, a, b, "fail")["ok"]  # second fail → error

    def test_duplicate_edge_is_noop_note(self):
        d, a, b, _ = self._pair()
        assert wt.wire(d, a, b, "pass")["ok"]
        r = wt.wire(d, a, b, "pass")
        assert r["ok"] and r.get("note") == "edge already exists"
        assert len(d["connections"]) == 1

    def test_unwire_then_rewire(self):
        d, a, b, c = self._pair()
        wt.wire(d, a, b, "pass")
        assert wt.unwire(d, a, b)["ok"]
        assert wt.wire(d, a, c, "pass")["ok"]

    def test_unwire_missing_edge_errors(self):
        d, a, b, _ = self._pair()
        assert not wt.unwire(d, a, b, "fail")["ok"]

    def test_self_edge_rejected(self):
        d, a, _, _ = self._pair()
        assert not wt.wire(d, a, a, "pass")["ok"]


class TestLocalIssues:
    def test_empty_flow_flagged(self):
        assert "no nodes" in " ".join(wt.local_issues(_fresh()))

    def test_no_start_flagged(self):
        d = _fresh()
        wt.add_node(d, "Database", "a", {})
        d["nodes"][0]["isStart"] = False
        assert any("start" in i for i in wt.local_issues(d))

    def test_dangling_edge_flagged(self):
        d = _fresh()
        wt.add_node(d, "Database", "a", {})
        d["connections"].append({"source": "ghost", "target": "phantom", "type": "pass"})
        assert any("missing node" in i for i in wt.local_issues(d))


# ─── 2b. Save → read-back honesty (HTTP mocked) ──────────────────────────

class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _mock_http(monkeypatch, *, save_resp, rows, row_defs):
    """Mock workflow_tools' requests: POST /save/workflow → save_resp;
    GET /get/workflows → rows (double-encoded like the real route);
    GET /get/workflow/<id> → row_defs[id]."""
    import json as _json

    def fake_post(url, json=None, headers=None, timeout=None):
        assert url.endswith("/save/workflow")
        return _Resp(*save_resp)

    def fake_get(url, headers=None, timeout=None):
        if url.endswith("/get/workflows"):
            return _Resp(200, _json.dumps(rows))  # double-encoded, like prod
        wf_id = int(url.rsplit("/", 1)[1])
        if wf_id in row_defs:
            return _Resp(200, row_defs[wf_id])
        return _Resp(404, {"error": "Workflow not found"})

    monkeypatch.setattr(wt.requests, "post", fake_post)
    monkeypatch.setattr(wt.requests, "get", fake_get)
    monkeypatch.setattr(wt, "_base", lambda: "http://mock")
    # cc_config import inside _headers — bypass it entirely.
    monkeypatch.setattr(wt, "_headers", lambda: {"X-API-Key": "test"})


class TestSaveReadbackHonesty:
    def test_clean_save_reports_readback_types(self, monkeypatch):
        d = _fresh()
        wt.add_node(d, "Database", "a", {"connection": "1"})
        _mock_http(
            monkeypatch,
            save_resp=(200, {"workflow_id": 42, "is_valid": True,
                             "saved_as_draft": False, "validation_errors": []}),
            rows=[{"id": 42, "workflow_name": "t1"}],
            row_defs={42: {"nodes": [{"type": "Database", "label": "a"}], "connections": []}},
        )
        res = wt.save_definition("t1", d)
        assert res["ok"] and res["workflow_id"] == 42
        msg = wt.summarize_save("t1", res)
        assert "🧾 Read-back" in msg and "Database" in msg
        assert "passed (runnable)" in msg

    def test_draft_save_reports_draft_and_errors(self, monkeypatch):
        d = _fresh()
        _mock_http(
            monkeypatch,
            save_resp=(200, {"workflow_id": 42, "is_valid": False,
                             "saved_as_draft": True, "validation_errors": ["NO START NODE"]}),
            rows=[{"id": 42, "workflow_name": "t1"}],
            row_defs={42: {"nodes": [], "connections": []}},
        )
        msg = wt.summarize_save("t1", wt.save_definition("t1", d))
        assert "DRAFT" in msg and "NO START NODE" in msg
        assert "EMPTY" in msg  # empty read-back is called out

    def test_row_mismatch_is_alarmed(self, monkeypatch):
        # AIHUB-0041: save vouches id 42, but the NAME resolves to id 99 — the
        # message must scream, never claim verified.
        d = _fresh()
        wt.add_node(d, "Database", "a", {})
        _mock_http(
            monkeypatch,
            save_resp=(200, {"workflow_id": 42, "is_valid": True,
                             "saved_as_draft": False, "validation_errors": []}),
            rows=[{"id": 99, "workflow_name": "t1"}],
            row_defs={99: {"nodes": [], "connections": []}},
        )
        msg = wt.summarize_save("t1", wt.save_definition("t1", d))
        assert "ROW MISMATCH" in msg

    def test_server_refusal_passes_message_verbatim(self, monkeypatch):
        # The AIHUB-0039 kind-guard path: 400 + message → surfaced, ok=False.
        d = _fresh()
        wt.add_node(d, "Database", "a", {})
        _mock_http(
            monkeypatch,
            save_resp=(400, {"status": "error",
                             "message": "Refusing to overwrite 'x': it is a Code Flow"}),
            rows=[], row_defs={},
        )
        res = wt.save_definition("t1", d)
        assert not res["ok"] and "Code Flow" in res["error"]
        assert "❌" in wt.summarize_save("t1", res)

    def test_bad_name_rejected_before_http(self, monkeypatch):
        res = wt.save_definition("../evil", _fresh())
        assert not res["ok"] and "name" in res["error"]

    def test_get_definition_refuses_code_flow_rows(self, monkeypatch):
        _mock_http(
            monkeypatch,
            save_resp=(200, {}),
            rows=[{"id": 7, "workflow_name": "flowy",
                   "workflow_data": '{"kind": "code_flow"}'}],
            row_defs={7: {"kind": "code_flow", "nodes": []}},
        )
        res = wt.get_definition("flowy")
        assert not res["ok"] and "code-flow tools" in res["error"]

    def test_resolve_is_exact_never_fuzzy(self, monkeypatch):
        _mock_http(monkeypatch, save_resp=(200, {}),
                   rows=[{"id": 1, "workflow_name": "sales-export"}], row_defs={})
        assert wt.resolve("sales-export")["ok"]
        assert wt.resolve("SALES-EXPORT")["ok"]  # case-insensitive exact
        assert not wt.resolve("sales")["ok"]     # substring must NOT match


class TestRunHonesty:
    def test_running_never_reads_as_success(self):
        msg = wt.summarize_run({"ok": True,
                                "execution": {"execution_id": "e1", "status": "Running"},
                                "steps": []})
        assert "Running" in msg and "check again" in msg
        assert "success" not in msg.lower()

    def test_failed_step_surfaces_error(self):
        msg = wt.summarize_run({
            "ok": True,
            "execution": {"execution_id": "e1", "status": "Failed"},
            "steps": [{"step_name": "Pull sales", "status": "Failed",
                       "error_message": "No start node defined in the workflow"}],
        })
        assert "✗" in msg and "No start node defined" in msg

    def test_paused_reports_approval_wait(self):
        msg = wt.summarize_run({"ok": True,
                                "execution": {"execution_id": "e1", "status": "Paused",
                                              "current_step": {"waiting_for_approval": True}},
                                "steps": []})
        assert "approval" in msg


# ─── 3. The edges gate (native vs classic routing) ───────────────────────

@pytest.mark.skipif(not _EDGES_OK, reason="graph.edges not importable (langgraph missing)")
class TestRouteByIntentGate:
    def _state(self, text, impl=None, intent="build"):
        msg = types.SimpleNamespace(content=text)
        st = {"intent": intent, "messages": [msg]}
        if impl is not None:
            st["agent_impl"] = impl
        return st

    def test_classic_build_still_routes_to_build_node(self):
        assert edges.route_by_intent(self._state("build a workflow for invoices")) == "build"

    def test_classic_explicit_impl_routes_to_build_node(self):
        assert edges.route_by_intent(
            self._state("build a workflow for invoices", impl="classic")) == "build"

    def test_native_workflow_build_routes_to_converse(self):
        assert edges.route_by_intent(
            self._state("build a workflow for invoices", impl="native")) == "converse"

    def test_native_object_build_keeps_build_node(self):
        assert edges.route_by_intent(
            self._state("create a data agent for ERPDB", impl="native")) == "build"

    def test_native_mixed_request_keeps_build_node(self):
        assert edges.route_by_intent(
            self._state("build an agent and a workflow that calls it", impl="native")) == "build"

    def test_non_build_intents_untouched(self):
        assert edges.route_by_intent(self._state("hello", impl="native", intent="chat")) == "converse"
        assert edges.route_by_intent(self._state("sales?", impl="native", intent="query")) == "gather_data"

    def test_agent_impl_is_declared_state_channel(self):
        # AIHUB-0034 lesson: an undeclared key would be silently dropped by
        # LangGraph and the native gate would never see it.
        assert "agent_impl" in cc_state.CommandCenterState.__annotations__
