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
        if isinstance(node, ast.FunctionDef) and node.name in names:
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
