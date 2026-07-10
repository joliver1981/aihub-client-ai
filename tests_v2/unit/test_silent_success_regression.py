"""
Anti-silent-success regression harness (Phase 5).

Standing oracle for the "silent success" remediation — the class of bugs where a
failed/unconfirmed platform mutation was reported to the user as done. It locks in
the deterministic decision logic of each phase so a future edit can't quietly
re-open the hole:

  Phase 0  delegator status is fail-closed          (_derive_delegation_status)
  Phase 1  honest-HTTP normalizer coerces only      (_normalize_internal_failure_status)
           executor-marked failure bodies, UI-safe
  Phase 2  read-back verifier CONFIRMED/DISPROVED/   (verification.py check fns)
           INCONCLUSIVE, and only DISPROVED fails
  Phase 3  compile is three-way: success/draft/error (_compile_outcome_status)
  Phase 4  CC messaging surfaces failed/unverified   (_summarize_verification,
           steps and never records disproved creates  _verification_footer,
                                                       _extract_created_resources)

These are pure-logic tests: verification.py is imported directly; functions that
live inside heavy service modules (app.py, the CC graph, the delegator, the
compile route) are AST-extracted and exercised with mocks, so this file has NO
app/service import dependencies and runs in any environment.
"""
from __future__ import annotations

import ast
import importlib.util
import logging
import os
import typing

import pytest

pytestmark = pytest.mark.unit

# ── repo layout ─────────────────────────────────────────────────────────────
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APP_PY = os.path.join(_REPO, "app.py")
DELEGATOR_PY = os.path.join(_REPO, "command_center", "orchestration", "delegator.py")
ROUTES_PY = os.path.join(_REPO, "workflow_builder_routes.py")
CC_NODES_PY = os.path.join(_REPO, "command_center_service", "graph", "nodes.py")
VERIFICATION_PY = os.path.join(_REPO, "builder_service", "execution", "verification.py")


