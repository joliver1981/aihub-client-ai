"""schedule_agent_task — bounded recurrence + engine-native cron timezone (2026-08-22).

Pinned down here, with the scheduler REST stubbed (httpx.MockTransport; the tool
imports httpx per call, so patching httpx.AsyncClient is picked up):

  * every_minutes -> interval schedule with interval_minutes + an anchored start_date
  * for_minutes  -> end_date (UTC, just past the last planned fire) AND
                    max_runs = floor(window / interval)  — both engine-native
  * occurrences  -> max_runs AND the derived end_date
  * cron is stored AS WRITTEN plus an engine-canonical parameters.timezone — the
    engine applies the zone at fire time (DST-aware). It must NOT be pre-shifted
    to UTC: the engine re-applies the zone and the job fires double-shifted
    (live repro 2026-08-22: job 453, 7am Eastern stored as 11 UTC + tz
    America/New_York -> engine next run 15:00 UTC).
  * existing shapes (every_hours, run_in_minutes) unchanged; a one-shot refuses a bound
  * honesty: no active row -> NOT scheduled; bound not recorded -> job DELETED +
    NOT scheduled; HTTP failure -> NOT scheduled
  * Part 2 seam: the originating chat session_id rides in the job parameters

Runs standalone (C:\\Users\\james\\miniconda3\\envs\\aihub-agent\\python.exe
test_agent_schedule_bounded.py) or under pytest in an env with claude_agent_sdk;
in an env WITHOUT the SDK (main-app pytest sweep) every test self-skips.
"""
import asyncio
import datetime as dt
import json
import os
import re
import sys

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

try:
    import httpx                       # noqa: E402
    import work_tools as W             # noqa: E402
    from platform_tools import CURRENT_USER  # noqa: E402
    HAVE_SDK = True
except ImportError as e:               # main-env pytest sweep: no claude_agent_sdk
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


class Sched:
    """Fake scheduler REST: records the POST body, serves a read-back, accepts DELETE.
    Default read-back echoes the posted schedule as ONE active row (what the real
    route returns after a successful create)."""

    def __init__(self, readback=None, post_status=201, post_json=None):
        self.posted, self.deleted = None, []
        self.readback, self.post_status = readback, post_status
        self.post_json = {"id": "4242"} if post_json is None else post_json

    def handler(self, request):
        path = request.url.path
        if request.method == "POST" and path == "/api/scheduler/jobs":
            self.posted = json.loads(request.content.decode())
            return httpx.Response(self.post_status, json=self.post_json)
        if request.method == "GET" and path.startswith("/api/scheduler/jobs/"):
            rb = self.readback
            if rb is None:
                sch = dict((self.posted or {}).get("schedule") or {})
                row = {"is_active": True, "type": sch.get("type"),
                       "interval_minutes": sch.get("interval_minutes"),
                       "interval_hours": sch.get("interval_hours"),
                       "interval_days": sch.get("interval_days"),
                       "cron_expression": sch.get("cron_expression"),
                       "start_date": sch.get("start_date"),
                       "end_date": sch.get("end_date"),
                       "max_runs": sch.get("max_runs"), "current_runs": 0}
                rb = {"id": 4242, "schedules": [row],
                      "parameters": (self.posted or {}).get("parameters") or {}}
            return httpx.Response(200, json=rb)
        if request.method == "DELETE" and path.startswith("/api/scheduler/jobs/"):
            self.deleted.append(path)
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, json={"error": "unexpected " + path})


def _call(args, sched=None, user=None):
    sched = sched or Sched()
    orig = httpx.AsyncClient
    httpx.AsyncClient = lambda **kw: _RealAsyncClient(
        transport=httpx.MockTransport(sched.handler))
    CURRENT_USER.set(user or {"user_id": 7, "role": 3, "username": "unit"})
    try:
        res = asyncio.run(W.schedule_agent_task.handler(args))
    finally:
        httpx.AsyncClient = orig
    return res, sched


def _txt(res):
    return res["content"][0]["text"]


def _ts(s):
    return dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def _near(ts, expected, tol=8):
    return abs((_ts(ts) - expected).total_seconds()) <= tol


