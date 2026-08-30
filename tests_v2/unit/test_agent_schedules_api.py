"""Unit tests for agent_service/schedules_api.py — the Schedules surface.

Covers the honesty and privacy seams:
- visibility: Dev+ sees every job; role-1 sees ONLY jobs whose user_id param is
  theirs, and jobs whose details cannot be fetched are HIDDEN from role-1
  (fail closed: ownership unproven) while Dev+ gets an honest partial row.
- cadence text for cron/interval/date rows.
- run_now relays the run-once route's answer and reports a missing route
  honestly (a main app predating it answers 404 on the path).
- set_active / delete_job verify by read-back and refuse to report a state the
  DB does not hold.
- create(): role gates per kind, bad input refusals, and the bounded-ask
  read-back doctrine (a job whose bound the engine dropped is deleted and
  reported as NOT scheduled).

Runs standalone (aihub-agent python test_agent_schedules_api.py) or under
pytest; self-skips in envs without the agent service deps.
"""
import asyncio
import json
import os
import sys

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

try:
    import schedules_api  # noqa: E402
    HAVE_DEPS = True
except ImportError as e:
    HAVE_DEPS = False
    _IMPORT_ERR = e

if not HAVE_DEPS:
    try:
        import pytest
        pytest.skip(f"agent service deps unavailable: {_IMPORT_ERR}",
                    allow_module_level=True)
    except ImportError:
        print(f"SKIP: agent service deps unavailable: {_IMPORT_ERR}")
        sys.exit(0)

from fastapi import HTTPException  # noqa: E402

DEV = {"user_id": 13, "role": 3, "username": "admin", "tenant_id": "t1"}
USER = {"user_id": 42, "role": 1, "username": "pat", "tenant_id": "t1"}


def _job(jid, jtype="agent_session", uid=None, active=True, schedules=None,
         params=None, **kw):
    p = dict(params or {})
    if uid is not None:
        p["user_id"] = {"value": str(uid), "type": "string"}
    return {"id": jid, "name": kw.get("name", f"Job {jid}"), "type": jtype,
            "target_id": kw.get("target_id", 0),
            "description": kw.get("description", ""),
            "created_by": kw.get("created_by", "someone"),
            "created_at": "2026-08-30T12:00:00", "modified_by": None,
            "modified_at": None, "is_active": active,
            "schedules": schedules if schedules is not None else
            [{"id": 1, "type": "cron", "cron_expression": "0 8 * * *",
              "interval_seconds": None, "interval_minutes": None,
              "interval_hours": None, "interval_days": None,
              "interval_weeks": None, "start_date": None, "end_date": None,
              "next_run_time": "2026-08-31T12:00:00",
              "last_run_time": "2026-08-30T12:00:05", "max_runs": None,
              "current_runs": 0, "is_active": True}],
            "parameters": p}


class FakeResp:
    def __init__(self, status=200, body=None, text=""):
        self.status_code = status
        self._body = body
        self.text = text or (json.dumps(body) if body is not None else "")

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeClient:
    """Routes (METHOD, url-suffix-after-base) -> FakeResp or callable."""
    routes = {}
    calls = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def _dispatch(self, method, url, **kw):
        FakeClient.calls.append((method, url, kw))
        for (m, suffix), resp in FakeClient.routes.items():
            if m == method and url.endswith(suffix):
                return resp(kw) if callable(resp) else resp
        return FakeResp(404, {"error": "no fake route: " + url})

    async def get(self, url, **kw):
        return await self._dispatch("GET", url, **kw)

    async def post(self, url, **kw):
        return await self._dispatch("POST", url, **kw)

    async def put(self, url, **kw):
        return await self._dispatch("PUT", url, **kw)

    async def delete(self, url, **kw):
        return await self._dispatch("DELETE", url, **kw)


_REAL_CLIENT = schedules_api.httpx.AsyncClient


def _use_fake(routes):
    FakeClient.routes = routes
    FakeClient.calls = []
    schedules_api.httpx.AsyncClient = FakeClient


def _restore():
    schedules_api.httpx.AsyncClient = _REAL_CLIENT


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ------------------------------------------------------------- pure helpers

def test_visibility_rules():
    mine = _job(1, uid=42)
    other = _job(2, uid=13)
    unowned = _job(3, jtype="workflow")  # no user_id param (legacy platform job)
    assert schedules_api._visible(DEV, mine)
    assert schedules_api._visible(DEV, other)
    assert schedules_api._visible(DEV, unowned)
    assert schedules_api._visible(USER, mine)
    assert not schedules_api._visible(USER, other)
    assert not schedules_api._visible(USER, unowned)