def _load_functions(path: str, names, extra_globals=None) -> dict:
    """AST-extract the named top-level functions from `path` and exec each in an
    isolated namespace (so we test the REAL source without importing the heavy
    module). `extra_globals` injects mocks/values the functions reference as module
    globals (e.g. request, logger)."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    ns: dict = {k: getattr(typing, k) for k in
                ("Optional", "Dict", "Any", "List", "Tuple", "Callable")}
    if extra_globals:
        ns.update(extra_globals)
    found = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            node.decorator_list = []  # strip decorators we can't resolve (e.g. @app.after_request)
            exec(compile(ast.Module(body=[node], type_ignores=[]), path, "exec"), ns)
            found[node.name] = ns[node.name]
    missing = set(names) - set(found)
    assert not missing, f"functions not found in {path}: {missing}"
    return found


def _load_module(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ═══════════════════════════════════════════════════════════════════════════
# Phase 0 — delegator status is fail-closed
# ═══════════════════════════════════════════════════════════════════════════
_derive = _load_functions(DELEGATOR_PY, ["_derive_delegation_status"])["_derive_delegation_status"]


@pytest.mark.parametrize("saw_error,plan_status,expected", [
    (True, None, "failed"),
    (True, "completed", "failed"),      # error event overrides a completed plan
    (False, "failed", "failed"),
    (False, "partial", "partial"),
    (False, "completed", "completed"),
    (False, "draft", "completed"),      # draft awaiting confirmation is a success
    (False, "delegated", "completed"),
    (False, None, "completed"),
])
def test_phase0_delegation_status(saw_error, plan_status, expected):
    assert _derive(saw_error, plan_status) == expected


# ═══════════════════════════════════════════════════════════════════════════
# Phase 1 — honest-HTTP normalizer (executor-marked only; UI untouched)
# ═══════════════════════════════════════════════════════════════════════════
class _FakeReq:
    def __init__(self):
        self.headers = {}
        self.method = "POST"
        self.path = "/x"


class _FakeResp:
    def __init__(self, status_code, content_type, body):
        self.status_code = status_code
        self.content_type = content_type
        self._body = body

    def get_json(self, silent=False):
        return self._body


_norm_req = _FakeReq()
_normalize = _load_functions(
    APP_PY, ["_normalize_internal_failure_status"],
    {"request": _norm_req, "logger": logging.getLogger("ss-test")},
)["_normalize_internal_failure_status"]

_MARK = {"X-AIHub-Internal-Exec": "1"}


@pytest.mark.parametrize("headers,code,ctype,body,expected", [
    # executor-marked failure bodies -> coerced to 500
    (_MARK, 200, "application/json", {"status": "error"}, 500),
    (_MARK, 200, "application/json", {"status": "failed"}, 500),
    (_MARK, 200, "application/json", {"status": "failure"}, 500),
    (_MARK, 200, "application/json", {"success": False}, 500),
    # executor-marked success / excluded / non-JSON / already-error -> untouched
    (_MARK, 200, "application/json", {"status": "success"}, 200),
    (_MARK, 200, "application/json", {"success": True, "errors": ["x"]}, 200),
    (_MARK, 200, "text/html", None, 200),
    (_MARK, 400, "application/json", {"status": "error"}, 400),
    # NO marker (browser UI) -> never coerced, even on an error body
    ({}, 200, "application/json", {"status": "error"}, 200),
])
def test_phase1_normalizer(headers, code, ctype, body, expected):
    _norm_req.headers = headers
    resp = _FakeResp(code, ctype, body)
    out = _normalize(resp)
    assert out.status_code == expected


# ═══════════════════════════════════════════════════════════════════════════
# Phase 2 — deterministic read-back verifier
# ═══════════════════════════════════════════════════════════════════════════
V = _load_module(VERIFICATION_PY, "ss_verification")


def test_phase2_tools_create_confirmed():
    pk = {"packages": ["existing", "my_tool"]}
    assert V._check_tools_create({"name": "my_tool"}, {}, pk)[0] == V.CONFIRMED


def test_phase2_tools_create_disproved():
    pk = {"packages": ["existing"]}
    assert V._check_tools_create({"name": "ghost"}, {}, pk)[0] == V.DISPROVED


def test_phase2_tools_create_inconclusive_when_unreadable():
    # read-back returned no package list (e.g. endpoint 404) -> never a false fail
    assert V._check_tools_create({"name": "x"}, {}, {"status": "error"})[0] == V.INCONCLUSIVE


def test_phase2_agents_create_by_id():
    agents = {"agents": [{"agent_id": 5, "agent_name": "Bot"}]}
    assert V._check_agents_create({"agent_description": "z"}, {"agent_id": 5}, agents)[0] == V.CONFIRMED


def test_phase2_agents_create_by_name_when_id_is_prose():
    agents = {"agents": [{"agent_id": 5, "agent_name": "Bot"}]}
    # response 'message' wasn't a numeric id — fall back to name match
    assert V._check_agents_create({"agent_description": "Bot"}, {"agent_id": "created ok"}, agents)[0] == V.CONFIRMED


def test_phase2_agents_create_missing_disproved():
    agents = {"agents": [{"agent_id": 5, "agent_name": "Bot"}]}
    assert V._check_agents_create({"agent_description": "New"}, {"agent_id": 999}, agents)[0] == V.DISPROVED


def test_phase2_mcp_create_confirmed_by_name():
    srv = [{"server_id": 1, "server_name": "S1", "server_url": "http://a"}]
    assert V._check_mcp_create({"server_name": "S1", "server_url": "http://b"}, {}, srv)[0] == V.CONFIRMED


def test_phase2_mcp_create_missing_disproved():
    srv = [{"server_id": 1, "server_name": "S1", "server_url": "http://a"}]
    assert V._check_mcp_create({"server_name": "Z", "server_url": "http://z"}, {}, srv)[0] == V.DISPROVED


def test_phase2_email_trigger_confirmed():
    cfg = {"config": {"workflow_trigger_enabled": True, "workflow_id": 5, "inbound_enabled": True}}
    p = {"agent_id": 1, "workflow_trigger_enabled": True, "workflow_id": 5, "inbound_enabled": True}
    assert V._check_email_configure(p, {}, cfg)[0] == V.CONFIRMED


def test_phase2_email_trigger_disproved_when_inbound_off():
    # F5 clobber: trigger enabled but the full-replace endpoint left inbound disabled
    cfg = {"config": {"workflow_trigger_enabled": True, "workflow_id": 5, "inbound_enabled": False}}
    p = {"agent_id": 1, "workflow_trigger_enabled": True, "workflow_id": 5}
    assert V._check_email_configure(p, {}, cfg)[0] == V.DISPROVED


def test_phase2_email_trigger_disproved_when_flag_didnt_take():
    cfg = {"config": {"workflow_trigger_enabled": False, "workflow_id": None, "inbound_enabled": True}}
    p = {"agent_id": 1, "workflow_trigger_enabled": True, "workflow_id": 5}
    assert V._check_email_configure(p, {}, cfg)[0] == V.DISPROVED


def test_phase2_email_inconclusive_without_config():
    assert V._check_email_configure({"agent_id": 1}, {}, {"success": True})[0] == V.INCONCLUSIVE


def test_phase2_agents_delete_still_present_disproved():
    check = V.VERIFICATION_SPECS["agents.delete"]["check"]
    assert check({"agent_id": 1}, {}, {"agents": [{"agent_id": 1}]})[0] == V.DISPROVED


def test_phase2_agents_delete_gone_confirmed():
    check = V.VERIFICATION_SPECS["agents.delete"]["check"]
    assert check({"agent_id": 9}, {}, {"agents": [{"agent_id": 1}]})[0] == V.CONFIRMED


def test_phase2_tools_delete_still_present_disproved():
    assert V._check_tools_delete({"package_name": "t"}, {}, {"packages": ["t"]})[0] == V.DISPROVED


def test_phase2_tools_delete_gone_confirmed():
    assert V._check_tools_delete({"package_name": "t"}, {}, {"packages": ["other"]})[0] == V.CONFIRMED


def test_phase2_spec_table_covers_the_criticals():
    for cap in ("tools.create", "agents.create", "mcp.create_server", "email.configure",
                "agents.delete", "tools.delete", "mcp.delete_server"):
        assert cap in V.VERIFICATION_SPECS, f"missing verification spec for {cap}"


# ═══════════════════════════════════════════════════════════════════════════
# Phase 3 — compile is three-way (success / draft / error)
# ═══════════════════════════════════════════════════════════════════════════
_compile_status = _load_functions(ROUTES_PY, ["_compile_outcome_status"])["_compile_outcome_status"]


@pytest.mark.parametrize("result,expected", [
    ({"success": True, "is_valid": True}, ("success", 200)),
    ({"success": True, "is_valid": False}, ("draft", 200)),      # F2: saved but invalid
    ({"success": True}, ("success", 200)),                        # is_valid defaults True
    ({"success": False, "error": "gen failed"}, ("error", 500)),
    ({"success": False}, ("error", 500)),
])
def test_phase3_compile_outcome(result, expected):
    assert _compile_status(result) == expected


# ═══════════════════════════════════════════════════════════════════════════
# Phase 4 — CC fail-closed messaging
# ═══════════════════════════════════════════════════════════════════════════
_cc = _load_functions(
    CC_NODES_PY,
    ["_summarize_verification", "_verification_footer", "_extract_created_resources"],
)
_summarize = _cc["_summarize_verification"]
_footer = _cc["_verification_footer"]
_extract = _cc["_extract_created_resources"]

_MIXED_PLAN = {"status": "partial", "steps": [
    {"status": "completed", "description": "create agent Bot",
     "result": {"verified": True, "verification_detail": "present",
                "data": {"agent_id": 5, "agent_description": "Bot"}}},
    {"status": "completed", "description": "create tool T",
     "result": {"verified": None, "verification_detail": "read-back 404", "data": {}}},
    {"status": "completed", "description": "list agents",
     "result": {"verified": None, "verification_detail": None, "data": {}}},   # read: not flagged
    {"status": "failed", "description": "test mcp",
     "result": {"verified": None, "error": "refused", "data": {}}},
    {"status": "completed", "description": "create connection C",
     "result": {"verified": False, "verification_detail": "not found",
                "data": {"connection_id": 9, "connection_name": "C"}}},
]}


def test_phase4_summary_classification():
    s = _summarize(_MIXED_PLAN)
    assert len(s["verified"]) == 1
    assert len(s["unverified"]) == 1 and "tool" in s["unverified"][0]["label"]
    assert len(s["failed"]) == 2                     # explicit failure + DISPROVED


def test_phase4_footer_surfaces_failed_and_unverified():
    f = _footer(_summarize(_MIXED_PLAN))
    assert "did not succeed" in f and "test mcp" in f
    assert "could not be" in f and "create tool T" in f


def test_phase4_no_footer_on_clean_success():
    s = _summarize({"status": "completed", "steps": [
        {"status": "completed", "description": "x",
         "result": {"verified": True, "verification_detail": "ok", "data": {}}}]})
    assert _footer(s) == ""


def test_phase4_no_footer_on_draft_plan():
    s = _summarize({"status": "draft", "steps": [
        {"status": "pending", "description": "x", "result": {}}]})
    assert _footer(s) == ""


def test_phase4_created_resources_excludes_disproved_and_failed():
    res = _extract(_MIXED_PLAN)
    types = {r["type"]: r["id"] for r in res}
    assert types.get("agent") == 5            # verified create recorded
    assert "connection" not in types          # DISPROVED create NOT recorded


# ═══════════════════════════════════════════════════════════════════════════
# WIRING — the audit's key lesson: the pure decisions above are inert unless the
# executor/app/compiler actually INVOKE them. These bind the decisions to their
# call sites (behavioral where cheap, source-contract where a heavy import isn't
# worth it) so a "delete the fix line" revert fails loudly.
# ═══════════════════════════════════════════════════════════════════════════
import asyncio  # noqa: E402

_EXECUTOR_PY = os.path.join(_REPO, "builder_service", "execution", "executor.py")
_WF_COMPILER_PY = os.path.join(_REPO, "workflow_compiler.py")
_BUILDER_NODES_PY = os.path.join(_REPO, "builder_service", "graph", "nodes.py")


def _src(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _import_from(paths, dotted):
    import sys
    for p in paths:
        if p not in sys.path:
            sys.path.insert(0, p)
    import importlib
    return importlib.import_module(dotted)


def _executor_mod():
    return _import_from([_REPO, os.path.join(_REPO, "builder_service")],
                        "builder_service.execution.executor")


# ── Phase 2 wiring: _verify_write consumes the verdict (the load-bearing piece) ──
def test_phase2_verify_write_disproved_downgrades_to_failed():
    EX = _executor_mod()

    async def run():
        ex = EX.ActionExecutor(api_key="x")

        async def read(domain, action, params, description=""):   # read-back does NOT contain the tool
            return EX.ExecutionResult(status=EX.ExecutionStatus.SUCCESS, message="", data={"packages": ["other"]})
        ex.execute_step = read
        r = EX.ExecutionResult(status=EX.ExecutionStatus.SUCCESS, message="ok", data={})
        return await ex._verify_write("tools.create", {"name": "my_tool"}, r)

    out = asyncio.run(run())
    assert out.status == EX.ExecutionStatus.FAILED and out.verified is False


def test_phase2_verify_write_confirmed_stays_success():
    EX = _executor_mod()

    async def run():
        ex = EX.ActionExecutor(api_key="x")

        async def read(domain, action, params, description=""):
            return EX.ExecutionResult(status=EX.ExecutionStatus.SUCCESS, message="", data={"packages": ["my_tool"]})
        ex.execute_step = read
        r = EX.ExecutionResult(status=EX.ExecutionStatus.SUCCESS, message="ok", data={})
        return await ex._verify_write("tools.create", {"name": "my_tool"}, r)

    out = asyncio.run(run())
    assert out.status == EX.ExecutionStatus.SUCCESS and out.verified is True


def test_phase2_verify_write_inconclusive_when_readback_fails():
    EX = _executor_mod()

    async def run():
        ex = EX.ActionExecutor(api_key="x")

        async def read(domain, action, params, description=""):   # read-back endpoint failed (e.g. 404)
            return EX.ExecutionResult(status=EX.ExecutionStatus.FAILED, message="", data={}, http_status=404)
        ex.execute_step = read
        r = EX.ExecutionResult(status=EX.ExecutionStatus.SUCCESS, message="ok", data={})
        return await ex._verify_write("tools.create", {"name": "my_tool"}, r)

    out = asyncio.run(run())
    # never regress a success we cannot disprove
    assert out.status == EX.ExecutionStatus.SUCCESS and out.verified is None


def test_phase2_execution_result_todict_carries_verified():
    EX = _executor_mod()
    d = EX.ExecutionResult(status=EX.ExecutionStatus.SUCCESS, message="", data={}).to_dict()
    assert "verified" in d and "verification_detail" in d


# ── Registry contracts: the F3/F4/F5 schema fixes must stay declared ──
def _platform_actions_by_id():
    pa = _import_from([_REPO], "builder_agent.actions.platform_actions")
    return {a.capability_id: a for a in pa.get_platform_actions()}


def test_mutating_actions_declare_success_indicator():
    by_id = _platform_actions_by_id()
    for cap in ("tools.create", "tools.delete", "integrations.test",
                "mcp.test_server", "integrations.delete", "mcp.delete_server"):
        a = by_id.get(cap)
        assert a is not None, f"{cap} missing from registry"
        assert a.primary_route.success_indicator == "status", \
            f"{cap} lost success_indicator='status' (F3/F4 silent-success regression)"


def test_mcp_test_server_has_type_remote_field():
    by_id = _platform_actions_by_id()
    fields = {f.name: getattr(f, "default", None)
              for f in by_id["mcp.test_server"].primary_route.input_fields}
    assert fields.get("type") == "remote", "mcp.test_server lost the type='remote' field (F4 regression)"


def test_email_configure_exposes_workflow_trigger_fields():
    by_id = _platform_actions_by_id()
    names = {f.name for f in by_id["email.configure"].primary_route.input_fields}
    for f in ("workflow_trigger_enabled", "workflow_id", "workflow_filter_rules"):
        assert f in names, f"email.configure lost '{f}' (F5 regression — capability unreachable again)"


# ── Source-contract guards: the fix lines whose deletion re-opens a silent success ──
def test_wiring_normalizer_registered_as_after_request():
    assert "@app.after_request\ndef _normalize_internal_failure_status" in _src(APP_PY), \
        "normalizer lost its @app.after_request registration — Phase 1 silently disabled"


def test_wiring_executor_sends_marker_header():
    assert "X-AIHub-Internal-Exec" in _src(_EXECUTOR_PY), \
        "executor no longer sends the internal-exec marker header — Phase 1 normalizer never fires"


def test_wiring_execute_step_invokes_verify_write():
    assert "_verify_write(" in _src(_EXECUTOR_PY), \
        "execute_step no longer calls _verify_write — Phase 2 read-back unwired"


def test_wiring_compiler_threads_real_is_valid():
    assert 'result["is_valid"] = is_valid' in _src(_WF_COMPILER_PY), \
        "compiler no longer threads the real is_valid — F2 draft path dead, invalid workflows 'ready to use'"


def test_wiring_compile_route_uses_status_mapper():
    assert "_compile_outcome_status(" in _src(ROUTES_PY), \
        "compile route no longer uses the three-way status mapper — F2"


def test_wiring_cc_build_node_appends_footer():
    s = _src(CC_NODES_PY)
    assert "_verification_footer(" in s and "response_text = response_text + _ver_footer" in s, \
        "CC build node no longer appends the deterministic verification footer — Phase 4 honesty guarantee gone"


def test_wiring_builder_messaging_has_draft_branch():
    assert 'get("status") == "draft"' in _src(_BUILDER_NODES_PY), \
        "builder messaging lost the draft branch — invalid workflows report 'ready to use' again (F2)"


# ═══════════════════════════════════════════════════════════════════════════
# Phase 6 — Bucket A (residual silent-success in the layers Phase 0-4 didn't reach)
# ═══════════════════════════════════════════════════════════════════════════

# ── W1 (#8): a swallowed agent timeout/failure becomes a FAILED delegation ──
_conv_fail = _load_functions(_BUILDER_NODES_PY, ["_conversation_failure_result"])["_conversation_failure_result"]


class _FakeStatus:
    def __init__(self, v):
        self.value = v


class _FakeConv:
    def __init__(self, status_value, error=None):
        self.status = _FakeStatus(status_value) if status_value else None
        self.error = error


class _FakeMgr:
    def __init__(self, conv):
        self._c = conv

    def get_conversation(self, cid):
        return self._c


@pytest.mark.parametrize("status,expect_fail", [
    ("timeout", True),
    ("failed", True),
    ("active", False),
    ("completed", False),
    ("waiting_for_user", False),
])
def test_phase6_w1_conversation_failure(status, expect_fail):
    r = _conv_fail(_FakeMgr(_FakeConv(status, error="boom")), "c1", "agent-x")
    if expect_fail:
        assert r is not None and r["success"] is False and r["agent_id"] == "agent-x"
        assert "boom" in r["error"]
    else:
        assert r is None


def test_phase6_w1_none_conversation_is_not_a_failure():
    assert _conv_fail(_FakeMgr(None), "c1", "a") is None


def test_phase6_w1_delegation_checks_conversation_failure():
    # wired at both the post-primary-send and pre-final-return call sites
    assert _src(_BUILDER_NODES_PY).count(
        "_conversation_failure_result(manager, conversation.id, agent_id)") >= 2, \
        "agent delegation no longer checks for terminal conversation failure (#8 regression)"


# ── W5 (#17): deterministic success message when the distiller LLM crashes ──
_w5 = _load_functions(CC_NODES_PY, ["_deterministic_builder_summary", "_extract_created_resources",
                                    "_plan_has_unready_artifact"])
_det_summary = _w5["_deterministic_builder_summary"]
_extract_cr = _w5["_extract_created_resources"]
_unready = _w5["_plan_has_unready_artifact"]


def test_phase6_w5_pure_success_names_created_resource():
    plan = {"steps": [{"status": "completed", "description": "create agent Bot",
                       "result": {"verified": True, "verification_detail": "present",
                                  "data": {"agent_id": 5, "agent_description": "Bot"}}}]}
    summary = {"verified": [{"label": "create agent Bot"}], "failed": [], "unverified": []}
    msg = _det_summary(summary, plan)
    assert msg is not None and "✅" in msg and "Bot" in msg


def test_phase6_w5_pure_success_without_resources():
    summary = {"verified": [{"label": "assign tools"}], "failed": [], "unverified": []}
    msg = _det_summary(summary, {"steps": []})
    assert msg is not None and "✅" in msg and "assign tools" in msg


def test_phase6_w5_mixed_outcome_is_neutral_leadin():
    summary = {"verified": [{"label": "a"}], "failed": [{"label": "b"}], "unverified": []}
    msg = _det_summary(summary, {"steps": []})
    assert msg is not None and "✅" not in msg   # neutral; the footer carries the ❌


def test_phase6_w5_nothing_verifiable_returns_none():
    # draft/read-only turn -> None -> caller keeps its generic fallback
    assert _det_summary({"verified": [], "failed": [], "unverified": []}, {"steps": []}) is None


def test_wiring_w5_distiller_fallback_uses_deterministic_summary():
    assert "_deterministic_builder_summary(verification_summary, latest_plan)" in _src(CC_NODES_PY), \
        "distiller fallback no longer tries the deterministic success summary (#17 regression)"


# ── W3b (#14): the classifier must not close a failed compile as 'completed' ──
_stay_active = _load_functions(
    _BUILDER_NODES_PY, ["_workflow_turn_should_stay_active"])["_workflow_turn_should_stay_active"]


@pytest.mark.parametrize("agent_id,phase,has_def,compile_status,expected", [
    ("workflow_agent", "planning", False, None, True),      # plan-only — keep active
    ("workflow_agent", "building", False, "error", True),   # #14: failed compile — keep active
    ("workflow_agent", "complete", False, "error", True),   # #14: phase-agnostic
    ("workflow_agent", "building", False, "failed", True),  # #14: other failure status
    ("workflow_agent", "building", False, "success", False),  # genuinely done — close
    ("workflow_agent", "building", True, "draft", False),   # draft is a terminal compile turn (Phase 3)
    ("workflow_agent", "planning", True, "success", False),   # definitive success — close
    ("some_other_agent", "building", False, "error", False),  # non-workflow unaffected
])
def test_phase6_w3b_stay_active(agent_id, phase, has_def, compile_status, expected):
    assert _stay_active(agent_id, phase, has_def, compile_status) is expected


def test_wiring_w3b_classifier_branch_uses_stay_active_guard():
    assert "_workflow_turn_should_stay_active(" in _src(_BUILDER_NODES_PY), \
        "classifier-completion branch no longer guards on compile status — a failed compile can close the thread (#14)"


def test_wiring_w3b2_keep_active_precedes_completion_branch():
    # #14 second path: the manager phrase-matcher can set status='completed' on prose; the
    # keep-active guard must be evaluated BEFORE the status=='completed'/has_definitive
    # completion branch so a failed workflow compile isn't closed regardless of that status.
    s = _src(_BUILDER_NODES_PY)
    i_keep = s.find("if _keep_active:")
    i_done = s.find('updated_conversation.status.value == "completed" or has_definitive_result')
    assert i_keep != -1 and i_done != -1 and i_keep < i_done, \
        "keep-active guard no longer precedes the completion branch — a phrase-matched 'completed' can close a failed compile (#14 second path)"


# ── W4 (#18): mcp.create_server verifier asserts the created row's TYPE, not just name ──
def test_phase6_w4_mcp_create_type_mismatch_disproved():
    servers = [{"server_name": "S1", "server_url": "http://a", "server_type": "local"}]
    assert V._check_mcp_create(
        {"server_name": "S1", "server_url": "http://a", "server_type": "remote"}, {}, servers)[0] == V.DISPROVED


def test_phase6_w4_mcp_create_type_match_confirmed():
    servers = [{"server_name": "S1", "server_url": "http://a", "server_type": "remote"}]
    assert V._check_mcp_create(
        {"server_name": "S1", "server_url": "http://a", "server_type": "remote"}, {}, servers)[0] == V.CONFIRMED


def test_phase6_w4_mcp_create_default_remote_vs_local_disproved():
    # planner omits server_type -> expected defaults to 'remote'; row written 'local' -> DISPROVED
    servers = [{"server_name": "S1", "server_url": "http://a", "server_type": "local"}]
    assert V._check_mcp_create({"server_name": "S1", "server_url": "http://a"}, {}, servers)[0] == V.DISPROVED


def test_phase6_w4_mcp_create_missing_type_field_still_confirms():
    # row has no server_type key -> don't false-DISPROVE on missing read-back data
    servers = [{"server_name": "S1", "server_url": "http://a"}]
    assert V._check_mcp_create(
        {"server_name": "S1", "server_url": "http://a", "server_type": "remote"}, {}, servers)[0] == V.CONFIRMED


def test_phase6_w4_mcp_create_server_defaults_remote_in_schema():
    by_id = _platform_actions_by_id()
    fields = {f.name: getattr(f, "default", None)
              for f in by_id["mcp.create_server"].primary_route.input_fields}
    assert fields.get("server_type") == "remote", \
        "mcp.create_server server_type no longer defaults to 'remote' — omitting it writes a broken local row (#18)"


# ── W5b (#17 closer): distiller-crash success message covers workflow builds too ──
# WorkflowAgent builds carry saved_workflow_id at the delegation-result TOP LEVEL and are
# NOT in VERIFICATION_SPECS, so the original W5 (verification-only) missed them.
_WF_CLEAN = {"status": "completed", "steps": [
    {"status": "completed", "description": "build workflow",
     "result": {"saved_workflow_id": 42, "saved_workflow_name": "Invoice WF",
                "compile_result": {"status": "success"}}}]}
_WF_DRAFT = {"status": "completed", "steps": [
    {"status": "completed", "description": "build workflow",
     "result": {"saved_workflow_id": 42, "saved_workflow_name": "Invoice WF",
                "saved_as_draft": True, "compile_result": {"status": "draft"}}}]}
_EMPTY_SUMMARY = {"verified": [], "failed": [], "unverified": []}


def test_phase6_w5b_extract_finds_toplevel_workflow_id():
    res = _extract_cr(_WF_CLEAN)
    assert any(r["type"] == "workflow" and str(r["id"]) == "42" for r in res), \
        "workflow builds (top-level saved_workflow_id) are not recorded as created resources"


def test_phase6_w5b_clean_workflow_build_announced_without_verification():
    msg = _det_summary(_EMPTY_SUMMARY, _WF_CLEAN)     # empty summary (workflows not spec'd)
    assert msg is not None and "✅" in msg and "Invoice WF" in msg


def test_phase6_w5b_draft_workflow_not_announced_as_success():
    # fail-closed: a saved DRAFT has an id + non-failed status, but must NOT be "✅ Created"
    assert _det_summary(_EMPTY_SUMMARY, _WF_DRAFT) is None


def test_phase6_w5b_failed_plan_not_announced():
    plan = {"status": "failed", "steps": [{"status": "failed", "description": "x", "result": {}}]}
    assert _det_summary(_EMPTY_SUMMARY, plan) is None


def test_phase6_w5b_unready_artifact_detector():
    assert _unready(_WF_DRAFT) is True
    assert _unready(_WF_CLEAN) is False


# ── W3a (#15): converse/gather_data build consumers derive honest build_status ──
_derive_bs = _load_functions(
    CC_NODES_PY, ["_derive_build_state", "_extract_created_resources"])["_derive_build_state"]


def _bresult(plan_status, steps=None, sid="b-1"):
    r = {"text": "ok", "status": "completed", "builder_session_id": sid}
    if plan_status is not None:
        r["plan"] = {"status": plan_status, "steps": steps or []}
    return r


@pytest.mark.parametrize("plan_status,expected", [
    ("completed", "completed"),
    ("partial", "partial"),
    ("failed", "failed"),
    ("draft", "in_progress"),        # not a terminal build_status -> stays in_progress
    ("delegated", "in_progress"),
    (None, "in_progress"),           # no plan
])
def test_phase6_w3a_build_status_from_plan(plan_status, expected):
    assert _derive_bs(_bresult(plan_status))["build_status"] == expected


def test_phase6_w3a_none_result_is_in_progress():
    assert _derive_bs(None)["build_status"] == "in_progress"


def test_phase6_w3a_captures_builder_session_id():
    assert _derive_bs(_bresult("completed", sid="xyz"))["builder_session_id"] == "xyz"


def test_phase6_w3a_completed_extracts_resources_and_timestamp():
    steps = [{"status": "completed", "description": "create agent",
              "result": {"verified": True, "data": {"agent_id": 7, "agent_description": "Bot"}}}]
    st = _derive_bs(_bresult("completed", steps=steps))
    assert st["completed_at"] is not None
    assert any(r["type"] == "agent" and r["id"] == 7 for r in st["created_resources"])


def test_wiring_w3a_consumers_derive_build_state():
    # both the converse tool-result handler and the gather_data continuation must derive
    assert _src(CC_NODES_PY).count("_derive_build_state(") >= 2, \
        "converse/gather_data build consumers no longer derive build_status from the plan (#15 regression)"


def test_wiring_w3a_tool_surfaces_plan():
    assert '_builder_capture["result"] = result' in _src(CC_NODES_PY), \
        "delegate_to_builder_agent tool no longer surfaces the plan to its consumer (#15)"


# ── W2 step-2 (#6/#10): validator flags unknown/unimplemented node types -> is_valid False ──
_wfval = _load_module(os.path.join(_REPO, "workflow_deterministic_validator.py"), "ss_wf_validator")
# Seed the canonical-set cache so the detector logic is exercised independently of importing
# the (large) system_prompts module. Deliberately excludes 'Server', includes 'Portal'.
_wfval._KNOWN_NODE_TYPES_CACHE = {"Database", "AI Action", "Portal", "Human Approval", "Integration"}


def test_phase6_w2_unknown_node_type_flagged():
    issues = _wfval.detect_unknown_node_type({"nodes": [{"type": "Server", "id": "n1"}]})
    assert len(issues) == 1 and issues[0].code == "UNKNOWN_NODE_TYPE" and issues[0].severity == "error"


def test_phase6_w2_hallucinated_type_flagged():
    assert len(_wfval.detect_unknown_node_type({"nodes": [{"type": "MagicNode", "id": "n2"}]})) == 1


def test_phase6_w2_known_types_not_flagged():
    for t in ("Database", "Portal", "Human Approval", "Integration"):
        assert _wfval.detect_unknown_node_type({"nodes": [{"type": t, "id": "n"}]}) == [], f"{t} wrongly flagged"


def test_phase6_w2_missing_type_skipped():
    assert _wfval.detect_unknown_node_type({"nodes": [{"id": "n"}]}) == []


def test_phase6_w2_disabled_when_canonical_unavailable():
    saved = _wfval._KNOWN_NODE_TYPES_CACHE
    _wfval._KNOWN_NODE_TYPES_CACHE = set()   # simulate import failure -> detector self-disables
    try:
        assert _wfval.detect_unknown_node_type({"nodes": [{"type": "Server", "id": "n1"}]}) == []
    finally:
        _wfval._KNOWN_NODE_TYPES_CACHE = saved


def test_phase6_w2_unknown_node_is_unfixable_error():
    # end-to-end through run(): an unknown node -> unfixable_errors (=> caller sets is_valid False)
    res = _wfval.run({"nodes": [{"type": "Server", "id": "n1", "isStart": True}], "connections": []})
    assert any(i.code == "UNKNOWN_NODE_TYPE" for i in res.unfixable_errors)


def test_phase6_w2_registered_and_has_no_fixer():
    assert _wfval.detect_unknown_node_type in _wfval.DETECTORS
    assert "UNKNOWN_NODE_TYPE" not in _wfval.FIXERS   # no fixer -> stays unfixable -> is_valid False


def test_phase6_w2_canonical_list_has_portal_not_server():
    import importlib
    import sys as _sys
    if _REPO not in _sys.path:
        _sys.path.insert(0, _REPO)
    sp = importlib.import_module("system_prompts")
    assert "Portal" in sp.VALID_WORKFLOW_NODE_TYPES, "Portal (implemented) missing from canonical list"
    assert "Server" not in sp.VALID_WORKFLOW_NODE_TYPES, "Server (no runtime handler) still in canonical list"


# ── W2 step-4 (#6/#10): runtime fails an unimplemented node instead of no-op success ──
def test_wiring_w2_step4_runtime_fails_unimplemented_node():
    s = _src(os.path.join(_REPO, "workflow_execution.py"))
    # the unimplemented-node else branch must now yield a FAILURE the (~584) check raises on
    assert "is not implemented by the workflow engine" in s and \
        "result = {'success': False, 'error': msg}" in s, \
        "runtime no longer fails unimplemented node types (#6/#10 step 4 — silent no-op returns)"
    # the old passing no-op for unimplemented types must be gone
    assert "Node type '{node_type}' not implemented yet" not in s, \
        "runtime still has the old 'not implemented yet' -> success:True no-op path (#6/#10)"


# ═══════════════════════════════════════════════════════════════════════════
# Round-2 residual closers — #8 timeout-clobber, #17 plan-only compile id,
# #18 dropped transport_type, #11 email auto-reply approval stub
# ═══════════════════════════════════════════════════════════════════════════
from datetime import datetime as _datetime  # noqa: E402

_MODELS_PY = os.path.join(_REPO, "builder_service", "agent_communication", "models.py")
_EMAIL_DISPATCHER_PY = os.path.join(_REPO, "email_agent_dispatcher.py")


def _load_method(path: str, method_name: str, extra_globals=None):
    """AST-extract a single METHOD by name (from any class) and exec it as a bare
    function — same no-heavy-import philosophy as _load_functions, but reaches inside a
    ClassDef. The returned callable still takes `self` as its first positional arg."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    ns = {k: getattr(typing, k) for k in ("Optional", "Dict", "Any", "List", "Tuple", "Callable")}
    if extra_globals:
        ns.update(extra_globals)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name:
            node.decorator_list = []
            exec(compile(ast.Module(body=[node], type_ignores=[]), path, "exec"), ns)
            return ns[method_name]
    raise AssertionError(f"method {method_name} not found in {path}")


