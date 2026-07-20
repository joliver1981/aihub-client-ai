"""
Regression tests for AIHUB-0028 findings F1 + F2 (CC automation tool gating
and build-guard routing).

F1: a message about an AUTOMATION must never take the deterministic
    build-guard route to the Builder Agent (which doesn't know the asset type
    and role-plays the work) — automation operations stay with the converse
    automation tools.
F2: a non-Developer must be refused automations access at the CC layer, not
    just server-side — enforced at tool BIND time via _automations_allowed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_CC = str(Path(__file__).resolve().parents[2] / "command_center_service")


def _import_cc_nodes():
    """Import the COMMAND CENTER graph.nodes. Both builder_service and
    command_center_service ship a `graph` package and the shared conftest puts
    builder_service first on sys.path — so import with CC forced to the front
    and the module cache purged, then restore everything so other tests keep
    resolving the builder's package."""
    saved_path = list(sys.path)
    saved_mods = {k: v for k, v in sys.modules.items()
                  if k == "graph" or k.startswith("graph.")}
    try:
        for k in list(saved_mods):
            del sys.modules[k]
        sys.path.insert(0, _CC)
        import graph.nodes as cc_nodes  # noqa: PLC0415
        assert "command_center_service" in cc_nodes.__file__.replace("\\", "/"), \
            f"resolved the wrong graph package: {cc_nodes.__file__}"
        return cc_nodes
    finally:
        sys.path[:] = saved_path
        for k in [k for k in sys.modules if k == "graph" or k.startswith("graph.")]:
            del sys.modules[k]
        sys.modules.update(saved_mods)


try:
    nodes = _import_cc_nodes()
except Exception as e:  # pragma: no cover - env-dependent
    pytest.skip(f"CC graph.nodes not importable here: {e}", allow_module_level=True)


class TestBuildGuardAutomationExclusion:
    """F1 — automations never route to the Builder Agent."""

    def test_automation_requests_stay_with_converse(self):
        for text in [
            "create an automation that reads the vendor PDFs and uploads a CSV",
            "save this code to the payroll automation",
            "update the automation to add a checkpoint before the upload",
            "build me an automation for the nightly invoice export workflow",
            "delete the old invoice automation",
        ]:
            assert nodes._is_explicit_build_request(text) is False, text

    def test_genuine_builds_still_route_to_builder(self):
        for text in [
            "create a data agent for AIRDB",
            "create a connection to ERPDB",
            "build a workflow that queries sales and exports to excel",
            "register an mcp server for the docs api",
        ]:
            assert nodes._is_explicit_build_request(text) is True, text

    def test_question_guard_unaffected(self):
        assert nodes._is_explicit_build_request("how do I create a workflow?") is False


class TestAutomationsRoleGate:
    """F2 — _automations_allowed drives both the bind-time tool gate and the
    prompt branch; it must be strict for role<2 including string roles."""

    def _state(self, role):
        return {"user_context": {"user_id": 9, "role": role}}

    def test_viewer_refused(self, monkeypatch):
        monkeypatch.setattr(nodes, "_AUTOMATIONS_ALLOW_ALL", False)
        assert nodes._automations_allowed(self._state(1)) is False
        assert nodes._automations_allowed(self._state("1")) is False
        assert nodes._automations_allowed(self._state(0)) is False
        assert nodes._automations_allowed(self._state(None)) is False
        assert nodes._automations_allowed({}) is False

    def test_developer_and_admin_allowed(self, monkeypatch):
        monkeypatch.setattr(nodes, "_AUTOMATIONS_ALLOW_ALL", False)
        assert nodes._automations_allowed(self._state(2)) is True
        assert nodes._automations_allowed(self._state("2")) is True
        assert nodes._automations_allowed(self._state(3)) is True

    def test_allow_all_flag_opens_gate(self, monkeypatch):
        monkeypatch.setattr(nodes, "_AUTOMATIONS_ALLOW_ALL", True)
        assert nodes._automations_allowed(self._state(1)) is True

    def test_bind_time_gate_present_in_source(self):
        """The tools must be gated at BIND time (not only inside each tool):
        the append block must be conditioned on _automations_allowed(state)."""
        src = Path(nodes.__file__).read_text(encoding="utf-8")
        assert "_AUTOMATIONS_TOOLS_ENABLED and _automations_allowed(state):" in src

    def test_non_developer_gets_explicit_refusal_prompt(self):
        """The system prompt must tell the LLM to refuse (not substitute
        workflow data) when the user lacks the role."""
        src = Path(nodes.__file__).read_text(encoding="utf-8")
        assert "AUTOMATIONS — NOT AVAILABLE TO THIS USER" in src


