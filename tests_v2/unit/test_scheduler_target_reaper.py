"""Target-existence reaper: scheduler jobs whose target row was deleted are
removed by the sync loop (TargetId is polymorphic, so no DB cascade can cover
e.g. Workflows -> ScheduledJobs; deleted workflows left schedules firing 404s
every 5 minutes forever).

Uses the same JobSchedulerService.__new__ pattern as test_automations.py —
no constructor, no DB, apscheduler stubbed if absent.
"""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _import_job_scheduler():
    """Import job_scheduler, stubbing apscheduler if the test env lacks it
    (the scheduler service runs in its own conda env)."""
    try:
        import apscheduler  # noqa: F401
    except ImportError:
        for mod in ["apscheduler", "apscheduler.schedulers", "apscheduler.schedulers.background",
                    "apscheduler.jobstores", "apscheduler.jobstores.sqlalchemy",
                    "apscheduler.jobstores.memory", "apscheduler.executors",
                    "apscheduler.executors.pool", "apscheduler.triggers",
                    "apscheduler.triggers.cron", "apscheduler.triggers.date",
                    "apscheduler.triggers.interval"]:
            if mod not in sys.modules:
                m = types.ModuleType(mod)
                for attr in ("BackgroundScheduler", "SQLAlchemyJobStore", "MemoryJobStore",
                             "ThreadPoolExecutor", "ProcessPoolExecutor",
                             "CronTrigger", "DateTrigger", "IntervalTrigger"):
                    setattr(m, attr, MagicMock())
                sys.modules[mod] = m
    import job_scheduler
    return job_scheduler


class _FakeCursor:
    def __init__(self, select_rows):
        self._select_rows = select_rows
        self.executed = []          # (sql, params) tuples
        self._last_was_select = False

    def execute(self, sql, *params):
        self.executed.append((" ".join(sql.split()), params))
        self._last_was_select = sql.lstrip().upper().startswith("SELECT")

    def fetchall(self):
        return self._select_rows if self._last_was_select else []

    def close(self):
        pass


class _FakeConn:
    def __init__(self, select_rows):
        self.cursor_obj = _FakeCursor(select_rows)
        self.commits = 0

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commits += 1


def _svc(select_rows):
    js = _import_job_scheduler()
    svc = js.JobSchedulerService.__new__(js.JobSchedulerService)
    svc.scheduler = MagicMock()
    svc.scheduler.get_job.return_value = object()   # pretend job is registered
    svc._job_fingerprints = {}
    svc._expired_onetime = {}
    svc._last_written_next_run = {}
    return svc, _FakeConn(select_rows)


class TestTargetReaper:
    def test_reaps_orphan_job_with_multiple_schedules(self, monkeypatch):
        # One orphaned job (target gone) carrying two schedules -> ONE job
        # DELETE (cascades handle the rest), both APScheduler entries removed.
        monkeypatch.setenv("JOB_REAPER_MODE", "delete")
        rows = [
            (179, "Pricing Download Process", 1217, 279),
            (179, "Pricing Download Process", 1217, 280),
        ]
        svc, conn = _svc(rows)
        svc._reap_orphaned_target_jobs(conn)

        deletes = [e for e in conn.cursor_obj.executed if e[0].startswith("DELETE FROM ScheduledJobs")]
        assert len(deletes) == 1
        assert deletes[0][1] == (179,)
        removed = [c.args[0] for c in svc.scheduler.remove_job.call_args_list]
        assert removed == ["workflow_179_279", "workflow_179_280"]
        assert conn.commits == 1

    def test_no_orphans_means_no_writes(self, monkeypatch):
        monkeypatch.setenv("JOB_REAPER_MODE", "delete")
        svc, conn = _svc([])
        svc._reap_orphaned_target_jobs(conn)

        deletes = [e for e in conn.cursor_obj.executed if e[0].startswith("DELETE")]
        assert deletes == []
        assert conn.commits == 0
        svc.scheduler.remove_job.assert_not_called()

    def test_report_mode_is_default_and_never_deletes(self, monkeypatch):
        # Without JOB_REAPER_MODE=delete the reaper only logs — no DELETE, no
        # commit, no APScheduler removal — and reports each job only once.
        monkeypatch.delenv("JOB_REAPER_MODE", raising=False)
        rows = [(179, "Pricing Download Process", 1217, 279)]
        svc, conn = _svc(rows)
        svc._reap_orphaned_target_jobs(conn)
        svc._reap_orphaned_target_jobs(conn)   # second poll: no duplicate report

        deletes = [e for e in conn.cursor_obj.executed if e[0].startswith("DELETE")]
        assert deletes == []
        assert conn.commits == 0
        svc.scheduler.remove_job.assert_not_called()
        assert svc._reaper_reported == {179}

    def test_existence_check_is_left_join_on_target_table(self):
        # The reap decision must come from the DB (LEFT JOIN ... IS NULL),
        # never from an HTTP result — a transient failure must not delete.
        svc, conn = _svc([])
        svc._reap_orphaned_target_jobs(conn)

        selects = [e for e in conn.cursor_obj.executed if e[0].startswith("SELECT")]
        assert len(selects) == 1
        sql, params = selects[0]
        assert "LEFT JOIN [dbo].[Workflows]" in sql
        assert "IS NULL" in sql
        assert params == ("workflow",)

    def test_mapping_stays_fail_open(self):
        # Only types whose TargetId provably keys a table may be listed.
        # portal_workflow (slug in parameters) and automation (GUID in
        # parameters) must never appear; add a type ONLY with a verified
        # TargetId -> table mapping.
        js = _import_job_scheduler()
        mapping = js.JobSchedulerService.REAPABLE_TARGET_TABLES
        assert set(mapping) == {"workflow"}
        assert mapping["workflow"] == ("[dbo].[Workflows]", "id")