def test_cadence_text():
    assert schedules_api._cadence_text(
        {"type": "cron", "cron_expression": "0 8 * * 1-5"}, "America/New_York") \
        == "cron 0 8 * * 1-5 · America/New_York"
    assert schedules_api._cadence_text(
        {"type": "interval", "interval_minutes": 90}, "") == "every 90 minutes"
    assert schedules_api._cadence_text(
        {"type": "interval", "interval_hours": 2}, "") == "every 2 hours"
    assert schedules_api._cadence_text(
        {"type": "interval", "interval_days": 1}, "") == "every 1 day"
    assert schedules_api._cadence_text({"type": "date"}, "") == "one-time"


def test_normalize_picks_active_rows_and_bound():
    job = _job(7, uid=42, params={"timezone": {"value": "America/Chicago",
                                               "type": "string"}},
               schedules=[
                   {"id": 1, "type": "date", "is_active": False,
                    "next_run_time": None, "last_run_time": "2026-08-29T01:00:00"},
                   {"id": 2, "type": "interval", "interval_minutes": 10,
                    "is_active": True, "next_run_time": "2026-08-31T00:10:00",
                    "last_run_time": "2026-08-30T23:00:00", "max_runs": 6,
                    "current_runs": 2, "end_date": "2026-08-31T01:00:00"}])
    row = schedules_api._normalize(job, USER)
    assert row["cadence"] == "every 10 minutes"
    assert row["next_run_time"] == "2026-08-31T00:10:00"
    assert row["last_run_time"] == "2026-08-30T23:00:00"
    assert row["bound"] == {"end_date": "2026-08-31T01:00:00", "max_runs": 6,
                            "current_runs": 2}
    assert row["mine"] is True
    assert row["timezone"] == "America/Chicago"


# ------------------------------------------------------------- list_jobs

def test_list_jobs_role1_fails_closed_dev_sees_partial():
    jobs = [{"id": 1, "name": "A", "type": "agent_session", "is_active": True},
            {"id": 2, "name": "B", "type": "agent_session", "is_active": True},
            {"id": 3, "name": "C", "type": "workflow", "is_active": True}]
    routes = {
        ("GET", "/api/scheduler/jobs"): FakeResp(200, jobs),
        ("GET", "/api/scheduler/jobs/1"): FakeResp(200, _job(1, uid=42)),
        ("GET", "/api/scheduler/jobs/2"): FakeResp(500, None, "boom"),
        ("GET", "/api/scheduler/jobs/3"): FakeResp(200, _job(3, jtype="workflow")),
    }
    _use_fake(routes)
    try:
        out = _run(schedules_api.list_jobs(USER))
        ids = [s["id"] for s in out["schedules"]]
        assert ids == [1], ids                      # own only; unfetchable hidden
        assert any("hidden" in e for e in out["errors"])
        assert out["can_see_all"] is False

        out = _run(schedules_api.list_jobs(DEV))
        ids = sorted(s["id"] for s in out["schedules"])
        assert ids == [1, 2, 3], ids                # dev: all, incl. partial row
        partial = next(s for s in out["schedules"] if s["id"] == 2)
        assert partial.get("detail_error")
        assert out["can_see_all"] is True
    finally:
        _restore()


# ------------------------------------------------------------- run / act

def test_run_now_relays_queue_and_missing_route():
    job1 = _job(5, uid=42)
    routes = {
        ("GET", "/api/scheduler/jobs/5"): FakeResp(200, job1),
        ("POST", "/api/scheduler/jobs/5/run-once"): FakeResp(
            202, {"queued": True, "note": "fires soon"}),
    }
    _use_fake(routes)
    try:
        out = _run(schedules_api.run_now(USER, 5))
        assert out["ok"] and out["note"] == "fires soon"
    finally:
        _restore()

    # a main app without the route answers 404 on the path
    routes = {
        ("GET", "/api/scheduler/jobs/5"): FakeResp(200, job1),
        ("POST", "/api/scheduler/jobs/5/run-once"): FakeResp(404, None, "not found"),
    }
    _use_fake(routes)
    try:
        out = _run(schedules_api.run_now(USER, 5))
        assert not out["ok"] and "run-once route" in out["error"]
    finally:
        _restore()


def test_run_now_hides_foreign_job_from_role1():
    routes = {("GET", "/api/scheduler/jobs/9"): FakeResp(200, _job(9, uid=13))}
    _use_fake(routes)
    try:
        try:
            _run(schedules_api.run_now(USER, 9))
            assert False, "expected 404"
        except HTTPException as e:
            assert e.status_code == 404
    finally:
        _restore()


def test_set_active_readback_honesty():
    # the PUT "succeeds" but the read-back still shows active → honest failure
    routes = {
        ("GET", "/api/scheduler/jobs/5"): FakeResp(200, _job(5, uid=42, active=True)),
        ("PUT", "/api/scheduler/jobs/5"): FakeResp(200, {"message": "ok"}),
    }
    _use_fake(routes)
    try:
        out = _run(schedules_api.set_active(USER, 5, False))
        assert not out["ok"] and "did not record" in out["error"]
    finally:
        _restore()