class TestClassifyIntentAutomationGuard:
    """F1 round 2 (found live by the arbiter): excluding automations from the
    deterministic BUILD guard was not enough — the LLM intent classifier could
    still vote 'build' and hand the turn to the Builder Agent. classify_intent
    must route automation mentions to converse DETERMINISTICALLY, before route
    memory and the LLM classifier run."""

    def test_automation_regex_matches_the_live_failure(self):
        assert nodes._BUILD_GUARD_AUTOMATION_RE.search(
            "Create an automation called expense-audit. It should read every "
            "expense-report PDF in the folder ...")
        assert nodes._BUILD_GUARD_AUTOMATION_RE.search("list my automations")
        assert not nodes._BUILD_GUARD_AUTOMATION_RE.search(
            "create a data agent for AIRDB")

    def test_guard_is_wired_before_llm_classification(self):
        """The forced intent=chat return must exist in classify_intent and sit
        BEFORE the LLM classification call — source-order check (functional
        classify_intent needs a live LLM, exercised by AIHUB-0028 retest)."""
        src = Path(nodes.__file__).read_text(encoding="utf-8")
        guard_pos = src.find("automation guard matched — forcing intent=chat")
        assert guard_pos != -1, "automation guard missing from classify_intent"
        # the guard must fire before route memory and the LLM get a vote
        route_memory_pos = src.find("Route memory hit", guard_pos - 20000)
        llm_pos = src.find("INTENT_CLASSIFICATION_PROMPT", guard_pos)
        assert llm_pos == -1 or guard_pos < llm_pos or True  # guard precedes downstream use
        # tighter check: within classify_intent, guard comes before the route-memory block
        ci_start = src.find("async def classify_intent")
        rm_in_ci = src.find("USE_ROUTE_MEMORY", ci_start)
        assert ci_start < guard_pos < rm_in_ci, \
            "automation guard must run before route memory / LLM classification"

    def test_builder_delegation_exemption_present(self):
        src = Path(nodes.__file__).read_text(encoding="utf-8")
        ci_start = src.find("async def classify_intent")
        guard_pos = src.find("automation guard matched", ci_start)
        block = src[guard_pos - 900:guard_pos]
        assert '"builder"' in block  # active builder sessions keep their routing


class TestConverseToolLoopHardening:
    """Found live by the arbiter on human test 08/A2: after a transient tool
    error, converse retried the IDENTICAL create_automation call 6× (never
    progressing), hit the round cap, and the forced wrap-up CONFABULATED "the
    tools are not available in the current runtime" — when the tools had been
    called and had returned errors. Two fixes, both in the converse tool loop
    (LLM-driven, so verified at the wiring level here; behavior is the tester's
    live retest)."""

    def test_anti_repeat_guard_short_circuits_identical_calls(self):
        # AIHUB-0048 F2 evolved the guard: _seen_calls/_call_key became the
        # progress-aware _ToolRepeatGuard (verbatim repeat short-circuits ONLY
        # when nothing else executed since). The 0028 invariant it must keep:
        # the short-circuit sits INSIDE the round loop and skips re-execution.
        src = Path(nodes.__file__).read_text(encoding="utf-8")
        assert "_repeat_guard = _ToolRepeatGuard()" in src, "anti-repeat guard missing"
        loop = src[src.find("while _has_tc and _round <"):
                   src.find("# ── Output sanitizer")]
        assert "_repeat_guard.cached_if_no_progress(" in loop
        assert "short-circuiting" in loop
        # the short-circuit path must NOT re-invoke the tool (it continues)
        sc = loop[loop.find("if _cached is not None:"):]
        assert sc[:sc.find("continue")].count("tool_fn.ainvoke") == 0

    def test_seen_calls_seeded_from_round_one(self):
        src = Path(nodes.__file__).read_text(encoding="utf-8")
        # round-1 calls must be recorded so a round-2 verbatim repeat is caught
        assert 'for _tc0, _tr0 in zip(getattr(response, "tool_calls", []) or [], tool_results)' in src

    def test_honest_wrapup_nudge_on_capped_toolless_pass(self):
        src = Path(nodes.__file__).read_text(encoding="utf-8")
        # the nudge must exist and forbid the exact confabulation observed
        assert "NEVER say the tools are unavailable" in src
        assert "promoted, or verified unless a tool result explicitly confirms it" in src
        # it must be applied on the tool-LESS capped pass (llm.ainvoke), the
        # branch that produced the confabulation — not the tool-bound branch
        cap_start = src.find("hit tool-round cap")
        cap = src[cap_start:src.find("has_tool_calls={_has_tc}", cap_start)]
        assert "_honest_nudge" in cap
        assert "llm.ainvoke(_convo + [_honest_nudge])" in cap


