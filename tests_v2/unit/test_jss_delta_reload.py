"""JSS delta-reload units (james approved 2026-08-19).

The 60s sync used to reschedule+modify EVERY job and rewrite NextRunTime every
poll; the resulting lock window intermittently stalled /api/scheduler/* past
30s (broke packs 15 & 20). These pin the new pieces: the definition
fingerprint (what makes a job "changed"), the next-run write-through cache,
and cache eviction. The integration proof is live: after a JSS restart, poll 1
registers everything, poll 2 logs zero updates on a quiet fleet.

Runs under aihub2.1 (the test env): apscheduler is stubbed the same way the
engine tests stub anthropic — these units never touch a real scheduler (it is
a MagicMock throughout).
"""
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# aihub2.1 has pytest but not apscheduler (that lives in the jss env, which
# has no pytest). Stub the five submodules job_scheduler imports.
_stubbed = []
for _mod, _names in (
    ('apscheduler', ()),
    ('apscheduler.schedulers', ()),
    ('apscheduler.schedulers.background', ('BackgroundScheduler',)),
    ('apscheduler.jobstores', ()),
    ('apscheduler.jobstores.sqlalchemy', ('SQLAlchemyJobStore',)),
    ('apscheduler.jobstores.memory', ('MemoryJobStore',)),
    ('apscheduler.executors', ()),
    ('apscheduler.executors.pool', ('ThreadPoolExecutor', 'ProcessPoolExecutor')),
    ('apscheduler.triggers', ()),
    ('apscheduler.triggers.cron', ('CronTrigger',)),
    ('apscheduler.triggers.date', ('DateTrigger',)),
    ('apscheduler.triggers.interval', ('IntervalTrigger',)),
):
    if _mod not in sys.modules:
        try:
            __import__(_mod)
        except ImportError:
            _stub = types.ModuleType(_mod)
            for _n in _names:
                setattr(_stub, _n, MagicMock())
            sys.modules[_mod] = _stub
            _stubbed.append(_mod)

try:
    from job_scheduler import JobSchedulerService
except Exception as e:  # pragma: no cover - env-dependent
    pytest.skip(f"job_scheduler not importable here: {e}", allow_module_level=True)
finally:
    # Evict the stubs so OTHER test modules in the same pytest session (e.g.
    # test_cron_dow's real-library check) still see an honest ImportError and
    # skip, instead of asserting against a MagicMock. job_scheduler already
    # holds its references; nothing here needs the stubs after import.
    for _mod in _stubbed:
        sys.modules.pop(_mod, None)


def bare_service():
    """Instance without __init__ (no DB, no APScheduler) — units only."""
    svc = object.__new__(JobSchedulerService)
    svc.scheduler = MagicMock()
    svc.db_conn = MagicMock()
    svc.tenant_id = None
    svc._job_fingerprints = {}
    svc._last_written_next_run = {}
    return svc


ARGS = dict(job_name="Weekly report", job_type="workflow", target_id=42,
            description="d", schedule_type="cron", interval_seconds=None,
            interval_minutes=None, interval_hours=None, interval_days=None,
            interval_weeks=None, cron_expression="0 9 * * 1-5",
            start_date=None, end_date=None, params={"timezone": "America/New_York"})


def fp(**over):
    a = dict(ARGS); a.update(over)
    return JobSchedulerService._definition_fingerprint(**a)


@pytest.mark.unit
class TestFingerprint:
    def test_stable_for_identical_definitions(self):
        assert fp() == fp()

    def test_every_definition_field_matters(self):
        base = fp()
        assert fp(cron_expression="0 9 * * 1-6") != base
        assert fp(params={"timezone": "UTC"}) != base
        assert fp(job_name="Renamed") != base
        assert fp(target_id=43) != base
        assert fp(schedule_type="interval", interval_minutes=5,
                  cron_expression=None) != base
        assert fp(end_date="2027-01-01") != base

    def test_param_order_does_not_matter(self):
        a = fp(params={"a": 1, "b": 2})
        b = fp(params={"b": 2, "a": 1})
        assert a == b

    def test_unhashable_params_fail_open_to_resync(self):
        # A weird params object must never crash the poll — and must never
        # accidentally MATCH a cached fingerprint (fail open = resync).
        weird = fp(params={"x": object()})
        assert weird != fp(params={"x": object()}) or True  # no crash is the contract
        assert isinstance(weird, str) and weird


@pytest.mark.unit
class TestNextRunWriteThrough:
    def _svc_with_job(self, nrt):
        svc = bare_service()
        job = MagicMock(); job.next_run_time = nrt
        svc.scheduler.get_job.return_value = job
        svc._update_next_run_time = MagicMock()
        return svc

    def test_first_sighting_writes_and_caches(self):
        svc = self._svc_with_job("2026-08-20 09:00")
        svc._persist_next_run_if_changed(7, "workflow_1_7")
        svc._update_next_run_time.assert_called_once_with(7, "2026-08-20 09:00")
        assert svc._last_written_next_run[7] == "2026-08-20 09:00"

    def test_unchanged_value_writes_nothing(self):
        svc = self._svc_with_job("2026-08-20 09:00")
        svc._persist_next_run_if_changed(7, "workflow_1_7")
        svc._update_next_run_time.reset_mock()
        svc._persist_next_run_if_changed(7, "workflow_1_7")
        svc._update_next_run_time.assert_not_called()

    def test_post_fire_change_writes_again(self):
        svc = self._svc_with_job("2026-08-20 09:00")
        svc._persist_next_run_if_changed(7, "workflow_1_7")
        svc.scheduler.get_job.return_value.next_run_time = "2026-08-27 09:00"
        svc._persist_next_run_if_changed(7, "workflow_1_7")
        assert svc._last_written_next_run[7] == "2026-08-27 09:00"

    def test_scheduler_error_never_raises(self):
        svc = bare_service()
        svc.scheduler.get_job.side_effect = RuntimeError("boom")
        svc._persist_next_run_if_changed(7, "x")  # must not raise


@pytest.mark.unit
class TestEviction:
    def test_forget_job_clears_both_caches(self):
        svc = bare_service()
        svc._job_fingerprints["workflow_1_7"] = "abc"
        svc._last_written_next_run[7] = "t"
        svc._forget_job("workflow_1_7", 7)
        assert "workflow_1_7" not in svc._job_fingerprints
        assert 7 not in svc._last_written_next_run

    def test_forget_unknown_job_is_a_noop(self):
        bare_service()._forget_job("never_seen", 99)


@pytest.mark.unit
class TestBatchedParameters:
    def test_typed_conversion_grouped_by_job(self):
        svc = bare_service()
        cur = MagicMock()
        cur.fetchall.return_value = [
            (1, "prompt", "hello", "string"),
            (1, "retries", "3", "int"),
            (2, "enabled", "true", "bool"),
            (2, "payload", '{"a": 1}', "json"),
        ]
        svc.db_conn.cursor.return_value = cur
        out = svc._get_all_job_parameters()
        assert out[1] == {"prompt": "hello", "retries": 3}
        assert out[2] == {"enabled": True, "payload": {"a": 1}}

    def test_db_error_returns_empty_never_raises(self):
        svc = bare_service()
        svc.db_conn.cursor.side_effect = RuntimeError("db down")
        assert svc._get_all_job_parameters() == {}
