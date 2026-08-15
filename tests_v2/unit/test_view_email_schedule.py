"""schedule_view_email — the "email me this dashboard every weekday at 9am" tool.

Two things are pinned down here:

  * TIMEZONE. schedule_view_refresh passes none, so its crons fire on the
    scheduler's default zone (UTC) and a user's "9am" is simply wrong. This tool
    must carry parameters.timezone, which job_scheduler.py reads to build a
    DST-aware CronTrigger — and must warn when the user gave a clock time with
    no zone.

  * FAIL AT SCHEDULE TIME, NOT AT 9AM. A job whose sender has no active address,
    or whose outbound is switched off, would fail silently every morning. Those
    checks belong at creation.

Runs standalone (python test_view_email_schedule.py) or under pytest.
"""
import asyncio
import json
import os
import sys

import httpx

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

import email_store  # noqa: E402
import readthrough  # noqa: E402
import views_store  # noqa: E402
import views_tools as V  # noqa: E402
from platform_tools import CURRENT_USER  # noqa: E402

_RealAsyncClient = httpx.AsyncClient
_handler = V.schedule_view_email.handler

VIEW = {"view_id": "v1", "name": "Ops Board", "scope": "user", "group_id": 0,
        "tiles": [], "version": 1}
ADDRESS = {"email_address": "j-agent.1@mail.everiai.ai", "prefix": "j",
           "is_active": 1, "outbound_enabled": 1, "auto_send": 1}


def _call(args, view=VIEW, address=ADDRESS):
    """Invoke the tool with the scheduler API stubbed; returns (result, posted)."""
    posted = {}

    def handle(request):
        if request.method == "POST":
            posted.update(json.loads(request.content.decode()))
            return httpx.Response(200, json={"id": 4242})
        return httpx.Response(200, json={"schedules": [{"is_active": True}]})

    saved = (views_store.get, email_store.get_address,
             readthrough.user_group_ids, V.httpx.AsyncClient
             if hasattr(V, "httpx") else None)
    views_store.get = lambda *a, **k: view
    email_store.get_address = lambda uid: address
    readthrough.user_group_ids = lambda uid: []
    import httpx as _h
    orig_client = _h.AsyncClient
    _h.AsyncClient = lambda **kw: _RealAsyncClient(
        transport=httpx.MockTransport(handle))
    CURRENT_USER.set({"user_id": 13, "role": 3, "username": "admin"})
    try:
        result = asyncio.run(_handler(args))
    finally:
        views_store.get, email_store.get_address, readthrough.user_group_ids = saved[:3]
        _h.AsyncClient = orig_client
    return result, posted


def _says(result):
    return " ".join(b.get("text", "") for b in result.get("content", []))


def _is_error(result):
    return bool(result.get("isError") or result.get("is_error"))


# ---------------------------------------------------------------------------
# Timezone
# ---------------------------------------------------------------------------

def test_a_spoken_timezone_is_resolved_and_carried_as_a_job_parameter():
    result, posted = _call({"name": "Ops Board", "to": ["j@x.co"],
                            "cron_expression": "0 9 * * 1-5",
                            "timezone": "Eastern"})
    assert not _is_error(result), _says(result)
    assert posted["parameters"]["timezone"]["value"] == "America/New_York"
    assert posted["type"] == "view_email"
    assert "Eastern" in _says(result) or "America/New_York" in _says(result)


def test_a_cron_without_a_timezone_warns_instead_of_silently_using_utc():
    result, posted = _call({"name": "Ops Board", "to": ["j@x.co"],
                            "cron_expression": "0 9 * * *"})
    assert not _is_error(result)
    assert "timezone" not in posted["parameters"]
    said = _says(result)
    assert "UTC" in said and "no timezone" in said.lower()


def test_weekday_cron_is_stored_as_day_names_not_numbers():
    """The engine parses with APScheduler's CronTrigger.from_crontab, whose
    day_of_week is 0=MONDAY, while standard crontab uses 0=SUNDAY — and it does
    NOT remap. So a stored '0 9 * * 1-5' fires Tue-Sat, verified on APScheduler
    3.11.0 and against nine live schedules on this install. Names mean the same
    thing under either numbering."""
    _, posted = _call({"name": "Ops Board", "to": ["j@x.co"],
                       "cron_expression": "0 9 * * 1-5"})
    assert posted["schedule"]["cron_expression"] == "0 9 * * mon-fri"