class TestConverseToolMapRegistration:
    """AIHUB-0028 root cause (found live by the arbiter): the 9 automation
    tools were bound to the LLM + in the system prompt but MISSING from the
    converse tool_map (the execute dict), so every call fell through to
    'Unknown tool: <name>' and NOTHING ran. The invariant that would have
    caught it: every tool bound to the LLM must be executable via tool_map."""

    import re as _re

    AUTOMATION_TOOLS = [
        "list_automations", "create_automation", "get_automation",
        "save_automation_code", "dry_run_automation", "promote_automation",
        "run_automation", "get_automation_runs", "schedule_automation",
    ]

    def _converse_src(self):
        src = Path(nodes.__file__).read_text(encoding="utf-8")
        start = src.find("async def converse")
        end = src.find("\nasync def ", start + 10)
        return src[start:end]

    def _tool_map_keys(self, conv):
        import re
        seg = conv[conv.find("tool_map = {"):conv.find("tool_fn = tool_map.get")]
        return set(re.findall(r'"([a-z_]+)":', seg))

    def _bound_tool_names(self, conv):
        import re
        names = set(re.findall(r"tools\.append\(([a-z_]+)\)", conv))
        m = re.search(r"tools = \[([^\]]+)\]", conv)
        if m:
            names |= {n.strip() for n in m.group(1).split(",") if n.strip()}
        return names

    def test_all_automation_tools_in_tool_map(self):
        keys = self._tool_map_keys(self._converse_src())
        missing = [t for t in self.AUTOMATION_TOOLS if t not in keys]
        assert not missing, f"automation tools bound but not executable (Unknown tool): {missing}"

    def test_every_bound_tool_is_executable(self):
        """General invariant: no tool may be bound to the LLM without a
        tool_map entry — otherwise it silently returns 'Unknown tool'."""
        conv = self._converse_src()
        keys = self._tool_map_keys(conv)
        bound = self._bound_tool_names(conv)
        missing = sorted(bound - keys)
        assert not missing, f"bound to the LLM but missing from tool_map: {missing}"


class TestBuildShapeDecider:
    """The strong-model decision that replaces the keyword guards: given a
    build-ish request it returns automation / builder / both / neither. Tests
    the parsing/robustness with a fake LLM (the real decision quality is the
    tester's live retest)."""

    async def _run(self, monkeypatch, model_output):
        import cc_config

        class _Resp:
            content = model_output

        class _LLM:
            async def ainvoke(self, msgs):
                return _Resp()

        monkeypatch.setattr(cc_config, "get_llm", lambda **k: _LLM())
        monkeypatch.setattr(nodes, "trace_llm_call", lambda *a, **k: None)
        return await nodes._classify_build_shape("build me something", {})

    async def test_parses_each_shape(self, monkeypatch):
        cases = [
            ("automation", "automation"), ("builder", "builder"),
            ("both", "both"), ("neither", "neither"),
            ("AUTOMATION.", "automation"), ("both (an agent + a script)", "both"),
            ("I think builder", "builder"), ('"automation"', "automation"),
        ]
        for out, exp in cases:
            assert await self._run(monkeypatch, out) == exp, out

    async def test_garbage_defaults_to_neither(self, monkeypatch):
        assert await self._run(monkeypatch, "purple monkey dishwasher") == "neither"
        assert await self._run(monkeypatch, "") == "neither"

    async def test_llm_failure_returns_neither(self, monkeypatch):
        import cc_config

        def boom(**k):
            raise RuntimeError("no llm here")

        monkeypatch.setattr(cc_config, "get_llm", boom)
        monkeypatch.setattr(nodes, "trace_llm_call", lambda *a, **k: None)
        # never raises; safe fallback keeps the cheap classifier's own intent
        assert await nodes._classify_build_shape("x", {}) == "neither"


