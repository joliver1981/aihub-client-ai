"""
Unit tests for the CC scheduled-tasks store (per-user local JSON).
=================================================================
Isolated: the store directory is redirected to a tmp path, so the real
command_center_service/data/cc_schedules/ is never touched.

Run:
    python -m pytest tests/unit/test_schedule_store.py -v
"""
import sys

import pytest

_SVC = r"C:/src/aihub-client-ai-dev/command_center_service"
if _SVC not in sys.path:
    sys.path.insert(0, _SVC)

from scheduling import schedule_store as store  # noqa: E402


@pytest.fixture
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_dir", lambda: tmp_path)
    return tmp_path


def test_add_list_get_find_remove_task(tmp_store):
    store.add_task(13, "101", "Daily orders", "pull orders", "cron '0 8 * * 1-5'",
                   agent_id="7", agent_name="Orders Agent")
    tasks = store.list_tasks(13)
    assert len(tasks) == 1 and tasks[0]["task_name"] == "Daily orders"
    assert store.get_task(13, "101")["agent_name"] == "Orders Agent"
    assert store.find_task_by_name(13, "daily orders")["job_id"] == "101"  # loose, case-insensitive
    assert store.remove_task(13, "101") is True
    assert store.list_tasks(13) == []


def test_results_unread_and_mark_read(tmp_store):
    store.add_task(13, "101", "T", "p", "every 6 hour(s)")
    r1 = store.add_result(13, "101", "T", "completed", "summary one", ["a1"])
    r2 = store.add_result(13, "101", "T", "completed", "summary two")
    assert store.unread_count(13) == 2
    res = store.list_results(13)
    assert res[0]["run_id"] == r2["run_id"]                 # newest first
    assert store.get_task(13, "101")["last_status"] == "completed"  # task updated
    assert store.mark_read(13, [r1["run_id"]]) == 1
    assert store.unread_count(13) == 1
    assert store.mark_read(13) == 1                          # mark all remaining
    assert store.unread_count(13) == 0
    assert store.list_results(13, unread_only=True) == []


def test_per_user_isolation(tmp_store):
    store.add_task(13, "101", "Mine", "p", "daily")
    assert store.list_tasks(99) == []
    assert store.get_task(99, "101") is None


def test_results_capped_at_50(tmp_store):
    for i in range(60):
        store.add_result(7, "1", "T", "completed", f"r{i}")
    res = store.list_results(7)
    assert len(res) == 50                 # _MAX_RESULTS
    assert res[0]["summary"] == "r59"     # newest kept, oldest dropped