# ── #8: mark_waiting_for_user must not downgrade a terminal TIMEOUT/FAILED ──
class _FakeConvStatus:                         # str-valued members mirror the real (str, Enum)
    ACTIVE = "active"
    WAITING_FOR_USER = "waiting_for_user"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


_mark_waiting = _load_method(
    _MODELS_PY, "mark_waiting_for_user",
    {"ConversationStatus": _FakeConvStatus, "datetime": _datetime})


class _Conv:
    def __init__(self, status):
        self.status = status
        self.pending_question = None
        self.updated_at = None


@pytest.mark.parametrize("status,sticks", [
    (_FakeConvStatus.TIMEOUT, True),
    (_FakeConvStatus.FAILED, True),
    (_FakeConvStatus.ACTIVE, False),
    (_FakeConvStatus.WAITING_FOR_USER, False),
])
def test_residual8_mark_waiting_preserves_terminal_status(status, sticks):
    conv = _Conv(status)
    _mark_waiting(conv, "please advise")
    if sticks:
        assert conv.status == status, (
            "mark_waiting_for_user clobbered a terminal failure state (#8) — the auto-reply "
            "timeout gets masked and the delegation falsely reports success with no build")
    else:
        assert conv.status == _FakeConvStatus.WAITING_FOR_USER