def test_delete_readback_honesty():
    routes = {
        ("GET", "/api/scheduler/jobs/5"): FakeResp(200, _job(5, uid=42)),
        ("DELETE", "/api/scheduler/jobs/5"): FakeResp(200, {"message": "deleted"}),
    }
    _use_fake(routes)   # GET after DELETE still returns the job
    try:
        out = _run(schedules_api.delete_job(USER, 5))
        assert not out["ok"] and "NOT deleted" in out["error"]
    finally:
        _restore()


# ------------------------------------------------------------- create

def test_create_gates_and_refusals():
    # role-1 + build flag off → automation refused before any HTTP
    os.environ.pop("AGENT_BUILD_ALLOW_ALL_USERS", None)
    out = _run(schedules_api.create(USER, {"kind": "automation",
                                           "automation_id": "x",
                                           "every_hours": 1}))
    assert not out["ok"] and "Developer role" in out["error"]

    # portal workflows are recurring — one-shot refused
    out = _run(schedules_api.create(DEV, {"kind": "portal_workflow",
                                          "slug": "s", "run_in_minutes": 5}))
    assert not out["ok"] and "recurring" in out["error"]

    # agent task needs name + prompt
    out = _run(schedules_api.create(DEV, {"kind": "agent_task",
                                          "every_minutes": 5}))
    assert not out["ok"] and "name and a prompt" in out["error"]

    # unknown kind
    out = _run(schedules_api.create(DEV, {"kind": "nope", "every_minutes": 5}))
    assert not out["ok"] and "Unknown schedule kind" in out["error"]

    # no cadence at all → _build_schedule's honest refusal
    out = _run(schedules_api.create(DEV, {"kind": "agent_task", "name": "n",
                                          "prompt": "p"}))
    assert not out["ok"] and "Nothing was scheduled" in out["error"]

    # bad inputs JSON on an automation (role gate passes for DEV)
    out = _run(schedules_api.create(DEV, {"kind": "automation",
                                          "automation_id": "abc",
                                          "inputs": "{not json",
                                          "every_hours": 1}))
    assert not out["ok"] and "JSON object" in out["error"]


def test_create_agent_task_bound_dropped_is_deleted():
    # engine "creates" the job but the read-back shows NO max_runs/end_date →
    # the job must be deleted and reported NOT scheduled
    created = {"id": 77}
    readback = _job(77, uid=13, schedules=[
        {"id": 1, "type": "interval", "interval_minutes": 5, "is_active": True,
         "next_run_time": "2026-08-31T00:05:00", "last_run_time": None,
         "max_runs": None, "end_date": None, "current_runs": 0}])
    deleted = []
    routes = {
        ("POST", "/api/scheduler/jobs"): FakeResp(200, created),
        ("GET", "/api/scheduler/jobs/77"): FakeResp(200, readback),
        ("DELETE", "/api/scheduler/jobs/77"):
            lambda kw: (deleted.append(1), FakeResp(200, {"message": "gone"}))[1],
    }
    _use_fake(routes)
    try:
        out = _run(schedules_api.create(DEV, {"kind": "agent_task", "name": "t",
                                              "prompt": "p", "every_minutes": 5,
                                              "occurrences": 3}))
        assert not out["ok"] and "NOT scheduled" in out["error"]
        assert deleted, "job with dropped bound must be deleted"
    finally:
        _restore()


def test_create_agent_task_happy_path_carries_identity():
    created = {"id": 88}
    readback = _job(88, uid=13, schedules=[
        {"id": 1, "type": "cron", "cron_expression": "0 8 * * *",
         "is_active": True, "next_run_time": "2026-08-31T12:00:00",
         "last_run_time": None, "max_runs": None, "end_date": None}])
    posted = {}

    def capture(kw):
        posted.update(kw.get("json") or {})
        return FakeResp(200, created)
    routes = {
        ("POST", "/api/scheduler/jobs"): capture,
        ("GET", "/api/scheduler/jobs/88"): FakeResp(200, readback),
    }
    _use_fake(routes)
    try:
        out = _run(schedules_api.create(
            dict(DEV), {"kind": "agent_task", "name": "morning brief",
                        "prompt": "summarize overnight orders",
                        "cron_expression": "0 8 * * *",
                        "timezone": "America/New_York"}))
        assert out["ok"] and out["job_id"] == 88, out
        assert posted["type"] == "agent_session"
        assert posted["target_id"] == "0"           # int 0 reads as missing
        p = posted["parameters"]
        assert p["user_id"]["value"] == "13"
        assert p["prompt"]["value"] == "summarize overnight orders"
        assert p["timezone"]["value"] == "America/New_York"   # engine fires cron in-zone
        # cron stored AS WRITTEN — the double-shift lesson (fb795f3)
        assert posted["schedule"] == {"type": "cron", "cron_expression": "0 8 * * *"}
    finally:
        _restore()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
