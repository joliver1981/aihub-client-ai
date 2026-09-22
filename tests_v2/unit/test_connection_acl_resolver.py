"""connection_acl.accessible_connection_ids — the three-state contract
(docs/handoff-the-agent-connection-acl.md §5 item 1, 2026-09-22).

  None   = unrestricted (role >= CONNECTION_ACL_UNRESTRICTED_ROLE, default 2)
  [ids]  = exactly the connections behind the Data Assistants shared with the
           user's groups (sorted, de-duplicated)
  []     = deny-all, and it FAILS CLOSED on any resolver error / bad identity

The REAL module runs with only its DB connect faked (the doc-acl recipe).
Force-add to git (gitignore hides test*.py).
"""
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import connection_acl as ca  # noqa: E402

pytestmark = pytest.mark.unit


class _Cur:
    def __init__(self, state):
        self.state = state
        self.params = None

    def execute(self, sql, *params):
        if self.state["fail"]:
            raise RuntimeError("db down")
        self.state["sql"] = sql
        self.params = params

    def fetchall(self):
        return self.state["rows"]

    def close(self):
        pass


class _Conn:
    closed = False

    def close(self):
        self.closed = True


@pytest.fixture
def fake_db(monkeypatch):
    state = {"rows": [], "fail": False, "conn": None, "sql": None, "connects": 0}

    def _connect():
        state["connects"] += 1
        c = _Conn()
        state["conn"] = c
        return c, _Cur(state)

    monkeypatch.setattr(ca, "_connect", _connect)
    monkeypatch.delenv("CONNECTION_ACL_UNRESTRICTED_ROLE", raising=False)
    return state


def test_role_at_or_above_floor_is_unrestricted_without_touching_the_db(fake_db):
    assert ca.accessible_connection_ids(141, 2) is None
    assert ca.accessible_connection_ids("141", "3") is None
    assert fake_db["connects"] == 0


def test_regular_user_gets_exact_sorted_ids_and_the_connection_is_closed(fake_db):
    fake_db["rows"] = [(59,), (20,), (59,), (None,)]
    assert ca.accessible_connection_ids("13", 1) == [20, 59]
    assert fake_db["conn"].closed
    # the grant chain is the classic Data Assistants rule
    sql = fake_db["sql"]
    for needle in ("AgentConnections", "AgentGroups", "UserGroups",
                   "is_data_agent = 1", "enabled = 1"):
        assert needle in sql, needle


def test_regular_user_without_grants_is_deny_all(fake_db):
    assert ca.accessible_connection_ids(10, 1) == []
    assert ca.deny_all([]) is True
    assert ca.deny_all(None) is False
    assert ca.deny_all([1]) is False


def test_db_error_fails_closed(fake_db):
    fake_db["fail"] = True
    fake_db["rows"] = [(20,)]
    assert ca.accessible_connection_ids(10, 1) == []


def test_bad_identity_fails_closed(fake_db):
    assert ca.accessible_connection_ids(None, 1) == []
    assert ca.accessible_connection_ids("abc", 1) == []
    # an unparseable role is treated as 0 (restricted), never as unrestricted
    fake_db["rows"] = [(20,)]
    assert ca.accessible_connection_ids(13, "x") == [20]
    assert ca.accessible_connection_ids(13, None) == [20]


def test_floor_is_configurable(fake_db, monkeypatch):
    monkeypatch.setenv("CONNECTION_ACL_UNRESTRICTED_ROLE", "3")
    fake_db["rows"] = [(20,)]
    assert ca.unrestricted_from_role() == 3
    assert ca.accessible_connection_ids(141, 2) == [20]
    assert ca.accessible_connection_ids(1, 3) is None
    monkeypatch.setenv("CONNECTION_ACL_UNRESTRICTED_ROLE", "garbage")
    assert ca.unrestricted_from_role() == 2