def test_residual8_guard_present_in_source():
    s = _src(_MODELS_PY)
    assert "ConversationStatus.TIMEOUT, ConversationStatus.FAILED" in s and \
        "def mark_waiting_for_user" in s, \
        "the terminal-status guard in mark_waiting_for_user was removed (#8)"


# ── #17: plan-only compile path stores the workflow id under compile_result ──
_WF_COMPILE_OK = {"status": "completed", "steps": [
    {"status": "completed", "description": "build workflow",
     "result": {"compile_result": {"status": "success", "workflow_id": 77,
                                    "workflow_name": "ERP Export WF"}}}]}
_WF_COMPILE_DRAFT = {"status": "completed", "steps": [
    {"status": "completed", "description": "build workflow",
     "result": {"compile_result": {"status": "draft", "workflow_id": 77,
                                    "workflow_name": "ERP Export WF"}}}]}


def test_residual17_extract_finds_compile_result_workflow_id():
    res = _extract_cr(_WF_COMPILE_OK)
    assert any(r["type"] == "workflow" and str(r["id"]) == "77" and r["name"] == "ERP Export WF"
               for r in res), \
        "plan-only build (id under compile_result.workflow_id) is not recorded as created (#17)"


def test_residual17_clean_compile_only_announced_on_distiller_crash():
    msg = _det_summary(_EMPTY_SUMMARY, _WF_COMPILE_OK)     # empty summary => no-verification branch
    assert msg is not None and "✅" in msg and "ERP Export WF" in msg, \
        "a clean plan-only workflow build still shows the generic apology on a distiller crash (#17)"