def test_dow_normalisation_covers_the_shapes_a_model_will_write():
    n = V.normalize_cron_dow
    assert n("0 9 * * 1-5") == "0 9 * * mon-fri"
    assert n("30 7 * * 1,3") == "30 7 * * mon,wed"
    assert n("0 22 * * 0") == "0 22 * * sun"
    assert n("0 6 * * 7") == "0 6 * * sun"        # 7 is Sunday too
    assert n("0 9 * * 6") == "0 9 * * sat"
    # left alone: wildcards, steps, names already used, and malformed input
    assert n("0 9 * * *") == "0 9 * * *"
    assert n("0 9 * * */2") == "0 9 * * */2"
    assert n("0 9 * * mon-fri") == "0 9 * * mon-fri"
    assert n("nonsense") == "nonsense"
    assert n("") == ""


def test_target_id_is_the_string_zero():
    """The scheduler route's presence check treats int 0 as missing."""
    _, posted = _call({"name": "Ops Board", "to": ["j@x.co"],
                       "cron_expression": "0 9 * * *"})
    assert posted["target_id"] == "0"


def test_recipients_and_principal_ride_along_for_the_headless_send():
    _, posted = _call({"name": "Ops Board", "to": ["a@x.co", "b@x.co"],
                       "cron_expression": "0 9 * * *"})
    p = posted["parameters"]
    assert p["to"]["value"] == "a@x.co,b@x.co"
    assert p["user_id"]["value"] == "13"
    assert p["view_name"]["value"] == "Ops Board"


# ---------------------------------------------------------------------------
# Fail at schedule time, not at 9am
# ---------------------------------------------------------------------------

def test_no_agent_address_refuses_to_schedule():
    result, posted = _call({"name": "Ops Board", "to": ["j@x.co"],
                            "cron_expression": "0 9 * * *"}, address=None)
    assert _is_error(result) and not posted
    assert "no active agent email address" in _says(result)


def test_outbound_disabled_refuses_to_schedule():
    off = dict(ADDRESS, outbound_enabled=0)
    result, posted = _call({"name": "Ops Board", "to": ["j@x.co"],
                            "cron_expression": "0 9 * * *"}, address=off)
    assert _is_error(result) and not posted
    assert "DISABLED" in _says(result)


def test_no_recipients_refuses_to_schedule():
    result, posted = _call({"name": "Ops Board", "to": [],
                            "cron_expression": "0 9 * * *"})
    assert _is_error(result) and not posted


def test_an_invisible_view_refuses_to_schedule():
    result, posted = _call({"name": "Nope", "to": ["j@x.co"],
                            "cron_expression": "0 9 * * *"}, view=None)
    assert _is_error(result) and not posted


def test_no_cadence_refuses_to_schedule():
    result, posted = _call({"name": "Ops Board", "to": ["j@x.co"]})
    assert _is_error(result) and not posted


# ---------------------------------------------------------------------------
# Honest reporting
# ---------------------------------------------------------------------------

def test_a_job_with_no_active_schedule_row_is_reported_as_not_scheduled():
    def handle(request):
        if request.method == "POST":
            return httpx.Response(200, json={"id": 7})
        return httpx.Response(200, json={"schedules": [{"is_active": False}]})

    import httpx as _h
    saved = (views_store.get, email_store.get_address, readthrough.user_group_ids)
    views_store.get = lambda *a, **k: VIEW
    email_store.get_address = lambda uid: ADDRESS
    readthrough.user_group_ids = lambda uid: []
    orig = _h.AsyncClient
    _h.AsyncClient = lambda **kw: _RealAsyncClient(
        transport=httpx.MockTransport(handle))
    CURRENT_USER.set({"user_id": 13, "role": 3, "username": "admin"})
    try:
        result = asyncio.run(_handler({"name": "Ops Board", "to": ["j@x.co"],
                                       "cron_expression": "0 9 * * *"}))
    finally:
        views_store.get, email_store.get_address, readthrough.user_group_ids = saved
        _h.AsyncClient = orig
    assert _is_error(result)
    assert "NOT scheduled" in _says(result)


def test_the_result_states_there_is_no_approval_step():
    """James's rule: scheduling it IS the consent."""
    result, _ = _call({"name": "Ops Board", "to": ["j@x.co"],
                       "cron_expression": "0 9 * * 1-5", "timezone": "Eastern"})
    assert "no approval step" in _says(result)


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