BASE = {"task_prompt": "say hi and stop", "name": "unit job"}


# ------------------------------------------------------------------ builder

def test_every_minutes_builds_interval_with_anchor():
    now = dt.datetime.utcnow()
    res, s = _call(dict(BASE, every_minutes=10))
    assert not res.get("is_error"), _txt(res)
    sch = s.posted["schedule"]
    assert sch["type"] == "interval" and sch["interval_minutes"] == 10
    assert _near(sch["start_date"], now)
    assert "end_date" not in sch and "max_runs" not in sch
    assert s.posted["type"] == "agent_session" and s.posted["target_id"] == "0"
    t = _txt(res)
    assert "job #4242" in t and "every 10 minutes" in t and "verified active" in t


def test_for_minutes_sets_end_date_and_max_runs():
    now = dt.datetime.utcnow()
    res, s = _call(dict(BASE, every_minutes=10, for_minutes=60))
    assert not res.get("is_error"), _txt(res)
    sch = s.posted["schedule"]
    assert sch["interval_minutes"] == 10
    assert _near(sch["end_date"], now + dt.timedelta(minutes=60, seconds=W._BOUND_SLACK_SECONDS))
    assert sch["max_runs"] == 6
    t = _txt(res)
    assert "about 6 run" in t and "stops by" in t and "first run ~10 min" in t
    assert "job #4242" in t


def test_occurrences_sets_max_runs_and_end_date():
    now = dt.datetime.utcnow()
    res, s = _call(dict(BASE, every_minutes=5, occurrences=12))
    assert not res.get("is_error"), _txt(res)
    sch = s.posted["schedule"]
    assert sch["max_runs"] == 12
    assert _near(sch["end_date"], now + dt.timedelta(minutes=60, seconds=W._BOUND_SLACK_SECONDS))
    assert "about 12 run" in _txt(res)


def test_both_bounds_take_the_tighter_one():
    res, s = _call(dict(BASE, every_minutes=10, for_minutes=60, occurrences=3))
    sch = s.posted["schedule"]
    assert sch["max_runs"] == 3            # min(6 by window, 3 by count)
    assert "about 3 run" in _txt(res)


def test_bound_shorter_than_interval_refused():
    res, s = _call(dict(BASE, every_minutes=10, for_minutes=5))
    assert res.get("is_error") and "shorter than the interval" in _txt(res)
    assert s.posted is None                # nothing reached the scheduler


def test_one_shot_refuses_bound():
    res, s = _call(dict(BASE, run_in_minutes=3, for_minutes=10))
    assert res.get("is_error") and "fires exactly once" in _txt(res)
    assert s.posted is None


def test_one_shot_unchanged():
    now = dt.datetime.utcnow()
    res, s = _call(dict(BASE, run_in_minutes=3))
    assert not res.get("is_error"), _txt(res)
    sch = s.posted["schedule"]
    assert sch["type"] == "date" and _near(sch["start_date"], now + dt.timedelta(minutes=3))
    assert "fires ONCE" in _txt(res) and "job #4242" in _txt(res)


def test_every_hours_and_days_unchanged():
    now = dt.datetime.utcnow()
    res, s = _call(dict(BASE, every_hours=6))
    sch = s.posted["schedule"]
    assert sch["type"] == "interval" and sch["interval_hours"] == 6 and _near(sch["start_date"], now)
    assert "end_date" not in sch and "max_runs" not in sch
    assert "every 6 hours" in _txt(res)
    res, s = _call(dict(BASE, every_days=2))
    assert s.posted["schedule"]["interval_days"] == 2 and "every 2 days" in _txt(res)


def test_no_cadence_refused():
    res, s = _call(dict(BASE))
    assert res.get("is_error") and "Provide cron_expression" in _txt(res)
    assert s.posted is None


# -------------------------------------------------------------------- cron