def test_residual17_draft_compile_result_not_recorded_or_announced():
    assert not any(r["type"] == "workflow" for r in _extract_cr(_WF_COMPILE_DRAFT)), \
        "a DRAFT compile must not be surfaced as a created workflow (#17 fail-closed)"
    assert _det_summary(_EMPTY_SUMMARY, _WF_COMPILE_DRAFT) is None


# ── #18: the silently-dropped transport_type must be gone from both MCP actions ──
def test_residual18_transport_type_removed_from_mcp_create():
    by_id = _platform_actions_by_id()
    names = {f.name for f in by_id["mcp.create_server"].primary_route.input_fields}
    assert "transport_type" not in names, \
        "mcp.create_server still collects transport_type, silently dropped by the handler (#18)"


def test_residual18_transport_type_removed_from_mcp_test():
    by_id = _platform_actions_by_id()
    names = {f.name for f in by_id["mcp.test_server"].primary_route.input_fields}
    assert "transport_type" not in names, \
        "mcp.test_server still collects transport_type, silently dropped by the handler (#18)"


# ── #11: email auto-reply notification is honest across sent / not-sent / pending ──
# (The former _queue_for_approval honesty-stub was REMOVED once the Agent Email
# Approvals feature was rebuilt — queuing now happens inside
# agent_email_send.send_agent_email, covered by
# tests/unit/test_email_agent_dispatcher.py::TestAutoResponseGate.)
def test_residual11_auto_reply_notification_is_outcome_honest():
    s = _src(_EMAIL_DISPATCHER_PY)
    assert "An auto-reply was NOT sent by" in s and "elif result.get('success'):" in s, \
        "auto-reply notification no longer distinguishes sent vs could-not-send — would claim 'sent' on failure (#11)"


def test_residual11_dispatcher_routes_through_chokepoint():
    # the rebuilt dispatcher must queue/send via the agent_email_send chokepoint,
    # not the old direct-send/stub path (the "undo #11" wiring).
    s = _src(_EMAIL_DISPATCHER_PY)
    assert "from agent_email_send import send_agent_email" in s and "send_agent_email(" in s, \
        "dispatcher no longer routes auto-replies through send_agent_email (#11 rebuild)"
    assert "def _queue_for_approval" not in s, \
        "the dead _queue_for_approval stub should have been removed when the feature was rebuilt"


# ═══════════════════════════════════════════════════════════════════════════
# Agent Email Approvals rebuild — accessible_agent_ids authz (fail-closed)
# ═══════════════════════════════════════════════════════════════════════════
_DATAUTILS_PY = os.path.join(_REPO, "DataUtils.py")


def _load_accessible(pyodbc_stub):
    return _load_functions(
        _DATAUTILS_PY, ["accessible_agent_ids"],
        {"pyodbc": pyodbc_stub, "os": os,
         "database_server": "s", "database_name": "d", "username": "u", "password": "p"},
    )["accessible_agent_ids"]


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
    def execute(self, *a, **k):
        return None
    def fetchall(self):
        return self._rows
    def close(self):
        return None


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows
    def cursor(self):
        return _FakeCursor(self._rows)
    def close(self):
        return None


class _FakePyodbc:
    def __init__(self, rows=None, raise_on_connect=False):
        self._rows = rows or []
        self._raise = raise_on_connect
    def connect(self, *a, **k):
        if self._raise:
            raise RuntimeError("db down")
        return _FakeConn(self._rows)


@pytest.mark.parametrize("role", [3, "3", 4, 9])
def test_accessible_agent_ids_admin_returns_none(role):
    # admin (role >= 3) -> None (no filter), and MUST NOT touch the DB
    fn = _load_accessible(_FakePyodbc(raise_on_connect=True))
    assert fn(42, role) is None


def test_accessible_agent_ids_scoped_user_returns_group_ids():
    fn = _load_accessible(_FakePyodbc(rows=[(7,), (9,), (12,)]))
    assert fn(42, 1) == [7, 9, 12]


def test_accessible_agent_ids_no_groups_is_deny_all():
    fn = _load_accessible(_FakePyodbc(rows=[]))
    assert fn(42, 1) == []            # empty list = deny-all (fail-closed), NOT None


@pytest.mark.parametrize("role", [None, "", "bad"])
def test_accessible_agent_ids_unknown_role_is_scoped_not_admin(role):
    # a missing/garbage role must be treated as NON-admin (fail-closed), so it hits
    # the group query rather than returning None (all-access).
    fn = _load_accessible(_FakePyodbc(rows=[(5,)]))
    assert fn(42, role) == [5]


