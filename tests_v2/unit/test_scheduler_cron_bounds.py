"""Cron schedules with a start/end date, and per-schedule isolation in the sync poll.

Found 2026-10-07: _create_trigger assigned the DB's naive-UTC StartDate/EndDate
straight onto a CronTrigger, skipping CronTrigger's own tz conversion. add_job
then raised "can't compare offset-naive and offset-aware datetimes", and since
the sync loop had no per-row handling, that one bounded cron (job 663, created
2026-09-06) stopped every later schedule from ever registering.

Same JobSchedulerService.__new__ pattern as test_scheduler_target_reaper.py.
The cron-bound tests need the REAL apscheduler (the scheduler's own `jss` env);
they skip where only the stub is importable.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from test_scheduler_target_reaper import _import_job_scheduler  # noqa: E402


def _real_apscheduler(js):
    if not isinstance(js.CronTrigger, type):
        pytest.skip("real apscheduler not installed in this env (runs in the jss env)")


def _bare_service(js):
    svc = js.JobSchedulerService.__new__(js.JobSchedulerService)
    svc._job_fingerprints = {}
    svc._expired_onetime = {}
    svc._last_written_next_run = {}
    svc._sync_row_errors = {}
    return svc


class TestCronBounds:
    def test_past_end_date_registers_without_typeerror(self):
        js = _import_job_scheduler()
        _real_apscheduler(js)
        svc = _bare_service(js)
        # job 663's real shape: weekday cron, America/New_York, end date passed
        trig = svc._create_trigger("cron", cron_expression="30 7 * * 1-5",
                                   end_date=datetime(2026, 9, 20, 0, 44, 41),
                                   tz_name="America/New_York")
        assert trig.end_date.tzinfo is not None
        now = datetime(2026, 10, 7, 19, 0, tzinfo=timezone.utc)
        assert trig.get_next_fire_time(None, now) is None   # expired, no crash

    def test_future_end_date_still_fires_until_then(self):
        js = _import_job_scheduler()
        _real_apscheduler(js)
        svc = _bare_service(js)
        now = datetime(2026, 10, 7, 19, 0, tzinfo=timezone.utc)   # a Wednesday
        trig = svc._create_trigger("cron", cron_expression="30 7 * * 1-5",
                                   end_date=datetime(2026, 10, 21, 0, 0),
                                   tz_name="America/New_York")
        nxt = trig.get_next_fire_time(None, now)
        # Thu 2026-10-08 07:30 EDT == 11:30 UTC
        assert nxt is not None
        assert nxt.astimezone(timezone.utc) == datetime(2026, 10, 8, 11, 30, tzinfo=timezone.utc)

    def test_naive_start_date_is_read_as_utc(self):
        js = _import_job_scheduler()
        _real_apscheduler(js)
        svc = _bare_service(js)
        now = datetime(2026, 10, 7, 19, 0, tzinfo=timezone.utc)
        trig = svc._create_trigger("cron", cron_expression="0 * * * *",
                                   start_date=datetime(2026, 10, 9, 15, 0))
        nxt = trig.get_next_fire_time(None, now)
        assert nxt.astimezone(timezone.utc) == datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)

    def test_as_utc_aware_leaves_aware_values_alone(self):
        js = _import_job_scheduler()
        aware = datetime(2026, 10, 7, 12, 0, tzinfo=timezone(timedelta(hours=-4)))
        assert js._as_utc_aware(aware) is aware
        assert js._as_utc_aware(datetime(2026, 10, 7, 12, 0)).tzinfo is timezone.utc


class _Cursor:
    def __init__(self, rows):
        self._rows, self._next = rows, []

    def execute(self, sql, *params):
        self._next = self._rows if "WHERE j.IsActive = 1 AND s.IsActive = 1" in sql else []

    def fetchall(self):
        return self._next

    def fetchone(self):
        return (0,)

    def close(self):
        pass


class _Conn:
    def __init__(self, rows):
        self.main = _Cursor(rows)

    def cursor(self):
        return _Cursor([])          # orphan-cleanup pass: no rows

    def close(self):
        pass


def _row(job_id, schedule_id, cron):
    # same 19 columns, same order as the sync query
    return (job_id, f"Agent: job {job_id}", "agent_session", 0, "", schedule_id, "cron",
            None, None, None, None, None, cron, None, None, None, None, 0, True)


class TestSyncIsolation:
    def test_one_bad_schedule_does_not_block_the_rest(self, monkeypatch):
        js = _import_job_scheduler()
        svc = _bare_service(js)
        svc.job_types = {"agent_session": lambda data: None}
        svc.scheduler = MagicMock()
        svc.scheduler.get_job.return_value = None

        def add_job(func, trigger=None, id=None, args=None, replace_existing=None):
            if id == "agent_session_663_640":
                raise TypeError("can't compare offset-naive and offset-aware datetimes")
        svc.scheduler.add_job.side_effect = add_job

        rows = [_row(663, 640, "30 7 * * 1-5"), _row(664, 641, "0 9 * * 1")]
        conn = _Conn(rows)
        monkeypatch.setattr(svc, "_db_cursor", lambda: (conn, conn.main))
        monkeypatch.setattr(svc, "_reap_orphaned_target_jobs", lambda c: None)
        monkeypatch.setattr(svc, "_get_all_job_parameters", lambda: {})
        monkeypatch.setattr(svc, "_get_job_parameters", lambda job_id: {})
        monkeypatch.setattr(svc, "_create_trigger", lambda *a, **k: object())
        monkeypatch.setattr(svc, "_persist_next_run_if_changed", lambda *a: None)
        log = MagicMock()
        monkeypatch.setattr(js, "logger", log)

        svc._update_schedules_from_db()
        svc._update_schedules_from_db()          # second poll: no repeat log

        added = [c.kwargs["id"] for c in svc.scheduler.add_job.call_args_list]
        assert added.count("agent_session_664_641") == 2      # later row registered
        assert "agent_session_664_641" in svc._job_fingerprints
        assert "agent_session_663_640" not in svc._job_fingerprints
        assert "TypeError" in svc._sync_row_errors["agent_session_663_640"]
        skipped = [c for c in log.error.call_args_list
                   if "could not be registered" in str(c.args[0])]
        assert len(skipped) == 1
        aborted = [c for c in log.error.call_args_list
                   if "Error updating schedules from database" in str(c.args[0])]
        assert aborted == []