def test_cron_stored_as_written_with_engine_timezone():
    res, s = _call(dict(BASE, cron_expression="0 7 * * 1-5", timezone="Eastern"))
    assert not res.get("is_error"), _txt(res)
    sch = s.posted["schedule"]
    assert sch["type"] == "cron"
    assert sch["cron_expression"] == "0 7 * * 1-5"          # NOT pre-shifted to UTC
    params = s.posted["parameters"]
    assert params["timezone"]["value"] == "America/New_York"
    assert params["local_cron"]["value"] == "0 7 * * 1-5"
    assert "America/New_York" in _txt(res) and "DST-aware" in _txt(res)


def test_cron_fixed_offset_and_utc_labels():
    res, s = _call(dict(BASE, cron_expression="30 9 * * *", timezone="-05:00"))
    assert s.posted["schedule"]["cron_expression"] == "30 9 * * *"
    assert s.posted["parameters"]["timezone"]["value"] == "UTC-05:00"
    assert "fixed offset UTC-05:00" in _txt(res)
    res, s = _call(dict(BASE, cron_expression="30 9 * * *", timezone="UTC"))
    assert s.posted["parameters"]["timezone"]["value"] == "UTC"
    assert s.posted["schedule"]["cron_expression"] == "30 9 * * *"


def test_cron_server_local_default_is_engine_parsable():
    saved = os.environ.pop("AGENT_DEFAULT_TZ", None)
    try:
        res, s = _call(dict(BASE, cron_expression="0 9 * * *"))
    finally:
        if saved is not None:
            os.environ["AGENT_DEFAULT_TZ"] = saved
    assert not res.get("is_error"), _txt(res)
    tz = s.posted["parameters"]["timezone"]["value"]
    assert re.fullmatch(r"UTC[+-]\d\d:\d\d", tz), tz   # schedule_tz.to_tzinfo accepts this
    assert s.posted["schedule"]["cron_expression"] == "0 9 * * *"


def test_cron_with_for_minutes_sets_end_date_only():
    now = dt.datetime.utcnow()
    res, s = _call(dict(BASE, cron_expression="*/10 * * * *", timezone="UTC", for_minutes=60))
    assert not res.get("is_error"), _txt(res)
    sch = s.posted["schedule"]
    assert sch["cron_expression"] == "*/10 * * * *"
    assert _near(sch["end_date"], now + dt.timedelta(minutes=60, seconds=W._BOUND_SLACK_SECONDS))
    assert "max_runs" not in sch
    assert "stops by" in _txt(res)


def test_cron_with_occurrences_sets_max_runs_only():
    res, s = _call(dict(BASE, cron_expression="0 9 * * *", timezone="UTC", occurrences=3))
    sch = s.posted["schedule"]
    assert sch["max_runs"] == 3 and "end_date" not in sch
    assert "stops after 3 run" in _txt(res)


def test_unknown_timezone_and_bad_cron_refused():
    res, s = _call(dict(BASE, cron_expression="0 9 * * *", timezone="Narnia"))
    assert res.get("is_error") and "unknown timezone" in _txt(res) and s.posted is None
    res, s = _call(dict(BASE, cron_expression="0 9 * *", timezone="UTC"))
    assert res.get("is_error") and "5 fields" in _txt(res) and s.posted is None


# ----------------------------------------------------------------- honesty

def test_no_active_row_is_not_scheduled():
    res, s = _call(dict(BASE, every_minutes=10), sched=Sched(readback={"schedules": []}))
    assert res.get("is_error") and "NOT scheduled" in _txt(res)


def test_bound_not_recorded_deletes_job_and_reports_not_scheduled():
    rb = {"schedules": [{"is_active": True, "type": "interval", "interval_minutes": 10,
                         "end_date": None, "max_runs": None}]}
    res, s = _call(dict(BASE, every_minutes=10, for_minutes=60), sched=Sched(readback=rb))
    assert res.get("is_error")
    assert "did NOT record the requested bound" in _txt(res) and "NOT scheduled" in _txt(res)
    assert "/api/scheduler/jobs/4242" in s.deleted       # never leave an unbounded job