def test_accessible_agent_ids_db_error_fails_closed():
    fn = _load_accessible(_FakePyodbc(raise_on_connect=True))
    assert fn(42, 1) == []            # error -> [] (deny-all), never None (all-access)


# ═══════════════════════════════════════════════════════════════════════════
# Bucket-B #9 — created_resources registry (was dead: `updated_plan.steps` on a dict)
# ═══════════════════════════════════════════════════════════════════════════
_collect_cr = _load_functions(_BUILDER_NODES_PY, ["_collect_created_resources"])["_collect_created_resources"]


def test_bugb9_registers_agent_from_data():
    reg = _collect_cr({}, [{"result": {"data": {"agent_id": 7, "agent_description": "Sales Bot"}}}])
    assert reg.get("agents") == [{"id": 7, "name": "Sales Bot"}]


def test_bugb9_registers_workflow_from_toplevel_saved_id():
    # WorkflowAgent builds put saved_workflow_id at the delegation-result TOP LEVEL,
    # not under .data — the pre-fix .data-only scan (even with .steps fixed) missed it.
    steps = [{"result": {"success": True, "agent_id": "workflow_agent",
                         "saved_workflow_id": 42, "saved_workflow_name": "Invoice WF"}}]
    reg = _collect_cr({}, steps)
    ids = {r.get("id") for r in reg.get("workflows", [])}
    names = {r.get("name") for r in reg.get("workflows", [])}
    assert 42 in ids and "Invoice WF" in names


def test_bugb9_delegate_agent_not_registered_as_created():
    # a delegation result's TOP-LEVEL agent_id is the DELEGATE agent, not a created one
    steps = [{"result": {"success": True, "agent_id": "workflow_agent", "saved_workflow_id": 42}}]
    reg = _collect_cr({}, steps)
    assert not reg.get("agents"), "delegate agent wrongly registered as a created agent"


def test_bugb9_merges_with_prior_and_dedups():
    prior = {"agents": [{"id": 7, "name": "Sales Bot"}]}
    steps = [{"result": {"data": {"agent_id": 7, "agent_description": "Sales Bot"}}},
             {"result": {"data": {"connection_id": 5, "connection_name": "AIRDB"}}}]
    reg = _collect_cr(prior, steps)
    assert reg["agents"] == [{"id": 7, "name": "Sales Bot"}]          # de-duped, not doubled
    assert reg["connections"] == [{"id": 5, "name": "AIRDB"}]
    assert prior["agents"] == [{"id": 7, "name": "Sales Bot"}]        # prior NOT mutated


def test_bugb9_malformed_steps_are_safe():
    assert _collect_cr({}, []) == {}
    assert _collect_cr({}, [{"result": None}, {"result": "oops"}, "not-a-step", {}]) == {}


def test_bugb9_execute_uses_helper_not_attribute_access():
    s = _src(_BUILDER_NODES_PY)
    assert "updated_plan.steps" not in s, \
        "execute still does attribute access on a dict plan (#9 AttributeError) — registry stays dead"
    # helper is DEFINED once + called from execute + called from handle_agent_response
    assert s.count("_collect_created_resources(") >= 3, \
        "registry helper not wired into both execute and the delegation-completion path (#9)"


# ═══════════════════════════════════════════════════════════════════════════
# Bucket-B #5 — one agent timeout/error must NOT brick the builder session
# ═══════════════════════════════════════════════════════════════════════════
_EDGES_PY = os.path.join(_REPO, "builder_service", "graph", "edges.py")
_terminalize = _load_functions(
    _BUILDER_NODES_PY, ["_terminalize_agent_conversation"])["_terminalize_agent_conversation"]
_route_by_intent = _load_functions(
    _EDGES_PY, ["route_by_intent"], {"logger": logging.getLogger("t")})["route_by_intent"]


def test_bug5_terminalize_clears_id_and_fails_bound_step():
    state = {
        "current_agent_conversation_id": "c1",
        "agent_conversations": {"c1": {"status": "waiting_for_user", "pending_question": "?"}},
        "current_plan": {"steps": [
            {"order": 1, "status": "delegated", "result": {"conversation_id": "c1"}},
            {"order": 2, "status": "pending", "result": {}},
        ]},
    }
    upd = _terminalize(state, "c1", "timeout")
    assert upd["current_agent_conversation_id"] is None
    assert upd["agent_conversations"]["c1"]["status"] == "timeout"
    assert upd["agent_conversations"]["c1"]["pending_question"] is None
    steps = upd["current_plan"]["steps"]
    assert steps[0]["status"] == "failed", "the conversation's plan step must be failed"
    assert steps[1]["status"] == "pending", "an unrelated step must be untouched"
    # prior state must NOT be mutated (execute's resumption guard reads the old plan)
    assert state["agent_conversations"]["c1"]["status"] == "waiting_for_user"
    assert state["current_plan"]["steps"][0]["status"] == "delegated"


def test_bug5_terminalize_coerces_unknown_status_to_failed():
    upd = _terminalize({"agent_conversations": {}}, "c9", "weird")
    assert upd["agent_conversations"]["c9"]["status"] == "failed"


def test_bug5_terminalize_handles_missing_plan():
    upd = _terminalize({}, "c1", "failed")
    assert upd["current_agent_conversation_id"] is None
    assert "current_plan" not in upd


@pytest.mark.parametrize("status", ["failed", "timeout"])
def test_bug5_router_escapes_terminal_conversation(status):
    # a terminal conversation must NOT loop back into handle_agent_response forever
    state = {"intent": "build", "current_plan": None,
             "current_agent_conversation_id": "c1", "pending_agent_question": None,
             "agent_conversations": {"c1": {"status": status}}}
    assert _route_by_intent(state) != "handle_agent_response"


def test_bug5_router_still_forwards_active_pause():
    # a genuine mid-conversation pause is still forwarded (don't over-correct)
    state = {"intent": "build", "current_plan": None,
             "current_agent_conversation_id": "c1", "pending_agent_question": None,
             "agent_conversations": {"c1": {"status": "waiting_for_user"}}}
    assert _route_by_intent(state) == "handle_agent_response"


def test_bug5_router_no_loop_when_id_cleared():
    state = {"intent": "build", "current_plan": None,
             "current_agent_conversation_id": None, "pending_agent_question": None,
             "agent_conversations": {}}
    assert _route_by_intent(state) == "analyze_and_plan"


def test_bug5_handler_releases_on_failure_and_timeout():
    s = _src(_BUILDER_NODES_PY)
    # def + happy-path (Variant B) + except handler (Variant A)
    assert s.count("_terminalize_agent_conversation(") >= 3, \
        "handle_agent_response doesn't release a dead conversation on error/timeout (#5)"


# ── #5 THIRD path — state 'active' + manager COMPLETED (the #14 interaction) ──
_reopen = _load_method(_MODELS_PY, "reopen_for_followup",
                       {"ConversationStatus": _FakeConvStatus, "datetime": _datetime})


@pytest.mark.parametrize("status,expected", [
    (_FakeConvStatus.COMPLETED, _FakeConvStatus.ACTIVE),   # phrase-matched completed → reopened
    (_FakeConvStatus.TIMEOUT, _FakeConvStatus.TIMEOUT),    # terminal failures stay sticky (#8)
    (_FakeConvStatus.FAILED, _FakeConvStatus.FAILED),
    (_FakeConvStatus.ACTIVE, _FakeConvStatus.ACTIVE),      # no-op when already open
    (_FakeConvStatus.WAITING_FOR_USER, _FakeConvStatus.WAITING_FOR_USER),
])
def test_bug5c_reopen_only_from_completed(status, expected):
    conv = _Conv(status)
    conv.updated_at = None
    _reopen(conv)
    assert conv.status == expected


def test_bug5c_keep_active_sites_reconcile_manager():
    s = _src(_BUILDER_NODES_PY)
    assert s.count("reopen_for_followup()") >= 2, \
        "keep-active sites no longer reconcile a phrase-matched COMPLETED manager (#5 third path re-bricks)"


def test_bug5c_except_belt_releases_on_completed_mismatch():
    s = _src(_BUILDER_NODES_PY)
    assert '_live in ("timeout", "failed", "completed")' in s, \
        "except-handler belt no longer releases on the state-active/manager-COMPLETED mismatch (#5 third path)"


# ── Bucket-B #11 — still-gathering auto-reply must set needs_user_input ──
def test_bug11_needs_input_derived_from_waiting_status():
    s = _src(_BUILDER_NODES_PY)
    assert 'conversation_status == "waiting_for_user"' in s and "conversation_needs_input = (" in s, \
        "needs_user_input no longer derived from the final conversation_status (#11) — plan won't pause"


# ── Bucket-B #12 — plan goal = real user request, not the internal re-plan prompt ──
from types import SimpleNamespace as _NS  # noqa: E402