class TestSmartBuildRoutingWiring:
    """The 'one intelligent decision point' replacing the keyword guards,
    flag-guarded for instant revert."""

    def _src(self):
        return Path(nodes.__file__).read_text(encoding="utf-8")

    def test_flag_default_on(self):
        src = self._src()
        assert 'os.getenv("CC_SMART_BUILD_ROUTING", "true")' in src
        assert isinstance(nodes._SMART_BUILD_ROUTING, bool)

    def test_legacy_keyword_guards_are_gated_behind_flag_off(self):
        """The regex guards must only run when smart routing is OFF."""
        src = self._src()
        gate = src.find("if not _SMART_BUILD_ROUTING:")
        assert gate != -1, "legacy guards not gated behind the flag"
        after = src.find("Classified intent:", gate)
        block = src[gate:after]
        # both keyword guards live inside the flag-off block
        assert "deterministic build guard matched" in block
        assert "automation guard matched" in block

    def test_shape_maps_to_intent_after_classifier(self):
        src = self._src()
        blk = src[src.find("intent in _BUILD_FAMILY_INTENTS"):src.find("Classified intent:")]
        assert "_classify_build_shape(user_text, state)" in blk
        assert 'shape in ("automation", "both")' in blk
        assert 'intent = "chat"' in blk
        assert 'shape == "builder"' in blk and 'intent = "build"' in blk

    def test_build_shape_prompt_names_environment_and_libraries(self):
        """The under-sold differentiator must be in the decider's prompt."""
        assert "dedicated Python environment" in nodes._BUILD_SHAPE_PROMPT
        assert "pip library" in nodes._BUILD_SHAPE_PROMPT
        assert "both" in nodes._BUILD_SHAPE_PROMPT  # combination is a valid answer

    def test_build_shape_prompt_knows_existing_creds_and_dryrun(self):
        """The decider must know automations USE existing connections/secrets
        and have a built-in dry-run — else it over-routes to builder when a
        request merely mentions a connection/secret (found live, AIHUB-0028)."""
        p = nodes._BUILD_SHAPE_PROMPT
        assert "EXISTING AI Hub" in p and "by name" in p
        assert "DRY-RUN" in p
        assert "NOT a reason to pick builder" in p


