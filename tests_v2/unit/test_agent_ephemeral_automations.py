"""The Agent — ephemeral one-off automations (agent_service/authoring_tools.py,
2026-09-05).

  * create_automation(ephemeral=true): collision-proof '-adhoc-xxxxxx' name,
    ephemeral + provision_environment=false in the manage payload, a reply
    that says what happens next; the default call is byte-for-byte unchanged.
  * delete_automation: an ephemeral one is pre-approved — deleted on the
    first call; a keeper still gets the two-step confirmation.
  * dry_run / check_automation_run / decide_automation_checkpoint: a TERMINAL
    outcome on an ephemeral automation carries the delete-it-now epilogue; a
    paused checkpoint or a keeper does not.

Runs standalone (C:\\Users\\james\\miniconda3\\envs\\aihub-agent\\python.exe
tests_v2\\unit\\test_agent_ephemeral_automations.py) or under pytest in an env
with claude_agent_sdk; without the SDK every test self-skips.
"""
import asyncio
import os
import re
import sys
import uuid
from unittest import mock

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

try:
    import authoring_tools as A                   # noqa: E402
    from platform_tools import CURRENT_USER       # noqa: E402
    HAVE_SDK = True
except ImportError as e:                          # main-env pytest sweep: no claude_agent_sdk
    HAVE_SDK = False
    _IMPORT_ERR = e

if not HAVE_SDK:
    try:
        import pytest
        pytestmark = pytest.mark.skip(
            reason=f"needs the aihub-agent env (claude_agent_sdk): {_IMPORT_ERR}")
    except ImportError:
        pass


def _run(coro):
    return asyncio.run(coro)


def _txt(res):
    return res["content"][0]["text"]


def _as(role, uid=7):
    return CURRENT_USER.set({"user_id": uid, "role": role, "username": f"u{uid}",
                             "name": f"User {uid}"})


class _FakeManage:
    """Stands in for platform_tools._post: one automation, scripted answers."""

    def __init__(self, lifecycle="keep", run_status="success", paused=False):
        self.calls = []
        self.aid = str(uuid.uuid4())
        self.name = "fix-invoice"
        self.lifecycle = lifecycle
        self.run_status = run_status
        self.paused = paused

    def _auto(self):
        manifest = {"name": self.name, "connections": []}
        if self.lifecycle == "ephemeral":
            manifest["lifecycle"] = "ephemeral"
        return {"automation_id": self.aid, "name": self.name, "lifecycle": self.lifecycle,
                "manifest": manifest, "current_version": 1, "pinned_version": 0,
                "versions": [1], "status": "active", "code": "print(1)",
                "description": "One-off"}

    async def post(self, path, body, timeout=None):
        assert path == "/automations/api/internal/manage"
        action, payload = body["action"], body.get("payload") or {}
        self.calls.append((action, payload))
        if action == "create":
            self.name = payload["name"]
            self.lifecycle = "ephemeral" if payload.get("ephemeral") else "keep"
            return {"automation": {"automation_id": self.aid, "name": self.name,
                                   "lifecycle": self.lifecycle, "current_version": 0,
                                   "pinned_version": 0}}, 201
        if action == "get":
            return {"automation": self._auto()}, 200
        if action == "list":
            return {"automations": [self._auto()]}, 200
        if action == "delete":
            return {"deleted": self.aid, "name": self.name, "schedules_deactivated": 0}, 200
        if action in ("dry_run", "run"):
            if self.paused:
                return {"run_id": "r1", "waiting_on_checkpoint": True,
                        "pending_checkpoint": {"checkpoint_id": "c1", "message": "ok to write?"}}, 200
            return {"run_id": "r1", "status": self.run_status, "exit_code": 0,
                    "version": 1, "stdout_tail": "done"}, 200
        if action == "run_events":
            return {"run": {"run_id": "r1", "automation_id": self.aid,
                            "status": self.run_status, "exit_code": 0, "version": 1,
                            "trigger_source": "dry_run"}, "events": []}, 200
        if action == "checkpoint_decision":
            return {"ok": True}, 200
        return {"error": f"unexpected action {action}"}, 400

    def actions(self):
        return [a for a, _ in self.calls]


EPILOGUE = "EPHEMERAL (a one-off) and its run has finished"


# ─────────────────────────────────────────────── create_automation
def test_create_ephemeral_suffixes_name_and_skips_provisioning():
    fake = _FakeManage()
    tok = _as(2)
    try:
        with mock.patch.object(A, "_post", fake.post):
            res = _run(A.create_automation.handler(
                {"name": "update-invoice-status", "description": "One-off", "ephemeral": True}))
    finally:
        CURRENT_USER.reset(tok)
    assert not res.get("is_error"), _txt(res)
    action, payload = fake.calls[0]
    assert action == "create"
    assert payload["ephemeral"] is True
    assert payload["provision_environment"] is False
    assert re.fullmatch(r"update-invoice-status-adhoc-[0-9a-f]{6}", payload["name"]), payload["name"]
    text = _txt(res)
    assert "EPHEMERAL" in text and "delete_automation" in text and "no confirmation" in text


def test_create_default_payload_is_unchanged():
    fake = _FakeManage()
    tok = _as(2)
    try:
        with mock.patch.object(A, "_post", fake.post):
            res = _run(A.create_automation.handler({"name": "monthly-recon", "description": "keep"}))
    finally:
        CURRENT_USER.reset(tok)
    assert not res.get("is_error"), _txt(res)
    _, payload = fake.calls[0]
    assert payload == {"name": "monthly-recon", "description": "keep"}
    assert "EPHEMERAL" not in _txt(res)