_goal_from = _load_functions(_BUILDER_NODES_PY, ["_goal_from_messages"])["_goal_from_messages"]


def _h(c):
    return _NS(type="human", content=c)


def test_bug12_normal_flow_uses_human_request():
    assert _goal_from([_h("build a workflow that emails the finance team")]) \
        == "build a workflow that emails the finance team"


def test_bug12_confirm_yes_reflow_skips_system_prompt_and_bare_yes():
    msgs = [_h("build a workflow that pulls invoices and emails them"),
            _NS(type="ai", content="Here's a plan..."),
            _h("yes"),
            _NS(type="system", content="IMPORTANT: Your previous response was not parsed into an executable plan. ...")]
    goal = _goal_from(msgs)
    assert "IMPORTANT" not in goal and goal != "yes"
    assert goal == "build a workflow that pulls invoices and emails them"


def test_bug12_dict_form_user_message():
    assert _goal_from([{"role": "user", "content": "do the thing"}]) == "do the thing"


def test_bug12_empty_and_all_confirmations():
    assert _goal_from([]) == ""
    assert _goal_from([_h("yes")]) == "yes"                 # only a confirmation → best available
    assert _goal_from([_h("Yes."), _h("ok")]) == "ok"       # all confirmations → most recent human


def test_bug12_compound_confirmation_is_substantive():
    # "yes, also add logging" is a real instruction, not a bare confirmation
    assert _goal_from([_h("build X"), _h("yes, also add logging")]) == "yes, also add logging"


# ── #12b — mini-LLM goal extraction (skip-list becomes the fallback only) ──
def _mk_goal_resolver(reply=None, raise_err=False, calls=None):
    class _Msg:
        def __init__(self, content):
            self.content = content

    async def fake_invoke(llm, msgs):
        if calls is not None:
            calls.append(msgs)
        if raise_err:
            raise RuntimeError("llm down")
        return _Msg(reply)

    return _load_functions(
        _BUILDER_NODES_PY, ["_resolve_goal_from_messages"],
        {"get_llm": lambda **k: object(), "safe_llm_invoke": fake_invoke,
         "SystemMessage": _Msg, "HumanMessage": _Msg,
         "logger": logging.getLogger("t"), "_goal_from_messages": _goal_from},
    )["_resolve_goal_from_messages"]


def test_bug12b_llm_consolidation_used_even_for_unlisted_confirmation():
    # the tester's residual: "go for it" isn't in the skip-list — the LLM handles it
    msgs = [_h("build a workflow that pulls invoices and emails finance"),
            _NS(type="ai", content="plan..."), _h("go for it")]
    fn = _mk_goal_resolver(reply="build a workflow that pulls invoices and emails finance")
    assert asyncio.run(fn(msgs)) == "build a workflow that pulls invoices and emails finance"


def test_bug12b_llm_failure_falls_back_deterministically():
    msgs = [_h("build a workflow that pulls invoices"), _h("yes")]
    fn = _mk_goal_resolver(raise_err=True)
    assert asyncio.run(fn(msgs)) == "build a workflow that pulls invoices"   # fallback skip-list
    fn_empty = _mk_goal_resolver(reply="   ")
    assert asyncio.run(fn_empty(msgs)) == "build a workflow that pulls invoices"


def test_bug12b_single_human_message_skips_llm():
    calls = []
    fn = _mk_goal_resolver(reply="SHOULD NOT BE USED", calls=calls)
    assert asyncio.run(fn([_h("just build X")])) == "just build X"
    assert calls == [], "single human message must not spend an LLM call"


def test_bug12b_llm_sees_only_human_messages():
    calls = []
    msgs = [_h("build the invoice workflow"),
            _NS(type="ai", content="AI-PLAN-TEXT"),
            _h("yes"),
            _NS(type="system", content="IMPORTANT: Your previous response was not parsed...")]
    fn = _mk_goal_resolver(reply="build the invoice workflow", calls=calls)
    asyncio.run(fn(msgs))
    sent = " ".join(m.content for m in calls[0])
    assert "IMPORTANT" not in sent and "AI-PLAN-TEXT" not in sent, \
        "non-human content leaked into the goal-extraction prompt (#12b)"
    assert "build the invoice workflow" in sent


def test_bug12b_wired_into_analyze_and_plan():
    assert "await _resolve_goal_from_messages(messages)" in _src(_BUILDER_NODES_PY), \
        "analyze_and_plan no longer derives the goal via the mini-LLM extractor (#12b)"


# ── #12 round-2 closers — completed skip-list + error-blob guard on the fallback path ──
@pytest.mark.parametrize("phrase", [
    "let's do it", "Let's do it!", "let’s do it",   # incl. curly apostrophe
    "go for it", "perfect", "Perfect.", "sounds great", "ship it", "lgtm", "works for me",
])
def test_bug12c_fallback_skiplist_covers_retest_phrases(phrase):
    msgs = [_h("build a workflow that pulls invoices"), _h(phrase)]
    assert _goal_from(msgs) == "build a workflow that pulls invoices", \
        f"fallback skip-list misses confirmation phrase: {phrase!r}"


def test_bug12c_error_blob_never_becomes_goal():
    # safe_llm_invoke's content-filter exhaustion returns a JSON blob with a canned
    # apology — that must fall through to the deterministic fallback, not become the goal
    blob = ('[{"type": "text", "content": "I had trouble processing that request. '
            'Could you try rephrasing or providing more context?"}]')
    msgs = [_h("build a workflow that pulls invoices"), _h("yes")]
    fn = _mk_goal_resolver(reply=blob)
    assert asyncio.run(fn(msgs)) == "build a workflow that pulls invoices"


def test_bug12c_attempt4_wrapped_genuine_reply_is_unwrapped():
    # safe_llm_invoke's attempt-4 retry wraps even SUCCESSFUL replies in the same JSON
    # shape — a genuine wrapped extraction must be unwrapped, not discarded
    wrapped = '[{"type": "text", "content": "build a workflow that pulls invoices"}]'
    msgs = [_h("build a workflow that pulls invoices"), _h("go for it"), _h("perfect")]
    fn = _mk_goal_resolver(reply=wrapped)
    assert asyncio.run(fn(msgs)) == "build a workflow that pulls invoices"


# ── #17 (multi-turn resume) — completed step carries the fresh compile outcome ──
_complete_step = _load_functions(_BUILDER_NODES_PY, ["_complete_step_result"])["_complete_step_result"]


def test_bug17_success_compile_merged_into_step_result():
    step = {"order": 1, "status": "awaiting_input", "result": {"conversation_id": "c1"}}
    agent_result = {"compile_result": {"status": "success", "workflow_id": 42,
                                       "workflow_name": "Invoice WF"}}
    out = _complete_step(step, agent_result)
    assert out["status"] == "completed"
    assert out["result"]["conversation_id"] == "c1"                # prior result preserved
    assert out["result"]["saved_workflow_id"] == 42                # registries scan this
    assert out["result"]["saved_workflow_name"] == "Invoice WF"
    assert out["result"]["compile_result"]["status"] == "success"
    # and the CC-side extractor actually finds it in a plan built from this step
    plan = {"status": "completed", "steps": [out]}
    assert any(r["type"] == "workflow" and str(r["id"]) == "42" for r in _extract_cr(plan))


def test_bug17_draft_compile_attached_but_not_counted_as_created():
    step = {"order": 1, "status": "delegated", "result": {"conversation_id": "c1"}}
    agent_result = {"compile_result": {"status": "draft", "workflow_id": 42}}
    out = _complete_step(step, agent_result)
    assert "saved_workflow_id" not in out["result"], "a DRAFT must not register as created (#17 fail-closed)"
    assert out["result"]["compile_result"]["status"] == "draft"    # draft-safety checks need it
    assert _unready({"steps": [out]}) is True                      # suppresses the ✅ announcement


def test_bug17_no_compile_result_is_safe():
    out = _complete_step({"order": 1, "status": "delegated", "result": {"conversation_id": "c1"}}, None)
    assert out["status"] == "completed" and out["result"] == {"conversation_id": "c1"}


def test_bug17_wired_into_plan_update():
    assert "_complete_step_result(step, agent_result)" in _src(_BUILDER_NODES_PY), \
        "handle_agent_response no longer merges the compile outcome into the completed step (#17)"


# ── Bucket-B #13 — token-bounded id matching + removed integration auto-exec ──
_mentions_id = _load_functions(_BUILDER_NODES_PY, ["_mentions_resource_id"])["_mentions_resource_id"]


@pytest.mark.parametrize("msg,rid", [
    ("show me workflow 12", 12), ("agent 7 email config", 7), ("integration 3?", 3),
    ("details for workflow 42.", 42), ("run #15 now", 15), ("42", 42),
])
def test_bug13_id_token_match_true(msg, rid):
    assert _mentions_id(rid, msg) is True