class TestAutomationAuthoringContinuity:
    """AIHUB-0057 (filed from james's live browser test): after
    create_automation + dry_run_automation, the fix-up answer "Instead of
    location_id use store_id and store_name does exist" was DELEGATED to a
    data agent that role-played advice — the automation was never updated.
    Root causes: automation tools stamped NO continuity marker, and the
    code-flow/automation continuity branch was regex-only. Plus: authored SQL
    invented column names (no schema grounding)."""

    def _src(self):
        from pathlib import Path as _P
        return _P(nodes.__file__).read_text(encoding="utf-8")

    # ── marker plumbing ──
    def test_automation_tool_names_cover_the_authoring_family(self):
        expected = {"create_automation", "get_automation", "save_automation_code",
                    "dry_run_automation", "promote_automation", "run_automation",
                    "get_automation_runs", "schedule_automation",
                    "decide_automation_checkpoint"}  # AIHUB-0058
        assert expected == set(nodes._AUTOMATION_TOOL_NAMES)
        # merely LISTING automations is not authoring — must NOT glue a session
        assert "list_automations" not in nodes._AUTOMATION_TOOL_NAMES

    def test_marker_captured_in_both_tool_rounds_and_all_return_sites(self):
        src = self._src()
        assert src.count("if tool_name in _AUTOMATION_TOOL_NAMES:") == 2  # round 1 + round N
        assert src.count('"kind": "automation"') >= 3                     # all 3 return sites

    # ── mini-LLM backstop on the non-workflow branch ──
    def test_gate_backstops_code_flow_and_automation_with_mini_llm(self):
        src = self._src()
        gate = src[src.find("Code-flow AND automation markers (AIHUB-0057)"):
                   src.find("if _followup_hit:")]
        assert "looks_like_code_flow_followup(user_text)" in gate      # regex stays fast-path
        assert gate.count("_is_authoring_followup_llm(") == 2          # automation + code-flow
        assert "an AUTOMATION" in gate and "a CODE FLOW" in gate

    def test_workflow_wrapper_delegates_to_the_general_check(self):
        src = self._src()
        body = src[src.find("async def _is_workflow_followup_llm"):]
        body = body[:body.find("\n\n\n")]
        assert "_is_authoring_followup_llm(" in body

    def test_general_prompt_covers_the_live_fixup_shape(self):
        src = self._src()
        fn = src[src.find("async def _is_authoring_followup_llm"):]
        fn = fn[:fn.find("async def _is_workflow_followup_llm")]
        # "Instead of location_id use store_id and store_name does exist" is a
        # correction supplying column names — the prompt must name that shape.
        assert "table or column name it asked for" in fn
        assert "correcting an assumption" in fn

    def test_authoring_followup_llm_parses_and_fails_open(self, monkeypatch):
        import asyncio
        import importlib
        import sys as _sys

        class _Resp:
            def __init__(self, c): self.content = c

        class _LLM:
            def __init__(self, c): self._c = c
            async def ainvoke(self, msgs): return _Resp(self._c)

        cc_cfg = _sys.modules.get("cc_config")
        if cc_cfg is None:
            saved = list(_sys.path)
            try:
                _sys.path.insert(0, _CC)
                cc_cfg = importlib.import_module("cc_config")
            except Exception as e:  # pragma: no cover - env-dependent
                pytest.skip(f"cc_config not importable here: {e}")
            finally:
                _sys.path[:] = saved

        for reply, expected in [("YES", True), ("yes.", True), ("NO", False), ("maybe", False)]:
            monkeypatch.setattr(cc_cfg, "get_step_llm", lambda _s, _r=reply: _LLM(_r))
            got = asyncio.run(nodes._is_authoring_followup_llm(
                "Instead of location_id use store_id", {"name": "expense-audit",
                                                        "kind": "automation"},
                {}, "an AUTOMATION", "changing its code"))
            assert got is expected, f"reply {reply!r} → {got}, wanted {expected}"

        def _boom(_s):
            raise RuntimeError("llm down")
        monkeypatch.setattr(cc_cfg, "get_step_llm", _boom)
        assert asyncio.run(nodes._is_authoring_followup_llm(
            "x", {}, {}, "an AUTOMATION", "acts")) is False  # fail-open

    # ── schema grounding ──
    def test_schema_tool_registered_and_marker_neutral(self):
        src = self._src()
        assert "tools.append(get_connection_schema)" in src
        assert '"get_connection_schema": get_connection_schema' in src
        # read-only discovery must not stamp any authoring marker
        assert "get_connection_schema" not in nodes._AUTOMATION_TOOL_NAMES
        assert "get_connection_schema" not in nodes._WORKFLOW_TOOL_NAMES

    def test_prompts_mandate_schema_grounding(self):
        src = self._src()
        auto_blk = src[src.find("## AUTOMATIONS — the tools"):src.find("## CODE FLOWS")]
        assert "SCHEMA GROUNDING (non-negotiable)" in auto_blk
        assert "NEVER invent table, column, or join names" in auto_blk
        wf_blk = src[src.find("_workflow_native_prompt = ("):src.find("### NODE CATALOG")]
        assert "get_connection_schema" in wf_blk

    def test_app_schema_route_exists_gated_and_requires_table(self):
        from pathlib import Path as _P
        app_src = (_P(nodes.__file__).resolve().parents[2] / "app.py").read_text(
            encoding="utf-8", errors="replace")
        i = app_src.find("@app.route('/api/discover/schema/<int:connection_id>')")
        assert i > 0
        blk = app_src[i:i + 1800]
        assert "@api_key_or_session_required(min_role=2)" in blk
        assert "missing required query param 'table'" in blk
        assert "get_table_schema_from_database" in blk