def test_unbounded_job_tolerates_no_bound_columns():
    rb = {"schedules": [{"is_active": True, "type": "interval", "interval_minutes": 10}]}
    res, s = _call(dict(BASE, every_minutes=10), sched=Sched(readback=rb))
    assert not res.get("is_error"), _txt(res)
    assert not s.deleted


def test_http_failure_is_honest():
    res, s = _call(dict(BASE, every_minutes=10),
                   sched=Sched(post_status=500, post_json={"error": "boom"}))
    assert res.get("is_error") and "Nothing was scheduled" in _txt(res)


def test_role_gate():
    saved = os.environ.pop("AGENT_BUILD_ALLOW_ALL_USERS", None)
    try:
        res, s = _call(dict(BASE, every_minutes=10),
                       user={"user_id": 7, "role": 1, "username": "viewer"})
    finally:
        if saved is not None:
            os.environ["AGENT_BUILD_ALLOW_ALL_USERS"] = saved
    assert res.get("is_error") and "Developer role" in _txt(res) and s.posted is None


# ------------------------------------------------------- Part 2 seam + schema

def test_session_id_param_captured_from_chat_session():
    saved = os.environ.pop("AGENT_DEFER_TO_CHAT", None)
    try:
        res, s = _call(dict(BASE, every_minutes=10),
                       user={"user_id": 7, "role": 3, "username": "unit",
                             "session_id": "abc-123"})
        assert s.posted["parameters"]["session_id"]["value"] == "abc-123"
        assert "appends it to this conversation" in _txt(res)
        # a plain headless run (fresh session) does NOT pin its throwaway session
        res, s = _call(dict(BASE, every_minutes=10),
                       user={"user_id": 7, "role": 3, "username": "unit",
                             "session_id": "headless-9", "mode": "headless"})
        assert "session_id" not in s.posted["parameters"]
        assert "conversation" not in _txt(res)
        # a headless run that RESUMED a chat chains into that chat
        res, s = _call(dict(BASE, every_minutes=10),
                       user={"user_id": 7, "role": 3, "username": "unit",
                             "session_id": "chat-1", "mode": "headless",
                             "chat_session_id": "chat-1"})
        assert s.posted["parameters"]["session_id"]["value"] == "chat-1"
        # flag off: the id is still recorded (provenance) but the promise is not made
        os.environ["AGENT_DEFER_TO_CHAT"] = "false"
        res, s = _call(dict(BASE, every_minutes=10),
                       user={"user_id": 7, "role": 3, "username": "unit",
                             "session_id": "abc-123"})
        assert s.posted["parameters"]["session_id"]["value"] == "abc-123"
        assert "conversation" not in _txt(res)
    finally:
        os.environ.pop("AGENT_DEFER_TO_CHAT", None)
        if saved is not None:
            os.environ["AGENT_DEFER_TO_CHAT"] = saved


def test_tool_schema_and_description_teach_the_bound():
    tool = W.schedule_agent_task
    schema = getattr(tool, "input_schema", None) or getattr(tool, "schema", None) or {}
    props = schema.get("properties") or {}
    for k in ("every_minutes", "for_minutes", "occurrences", "run_in_minutes",
              "every_hours", "every_days", "cron_expression", "timezone"):
        assert k in props, k
    desc = getattr(tool, "description", "") or ""
    assert "for_minutes" in desc and "occurrences" in desc and "every 10 minutes" in desc
    import brain
    assert "schedule_agent_task" in brain.MUTATING_TOOLS
    assert "for_minutes" in brain.SYSTEM_PROMPT


def test_portal_tool_uses_the_same_builder():
    """schedule_portal_workflow shares _build_schedule: same engine-native
    timezone contract and the same bounds (smoke via the builder itself)."""
    import portal_tools as PT
    assert PT._build_schedule is W._build_schedule
    plan = W._build_schedule({"every_minutes": 15, "for_minutes": 45})
    assert plan["schedule"]["interval_minutes"] == 15 and plan["schedule"]["max_runs"] == 3
    props = (getattr(PT.schedule_portal_workflow, "input_schema", None) or {}).get("properties") or {}
    for k in ("every_minutes", "for_minutes", "occurrences"):
        assert k in props, k


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