@pytest.mark.parametrize("msg,rid", [
    ("show me workflow 12", 1),    # the headline false-positive: 1 inside "12"
    ("meet at 10am", 1), ("in 2026 we plan", 2), ("v12 release notes", 12),
    ("1.5x retries", 1), ("using ratio 1.5", 5), ("budget review", 3),
])
def test_bug13_id_token_match_false(msg, rid):
    assert _mentions_id(rid, msg) is False


def test_bug13_id_match_safe_on_edges():
    assert _mentions_id(None, "x") is False
    assert _mentions_id(7, "") is False
    assert _mentions_id(1, ["content-block-list"]) is False   # non-str message must not raise


def test_bug13_query_node_no_live_execution_and_no_substring_matching():
    s = _src(_BUILDER_NODES_PY)
    assert "execute_integration_operation" not in s, \
        "query_and_respond still fires a live integration call from a read turn (#13)"
    for pat in ("str(agent_id) in last_user_msg", "str(conn_id) in last_user_msg",
                "str(wf_id) in last_user_msg", "str(integ_id) in last_user_msg",
                "name_match = ", "template_match = "):
        assert pat not in s, f"substring matching still present: {pat} (#13/#13b)"
    # #13b: the mini-LLM resolver is wired once, and all 4 loops consult its output
    assert "await _resolve_referenced_resources(" in s, \
        "query_and_respond no longer resolves references via the mini-LLM resolver (#13b)"
    assert s.count("resolved_refs.get(") >= 4, \
        "not all 4 resource loops consult the resolver output (#13b)"


# ── #13b — mini-LLM reference resolver (+ deterministic fallback) ──
_res13 = _load_functions(
    _BUILDER_NODES_PY,
    ["_name_mentioned", "_fallback_resolve_references", "_parse_reference_resolution",
     "_mentions_resource_id"])
_name_ment = _res13["_name_mentioned"]
_fallback_res = _res13["_fallback_resolve_references"]
_parse_res = _res13["_parse_reference_resolution"]


@pytest.mark.parametrize("name,msg,expected", [
    ("prod", "connect to prod db", True),
    ("prod", "move this workflow to production", False),   # the headline false-positive
    ("Sales Report", "run the sales report workflow", True),
    ("report", "the user reported an issue", False),
    ("prod-db", "check prod-db now", True),
    ("test", "please run the latest deploy", False),
    ("", "anything", False), (None, "x", False), ("x", "", False),
])
def test_bug13b_name_word_boundary(name, msg, expected):
    assert _name_ment(name, msg) is expected


def test_bug13b_fallback_resolves_by_id_name_and_alias():
    cands = {"agents": [{"id": 1, "name": "Sales Bot"}, {"id": 12, "name": "Ops Bot"}],
             "integrations": [{"id": 3, "name": "Stripe Billing", "aliases": ["stripe"]}]}
    assert _fallback_res("show me agent 12", cands) == {"agents": [12]}       # not id 1
    assert _fallback_res("what can the sales bot do?", cands) == {"agents": [1]}
    assert _fallback_res("does stripe have customer ops?", cands) == {"integrations": [3]}
    assert _fallback_res("nothing relevant here", cands) == {}


@pytest.mark.parametrize("content,expected", [
    ('{"agents": [12]}', {"agents": [12]}),
    ('```json\n{"agents": ["12"]}\n```', {"agents": [12]}),          # fenced + str id
    ('{"agents": [999]}', {}),                                        # hallucinated id dropped
    ('{"agents": ["Ops Bot"]}', {"agents": [12]}),                    # name leniently mapped
    ('{"agents": []}', {}),                                           # explicit none
    ('sure! the answer is 12', None),                                 # unusable → fallback
    ('[1,2]', None), (None, None), ("", None),
])
def test_bug13b_parse_constrained_to_candidates(content, expected):
    cands = {"agents": [{"id": 1, "name": "Sales Bot"}, {"id": 12, "name": "Ops Bot"}]}
    assert _parse_res(content, cands) == expected


def test_bug13b_resolver_uses_llm_and_falls_back():
    cands = {"agents": [{"id": 1, "name": "Sales Bot"}, {"id": 12, "name": "Ops Bot"}]}

    class _Msg:
        def __init__(self, content):
            self.content = content

    def _mk_resolver(reply=None, raise_err=False):
        async def fake_invoke(llm, msgs):
            if raise_err:
                raise RuntimeError("llm down")
            return _Msg(reply)
        return _load_functions(
            _BUILDER_NODES_PY, ["_resolve_referenced_resources"],
            {"get_llm": lambda **k: object(), "safe_llm_invoke": fake_invoke,
             "SystemMessage": _Msg, "HumanMessage": _Msg,
             "logger": logging.getLogger("t"),
             "_parse_reference_resolution": _parse_res,
             "_fallback_resolve_references": _fallback_res,
             "_mentions_resource_id": _res13["_mentions_resource_id"],
             "_name_mentioned": _name_ment},
        )["_resolve_referenced_resources"]

    # (a) good LLM reply is used (and constrained). Message chosen so the deterministic
    # fallback resolves NOTHING ("the second one" has no id/name token) — proving the
    # result really came from the LLM path, not a silently-invoked fallback.
    good = _mk_resolver(reply='{"agents": [12]}')
    assert asyncio.run(good("show me the second one", cands)) == {"agents": [12]}
    # (b) LLM error → deterministic fallback still resolves
    err = _mk_resolver(raise_err=True)
    assert asyncio.run(err("show me agent 12", cands)) == {"agents": [12]}
    # (c) garbage reply → fallback
    garbage = _mk_resolver(reply="I think you mean the Ops one!")
    assert asyncio.run(garbage("show me agent 12", cands)) == {"agents": [12]}
    # (d) no candidates → no LLM call, empty
    assert asyncio.run(good("anything", {})) == {}


# ── Bucket-B #19 — SSE error frame must surface the agent error, not StreamConsumed ──
_TEXT_CHAT_PY = os.path.join(_REPO, "builder_service", "agent_communication", "adapters", "text_chat.py")


def test_bug19_no_second_iterator_in_source():
    s = _src(_TEXT_CHAT_PY)
    assert "aiter_lines().__anext__()" not in s, \
        "SSE error handler still opens a SECOND aiter_lines() iterator → StreamConsumed (#19)"
    assert "error_pending" in s, "same-iterator error capture not present (#19)"


def test_bug19_error_frame_surfaces_agent_message():
    httpx = pytest.importorskip("httpx")
    send_message = _load_method(
        _TEXT_CHAT_PY, "send_message",
        {"httpx": httpx, "logger": logging.getLogger("t"), "asyncio": asyncio,
         "AsyncGenerator": typing.AsyncGenerator})

    class _ByteStream(httpx.AsyncByteStream):
        def __init__(self, chunks):
            self._chunks = chunks
        async def __aiter__(self):
            for c in self._chunks:
                yield c
        async def aclose(self):
            return None

    def _client(chunks):
        def handler(request):
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  stream=_ByteStream(chunks))
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    class _FakeAdapter:
        def __init__(self, c):
            self._c = c
        async def _get_client(self):
            return self._c
        def _resolve_endpoint(self, endpoint):
            return endpoint

    async def _drain(chunks):
        out = []
        async for chunk in send_message(_FakeAdapter(_client(chunks)), "http://agent/api", "hi", []):
            out.append(chunk)
        return out

    # mid-stream error frame → raises with the AGENT's message, not an httpx internal error
    with pytest.raises(Exception) as ei:
        asyncio.run(_drain([b"data: partial\n\n",
                            b'event: error\ndata: {"message": "boom-XYZ"}\n\n']))
    assert "boom-XYZ" in str(ei.value)
    assert "already been streamed" not in str(ei.value)
    assert "StreamConsumed" not in type(ei.value).__name__
    # happy path still streams normally
    assert asyncio.run(_drain([b"data: hello\n\n", b"data: [DONE]\n\n"])) == ["hello"]


# ── Adjacent: text_chat resolves RELATIVE built-in endpoints (else UnsupportedProtocol) ──
def test_textchat_resolves_relative_endpoint():
    resolve = _load_method(_TEXT_CHAT_PY, "_resolve_endpoint",
                           {"_get_base_url": lambda: "http://host:5001/"})

    class _S:
        pass
    s = _S()
    assert resolve(s, "/api/agents/data/chat") == "http://host:5001/api/agents/data/chat"
    assert resolve(s, "http://other/api/chat") == "http://other/api/chat"   # absolute unchanged
    assert resolve(s, "") == ""


def test_textchat_send_and_health_resolve_endpoint():
    s = _src(_TEXT_CHAT_PY)
    assert s.count("self._resolve_endpoint(endpoint)") >= 2, \
        "send_message/check_health no longer resolve relative endpoints (built-in text_chat can't connect)"
