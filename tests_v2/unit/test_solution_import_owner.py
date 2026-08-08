"""Regression: Solutions-import automation owner must satisfy FK_Automations_Owner.

Cross-system import bug (2026-08-08): imported automations were stamped with a
hard-coded owner_user_id=1 (the installer's remap was dead — the X-AIHub-User
token it needed was never forwarded). On a target where [dbo].[User].id=1 does
not exist, the INSERT violates FK_Automations_Owner -> [dbo].[User](id).

Fix: the installer threads the authenticated installer's LOCAL id, and
AutomationManager.resolve_owner_user_id GUARANTEES the value exists in this
tenant (admin fallback) before it's inserted. These tests pin both halves
with a mocked DB — no real platform state.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import automations.manager as manager_mod
from automations.manager import AutomationManager


class _FakeCursor:
    """Answers resolve_owner_user_id's three queries from a scripted user set."""

    def __init__(self, user_ids, admin_ids):
        self._users = set(user_ids)
        self._admins = sorted(admin_ids)
        self._all = sorted(user_ids)
        self._last = ""

    def execute(self, sql, *params):
        self._last = sql
        self._params = params

    def fetchone(self):
        s = self._last
        if "WHERE id = ?" in s:
            uid = int(self._params[0])
            return (uid,) if uid in self._users else None
        if "role >= 3" in s:
            return (self._admins[0],) if self._admins else (None,)
        if "MIN(id)" in s:
            return (self._all[0],) if self._all else (None,)
        return None

    def close(self):
        pass


def _mgr_with_users(user_ids, admin_ids):
    mgr = AutomationManager.__new__(AutomationManager)  # skip __init__/DB
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = _FakeCursor(user_ids, admin_ids)
    mgr._db_conn = lambda: fake_conn  # type: ignore
    return mgr


def test_valid_candidate_is_kept():
    mgr = _mgr_with_users([1, 8, 285], admin_ids=[12])
    assert mgr.resolve_owner_user_id(285) == 285


def test_bogus_candidate_falls_back_to_admin():
    mgr = _mgr_with_users([8, 12, 285], admin_ids=[12, 142])
    # 999999 is not a user -> lowest admin
    assert mgr.resolve_owner_user_id(999999) == 12


def test_none_candidate_falls_back_to_admin():
    mgr = _mgr_with_users([8, 12, 285], admin_ids=[12])
    assert mgr.resolve_owner_user_id(None) == 12


def test_fallback_to_any_user_when_no_admins():
    mgr = _mgr_with_users([8, 9, 10], admin_ids=[])
    assert mgr.resolve_owner_user_id(None) == 8


def test_hardcoded_one_is_never_assumed():
    # The exact bug shape: user 1 does NOT exist on this target.
    mgr = _mgr_with_users([8, 12, 285], admin_ids=[12])
    resolved = mgr.resolve_owner_user_id(1)          # old code returned 1 blindly
    assert resolved != 1
    assert resolved == 12                             # a proven-valid admin


def test_raises_when_tenant_has_no_users():
    mgr = _mgr_with_users([], admin_ids=[])
    try:
        mgr.resolve_owner_user_id(5)
        assert False, "expected ValueError when no users exist"
    except ValueError as e:
        assert "no users" in str(e).lower()


# ---------------------------------------------------------------------------
# Installer candidate resolution (instance method; threads current_user.id)
# ---------------------------------------------------------------------------

def _installer():
    from solution_installer import SolutionInstaller
    inst = SolutionInstaller.__new__(SolutionInstaller)
    return inst


def test_installer_prefers_threaded_user_id():
    inst = _installer()
    inst._installer_user_id = 7
    assert inst._resolve_installer_user_id({}) == 7


def test_installer_returns_none_without_id_or_token():
    inst = _installer()
    inst._installer_user_id = None
    # No X-AIHub-User token -> None (NOT the old hard-coded 1)
    assert inst._resolve_installer_user_id({}) is None


def test_installer_falls_back_to_signed_token_sub():
    inst = _installer()
    inst._installer_user_id = None
    with patch("shared_auth.verify_token", return_value=({"sub": "42"}, None)):
        assert inst._resolve_installer_user_id({"X-AIHub-User": "tok"}) == 42
