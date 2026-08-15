"""cron_dow — the shared day-of-week normalizer behind the scheduler root fix.

Why this exists: APScheduler's CronTrigger day_of_week numbering is 0=MONDAY,
and from_crontab does NOT remap standard crontab's 0=SUNDAY input despite its
name. The engine (job_scheduler.py) fed it stored standard-crontab strings, so
'0 9 * * 1-5' ("weekdays") fired Tue-Sat — nine live schedules were a day
late, including weekday-named automations that executed on Saturday
2026-08-15. The fix normalizes numeric day-of-week to NAMES (same days under
both conventions) at the engine's trigger callsite, and The Agent's
schedule_view_email shares the same implementation producer-side.

The apscheduler-dependent tests self-skip where the library isn't installed
(it lives in the `jss` env, not `aihub-agent`); run this file under BOTH.

Runs standalone (python test_cron_dow.py) or under pytest.
"""
import os
import sys

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

from cron_dow import normalize_cron_dow  # noqa: E402


# ---------------------------------------------------------------------------
# Pure mapping
# ---------------------------------------------------------------------------

def test_the_shapes_schedules_actually_use():
    """Every affected live expression on this install, translated."""
    assert normalize_cron_dow("0 9 * * 1-5") == "0 9 * * mon-fri"
    assert normalize_cron_dow("0 8 * * 1-5") == "0 8 * * mon-fri"
    assert normalize_cron_dow("30 6 * * 1-5") == "30 6 * * mon-fri"
    assert normalize_cron_dow("0 6 * * 1") == "0 6 * * mon"
    assert normalize_cron_dow("0 22 * * 0") == "0 22 * * sun"
    assert normalize_cron_dow("30 7 * * 1,3") == "30 7 * * mon,wed"


def test_standard_cron_numbering_zero_and_seven_are_sunday():
    assert normalize_cron_dow("0 0 * * 0") == "0 0 * * sun"
    assert normalize_cron_dow("0 0 * * 7") == "0 0 * * sun"
    assert normalize_cron_dow("0 0 * * 6") == "0 0 * * sat"
    assert normalize_cron_dow("0 0 * * 0,3,6") == "0 0 * * sun,wed,sat"
    assert normalize_cron_dow("0 0 * * 5-7") == "0 0 * * fri-sun"


def test_pass_through_never_makes_a_parseable_field_unparseable():
    """APScheduler's weekday-NAME expression has no step support, so step
    syntax must NOT be translated; names, wildcards and junk pass through."""
    for expr in ("0 9 * * *", "0 9 * * */2", "0 9 * * 1-5/2",
                 "0 9 * * mon-fri", "0 9 * * MON", "0 9 * * 1-mon",
                 "*/5 * * * *", "0 9 * *", "0 9 * * 1-5 extra",
                 "nonsense", "", None, "0 9 * * 8-9"):
        assert normalize_cron_dow(expr) == expr, expr


def test_other_fields_are_never_touched():
    assert normalize_cron_dow("1 2 3 4 5") == "1 2 3 4 fri"
    assert normalize_cron_dow("*/10 0-6 1,15 * 1-5") == "*/10 0-6 1,15 * mon-fri"


# ---------------------------------------------------------------------------
# Both consumers bind to the SAME implementation
# ---------------------------------------------------------------------------

def test_the_agent_tool_shares_this_implementation():
    try:
        import views_tools
    except Exception as e:
        print(f"SKIP (agent deps not installed in this env: {e})")
        return
    import cron_dow
    assert views_tools.normalize_cron_dow is cron_dow.normalize_cron_dow


# ---------------------------------------------------------------------------
# Engine semantics (self-skip where apscheduler isn't installed).
# job_scheduler itself is deliberately NOT imported: its module level rotates
# the live service's log file, which must not happen from a test process.
# The engine callsite is asserted at source level instead.
# ---------------------------------------------------------------------------

def test_engine_source_normalizes_before_building_the_trigger():
    src = open(os.path.join(APP_ROOT, "job_scheduler.py"), encoding="utf-8").read()
    assert "from cron_dow import normalize_cron_dow" in src
    assert "_normalize_cron_dow(cron_expression)" in src
    # the trigger must be built from the NORMALIZED expression
    assert "CronTrigger.from_crontab(\n                    _cron_norm," in src
    assert "CronTrigger.from_crontab(\n                    cron_expression," not in src


def test_normalized_weekdays_fire_mon_to_fri_in_the_engine_library():
    try:
        from apscheduler.triggers.cron import CronTrigger
    except Exception as e:
        print(f"SKIP (apscheduler not installed in this env: {e})")
        return
    from datetime import datetime
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("America/New_York")
    base = datetime(2026, 8, 14, 18, 30, tzinfo=tz)      # Friday evening

    def days(expr, n=5):
        t = CronTrigger.from_crontab(normalize_cron_dow(expr), timezone=tz)
        out, prev = [], None
        for _ in range(n):
            prev = t.get_next_fire_time(prev, base if prev is None else prev)
            out.append(prev.strftime("%a"))
        return out

    assert days("0 9 * * 1-5") == ["Mon", "Tue", "Wed", "Thu", "Fri"]
    assert days("0 22 * * 0", n=2) == ["Sun", "Sun"]
    assert days("0 6 * * 1", n=2) == ["Mon", "Mon"]
    assert days("30 7 * * 1,3", n=4) == ["Mon", "Wed", "Mon", "Wed"]
    # untranslated numerics would have given Tue-Sat — prove the raw library
    # really is shifted, so this test fails loudly if that ever changes
    raw = CronTrigger.from_crontab("0 9 * * 1-5", timezone=tz)
    assert raw.get_next_fire_time(None, base).strftime("%a") == "Sat"


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                passed += 1
                print(f"PASS {name}")
            except Exception as e:
                failed += 1
                print(f"FAIL {name}: {e}")
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
