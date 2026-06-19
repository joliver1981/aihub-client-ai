"""
Unit tests for CC self-scheduling: the schedule_logic core (scheduler-API payload + store
mirroring) and the scheduler 'command_center' job executor (param -> CC call mapping).
The scheduler HTTP API and the DB are stubbed; nothing live is contacted.

Run:
    python -m pytest tests/unit/test_cc_schedule.py -v
"""
import sys

import pytest

_SVC = r"C:/src/aihub-client-ai-dev/command_center_service"
_ROOT = r"C:/src/aihub-client-ai-dev"
for _p in (_SVC, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scheduling import schedule_logic as sl   # noqa: E402
from scheduling import schedule_store as store  # noqa: E402


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_dir", lambda: tmp_path)
    monkeypatch.setattr(sl, "_scheduler_base", lambda: "http://127.0.0.1:5001")
    monkeypatch.setattr(sl, "_api_key", lambda: "k")
    # Avoid any real /get/users network call during schedule creation.
    import user_lookup
    monkeypatch.setattr(user_lookup, "get_user_contact", lambda uid: {}, raising=False)
    return tmp_path


# --- schedule_logic.create_cc_schedule -------------------------------------

def test_create_cc_schedule_payload_and_store(isolated, monkeypatch):
    captured = {}

    class _Resp:
        status_code = 201

        def json(self):
            return {"id": 555}

    def _post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["payload"] = json
        return _Resp()

    monkeypatch.setattr(sl.requests, "post", _post)
    import user_lookup
    monkeypatch.setattr(user_lookup, "get_user_contact", lambda uid: {"email": "jo@x.com"})
    uc = {"user_id": 13, "tenant_id": 2, "role": 2, "username": "jo", "name": "Jo"}
    ad = {"agent_id": "7", "agent_name": "Orders"}
    res = sl.create_cc_schedule(uc, ad, "Daily orders", "pull orders",
                                {"type": "cron", "cron_expression": "0 8 * * 1-5"},
                                "cron '0 8 * * 1-5'")
    assert res["status"] == "ok" and res["job_id"] == 555
    p = captured["payload"]
    assert captured["url"].endswith("/api/scheduler/jobs")
    assert p["type"] == "command_center"
    assert p["target_id"] == 13
    assert p["schedule"]["cron_expression"] == "0 8 * * 1-5"
    assert p["parameters"]["prompt"]["value"] == "pull orders"
    assert p["parameters"]["agent_id"]["value"] == "7"
    assert p["parameters"]["user_id"]["value"] == "13"
    assert p["parameters"]["user_email"]["value"] == "jo@x.com"  # owner email snapshotted
    # mirrored into the per-user store
    assert store.get_task(13, "555")["task_name"] == "Daily orders"


def test_create_cc_schedule_error_does_not_store(isolated, monkeypatch):
    class _Resp:
        status_code = 500
        text = "boom"

        def json(self):
            return {}

    monkeypatch.setattr(sl.requests, "post", lambda *a, **k: _Resp())
    res = sl.create_cc_schedule({"user_id": 13}, {}, "T", "p",
                                {"type": "interval", "interval_hours": 6}, "every 6h")
    assert res["status"] == "error"
    assert store.list_tasks(13) == []  # nothing stored on failure


def test_interval_schedule_gets_start_date_anchor(isolated, monkeypatch):
    captured = {}

    class _Resp:
        status_code = 201

        def json(self):
            return {"id": 600}

    monkeypatch.setattr(sl.requests, "post",
                        lambda url, json=None, headers=None, timeout=None:
                        (captured.__setitem__("payload", json) or _Resp()))
    res = sl.create_cc_schedule({"user_id": 5}, {}, "Every 10m", "do it",
                                {"type": "interval", "interval_minutes": 10}, "every 10 min")
    assert res["status"] == "ok"
    sched = captured["payload"]["schedule"]
    assert sched["type"] == "interval" and sched["interval_minutes"] == 10
    # MUST be anchored, else the engine's reschedule poll drifts the next fire forever.
    assert sched.get("start_date")


def test_cancel_cc_schedule(isolated, monkeypatch):
    store.add_task(13, "555", "Daily orders", "p", "cron")
    monkeypatch.setattr(sl.requests, "delete",
                        lambda *a, **k: type("R", (), {"status_code": 200})())
    res = sl.cancel_cc_schedule({"user_id": 13}, "daily orders")  # by name
    assert res["status"] == "ok" and res["job_id"] == "555"
    assert store.get_task(13, "555") is None


def test_get_next_run_and_enriched_list(isolated, monkeypatch):
    store.add_task(13, "555", "Daily orders", "p", "cron '0 8 * * 1-5'")

    class _Resp:
        status_code = 200

        def json(self):
            return {"id": 555, "schedules": [
                {"is_active": True, "next_run_time": "2026-06-20T08:00:00"},
                {"is_active": False, "next_run_time": "2026-06-19T08:00:00"},  # inactive -> ignored
            ]}

    monkeypatch.setattr(sl.requests, "get", lambda *a, **k: _Resp())
    assert sl.get_next_run("555") == "2026-06-20T08:00:00"
    tasks = sl.list_cc_schedules_with_next_run({"user_id": 13})
    assert tasks[0]["next_run"] == "2026-06-20T08:00:00"

    # scheduler unreachable -> None, no crash (graceful)
    def _boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(sl.requests, "get", _boom)
    assert sl.get_next_run("555") is None


# --- user_lookup.get_user_contact ------------------------------------------

def test_user_lookup_resolves_email(monkeypatch):
    import json as _json
    import user_lookup
    monkeypatch.setattr(user_lookup, "_base_and_key", lambda: ("http://127.0.0.1:5001", "k"))
    records = [
        {"id": 13, "name": "Jo", "email": "jo@x.com", "phone": "555", "user_name": "jo"},
        {"id": 99, "email": "other@x.com"},
    ]

    # REAL shape: /get/users returns jsonify(df.to_json(orient='records')) -> r.json() is a
    # JSON-encoded STRING (this is what broke get_user_contact in production).
    class _StrResp:
        status_code = 200

        def json(self):
            return _json.dumps(records)

    monkeypatch.setattr(user_lookup.requests, "get", lambda *a, **k: _StrResp())
    info = user_lookup.get_user_contact(13)
    assert info["email"] == "jo@x.com" and info["name"] == "Jo" and info["phone"] == "555"
    assert user_lookup.get_user_contact(12345) == {}  # not found -> empty

    # Defensive: a plain list body also works.
    class _ListResp:
        status_code = 200

        def json(self):
            return records

    monkeypatch.setattr(user_lookup.requests, "get", lambda *a, **k: _ListResp())
    assert user_lookup.get_user_contact(13)["email"] == "jo@x.com"


# --- job_scheduler._execute_command_center_job -----------------------------

def test_executor_builds_cc_call(monkeypatch):
    try:
        import job_scheduler
    except Exception as e:  # pragma: no cover - env-dependent
        pytest.skip(f"job_scheduler not importable here: {e}")

    svc = job_scheduler.JobSchedulerService.__new__(job_scheduler.JobSchedulerService)
    for m in ("_create_execution_record", "_update_execution_record",
              "_increment_run_count", "_update_last_run_time"):
        monkeypatch.setattr(svc, m, (lambda *a, **k: 1), raising=False)

    captured = {}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "completed", "summary": "done"}

    def _post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["payload"] = json
        return _Resp()

    monkeypatch.setattr(job_scheduler.requests, "post", _post)
    import CommonUtils
    monkeypatch.setattr(CommonUtils, "get_command_center_api_base_url",
                        lambda: "http://127.0.0.1:5091", raising=False)

    job_data = {
        "scheduled_job_id": 555, "schedule_id": 9, "job_name": "Daily orders",
        "parameters": {
            "prompt": "pull orders", "user_id": "13", "tenant_id": "2", "role": "2",
            "username": "jo", "name": "Jo", "agent_id": "7", "agent_name": "Orders",
            "task_name": "Daily orders", "user_email": "jo@x.com",
        },
    }
    svc._execute_command_center_job(job_data)
    p = captured["payload"]
    assert captured["url"].endswith("/api/scheduled/run")
    assert p["prompt"] == "pull orders"
    assert p["user_context"]["user_id"] == "13"
    assert p["user_context"]["tenant_id"] == "2"
    assert p["user_context"]["email"] == "jo@x.com"
    assert p["agent_id"] == "7"
    assert p["job_id"] == 555