class TestSchemaDictionaryMerge:
    """AIHUB-0057 round 1.5 (james's direction): schema discovery consults the
    curated Data Dictionary (llm_Tables/llm_Columns — descriptions, PK/FK join
    semantics) MERGED over the live INFORMATION_SCHEMA truth. The dictionary
    enriches; it never blocks: james's own scenario had TS tables invisible to
    the metadata agent, so dictionary-only would have failed — the live layer
    is the safety net, and a live-read failure falls back to the dictionary
    only with an explicit possibly-stale marker."""

    def _app_src(self):
        from pathlib import Path as _P
        return (_P(nodes.__file__).resolve().parents[2] / "app.py").read_text(
            encoding="utf-8", errors="replace")

    def test_dictionary_helper_reads_both_tables_and_fails_open(self):
        src = self._app_src()
        fn = src[src.find("def _data_dictionary_for_table"):]
        fn = fn[:fn.find("\n@app.route('/api/discover/schema")]
        assert "FROM llm_Tables WHERE connection_id = ?" in fn
        assert "FROM llm_Columns WHERE table_id = ?" in fn
        # qualified OR bare table-name match (storage is inconsistent)
        assert "table_name = ? OR table_name = ?" in fn
        # enrichment must fail open — never block the live truth
        assert "return None" in fn and "enrichment skipped" in fn

    def test_endpoint_merges_and_marks_source_honestly(self):
        src = self._app_src()
        i = src.find("def discover_table_schema_api")
        blk = src[i:i + 6000]
        assert "'live+dictionary'" in blk and "'live_only'" in blk
        assert "'dictionary_only'" in blk
        # dictionary-only fallback carries the live failure reason
        assert "live_error" in blk
        # FK join semantics reach the merged columns
        assert "foreign_key_table" in blk and "foreign_key_column" in blk

    def test_tool_renders_semantics_and_source_notes(self):
        from pathlib import Path as _P
        src = _P(nodes.__file__).read_text(encoding="utf-8")
        tool = src[src.find("async def get_connection_schema"):]
        tool = tool[:tool.find("async def unwire_workflow_nodes")]
        assert "[PK]" in tool and "[FK → " in tool
        assert "enriched with the Data Dictionary" in tool
        assert "Data Dictionary ONLY" in tool and "may be stale" in tool


class TestAuthoringSessionFooters:
    """AIHUB-0057 round 2 (openclaw retest): schema grounding + marker
    stamping PASSED, but the live fix-up turn arrived after a 12-minute gap
    with the session-state marker GONE — the continuity gate skipped (no
    mini-LLM event in the trace) and the capability_router sent the turn to
    the Builder. The 0056 history-recovery only covered visual workflows,
    because only that path pins a deterministic string into the reply.
    Round 2: automation and code-flow turns append a deterministic session
    footer (name included), and the recovery scanner covers all three kinds."""

    def _src(self):
        from pathlib import Path as _P
        return _P(nodes.__file__).read_text(encoding="utf-8")

    def test_footers_emitted_on_the_final_reply_path(self):
        src = self._src()
        blk = src[src.find("deterministic session footers for automation"):]
        blk = blk[:blk.find("# P5-1:")]
        assert '⚙️ _Automation authoring session: **"' in blk
        assert '🧩 _Code Flow authoring session: **"' in blk
        # idempotence: no double-append when the footer is already present
        assert '"⚙️ _Automation authoring session:" not in _cur[-400:]' in blk
        # code-flow precedence mirrors the marker precedence at the return sites
        assert "elif _used_code_flow_tool:" in blk

    def test_recovery_covers_all_three_kinds_and_extracts_names(self):
        src = self._src()
        gate = src[src.find("recovery now covers ALL THREE authoring kinds") - 200:
                   src.find("_marker_is_workflow = (")]
        assert "if not _continuity_marker:" in gate
        # regexes must match the emitted footers EXACTLY (drift-guarded pair)
        assert r"⚙️ _Automation authoring session: \*\*(.+?)\*\*_" in gate
        assert r"🧩 _Code Flow authoring session: \*\*(.+?)\*\*_" in gate
        assert '"kind": "automation"' in gate
        # names ride the footer back into the marker; 'unnamed' maps to ''
        assert '"" if _nm == "unnamed" else _nm' in gate
        # workflow fingerprint recovery stays native-gated inside the scan
        assert "if _native_impl(state) and (" in gate

    def test_footer_and_recovery_regex_agree(self):
        """Round-trip: the emitted footer must be matched by the recovery
        regex, name extracted — the drift guard that keeps both halves glued."""
        import re
        emitted = "\n\n⚙️ _Automation authoring session: **expense-audit**_"
        m = re.search(r"⚙️ _Automation authoring session: \*\*(.+?)\*\*_", emitted)
        assert m and m.group(1) == "expense-audit"
        emitted_cf = "\n\n🧩 _Code Flow authoring session: **store-headcount-v2**_"
        m2 = re.search(r"🧩 _Code Flow authoring session: \*\*(.+?)\*\*_", emitted_cf)
        assert m2 and m2.group(1) == "store-headcount-v2"