def test_ephemeral_name_respects_the_200_char_cap_and_is_idempotent():
    long_name = "x" * 200
    out = A._ephemeral_name(long_name)
    assert len(out) <= 200 and re.search(r"-adhoc-[0-9a-f]{6}$", out)
    assert A._ephemeral_name(out) == out          # already suffixed: left alone


def test_create_respects_the_role_gate():
    fake = _FakeManage()
    tok = _as(1)
    try:
        with mock.patch.object(A, "_post", fake.post), \
                mock.patch.dict(os.environ, {"AGENT_BUILD_ALLOW_ALL_USERS": "false"}):
            res = _run(A.create_automation.handler({"name": "nope", "ephemeral": True}))
    finally:
        CURRENT_USER.reset(tok)
    assert res.get("is_error") and fake.calls == []


# ─────────────────────────────────────────────── delete_automation
def test_delete_ephemeral_is_pre_approved():
    fake = _FakeManage(lifecycle="ephemeral")
    tok = _as(2)
    try:
        with mock.patch.object(A, "_post", fake.post):
            res = _run(A.delete_automation.handler({"automation_id": fake.aid}))
    finally:
        CURRENT_USER.reset(tok)
    assert not res.get("is_error"), _txt(res)
    assert "delete" in fake.actions()
    assert "pre-approved" in _txt(res) and "Run history is retained" in _txt(res)


def test_delete_keeper_still_needs_confirmation():
    fake = _FakeManage(lifecycle="keep")
    tok = _as(2)
    try:
        with mock.patch.object(A, "_post", fake.post):
            first = _run(A.delete_automation.handler({"automation_id": fake.aid}))
            assert "CONFIRMATION REQUIRED" in _txt(first)
            assert "delete" not in fake.actions()
            second = _run(A.delete_automation.handler({"automation_id": fake.aid, "confirmed": True}))
    finally:
        CURRENT_USER.reset(tok)
    assert not second.get("is_error"), _txt(second)
    assert "delete" in fake.actions() and "pre-approved" not in _txt(second)


# ─────────────────────────────────────────────── run epilogue
def test_dry_run_terminal_on_ephemeral_carries_the_epilogue():
    fake = _FakeManage(lifecycle="ephemeral")
    tok = _as(2)
    try:
        with mock.patch.object(A, "_post", fake.post):
            res = _run(A.dry_run_automation.handler({"automation_id": fake.aid}))
    finally:
        CURRENT_USER.reset(tok)
    text = _txt(res)
    assert "Run outcome: **success**" in text
    assert EPILOGUE in text and "delete_automation" in text


def test_dry_run_terminal_on_keeper_has_no_epilogue():
    fake = _FakeManage(lifecycle="keep")
    tok = _as(2)
    try:
        with mock.patch.object(A, "_post", fake.post):
            res = _run(A.dry_run_automation.handler({"automation_id": fake.aid}))
    finally:
        CURRENT_USER.reset(tok)
    assert "Run outcome: **success**" in _txt(res) and EPILOGUE not in _txt(res)


def test_paused_checkpoint_is_not_terminal_so_no_epilogue():
    fake = _FakeManage(lifecycle="ephemeral", paused=True)
    tok = _as(2)
    try:
        with mock.patch.object(A, "_post", fake.post):
            res = _run(A.dry_run_automation.handler({"automation_id": fake.aid}))
    finally:
        CURRENT_USER.reset(tok)
    assert "RUN PAUSED" in _txt(res) and EPILOGUE not in _txt(res)


def test_failed_run_on_ephemeral_still_asks_for_cleanup():
    fake = _FakeManage(lifecycle="ephemeral", run_status="failed")
    tok = _as(2)
    try:
        with mock.patch.object(A, "_post", fake.post):
            res = _run(A.dry_run_automation.handler({"automation_id": fake.aid}))
    finally:
        CURRENT_USER.reset(tok)
    assert "Run outcome: **failed**" in _txt(res) and EPILOGUE in _txt(res)


def test_check_automation_run_terminal_carries_the_epilogue():
    fake = _FakeManage(lifecycle="ephemeral")
    with mock.patch.object(A, "_post", fake.post):
        res = _run(A.check_automation_run.handler({"run_id": "r1"}))
    assert "status **success**" in _txt(res) and EPILOGUE in _txt(res)


def test_checkpoint_decision_finish_carries_the_epilogue():
    fake = _FakeManage(lifecycle="ephemeral")
    tok = _as(2)
    try:
        with mock.patch.object(A, "_post", fake.post):
            res = _run(A.decide_automation_checkpoint.handler(
                {"run_id": "r1", "checkpoint_id": "c1", "decision": "proceed"}))
    finally:
        CURRENT_USER.reset(tok)
    assert "Run finished: **success**" in _txt(res) and EPILOGUE in _txt(res)


def test_get_automation_shows_lifecycle():
    fake = _FakeManage(lifecycle="ephemeral")
    with mock.patch.object(A, "_post", fake.post):
        res = _run(A.get_automation.handler({"automation_id": fake.aid}))
    assert "lifecycle: ephemeral" in _txt(res)


if __name__ == "__main__":
    if not HAVE_SDK:
        print(f"SKIP: {_IMPORT_ERR}")
        sys.exit(0)
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"\n{'ALL PASS' if not failed else str(failed) + ' FAILED'}")
    sys.exit(1 if failed else 0)
