"""
Ephemeral-automation sweep (automations/api.py, 2026-09-05).

sweep_ephemeral_automations() soft-deletes abandoned one-offs and leaves
everything else alone with a stated reason. The guards, in order:
flag in the manifest -> never promoted -> no run in flight -> no active
schedule -> not pinned to a View tile (fail closed when unreadable) ->
idle longer than the grace period. Deletes go through _delete_automation_impl
(the Mission Control button's path); disk is never touched.

Manager/runner are the same stubs test_automations.py uses; the DB-backed
schedule lookup and the schedule deactivation are monkeypatched.
"""
from __future__ import annotations

import json
import os
import sqlite3
import types
from datetime import datetime, timedelta, timezone

import pytest

import automations.api as api
from automations.manager import LIFECYCLE_EPHEMERAL
from tests_v2.unit.test_automations import StubManager, VALID_MANIFEST

pytestmark = pytest.mark.unit


def _utc(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).replace(tzinfo=None).isoformat()


class _StubRunner:
    def __init__(self):
        self.active = []
        self.runs = {}

    def list_active_runs(self):
        return list(self.active)

    def list_runs(self, automation_id, limit=50):
        return list(self.runs.get(automation_id, []))[:limit]


@pytest.fixture
def env(tmp_path, monkeypatch):
    mgr = StubManager(str(tmp_path / "autos"))
    runner = _StubRunner()
    monkeypatch.setattr(api, "_manager", mgr)
    monkeypatch.setattr(api, "_runner", runner)
    monkeypatch.setattr(api, "_tables_ensured", True)
    schedules = {}
    monkeypatch.setattr(api, "_automation_has_active_schedule",
                        lambda aid: bool(schedules.get(aid)))
    deactivated = []
    monkeypatch.setattr(api, "_deactivate_automation_schedules",
                        lambda aid: (deactivated.append(aid), 0)[1])
    views_db = tmp_path / "mywork.db"          # absent unless a test creates it
    monkeypatch.setenv("AUTOMATIONS_AGENT_VIEWS_DB", str(views_db))
    monkeypatch.delenv("AUTOMATIONS_EPHEMERAL_GRACE_HOURS", raising=False)
    return types.SimpleNamespace(mgr=mgr, runner=runner, schedules=schedules,
                                 deactivated=deactivated, views_db=views_db, tmp=tmp_path)


def _mk(env, name, lifecycle=LIFECYCLE_EPHEMERAL, hours_ago=48.0, pinned=0, stamps=True):
    ok, auto, err = env.mgr.create_automation(name, "", 1, lifecycle=lifecycle)
    assert ok, err
    aid = auto["automation_id"]
    if stamps:
        env.mgr._rows[aid]["created_at"] = _utc(hours_ago)
        env.mgr._rows[aid]["updated_at"] = _utc(hours_ago)
    if pinned:
        env.mgr._rows[aid]["pinned_version"] = pinned
    return aid


def _views_db(path, tiles_rows):
    c = sqlite3.connect(str(path))
    c.execute("CREATE TABLE views (view_id TEXT PRIMARY KEY, name TEXT, tiles TEXT NOT NULL)")
    for i, tiles in enumerate(tiles_rows):
        c.execute("INSERT INTO views VALUES (?, ?, ?)", (f"v{i}", f"view {i}", json.dumps(tiles)))
    c.commit()
    c.close()


def _reasons(report):
    return {s["automation_id"]: s["reason"] for s in report["skipped"]}