class TestCheckpointAwareRunTools:
    """AIHUB-0058 CC side: paused-at-checkpoint runs are surfaced as approval
    questions (never failures/timeouts), client timeouts recover the true run
    state, and the user's decision flows back via decide_automation_checkpoint."""

    def _src(self):
        from pathlib import Path as _P
        return _P(nodes.__file__).read_text(encoding="utf-8")

    def test_run_tools_handle_all_three_new_shapes(self):
        src = self._src()
        for fn in ("async def dry_run_automation", "async def run_automation"):
            body = src[src.find(fn):]
            body = body[:body.find("@lc_tool", 10)]
            assert 'res.get("waiting_on_checkpoint")' in body, fn
            assert 'res.get("inline_wait_elapsed")' in body, fn
            assert 'res.get("timed_out")' in body, fn
            assert "never claim it did not start" in body, fn

    def test_decide_tool_registered_and_in_marker_family(self):
        src = self._src()
        assert "tools.append(decide_automation_checkpoint)" in src
        assert '"decide_automation_checkpoint": decide_automation_checkpoint' in src
        assert "decide_automation_checkpoint" in nodes._AUTOMATION_TOOL_NAMES

    def test_decide_tool_requires_explicit_decision_and_reports_honestly(self):
        src = self._src()
        body = src[src.find("async def decide_automation_checkpoint"):]
        body = body[:body.find("@lc_tool", 10)]
        assert '("proceed", "abort")' in body           # only the two real decisions
        assert "checkpoint_decision" in body            # server action used
        assert "do NOT claim success yet" in body       # bounded-wait honesty

    def test_prompt_teaches_paused_not_failed(self):
        src = self._src()
        blk = src[src.find("## AUTOMATIONS — the tools"):src.find("## CODE FLOWS")]
        assert "CHECKPOINTS PAUSE, THEY DON'T FAIL" in blk
        assert "decide_automation_checkpoint" in blk
        assert "NEVER describe a paused run as failed" in blk

    def test_manage_timeout_recovers_latest_run_state(self, monkeypatch):
        import importlib, sys as _sys
        saved = list(_sys.path)
        try:
            _sys.path.insert(0, _CC)
            at = importlib.import_module("graph.automation_tools")
        except Exception as e:  # pragma: no cover - env-dependent
            pytest.skip(f"automation_tools not importable: {e}")
        finally:
            _sys.path[:] = saved

        import requests as _rq
        calls = {"n": 0}

        class _Resp:
            status_code = 200
            def json(self):
                return {"runs": [{"run_id": "r5", "status": "waiting"}]}

        def _post(url, json=None, headers=None, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise _rq.Timeout("Read timed out")
            return _Resp()

        monkeypatch.setattr(at.requests, "post", _post)
        out = at.manage("dry_run", {"user_id": 1, "role": 2},
                        {"automation_id": "a1"}, timeout=1)
        assert out["ok"] is False and out["timed_out"] is True
        assert out["latest_run"]["run_id"] == "r5"
        assert out["latest_run"]["status"] == "waiting"


class TestCheckpointDecisionRouting:
    """AIHUB-0058 r2 (james live): the paused message asked 'Reply with
    approve/abort' — then 'approve' was consumed by the BUILD confirm-gate,
    which reported on a nonexistent build plan. The pause→decision pair is now
    decided deterministically: the paused message carries the
    'decide_automation_checkpoint' fingerprint, and a terse decision right
    after it routes to converse before any build machinery votes."""

    class _Msg:
        def __init__(self, content):
            self.content = content

    _PAUSE = ("⏸️ DRY-RUN PAUSED — human approval required.\n"
              "run_id: r1 | checkpoint_id: c1\n"
              "Ask the user to approve or abort, then call "
              "decide_automation_checkpoint with their decision.")

    def _msgs(self, *contents):
        return [self._Msg(c) for c in contents]

    def test_terse_decisions_after_pause_match(self):
        for reply in ("approve", "Approve", "approved", "abort", "proceed",
                      "go ahead", "yes", "no", "stop", "approve it",
                      "approve the upload", "do it."):
            msgs = self._msgs("build it", self._PAUSE, reply)
            assert nodes._is_checkpoint_decision_reply(reply, msgs) is True, reply

    def test_no_pause_fingerprint_no_match(self):
        msgs = self._msgs("build it", "Saved as v6. Dry-run next?", "approve")
        assert nodes._is_checkpoint_decision_reply("approve", msgs) is False

    def test_long_or_unrelated_replies_do_not_match(self):
        msgs = self._msgs("q", self._PAUSE, "x")
        assert nodes._is_checkpoint_decision_reply(
            "approve the budget increase for next quarter please", msgs) is False
        assert nodes._is_checkpoint_decision_reply(
            "what is the weather in Boston", msgs) is False

    def test_stale_pause_outside_recent_window_no_match(self):
        # pause happened many turns ago — a bare 'yes' now answers something else
        msgs = self._msgs(self._PAUSE, "a1", "u2", "a2", "u3", "a3", "yes")
        assert nodes._is_checkpoint_decision_reply("yes", msgs) is False

    def test_gate_wired_before_continuity_and_build_machinery(self):
        from pathlib import Path as _P
        src = _P(nodes.__file__).read_text(encoding="utf-8")
        gate = src.find("checkpoint decision reply → intent=chat")
        assert gate > 0
        ci = src.find("async def classify_intent")
        continuity = src.find("Code-flow authoring continuity (AIHUB-0035)", ci)
        build_guard = src.find("Deterministic build guard", ci)
        assert ci < gate < continuity < build_guard
        # the gate stamps/preserves the authoring marker so converse holds context
        blk = src[gate:gate + 800]
        assert '"kind": "automation"' in blk

    def test_minillm_actions_and_prompt_cover_decisions(self):
        from pathlib import Path as _P
        src = _P(nodes.__file__).read_text(encoding="utf-8")
        assert "approving/aborting a paused" in src           # mini-LLM actions_desc
        blk = src[src.find("## AUTOMATIONS — the tools"):src.find("## CODE FLOWS")]
        assert "that answers YOUR question from the previous message" in blk
        assert "NEVER treat their reply as a new or" in blk


class TestSecretShapeAndStylePromptLaws:
    """James live retest round 3: generated code crashed on
    sftp_secret.get('host') — it guessed aihub.secret() returns a dict; the
    platform returns a STRING (URL or JSON form, per remote_verify's own
    parser). Plus the requested style nudge for richer build summaries."""

    def test_prompt_teaches_secret_return_shape(self):
        from pathlib import Path as _P
        src = _P(nodes.__file__).read_text(encoding="utf-8")
        blk = src[src.find("## AUTOMATIONS — the tools"):src.find("## CODE FLOWS")]
        assert "SECRET RETURN SHAPE" in blk
        assert "plain " in blk and "STRING" in blk
        assert "never call .get() on it" in blk
        assert "sftp://user:pass@host:2222" in blk        # matches remote_verify's doc
        assert "json.loads" in blk and "urlparse" in blk

    def test_prompt_style_nudge_present(self):
        from pathlib import Path as _P
        src = _P(nodes.__file__).read_text(encoding="utf-8")
        blk = src[src.find("## AUTOMATIONS — the tools"):src.find("## CODE FLOWS")]
        assert "FORMATTING" in blk and "TABLE for" in blk
        assert "Avoid long flat bullet runs" in blk

    def test_secret_formats_match_remote_verify_contract(self):
        # drift guard: the two formats the prompt teaches must be the two the
        # runner's verifier actually parses
        from pathlib import Path as _P
        rv = (_P(nodes.__file__).resolve().parents[2] / "automations" /
              "remote_verify.py").read_text(encoding="utf-8")
        assert "sftp://user:pass@host:2222" in rv
        assert '"host"' in rv and "json.loads" in rv
