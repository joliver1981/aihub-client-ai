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
3. Routing intelligence (mini-LLM upgrade, james's directive): the build-shape
   classifier gains a native-only 'visual_workflow' label (classic prompt and
   parse byte-identical), _native_workflow_shape_divert makes the decision
   with the regex demoted to a fast-path, and workflow-follow-up continuity
   consults a mini-LLM when the deterministic cues miss. route_by_intent is a
   pure map again — no regex gate at the edge.
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


# ─── 3. Routing intelligence: shape classifier, divert helper, continuity ─

import importlib


def _live(module: str):
    """Import (or fetch) a module under the CC package path so monkeypatches
    hit the SAME instance graph.nodes resolves at call time."""
    saved = list(sys.path)
    try:
        sys.path.insert(0, _CC)
        return importlib.import_module(module)
    finally:
        sys.path[:] = saved


try:
    nodes = _import_cc("graph.nodes")   # import LAST: later graph.* lazy imports resolve live
    cc_cfg = _live("cc_config")
    edges_live = _live("graph.edges")
    state_pkg = _live("graph")
    from langchain_core.messages import HumanMessage
    _NODES_OK = True
except Exception:  # pragma: no cover — langchain/langgraph missing in this env
    _NODES_OK = False


class _StubLLM:
    """Captures ainvoke calls and returns a canned reply (or raises)."""

    def __init__(self, reply="", raise_exc=False):
        self.reply = reply
        self.raise_exc = raise_exc
        self.calls = []

    async def ainvoke(self, msgs):
        self.calls.append(msgs)
        if self.raise_exc:
            raise RuntimeError("stub LLM failure")
        return types.SimpleNamespace(content=self.reply)


def _st(text="build something", native=True, marker=None):
    s = {
        "messages": [HumanMessage(content=text)],
        "session_id": "t-native-routing",
        "user_context": {"user_id": 1, "role": 3, "tenant_id": 1, "username": "admin"},
    }
    if native:
        s["agent_impl"] = "native"
    if marker is not None:
        s["code_flow_context"] = marker
    return s


pytestmark_nodes = pytest.mark.skipif(not _NODES_OK, reason="graph.nodes not importable here")


@pytest.mark.skipif(not _NODES_OK, reason="graph.nodes not importable here")
class TestBuildShapeNativeExtension:
    async def test_native_prompt_carries_label_and_parses_it(self, monkeypatch):
        stub = _StubLLM("visual_workflow")
        monkeypatch.setattr(cc_cfg, "get_llm", lambda mini=False, streaming=True: stub)
        shape = await nodes._classify_build_shape(
            "please set up the quarterly reporting flow for me", _st(native=True))
        assert shape == "visual_workflow"
        assert "visual_workflow" in stub.calls[0][0].content

    async def test_native_tolerates_space_variant(self, monkeypatch):
        stub = _StubLLM("visual workflow")
        monkeypatch.setattr(cc_cfg, "get_llm", lambda mini=False, streaming=True: stub)
        assert await nodes._classify_build_shape(
            "make that editable thing we discussed", _st(native=True)) == "visual_workflow"

    async def test_classic_prompt_and_parse_are_byte_identical(self, monkeypatch):
        # The classic prompt must not contain the new label, and even a rogue
        # 'visual_workflow' reply must parse exactly as before (→ neither).
        stub = _StubLLM("visual_workflow")
        monkeypatch.setattr(cc_cfg, "get_llm", lambda mini=False, streaming=True: stub)
        shape = await nodes._classify_build_shape(
            "please set up the quarterly reporting flow for me", _st(native=False))
        assert shape == "neither"
        assert stub.calls[0][0].content == nodes._BUILD_SHAPE_PROMPT
        assert "visual_workflow" not in nodes._BUILD_SHAPE_PROMPT

    async def test_classic_normal_labels_unchanged(self, monkeypatch):
        stub = _StubLLM("builder")
        monkeypatch.setattr(cc_cfg, "get_llm", lambda mini=False, streaming=True: stub)
        assert await nodes._classify_build_shape(
            "create a data agent", _st(native=False)) == "builder"

    async def test_code_process_fast_path_skips_llm(self, monkeypatch):
        stub = _StubLLM("visual_workflow")
        monkeypatch.setattr(cc_cfg, "get_llm", lambda mini=False, streaming=True: stub)
        shape = await nodes._classify_build_shape(
            "automate parsing the expense PDFs nightly and upload the CSV via SFTP",
            _st(native=True))
        assert shape == "automation"
        assert stub.calls == []


@pytest.mark.skipif(not _NODES_OK, reason="graph.nodes not importable here")
class TestNativeWorkflowShapeDivert:
    def _shape_stub(self, monkeypatch, result):
        calls = []

        async def fake_shape(user_text, state):
            calls.append(user_text)
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setattr(nodes, "_classify_build_shape", fake_shape)
        return calls

    async def test_classic_turn_is_free_and_false(self, monkeypatch):
        calls = self._shape_stub(monkeypatch, "visual_workflow")
        assert await nodes._native_workflow_shape_divert(
            "build a workflow for invoices", _st(native=False)) is False
        assert calls == []  # zero LLM cost on classic turns

    async def test_regex_fast_path_skips_llm(self, monkeypatch):
        calls = self._shape_stub(monkeypatch, "builder")
        assert await nodes._native_workflow_shape_divert(
            "build a workflow on the canvas for invoice review", _st(native=True)) is True
        assert calls == []  # deterministic accelerator, no shape call

    async def test_llm_decides_on_regex_miss(self, monkeypatch):
        calls = self._shape_stub(monkeypatch, "visual_workflow")
        assert await nodes._native_workflow_shape_divert(
            "set up something editable that queries AIRDB then emails Bob",
            _st(native=True)) is True
        assert len(calls) == 1

    async def test_builder_shape_keeps_builder_path(self, monkeypatch):
        self._shape_stub(monkeypatch, "builder")
        assert await nodes._native_workflow_shape_divert(
            "set up a new MCP server for the finance data",
            _st(native=True)) is False

    async def test_fail_open_on_error(self, monkeypatch):
        self._shape_stub(monkeypatch, RuntimeError("boom"))
        assert await nodes._native_workflow_shape_divert(
            "set up something editable that queries AIRDB then emails Bob",
            _st(native=True)) is False


@pytest.mark.skipif(not _NODES_OK, reason="graph.nodes not importable here")
class TestWorkflowContinuityMiniLLM:
    MARKER = {"name": "wf-x", "kind": "visual_workflow"}
    # deliberately misses every deterministic cue in _WORKFLOW_FOLLOWUP
    REGEX_MISS = "make the second one feed into the export instead"

    def _step_stub(self, monkeypatch, reply="YES", raise_exc=False):
        stub = _StubLLM(reply, raise_exc=raise_exc)
        monkeypatch.setattr(cc_cfg, "get_step_llm", lambda step, **kw: stub)
        return stub

    async def test_yes_means_followup(self, monkeypatch):
        self._step_stub(monkeypatch, "YES")
        assert await nodes._is_workflow_followup_llm(
            self.REGEX_MISS, self.MARKER, _st()) is True

    async def test_no_and_error_fail_open(self, monkeypatch):
        self._step_stub(monkeypatch, "NO")
        assert await nodes._is_workflow_followup_llm(
            self.REGEX_MISS, self.MARKER, _st()) is False
        self._step_stub(monkeypatch, raise_exc=True)
        assert await nodes._is_workflow_followup_llm(
            self.REGEX_MISS, self.MARKER, _st()) is False

    async def test_classify_intent_stays_native_on_llm_yes(self, monkeypatch):
        # Deterministic cues miss; the mini-LLM says YES → the turn stays in
        # converse on the same workflow, marker preserved.
        assert not br.looks_like_workflow_followup(self.REGEX_MISS)
        self._step_stub(monkeypatch, "YES")
        out = await nodes.classify_intent(
            _st(self.REGEX_MISS, native=True, marker=self.MARKER))
        assert out["intent"] == "chat"
        assert out["code_flow_context"] == self.MARKER


@pytest.mark.skipif(not _NODES_OK, reason="graph.nodes not importable here")
class TestSmartBuildRoutingShapeMapping:
    """Drive the REAL classify_intent to the final smart-build-routing block
    with the router/route-memory off and the classifier stubbed to 'build'."""

    def _wire(self, monkeypatch, shape):
        ls = _live("command_center.orchestration.landscape_scanner")

        async def fake_scan(user_context=None):
            return {"agents": [], "data_agents": [], "connections": []}

        monkeypatch.setattr(ls, "scan_platform", fake_scan)
        monkeypatch.setattr(ls, "format_landscape_summary", lambda *a, **k: "")
        monkeypatch.setattr(cc_cfg, "USE_ROUTE_MEMORY", False)
        monkeypatch.setattr(cc_cfg, "USE_CAPABILITY_ROUTER", False)
        monkeypatch.setattr(cc_cfg, "USE_INTENT_HEURISTICS", False)
        monkeypatch.setattr(cc_cfg, "get_step_llm",
                            lambda step, **kw: _StubLLM("build"))

        async def fake_shape(user_text, state):
            return shape

        monkeypatch.setattr(nodes, "_classify_build_shape", fake_shape)

    async def test_visual_workflow_shape_routes_to_chat(self, monkeypatch):
        self._wire(monkeypatch, "visual_workflow")
        out = await nodes.classify_intent(_st("please make me that editable thing"))
        assert out["intent"] == "chat"

    async def test_builder_shape_still_routes_to_build(self, monkeypatch):
        self._wire(monkeypatch, "builder")
        out = await nodes.classify_intent(_st("please make me that editable thing"))
        assert out["intent"] == "build"


@pytest.mark.skipif(not _NODES_OK, reason="graph.nodes not importable here")
class TestEdgeIsPureMapAgain:
    def test_no_regex_gate_at_the_edge(self):
        # The decision moved into classify_intent (LLM); the edge must not
        # overrule it. A build intent routes to the build node regardless of
        # impl or message text.
        st = {"intent": "build", "agent_impl": "native",
              "messages": [types.SimpleNamespace(content="build a workflow for invoices")]}
        assert edges_live.route_by_intent(st) == "build"

    def test_agent_impl_is_declared_state_channel(self):
        # AIHUB-0034 lesson: an undeclared key would be silently dropped by
        # LangGraph and every native seam would go dark.
        assert "agent_impl" in state_pkg.CommandCenterState.__annotations__