class TestSweep:
    def test_abandoned_ephemeral_is_soft_deleted_disk_untouched(self, env):
        aid = _mk(env, "one-off")
        report = api.sweep_ephemeral_automations(grace_hours=24)
        assert [s["automation_id"] for s in report["swept"]] == [aid]
        assert report["swept"][0]["action"] == "deleted"
        assert report["count"] == 1 and report["dry_run"] is False
        assert env.mgr._rows[aid]["status"] == "deleted"
        assert env.deactivated == [aid]                       # schedules shut off first
        assert os.path.isdir(env.mgr.automation_dir(aid))     # audit trail stays

    def test_keepers_are_invisible_to_the_sweep(self, env):
        aid = _mk(env, "keeper", lifecycle=None, hours_ago=1000)
        report = api.sweep_ephemeral_automations(grace_hours=0)
        assert report["swept"] == [] and report["skipped"] == []
        assert env.mgr._rows[aid]["status"] == "active"

    def test_within_grace_is_left_alone(self, env):
        aid = _mk(env, "fresh", hours_ago=1)
        report = api.sweep_ephemeral_automations(grace_hours=24)
        assert report["swept"] == []
        assert _reasons(report)[aid].startswith("within grace")
        assert env.mgr._rows[aid]["status"] == "active"

    def test_grace_zero_sweeps_immediately(self, env):
        aid = _mk(env, "now", hours_ago=0.01)
        report = api.sweep_ephemeral_automations(grace_hours=0)
        assert [s["automation_id"] for s in report["swept"]] == [aid]

    def test_default_grace_comes_from_env(self, env, monkeypatch):
        aid = _mk(env, "envgrace", hours_ago=2)
        monkeypatch.setenv("AUTOMATIONS_EPHEMERAL_GRACE_HOURS", "1")
        report = api.sweep_ephemeral_automations()
        assert report["grace_hours"] == 1.0
        assert [s["automation_id"] for s in report["swept"]] == [aid]

    def test_promoted_flag_leftover_is_a_keeper(self, env):
        # a legacy row whose manifest still says ephemeral but which was pinned
        aid = _mk(env, "pinned", pinned=1)
        report = api.sweep_ephemeral_automations(grace_hours=0)
        assert report["swept"] == []
        assert _reasons(report)[aid].startswith("promoted")

    def test_run_in_flight_blocks(self, env):
        aid = _mk(env, "busy")
        env.runner.active = [{"automation_id": aid, "status": "running", "run_id": "r1"}]
        report = api.sweep_ephemeral_automations(grace_hours=0)
        assert report["swept"] == [] and _reasons(report)[aid] == "run in flight"

    def test_active_schedule_blocks(self, env):
        aid = _mk(env, "scheduled")
        env.schedules[aid] = True
        report = api.sweep_ephemeral_automations(grace_hours=0)
        assert report["swept"] == [] and _reasons(report)[aid] == "active schedule"

    def test_recent_run_counts_as_activity(self, env):
        aid = _mk(env, "ran-recently", hours_ago=48)
        env.runner.runs[aid] = [{"run_id": "r9", "started_at": _utc(1), "finished_at": _utc(0.9)}]
        report = api.sweep_ephemeral_automations(grace_hours=24)
        assert report["swept"] == [] and _reasons(report)[aid].startswith("within grace")

    def test_no_timestamp_is_left_alone(self, env):
        aid = _mk(env, "undated", stamps=False)
        report = api.sweep_ephemeral_automations(grace_hours=0)
        assert report["swept"] == [] and _reasons(report)[aid] == "no activity timestamp"

    def test_view_tile_pin_by_name_blocks(self, env):
        aid = _mk(env, "pulse-source")
        _views_db(env.views_db, [[{"type": "sql", "sql": "select 1"},
                                 {"type": "automation", "automation": "Pulse-Source"}]])
        report = api.sweep_ephemeral_automations(grace_hours=0)
        assert report["swept"] == [] and _reasons(report)[aid] == "pinned to a View tile"

    def test_view_tile_pin_by_id_blocks(self, env):
        aid = _mk(env, "by-id")
        _views_db(env.views_db, [[{"type": "automation", "automation": aid}]])
        report = api.sweep_ephemeral_automations(grace_hours=0)
        assert report["swept"] == [] and _reasons(report)[aid] == "pinned to a View tile"

    def test_other_views_do_not_block(self, env):
        aid = _mk(env, "unpinned")
        _views_db(env.views_db, [[{"type": "automation", "automation": "something-else"}]])
        report = api.sweep_ephemeral_automations(grace_hours=0)
        assert [s["automation_id"] for s in report["swept"]] == [aid]

    def test_unreadable_views_store_fails_closed(self, env):
        aid = _mk(env, "careful")
        env.views_db.write_bytes(b"this is not a sqlite database")
        report = api.sweep_ephemeral_automations(grace_hours=0)
        assert report["swept"] == [] and _reasons(report)[aid] == "could not check View tiles"
        assert env.mgr._rows[aid]["status"] == "active"

    def test_dry_run_reports_without_deleting(self, env):
        aid = _mk(env, "preview")
        report = api.sweep_ephemeral_automations(grace_hours=0, dry_run=True)
        assert report["dry_run"] is True
        assert report["swept"][0]["action"] == "would_delete"
        assert report["swept"][0]["automation_id"] == aid
        assert env.mgr._rows[aid]["status"] == "active"
        assert env.deactivated == []

    def test_active_run_listing_failure_aborts_the_pass(self, env, monkeypatch):
        aid = _mk(env, "unknown-state")
        monkeypatch.setattr(env.runner, "list_active_runs",
                            lambda: (_ for _ in ()).throw(RuntimeError("db down")))
        report = api.sweep_ephemeral_automations(grace_hours=0)
        assert report["count"] == 0 and "could not list active runs" in report["error"]
        assert env.mgr._rows[aid]["status"] == "active"

    def test_mixed_population_only_touches_eligible(self, env):
        old = _mk(env, "old-one-off")
        fresh = _mk(env, "fresh-one-off", hours_ago=0.5)
        keeper = _mk(env, "keeper", lifecycle=None, hours_ago=500)
        report = api.sweep_ephemeral_automations(grace_hours=24)
        assert [s["automation_id"] for s in report["swept"]] == [old]
        assert set(_reasons(report)) == {fresh}
        assert env.mgr._rows[keeper]["status"] == "active"
        assert env.mgr._rows[fresh]["status"] == "active"


