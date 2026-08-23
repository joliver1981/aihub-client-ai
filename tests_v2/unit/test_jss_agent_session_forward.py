"""JSS agent_session executor forwards the originating chat session (2026-08-22).

Deferred-results-to-chat, Level 1: schedule_agent_task stores the conversation's
`session_id` as a job parameter; the JSS executor must forward it in the
/api/run body (The Agent resumes that conversation when it can). Legacy jobs
without the parameter must still send an empty string — never drop the key.

Runs wherever job_scheduler imports (jss / main-app envs: apscheduler, pyodbc);
self-skips elsewhere. Standalone: python test_jss_agent_session_forward.py
"""
import os
import sys

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)

try:
    import job_scheduler as JS          # noqa: E402
    HAVE_JS = True
except Exception as e:                  # env without apscheduler / pyodbc
    HAVE_JS = False
    _IMPORT_ERR = e

if not HAVE_JS:
    try:
        import pytest
        pytestmark = pytest.mark.skip(reason=f"job_scheduler not importable here: {_IMPORT_ERR}")
    except ImportError:
        pass


class _Resp:
    status_code = 200

    def json(self):
        return {"ok": True, "session_id": "s-9", "work_item_id": "wi"}


def _svc(captured):
    svc = object.__new__(JS.JobSchedulerService)
    svc._create_execution_record = lambda *a, **k: 1
    svc._update_execution_record = lambda *a, **k: None
    svc._increment_run_count = lambda *a, **k: None
    svc._update_last_run_time = lambda *a, **k: None

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(url=url, body=json, timeout=timeout)
        return _Resp()
    return svc, fake_post


def test_session_id_forwarded_to_agent_run():
    captured = {}
    svc, fake_post = _svc(captured)
    orig = JS.requests.post
    JS.requests.post = fake_post
    try:
        svc._execute_agent_session_job({
            "scheduled_job_id": 1, "schedule_id": 2, "job_name": "Agent: x",
            "parameters": {"prompt": "p", "user_id": "7", "role": "3",
                           "username": "u", "session_id": "s-9",
                           "user_timezone": "America/Chicago"}})
    finally:
        JS.requests.post = orig
    assert captured["url"].endswith("/api/run")
    assert captured["body"]["session_id"] == "s-9"
    assert captured["body"]["timezone"] == "America/Chicago"   # the user's zone rides along
    assert captured["body"]["prompt"] == "p" and captured["body"]["job_name"] == "Agent: x"
    assert str(captured["body"]["user_id"]) == "7"


def test_legacy_job_sends_empty_session_id():
    captured = {}
    svc, fake_post = _svc(captured)
    orig = JS.requests.post
    JS.requests.post = fake_post
    try:
        svc._execute_agent_session_job({
            "scheduled_job_id": 1, "schedule_id": 2, "job_name": "Agent: y",
            "parameters": {"prompt": "p"}})
    finally:
        JS.requests.post = orig
    assert captured["body"]["session_id"] == ""      # present, empty — never missing
    assert captured["body"]["timezone"] == ""


def test_dict_shaped_parameters_also_work():
    captured = {}
    svc, fake_post = _svc(captured)
    orig = JS.requests.post
    JS.requests.post = fake_post
    try:
        svc._execute_agent_session_job({
            "scheduled_job_id": 1, "schedule_id": 2, "job_name": "Agent: z",
            "parameters": {"prompt": {"value": "p", "type": "string"},
                           "session_id": {"value": "s-1", "type": "string"}}})
    finally:
        JS.requests.post = orig
    assert captured["body"]["session_id"] == "s-1" and captured["body"]["prompt"] == "p"


if __name__ == "__main__":
    if not HAVE_JS:
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
