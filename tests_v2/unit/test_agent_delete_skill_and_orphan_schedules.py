"""The Agent — delete_skill tool + code-flow deletes that take their schedules
with them (docs/handoff-codeflow-send-email-403.md §5, 2026-09-05).

  * delete_skill: two-step confirm, scope resolution, the same permission
    rules as /api/skills/delete (tenant/product = admin, group = member),
    read-back verification.
  * delete_code_flow: the unconfirmed call lists the flow's code-flow
    schedules, agent tasks LINKED via code_flow, and agent tasks that MENTION
    the flow by name; the confirmed call passes the mentions the user did not
    keep as also_delete_job_ids and reports what was removed / kept.
  * schedule_agent_task: an optional code_flow records the structured link
    (and refuses a flow that does not exist).

Runs standalone (C:\\Users\\james\\miniconda3\\envs\\aihub-agent\\python.exe
tests_v2\\unit\\test_agent_delete_skill_and_orphan_schedules.py) or under pytest
in an env with claude_agent_sdk; without the SDK every test self-skips.
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager
from unittest import mock

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

try:
    import httpx                                  # noqa: E402
    import work_tools as W                        # noqa: E402
    import authoring_tools as A                   # noqa: E402
    import skills_mount as SM                     # noqa: E402
    import readthrough as R                       # noqa: E402
    from platform_tools import CURRENT_USER       # noqa: E402
    HAVE_SDK = True
except ImportError as e:                          # main-env pytest sweep: no claude_agent_sdk
    HAVE_SDK = False
    _IMPORT_ERR = e

if not HAVE_SDK:
    try:
        import pytest
        pytestmark = pytest.mark.skip(
            reason=f"needs the aihub-agent env (claude_agent_sdk): {_IMPORT_ERR}")
    except ImportError:
        pass
else:
    _RealAsyncClient = httpx.AsyncClient


def _run(coro):
    return asyncio.run(coro)


def _txt(res):
    return res["content"][0]["text"]


def _as(role, uid=7):
    return CURRENT_USER.set({"user_id": uid, "role": role, "username": f"u{uid}",
                             "name": f"User {uid}"})


@contextmanager
def _isolated_skills():
    """Point every skills scope at a throwaway directory."""
    tmp = tempfile.mkdtemp(prefix="skills_")
    saved = {k: getattr(SM, k) for k in
             ("SKILLS_PRODUCT_DIR", "SKILLS_TENANT_DIR", "USERS_DIR", "GROUPS_DIR")}
    SM.SKILLS_PRODUCT_DIR = os.path.join(tmp, "product")
    SM.SKILLS_TENANT_DIR = os.path.join(tmp, "tenant")
    SM.USERS_DIR = os.path.join(tmp, "users")
    SM.GROUPS_DIR = os.path.join(tmp, "groups")
    try:
        yield tmp
    finally:
        for k, v in saved.items():
            setattr(SM, k, v)
        shutil.rmtree(tmp, ignore_errors=True)


# ─────────────────────────────────────────────── delete_skill
def test_delete_skill_two_step_then_verified_by_readback():
    tok = _as(2)
    try:
        with _isolated_skills(), mock.patch.object(R, "user_group_ids", lambda uid: []):
            SM.write_skill("user", "month-end-close", "Use when closing the month",
                           "1. pull the TB\n2. ...", user_id=7)
            path = os.path.join(SM.USERS_DIR, "7", "skills", "month-end-close", "SKILL.md")
            assert os.path.isfile(path)

            first = _run(W.delete_skill_tool.handler({"name": "month-end-close"}))
            assert "CONFIRMATION REQUIRED" in _txt(first) and "[user]" in _txt(first)
            assert "closing the month" in _txt(first)
            assert os.path.isfile(path), "preview must not delete"

            second = _run(W.delete_skill_tool.handler({"name": "month-end-close",
                                                       "confirmed": True}))
            assert not second.get("is_error"), _txt(second)
            assert "deleted (verified by read-back)" in _txt(second)
            assert not os.path.exists(path)
            assert "month-end-close" not in _txt(_run(W.list_skills_tool.handler({})))

            gone = _run(W.delete_skill_tool.handler({"name": "month-end-close",
                                                     "confirmed": True}))
            assert gone.get("is_error") and "nothing was deleted" in _txt(gone)
    finally:
        CURRENT_USER.reset(tok)


def test_delete_skill_scope_rules():
    with _isolated_skills(), mock.patch.object(R, "user_group_ids", lambda uid: [5]):
        SM.write_skill("tenant", "shared-proc", "tenant-wide", "body")
        SM.write_skill("group", "team-proc", "group 5", "body", group_id=5)
        SM.write_skill("group", "other-team", "group 9", "body", group_id=9)
        SM.write_skill("user", "shared-proc", "my private copy", "body", user_id=7)

        tok = _as(2)
        try:
            # same name in two visible scopes → must be told which
            res = _run(W.delete_skill_tool.handler({"name": "shared-proc", "confirmed": True}))
            assert res.get("is_error") and "several scopes" in _txt(res)
            # tenant scope: developers are refused, nothing deleted
            res = _run(W.delete_skill_tool.handler({"name": "shared-proc", "scope": "tenant",
                                                    "confirmed": True}))
            assert res.get("is_error") and "admin" in _txt(res)
            assert os.path.isdir(os.path.join(SM.SKILLS_TENANT_DIR, "shared-proc"))
            # the private copy goes with scope=user
            res = _run(W.delete_skill_tool.handler({"name": "shared-proc", "scope": "user",
                                                    "confirmed": True}))
            assert not res.get("is_error"), _txt(res)
            assert not os.path.exists(os.path.join(SM.USERS_DIR, "7", "skills", "shared-proc"))
            # a group the user is not in is invisible → not deleted
            res = _run(W.delete_skill_tool.handler({"name": "other-team", "scope": "group",
                                                    "group_id": 9, "confirmed": True}))
            assert res.get("is_error") and os.path.isdir(
                os.path.join(SM.GROUPS_DIR, "9", "skills", "other-team"))
            # own group: deletes
            res = _run(W.delete_skill_tool.handler({"name": "team-proc", "confirmed": True}))
            assert not res.get("is_error") and "[group 5]" in _txt(res)
            assert not os.path.exists(os.path.join(SM.GROUPS_DIR, "5", "skills", "team-proc"))
            # bad name never touches disk
            res = _run(W.delete_skill_tool.handler({"name": "Not Kebab!", "confirmed": True}))
            assert res.get("is_error") and "kebab" in _txt(res)
        finally:
            CURRENT_USER.reset(tok)

        tok = _as(3)   # admin: tenant scope allowed
        try:
            res = _run(W.delete_skill_tool.handler({"name": "shared-proc", "confirmed": True}))
            assert not res.get("is_error") and "[tenant]" in _txt(res)
            assert not os.path.exists(os.path.join(SM.SKILLS_TENANT_DIR, "shared-proc"))
        finally:
            CURRENT_USER.reset(tok)


# ─────────────────────────────────────────────── delete_code_flow + schedules
_SCHEDS = [
    {"job_id": 700, "name": "Code Flow: past-due-aging", "type": "workflow",
     "is_active": True, "match": "code_flow_schedule", "gist": "runs code flow"},
    {"job_id": 648, "name": "Agent: aging (linked)", "type": "agent_session",
     "is_active": True, "match": "linked", "gist": "agent task linked"},
    {"job_id": 649, "name": "Agent: weekday aging", "type": "agent_session",
     "is_active": True, "match": "mention",
     "gist": "Run code flow 'past-due-aging' and email the workbook to me"},
    {"job_id": 650, "name": "Agent: aging summary", "type": "agent_session",
     "is_active": False, "match": "mention", "gist": "Summarize the past-due-aging run"},
]


def _cf_with_schedules():
    return {"name": "past-due-aging", "workflow_id": 9,
            "nodes": [{"id": "s1", "label": "pull", "isStart": True}],
            "connections": [], "schedules": [dict(s) for s in _SCHEDS]}


def test_delete_code_flow_previews_schedules_and_passes_unkept_mentions():
    tok = _as(2)
    calls = []
    state = {"cf": _cf_with_schedules()}

    async def fake_manage(action, payload, timeout=900.0):
        calls.append((action, payload))
        if action == "get":
            return ({"code_flow": state["cf"]}, 200) if state["cf"] else \
                   ({"error": "code flow not found"}, 404)
        if action == "delete":
            also = set(payload.get("also_delete_job_ids") or [])
            removed = [s for s in _SCHEDS if s["match"] != "mention" or s["job_id"] in also]
            kept = [s for s in _SCHEDS if s["match"] == "mention" and s["job_id"] not in also]
            state["cf"] = None
            return {"deleted": payload["name"], "removed_schedules": removed,
                    "kept_mentions": kept, "removed_count": len(removed)}, 200
        raise AssertionError(action)

    try:
        with mock.patch.object(A, "_manage_cf", fake_manage):
            first = _run(A.delete_code_flow.handler({"name": "past-due-aging"}))
            t = _txt(first)
            assert "CONFIRMATION REQUIRED" in t and not any(c[0] == "delete" for c in calls)
            assert "#700" in t and "#648" in t and "code-flow schedule" in t
            assert "#649" in t and "#650" in t and "MENTION" in t and "keep_job_ids" in t
            assert "paused" in t                       # #650 is inactive — say so

            second = _run(A.delete_code_flow.handler({"name": "past-due-aging",
                                                      "confirmed": True,
                                                      "keep_job_ids": [650]}))
            t2 = _txt(second)
            assert not second.get("is_error"), t2
            dele = next(c for c in calls if c[0] == "delete")
            assert dele[1] == {"name": "past-due-aging", "also_delete_job_ids": [649]}
            assert "Deleted code flow 'past-due-aging'" in t2
            assert "Removed 3 schedule(s)" in t2 and "#649" in t2 and "#700" in t2
            assert "Kept 1" in t2 and "#650" in t2
    finally:
        CURRENT_USER.reset(tok)


def test_delete_code_flow_without_schedules_says_so_and_flags_listing_errors():
    tok = _as(2)
    cf = {"name": "lonely", "workflow_id": 3, "nodes": [], "connections": [], "schedules": []}

    async def fake_manage(action, payload, timeout=900.0):
        if action == "get":
            return {"code_flow": cf}, 200
        return {"deleted": "lonely", "removed_schedules": [], "kept_mentions": []}, 200

    try:
        with mock.patch.object(A, "_manage_cf", fake_manage):
            assert "No schedules reference this flow" in _txt(
                _run(A.delete_code_flow.handler({"name": "lonely"})))
            cf["schedules_error"] = "db down"
            assert "could not be listed" in _txt(_run(A.delete_code_flow.handler({"name": "lonely"})))
    finally:
        CURRENT_USER.reset(tok)


# ─────────────────────────────────────────────── schedule_agent_task code_flow link
class _Sched:
    def __init__(self):
        self.posted = None

    def handler(self, request):
        path = request.url.path
        if request.method == "POST" and path == "/api/scheduler/jobs":
            self.posted = json.loads(request.content.decode())
            return httpx.Response(201, json={"id": "4242"})
        if request.method == "GET" and path.startswith("/api/scheduler/jobs/"):
            sch = dict((self.posted or {}).get("schedule") or {})
            return httpx.Response(200, json={"id": 4242, "schedules": [
                {"is_active": True, "type": sch.get("type"),
                 "cron_expression": sch.get("cron_expression"),
                 "end_date": None, "max_runs": None}]})
        if request.method == "DELETE":
            return httpx.Response(200, json={"message": "deleted"})
        return httpx.Response(404, json={"error": path})


def _patched_client(handler):
    def factory(*a, **k):
        k.pop("transport", None)
        return _RealAsyncClient(transport=httpx.MockTransport(handler), **k)
    return mock.patch.object(httpx, "AsyncClient", factory)


def test_schedule_agent_task_records_the_code_flow_link():
    tok = _as(2)
    s = _Sched()

    async def flow_exists(name, *a, **k):
        return {"code_flow": {"name": "past-due-aging"}}, 200

    try:
        with _patched_client(s.handler), mock.patch.object(A, "_manage_cf", flow_exists):
            res = _run(W.schedule_agent_task.handler({
                "task_prompt": "Run the past-due aging flow and email me the workbook",
                "name": "weekday aging", "cron_expression": "30 7 * * 1-5",
                "code_flow": "past-due-aging"}))
        assert not res.get("is_error"), _txt(res)
        params = s.posted["parameters"]
        assert params["code_flow"] == {"value": "past-due-aging", "type": "string"}
        assert params["prompt"]["value"].startswith("Run the past-due")
    finally:
        CURRENT_USER.reset(tok)


def test_schedule_agent_task_refuses_a_missing_code_flow_and_omits_the_param_otherwise():
    tok = _as(2)
    s = _Sched()

    async def no_flow(name, *a, **k):
        return {"error": "code flow not found"}, 404

    try:
        with _patched_client(s.handler), mock.patch.object(A, "_manage_cf", no_flow):
            res = _run(W.schedule_agent_task.handler({
                "task_prompt": "Run the flow", "name": "x",
                "cron_expression": "30 7 * * 1-5", "code_flow": "ghost-flow"}))
            assert res.get("is_error") and "no code flow named 'ghost-flow'" in _txt(res)
            assert s.posted is None, "nothing may be scheduled for a flow that does not exist"
            res = _run(W.schedule_agent_task.handler({
                "task_prompt": "Summarize my inbox", "name": "inbox",
                "cron_expression": "0 8 * * *"}))
            assert not res.get("is_error"), _txt(res)
            assert "code_flow" not in s.posted["parameters"]
    finally:
        CURRENT_USER.reset(tok)


def test_registration_and_doctrine():
    import brain
    names = {t.name for t in W.WORK_TOOLS}
    assert "delete_skill" in names and "save_skill" in names
    assert "delete_skill" in brain.MUTATING_TOOLS
    assert "delete_skill" in brain.SYSTEM_PROMPT
    assert "code_flow=<exact name>" in brain.SYSTEM_PROMPT
    props = (getattr(W.schedule_agent_task, "input_schema", None)
             or getattr(W.schedule_agent_task, "schema", None) or {}).get("properties") or {}
    assert "code_flow" in props
    dprops = (getattr(A.delete_code_flow, "input_schema", None)
              or getattr(A.delete_code_flow, "schema", None) or {}).get("properties") or {}
    assert "keep_job_ids" in dprops


# -------------------------------------------------------------------- runner

if __name__ == "__main__":
    if not HAVE_SDK:
        print(f"SKIP-ALL: {_IMPORT_ERR}")
        sys.exit(0)
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in fns:
        try:
            f()
            print(f"PASS  {n}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {n}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {n}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