class TestSweeperThread:
    def test_kill_switch_prevents_the_thread(self, monkeypatch):
        monkeypatch.setenv("AUTOMATIONS_EPHEMERAL_SWEEP", "false")
        assert api.start_ephemeral_sweeper() is None

    def test_thread_starts_daemon_by_default(self, monkeypatch):
        monkeypatch.delenv("AUTOMATIONS_EPHEMERAL_SWEEP", raising=False)
        # a very long first-pass delay keeps the loop from ever running here
        monkeypatch.setattr(api, "_EPHEMERAL_SWEEP_FIRST_PASS_DELAY_S", 10_000)
        t = api.start_ephemeral_sweeper()
        assert t is not None and t.daemon and t.is_alive()
        assert t.name == "automation-ephemeral-sweep"


class TestManageWiring:
    """The internal manage dispatch: create with ephemeral=true flags the
    manifest and skips provisioning; get/list surface lifecycle; the
    sweep_ephemeral action runs the same pass."""

    @pytest.fixture
    def client(self, env, monkeypatch):
        from flask import Flask
        app = Flask("ephemeral-test")
        app.register_blueprint(api.automations_bp)
        monkeypatch.setattr(api, "_service_key_ok", lambda key: True)
        monkeypatch.setattr(api.cfg, "AUTOMATIONS_ENABLED", True, raising=False)
        provisioned = []
        monkeypatch.setattr(api, "_provision_environment_async", lambda auto: provisioned.append(auto))
        c = app.test_client()

        def manage(action, payload=None):
            r = c.post("/automations/api/internal/manage",
                       json={"action": action, "payload": payload or {},
                             "user_context": {"user_id": 1, "role": 2, "username": "dev"}},
                       headers={"X-API-Key": "k"})
            return r.get_json(), r.status_code
        return types.SimpleNamespace(manage=manage, provisioned=provisioned)

    def test_create_ephemeral_flags_manifest_and_skips_provisioning(self, env, client):
        data, code = client.manage("create", {"name": "fix-once", "ephemeral": True})
        assert code == 201, data
        auto = data["automation"]
        assert auto["lifecycle"] == "ephemeral"
        assert env.mgr.get_manifest(auto["automation_id"])["lifecycle"] == "ephemeral"
        assert client.provisioned == []
        assert "warning" not in data

    def test_create_default_still_provisions_and_is_keep(self, env, client):
        data, code = client.manage("create", {"name": "keeper"})
        assert code == 201, data
        assert data["automation"]["lifecycle"] == "keep"
        assert len(client.provisioned) == 1
        assert "warning" in data

    def test_get_and_list_surface_lifecycle(self, env, client):
        data, _ = client.manage("create", {"name": "peek", "ephemeral": True})
        aid = data["automation"]["automation_id"]
        got, code = client.manage("get", {"automation_id": aid})
        assert code == 200 and got["automation"]["lifecycle"] == "ephemeral"
        assert got["automation"]["manifest"]["lifecycle"] == "ephemeral"
        listed, _ = client.manage("list")
        assert {a["name"]: a["lifecycle"] for a in listed["automations"]} == {"peek": "ephemeral"}

    def test_sweep_action_dry_run_and_real(self, env, client):
        aid = _mk(env, "abandoned")
        data, code = client.manage("sweep_ephemeral", {"dry_run": True, "grace_hours": 0})
        assert code == 200 and data["dry_run"] is True
        assert data["swept"][0]["action"] == "would_delete"
        assert env.mgr._rows[aid]["status"] == "active"
        data, code = client.manage("sweep_ephemeral", {"grace_hours": 0})
        assert code == 200 and data["count"] == 1
        assert env.mgr._rows[aid]["status"] == "deleted"
