"""
AIHUB-0048 — native-agent F1 (fabricated no-tool edit) and F2 (insert-between
left the graph edge-less) guards.

Live findings (0048 A/B retest):
  F1 blocker: with ZERO tool calls, the native agent replied "✅ Inserted Set
     Variable node … Current persisted structure: […]" — DB proved the row
     unchanged; the AQG passed the narration at 0.98.
  F2 major: insert-between = add + unwire + wire + wire across turns; the
     legitimate second wire (identical args retried AFTER the unwire fixed its
     precondition) was suppressed by the verbatim-repeat dedup, persisting
     slot-repro-wf with edges=[].

Guards under test:
  - _ToolRepeatGuard: progress-aware — repeats short-circuit ONLY with no
    intervening execution; a retry after other calls runs for real.
  - workflow_tools.insert_between: ATOMIC in-memory insert with full rollback.
  - _claims_completed_mutation: detects just-completed-mutation claims so the
    no-tool fabrication footer can fire.
  - Source contracts: tool dual-registration, prompt teaching, output pins.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_CC = str(_ROOT / "command_center_service")


def _import_cc_nodes():
    saved_path = list(sys.path)
    saved_graph = {k: v for k, v in sys.modules.items() if k == "graph" or k.startswith("graph.")}
    try:
        for k in list(saved_graph):
            del sys.modules[k]
        sys.path.insert(0, _CC)
        import graph.nodes as cc_nodes  # noqa: PLC0415
        assert "command_center_service" in cc_nodes.__file__.replace("\\", "/")
        return cc_nodes
    finally:
        sys.path[:] = saved_path


try:
    nodes = _import_cc_nodes()
except Exception as e:  # pragma: no cover
    pytest.skip(f"CC graph.nodes not importable here: {e}", allow_module_level=True)


def _wt():
    path = os.path.join(_CC, "graph", "workflow_tools.py")
    spec = importlib.util.spec_from_file_location("_wt_0048", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class TestToolRepeatGuard:
    def test_no_progress_repeat_short_circuits(self):
        g = nodes._ToolRepeatGuard()
        g.record("wire", {"a": 1}, "err: competing edge")
        assert g.cached_if_no_progress("wire", {"a": 1}) == "err: competing edge"

    def test_retry_after_intervening_call_allowed(self):
        """The F2 live sequence: wire fails → unwire runs → wire retried."""
        g = nodes._ToolRepeatGuard()
        g.record("wire_workflow_nodes", {"from": "Q", "to": "MID"}, "error: competing edge")
        g.record("unwire_workflow_nodes", {"from": "Q", "to": "A"}, "ok")
        assert g.cached_if_no_progress("wire_workflow_nodes", {"from": "Q", "to": "MID"}) is None

    def test_repeat_spam_without_progress_still_blocked(self):
        """The original AIHUB-0028 case: same call over and over, nothing between."""
        g = nodes._ToolRepeatGuard()
        g.record("create_automation", {"n": "x"}, "transient error")
        assert g.cached_if_no_progress("create_automation", {"n": "x"}) is not None
        # a blocked repeat does NOT record → still blocked next round
        assert g.cached_if_no_progress("create_automation", {"n": "x"}) is not None

    def test_unseen_call_never_short_circuits(self):
        g = nodes._ToolRepeatGuard()
        assert g.cached_if_no_progress("anything", {"x": 1}) is None

    def test_rerecorded_retry_updates_cache(self):
        g = nodes._ToolRepeatGuard()
        g.record("wire", {"a": 1}, "fail")
        g.record("other", {}, "ok")
        assert g.cached_if_no_progress("wire", {"a": 1}) is None   # allowed
        g.record("wire", {"a": 1}, "ok now")
        assert g.cached_if_no_progress("wire", {"a": 1}) == "ok now"


class TestClaimsCompletedMutation:
    @pytest.mark.parametrize("text", [
        "✅ Inserted Set Variable node between Database and Excel Export.",
        "Done! ✅ Removed the obsolete Database→Excel Export connection.",
        "Current persisted structure: [Database, Set Variable, Excel Export, Alert]",
        "I've added the node and rewired the connection for you.",
        "I've now updated the Alert node config in the workflow.",
    ])
    def test_fabrication_shapes_detected(self, text):
        assert nodes._claims_completed_mutation(text) is True

    @pytest.mark.parametrize("text", [
        "I'll add a Set Variable node next — shall I proceed?",
        "Do you want me to remove that connection?",
        "The workflow has 3 nodes: Database, Excel Export, Alert.",
        "Earlier today the workflow was created and it runs fine. ✅ All good.",
        "Here's the plan: add the node, then rewire. Confirm to proceed.",
    ])
    def test_benign_shapes_ignored(self, text):
        assert nodes._claims_completed_mutation(text) is False


def _defn():
    return {
        "nodes": [
            {"id": "Q", "type": "Database", "label": "Q", "isStart": True,
             "position": {}, "config": {}},
            {"id": "A", "type": "Alert", "label": "A", "isStart": False,
             "position": {}, "config": {}},
        ],
        "connections": [
            {"source": "Q", "target": "A", "type": "pass",
             "sourceAnchor": "Right", "targetAnchor": "Left"},
        ],
    }


class TestInsertBetweenAtomic:
    def test_happy_path_rewires_atomically(self):
        wt = _wt()
        d = _defn()
        r = wt.insert_between(d, "Set Variable", "MID", {}, "Q", "A")
        assert r["ok"] is True
        mid = r["node_id"]
        assert len(d["nodes"]) == 3
        edges = {(c["source"], c["target"], c["type"]) for c in d["connections"]}
        assert ("Q", mid, "pass") in edges
        assert (mid, "A", "pass") in edges
        assert ("Q", "A", "pass") not in edges          # original edge replaced

    def test_missing_edge_is_error_and_untouched(self):
        wt = _wt()
        d = _defn()
        d["connections"] = []                            # the live slot-repro end-state
        before = repr(d)
        r = wt.insert_between(d, "Set Variable", "MID", {}, "Q", "A")
        assert r["ok"] is False and "no edge" in r["error"]
        assert repr(d) == before

    def test_bad_node_type_rolls_back_fully(self):
        wt = _wt()
        d = _defn()
        before = repr(d)
        r = wt.insert_between(d, "SFTP Upload", "MID", {}, "Q", "A")
        assert r["ok"] is False
        assert repr(d) == before                         # byte-identical rollback

    def test_edge_type_carries_over(self):
        wt = _wt()
        d = _defn()
        d["connections"][0]["type"] = "complete"
        r = wt.insert_between(d, "Set Variable", "MID", {}, "Q", "A")
        assert r["ok"] is True
        mid = r["node_id"]
        edges = {(c["source"], c["target"], c["type"]) for c in d["connections"]}
        assert ("Q", mid, "complete") in edges           # original type preserved
        assert (mid, "A", "pass") in edges


class TestProgressAwareRoundCap:
    """AIHUB-0050 F1 — the flat _MAX_TOOL_ROUNDS=6 truncated any one-turn build
    needing >6 sequential tool calls (standard branching build = 8: create +
    4 nodes + 3 edges), leaving honest-but-incomplete DRAFTs that needed a user
    nudge to finish. The cap is now progress-aware: rounds that execute at
    least one live (non-short-circuited) tool call never exhaust the budget;
    only consecutive all-cached rounds do (the 0028 spin), with a generous
    absolute backstop bounding true runaways."""

    def _src(self):
        return (Path(_CC) / "graph" / "nodes.py").read_text(encoding="utf-8")

    def _loop(self):
        src = self._src()
        return src[src.find("while _has_tc and _round <"):src.find("# ── Output sanitizer")]

    def test_flat_cap_gone_and_bounds_sized_for_real_builds(self):
        import re
        src = self._src()
        assert "_MAX_TOOL_ROUNDS = 6" not in src, "flat 6-round cap is back — 0050 F1 regressed"
        stall = int(re.search(r"_MAX_STALLED_ROUNDS = (\d+)", src).group(1))
        abs_cap = int(re.search(r"_MAX_TOOL_ROUNDS_ABS = (\d+)", src).group(1))
        # backstop must fit a branching (8 calls) AND a large (~20-node) build,
        # while the stall budget stays a tight anti-spin bound
        assert abs_cap >= 16, f"absolute backstop {abs_cap} would truncate large builds"
        assert 2 <= stall <= 4, f"stall budget {stall} defeats the anti-runaway purpose"

    def test_loop_and_reinvoke_gate_use_both_bounds(self):
        src = self._src()
        assert ("while _has_tc and _round < _MAX_TOOL_ROUNDS_ABS "
                "and _stalled_rounds < _MAX_STALLED_ROUNDS:") in src
        # the tools-bound re-invoke must mirror the while bounds exactly, so we
        # never solicit tool calls we won't execute
        gate = src[src.find("if _round < _MAX_TOOL_ROUNDS_ABS and "
                            "_stalled_rounds < _MAX_STALLED_ROUNDS:"):]
        assert "llm_with_tools.ainvoke(_convo)" in gate[:200]

    def test_stall_bookkeeping_is_progress_aware(self):
        loop = self._loop()
        # reset-or-increment on round progress; live counter zeroed each round
        assert "_stalled_rounds = 0 if _live_this_round else _stalled_rounds + 1" in loop
        assert "_live_this_round = 0" in loop
        # a live execution (the path that records into the repeat guard) counts
        # as progress; the cached short-circuit path must NOT
        rec = loop[loop.find("_repeat_guard.record(tool_name, tool_args, rn)"):]
        assert "_live_this_round += 1" in rec[:120]
        cached_branch = loop[loop.find("if _cached is not None:"):loop.find("continue")]
        assert "_live_this_round += 1" not in cached_branch

    def test_capped_wrapup_states_honest_reason(self):
        loop = self._loop()
        assert "absolute tool-round backstop" in loop
        assert "consecutive no-progress rounds" in loop
        # the honest-nudge tool-less wrap-up survives (0028 confabulation guard)
        assert "llm.ainvoke(_convo + [_honest_nudge])" in loop


class TestWorkflowNamePolicy:
    """AIHUB-0052 F1 — name hygiene. The charset regex allowed reserved Windows
    device names ('con' → mirror write failed AFTER the DB save, orphaning row
    1295) and .json-suffixed names ('report.json.json' → stray double-extension
    file). Policy now lives at BOTH layers: workflow_tools.name_error (instant
    honest agent refusal) and app.py _workflow_mirror_filename_error (the
    /save/workflow chokepoint every caller converges on — which previously had
    NO filename validation at all, so traversal was only client-blocked)."""

    @pytest.mark.parametrize("bad", [
        "con", "CON", "Con.json", "prn", "aux", "nul", "com3", "LPT9",
        "report.json.json", "REPORT.JSON", "data.json",
        "../evil", "x" * 130, "", ".hidden",
    ])
    def test_native_name_error_rejects(self, bad):
        assert _wt().name_error(bad) is not None

    @pytest.mark.parametrize("good", [
        "conference", "con-report", "console output", "nul-check",
        "Payroll v1.2", "Monthly Report", "lpt10",
    ])
    def test_native_name_error_accepts(self, good):
        assert _wt().name_error(good) is None

    def test_save_definition_uses_name_error(self):
        src = (Path(_CC) / "graph" / "workflow_tools.py").read_text(encoding="utf-8")
        body = src[src.find("def save_definition"):src.find("def ", src.find("def save_definition") + 10)]
        assert "name_error(name)" in body

    # ── server chokepoint (app.py is not importable in tests — 0039 pattern:
    #    extract the self-contained function source and exec it) ──
    def _server_fn(self):
        import os as _os
        import re as _re2
        src = (_ROOT / "app.py").read_text(encoding="utf-8", errors="replace")
        m = _re2.search(
            r"(def _workflow_mirror_filename_error\(filename\):.*?)\n\n\n?@app\.route",
            src, _re2.S)
        assert m, "_workflow_mirror_filename_error not found in app.py"
        ns = {"os": _os}
        exec(m.group(1), ns)  # noqa: S102 - our own source under test
        return ns["_workflow_mirror_filename_error"]

    @pytest.mark.parametrize("bad", [
        "../evil.json", "..\\evil.json", "a/b.json", "a\\b.json",
        "con.json", "COM3.json", "nul", "prn.json",
        "report.json.json",              # stem 'report.json' ends .json
        "x<y.json", 'q"o.json', "star*.json", "pipe|.json",
        "trailing..json",                # stem 'trailing.' ends with a dot
        ("y" * 160) + ".json", "", None, ".json",
    ])
    def test_server_filename_error_rejects(self, bad):
        assert self._server_fn()(bad) is not None

    @pytest.mark.parametrize("good", [
        "Payroll (v2).json",             # legacy UI shape must keep saving
        "conference.json", "report v1.2.json", "nul-check.json",
        "Monthly Report.json", "plain.json",
    ])
    def test_server_filename_error_accepts(self, good):
        assert self._server_fn()(good) is None

    def test_route_wires_validator_before_db_save_and_mirror_is_honest(self):
        src = (_ROOT / "app.py").read_text(encoding="utf-8", errors="replace")
        route = src[src.find('@app.route("/save/workflow"'):src.find("def rename_workflow")]
        call = route.find("_workflow_mirror_filename_error(filename)")
        db = route.find("save_workflow_to_database(workflow_name")
        assert 0 < call < db, "filename validation must run BEFORE any side effect"
        # mirror failure must report the partial state, not a generic error
        assert "mirror_write_failed" in route
        assert route.find("save_to_file(filename, workflow_data)") < route.find("mirror_write_failed")


class TestPlainEnglishBuildAndContinuity:
    """AIHUB-0056 — pack-10 §A/§B. (A) The agent interrogated the user for a
    numeric connection id (no lookup tool existed) across 4+ rounds instead of
    building a draft. (B) After that multi-turn build, the terse "run it"
    delegated to the builder: the visual_workflow marker only existed after a
    TOOL ran, so clarification turns had no continuity and the marker did not
    survive to the run turn. Fixes: list_data_connections tool + DRAFT-FIRST
    prompt; marker stamped AT THE DIVERT; marker recovery from the read-back
    fingerprints in the conversation history."""

    def _nodes_src(self):
        return (Path(_CC) / "graph" / "nodes.py").read_text(encoding="utf-8")

    # ── A: connection lookup ──
    def test_list_connections_whitelists_identity_fields_only(self, monkeypatch):
        wt = _wt()
        rows = [
            {"id": 58, "Connection_Name": "AIRDB", "connection_type": "sqlserver",
             "database": "AIR", "username": "ai_user", "password": "••••••••",
             "_password_type": "local", "server": "10.0.0.6"},
            {"id": 2, "name": "Warehouse", "engine": "postgres",
             "password": "supersecret-legacy"},
            "not-a-dict",
        ]
        monkeypatch.setattr(wt, "_get", lambda path, timeout=30: {"ok": True, "data": rows})
        res = wt.list_connections()
        assert res["ok"] is True
        assert res["connections"] == [
            {"id": "58", "name": "AIRDB", "type": "sqlserver", "database": "AIR"},
            {"id": "2", "name": "Warehouse", "type": "postgres", "database": ""},
        ]
        blob = repr(res)
        assert "••••" not in blob and "supersecret" not in blob
        assert "ai_user" not in blob and "10.0.0.6" not in blob

    def test_list_connections_error_passthrough(self, monkeypatch):
        wt = _wt()
        monkeypatch.setattr(wt, "_get", lambda path, timeout=30: {"ok": False, "error": "down"})
        assert wt.list_connections()["ok"] is False

    def test_lookup_tool_fully_registered_and_not_mutating(self):
        src = self._nodes_src()
        assert "list_data_connections" in nodes._WORKFLOW_TOOL_NAMES
        assert "list_data_connections" not in nodes._WORKFLOW_MUTATING_TOOL_NAMES
        assert "tools.append(list_data_connections)" in src
        assert '"list_data_connections": list_data_connections' in src

    def test_prompt_teaches_draft_first_and_self_resolution(self):
        src = self._nodes_src()
        block = src[src.find("_workflow_native_prompt = ("):src.find("### NODE CATALOG")]
        assert "DRAFT-FIRST (non-negotiable)" in block
        assert "list_data_connections and resolve the id YOURSELF" in block
        assert "AT MOST ONE focused question" in block
        assert "CONFIRM-THEN-BUILD" not in block  # the interrogation-era rule is gone

    # ── B: continuity marker ──
    def test_authoring_marker_stamps_and_preserves(self):
        fresh = nodes._authoring_marker({})
        assert fresh == {"name": "", "kind": "visual_workflow"}
        named = {"name": "daily-store-headcount", "kind": "visual_workflow"}
        assert nodes._authoring_marker({"code_flow_context": named}) is named
        # a code-flow marker (no visual kind) is NOT carried into workflow authoring
        cf = {"name": "store-headcount-v2"}
        assert nodes._authoring_marker({"code_flow_context": cf})["kind"] == "visual_workflow"

    def test_both_divert_sites_stamp_the_marker(self):
        src = self._nodes_src()
        # capability-router early return
        cap = src[src.find("_native_workflow_shape_divert(user_text, state):"):]
        assert '"code_flow_context": _authoring_marker(state)' in cap[:300]
        # smart-build visual_workflow branch feeds the final result
        assert "_wf_divert_marker = _authoring_marker(state)" in src
        assert '_final_out["code_flow_context"] = _wf_divert_marker' in src
        assert "_wf_divert_marker = None" in src  # initialized on every classify pass

    def test_marker_recovery_fingerprints_match_the_emitters(self):
        src = self._nodes_src()
        gate = src[src.find("marker RECOVERY from the conversation itself") - 400:
                   src.find("_marker_is_workflow = ")]
        assert 'if not _continuity_marker and _native_impl(state):' in gate
        # the fingerprints scanned must be the EXACT strings the native path
        # emits — if the pin wording drifts, this test forces the recovery
        # scanner to follow it.
        assert '"Authoritative persisted state" in _hc' in gate
        assert '"🧾 Read-back of the saved row" in _hc' in gate
        assert "Authoritative persisted state" in src[src.find("_wf_last_readback"):]
        wt_src = (Path(_CC) / "graph" / "workflow_tools.py").read_text(encoding="utf-8")
        assert "🧾 Read-back of the saved row" in wt_src

    def test_run_it_is_a_deterministic_followup_cue(self):
        saved_path = list(sys.path)
        saved = {k: v for k, v in sys.modules.items() if k == "graph" or k.startswith("graph.")}
        try:
            for k in list(saved):
                del sys.modules[k]
            sys.path.insert(0, _CC)
            from graph.build_routing import looks_like_workflow_followup  # noqa: PLC0415
            assert looks_like_workflow_followup("run it") is True
            assert looks_like_workflow_followup("test it") is True
            assert looks_like_workflow_followup("what's the weather in Boston") is False
        finally:
            sys.path[:] = saved_path
            for k in [k for k in sys.modules if k == "graph" or k.startswith("graph.")]:
                del sys.modules[k]
            sys.modules.update(saved)


class TestSourceContracts:
    def _src(self):
        return (Path(_CC) / "graph" / "nodes.py").read_text(encoding="utf-8")

    def test_insert_tool_fully_registered(self):
        src = self._src()
        assert '"insert_workflow_node_between",' in src                       # name set
        assert "tools.append(insert_workflow_node_between)" in src            # bound
        assert '"insert_workflow_node_between": insert_workflow_node_between' in src  # map
        assert "insert_workflow_node_between" in src.split("_WORKFLOW_MUTATING_TOOL_NAMES")[1][:600]

    def test_prompt_teaches_atomic_insert_and_no_fabrication(self):
        src = self._src()
        assert "ALWAYS use "
        assert "insert_workflow_node_between (ONE atomic call" in src
        assert "EDITS RUN TOOLS (non-negotiable)" in src

    def test_output_pins_present(self):
        src = self._src()
        assert "Authoritative persisted state" in src                 # read-back pin
        assert "Correction (automatic honesty check)" in src          # fabrication footer
        assert "fabrication_guard" in src                             # trace event
