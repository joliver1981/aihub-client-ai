"""Per-schedule cron zones (2026-10-09).

A cron's times are the user's local times. The Workflow Monitor keeps ONE
scheduler job per workflow and job parameters are shared by its schedules, so a
job-level "timezone" would also move a workflow's existing schedules. A cron
schedule's own zone is the job parameter "timezone@<ScheduleId>"; the engine
reads it first, a workflow run never receives it as a variable, and schedules
without one fire exactly as before (UTC).

Covers: the engine helpers + the workflow run payload, the scheduler routes
(create / update / delete / list), Run now, and Code Flow schedules (whose
zone used to be dropped). The cron trigger itself needs the real apscheduler
(the scheduler's `jss` env) — that test skips elsewhere; the live check reads
the engine's computed NextRunTime.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from test_scheduler_target_reaper import _import_job_scheduler  # noqa: E402

js = _import_job_scheduler()


# ------------------------------------------------------------------ engine

def test_schedule_zone_wins_over_job_zone():
    params = {"timezone": "UTC", "timezone@12": "America/Toronto", "inputFolder": "C:\\in"}
    assert js.schedule_timezone(params, 12) == "America/Toronto"
    assert js.schedule_timezone(params, 13) == "UTC"          # another schedule of the job
    assert js.schedule_timezone({"inputFolder": "x"}, 12) == ""   # engine default (UTC)
    assert js.schedule_timezone(None, 12) == ""


def test_workflow_run_never_receives_zone_keys(monkeypatch):
    svc = js.JobSchedulerService.__new__(js.JobSchedulerService)
    svc.api_base_url = "http://main"
    for name in ("_update_execution_record", "_increment_run_count", "_update_last_run_time"):
        monkeypatch.setattr(svc, name, lambda *a, **k: None)
    monkeypatch.setattr(svc, "_create_execution_record", lambda *a, **k: 1)
    sent = []

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"execution_id": "E1"}

    monkeypatch.setattr(js.requests, "post", lambda url, json=None, headers=None, **k: (sent.append(json), _Resp())[1])
    svc._execute_workflow_job({"scheduled_job_id": 4, "schedule_id": 12, "job_name": "w", "target_id": 7,
                               "parameters": {"timezone@12": "America/Toronto", "inputFolder": "C:\\in",
                                              "timezone": "kept-as-before"}})
    assert sent[-1]["variables"] == {"inputFolder": "C:\\in", "timezone": "kept-as-before"}
    svc._execute_workflow_job({"scheduled_job_id": 4, "schedule_id": 12, "job_name": "w", "target_id": 7,
                               "parameters": {"timezone@12": "America/Toronto"}})
    assert "variables" not in sent[-1]       # nothing left to pass


def test_cron_fires_at_local_time_in_the_schedule_zone():
    if not isinstance(js.CronTrigger, type):
        pytest.skip("real apscheduler not installed in this env (runs in the jss env)")
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    svc = js.JobSchedulerService.__new__(js.JobSchedulerService)
    trig = svc._create_trigger("cron", cron_expression="0 7 * * 1-5", tz_name="America/Toronto")
    nxt = trig.get_next_fire_time(None, datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc))
    local = nxt.astimezone(ZoneInfo("America/Toronto"))
    assert (local.hour, local.minute, local.weekday()) == (7, 0, 0)   # Monday 07:00 Toronto


# ------------------------------------------------------------------ routes

import scheduler_routes as sr  # noqa: E402
from flask import Flask  # noqa: E402

_app = Flask(__name__)


class _Cursor:
    """Answers by SQL substring; records every statement."""

    def __init__(self, answers):
        self.answers = answers          # [(substring, rows)]
        self.executed = []
        self._rows = []

    def execute(self, sql, *params):
        self.executed.append((" ".join(str(sql).split()), params))
        self._rows = []
        for sub, rows in self.answers:
            if sub in sql:
                self._rows = list(rows)
                break
        return self

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


class _Conn:
    def __init__(self, cursor):
        self._c = cursor
        self.commits = 0

    def cursor(self):
        return self._c

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        pass


def _zone_writes(cur):
    return [p for sql, p in cur.executed if "ScheduledJobParameters" in sql and sql.startswith("INSERT")]


def _post_schedule(monkeypatch, body, existing_job=41, new_sid=900):
    cur = _Cursor([("SELECT ScheduledJobId FROM ScheduledJobs", [(existing_job,)])])
    monkeypatch.setattr(sr, "get_db_connection", lambda: _Conn(cur))
    monkeypatch.setattr(sr, "set_tenant_context", lambda c, t=None: None)
    monkeypatch.setattr(sr, "_create_schedule", lambda c, job_id, data: new_sid)
    with _app.test_request_context("/x", method="POST", json=body):
        resp = sr.create_job_schedule_by_type.__wrapped__(7, "workflow")
    status = resp[1] if isinstance(resp, tuple) else 200
    data = (resp[0] if isinstance(resp, tuple) else resp).get_json()
    return status, data, cur


def test_create_cron_stores_the_zone_with_that_schedule(monkeypatch):
    status, data, cur = _post_schedule(monkeypatch, {"type": "cron", "cron_expression": "0 7 * * 1-5",
                                                     "timezone": "America/Toronto"})
    assert status == 201 and data["timezone"] == "America/Toronto"
    assert _zone_writes(cur) == [(41, "timezone@900", "America/Toronto")]
    # never a job-level zone (that would move the workflow's other schedules)
    assert not any("timezone'" in sql for sql, _ in cur.executed if sql.startswith("INSERT"))


def test_create_without_zone_or_non_cron_writes_none(monkeypatch):
    for body in ({"type": "cron", "cron_expression": "0 7 * * *"},
                 {"type": "interval", "interval_hours": 2, "timezone": "America/Toronto"}):
        status, data, cur = _post_schedule(monkeypatch, body)
        assert status == 201 and data["timezone"] is None and _zone_writes(cur) == []


def test_create_with_unknown_zone_is_refused_before_any_write(monkeypatch):
    calls = []
    monkeypatch.setattr(sr, "get_db_connection", lambda: calls.append(1))
    with _app.test_request_context("/x", method="POST",
                                   json={"type": "cron", "cron_expression": "0 7 * * *", "timezone": "Mars/Base"}):
        resp = sr.create_job_schedule_by_type.__wrapped__(7, "workflow")
    assert resp[1] == 400 and "unknown time zone" in resp[0].get_json()["error"] and calls == []


def test_update_changes_zone_only_when_asked(monkeypatch):
    def run(body):
        cur = _Cursor([("SELECT ScheduledJobId FROM ScheduledJobs", [(41,)]),
                       ("SELECT 1 FROM ScheduleDefinitions", [(1,)]),
                       ("SELECT ScheduleType FROM ScheduleDefinitions", [("cron",)])])
        monkeypatch.setattr(sr, "get_db_connection", lambda: _Conn(cur))
        monkeypatch.setattr(sr, "set_tenant_context", lambda c, t=None: None)
        with _app.test_request_context("/x", method="PUT", json=body):
            sr.update_job_schedule_by_type.__wrapped__(7, "workflow", 900)
        return cur
    untouched = run({"cron_expression": "30 7 * * 1-5", "timezone_offset": 240})
    assert not any("ScheduledJobParameters" in sql for sql, _ in untouched.executed)
    changed = run({"timezone": "Europe/London"})
    assert _zone_writes(changed) == [(41, "timezone@900", "Europe/London")]


def test_delete_removes_that_schedules_zone(monkeypatch):
    cur = _Cursor([("SELECT ScheduledJobId FROM ScheduledJobs", [(41,)]),
                   ("SELECT 1 FROM ScheduleDefinitions", [(1,)])])
    monkeypatch.setattr(sr, "get_db_connection", lambda: _Conn(cur))
    monkeypatch.setattr(sr, "set_tenant_context", lambda c, t=None: None)
    with _app.test_request_context("/x", method="DELETE"):
        sr.delete_job_schedule_by_type.__wrapped__(7, "workflow", 900)
    deletes = [p for sql, p in cur.executed if sql.startswith("DELETE FROM ScheduledJobParameters")]
    assert deletes == [(41, "timezone@900")]


def test_list_reports_each_schedules_zone_and_code_flows(monkeypatch):
    row = lambda sid, cron, kind: (41, "job", "workflow", 7, sid, "cron", None, None, None, None, None,  # noqa: E731
                                   cron, None, None, None, None, None, 0, 1, "Acme lines", kind)
    cur = _Cursor([("FROM ScheduledJobs j", [row(900, "0 7 * * 1-5", None), row(901, "0 6 * * *", None),
                                              row(902, "0 5 * * *", "code_flow")]),
                   ("FROM ScheduledJobParameters", [(41, "timezone@900", "America/Toronto")])])
    monkeypatch.setattr(sr, "get_db_connection", lambda: _Conn(cur))
    monkeypatch.setattr(sr, "set_tenant_context", lambda c, t=None: None)
    with _app.test_request_context("/x"):
        data = sr.get_all_schedules_by_type.__wrapped__("workflow").get_json()
    by = {s["id"]: s for s in data}
    assert by[900]["timezone"] == "America/Toronto"
    assert by[901]["timezone"] is None                 # an existing schedule: unchanged, UTC
    assert by[902]["workflow_kind"] == "code_flow" and by[900]["workflow_kind"] is None


def test_run_now_strips_zone_keys():
    assert sr._without_schedule_zones({"timezone@9": "America/Toronto", "a": 1}) == {"a": 1}
    assert sr._SCHEDULE_TZ_PREFIX == js.SCHEDULE_TZ_PREFIX        # routes and engine agree


# ------------------------------------------------------------- code flows

def test_code_flow_cron_keeps_the_users_zone(monkeypatch):
    from codeflows import api as cf_api
    cur = _Cursor([("SELECT @@IDENTITY", [(55,)])])
    monkeypatch.setattr(cf_api, "_get_manager", lambda: MagicMock(_db_conn=lambda: _Conn(cur)))
    monkeypatch.setattr(sr, "_create_schedule", lambda c, job_id, data: 901)
    resp, code = cf_api._create_code_flow_schedule(
        "aging", 12, {"type": "cron", "cron_expression": "30 7 * * 1-5"}, {}, user_id=1,
        username="u", timezone="America/Toronto")
    assert code == 201 and resp["timezone"] == "America/Toronto"
    assert _zone_writes(cur) == [(55, "timezone@901", "America/Toronto")]
    resp, code = cf_api._create_code_flow_schedule(
        "aging", 12, {"type": "cron", "cron_expression": "0 7 * * *"}, {}, user_id=1,
        username="u", timezone="Nowhere/City")
    assert code == 400
