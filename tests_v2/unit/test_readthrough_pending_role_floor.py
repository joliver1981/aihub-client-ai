"""My Work's workflow + automation read-throughs — the Developer+ floor on the
shared "unassigned" pool (RU retest finding F-12, 2026-09-08).

/api/work/list composes from four sources. F-7 (df05578) put the floor on
workitem_store.list_items; readthrough.workflow_pending and
readthrough.automation_pending kept the unconditional "unassigned means
everyone" branch, so ru_alex (group A), ru_casey (group B) and ru_drew (NO
group) all got byte-identical queues — 74 workflow rows and 3 automation
rows, one of them a Dayforce review naming two employees and their new-hire
document.

THE CONTRACT (both functions, `role` REQUIRED keyword-only, TypeError if
forgotten, missing / zero role fails CLOSED):
  role >= 2 : direct + group-member + unassigned/NULL pool — unchanged
  role <  2 : direct + group-member ONLY; the direct and group branches are
              untouched for every role (an assignee keeps their own work)
workflow_pending applies it on BOTH paths:
  * HTTP: the main app's readthrough op (app._rt_workflow_pending, source
    lifted here) takes `role` and drops the pool branches from its SQL
  * direct SQL: the same branches are dropped from the service's own query
  * and the rows that come back are filtered AGAIN in the service, so an
    older main app that ignores `role` still cannot widen a regular user's
    queue (a fix on one side only would behave differently on installs)
main.py threading the verified role into both is pinned in the sibling
test_workitem_store_role_visibility.py (unittest, aihub-agent env).

Run (main-app env):
  C:\\Users\\james\\miniconda3\\envs\\aihub2.1\\python.exe -m pytest tests_v2/unit/test_readthrough_pending_role_floor.py -v
"""
import json
import logging
import os
import sys
import types

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_HERE = os.path.dirname(os.path.abspath(__file__))
for p in (_ROOT, os.path.join(_ROOT, "agent_service"), _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    import claude_agent_sdk  # noqa: F401  (the aihub-agent env)
except ImportError:
    # platform_tools registers its tools with the SDK at import; a minimal stub
    # lets the pure-Python helpers under test import in the main env.
    _sdk = types.ModuleType("claude_agent_sdk")
    _sdk.tool = lambda *_a, **_k: (lambda fn: fn)
    _sdk.create_sdk_mcp_server = lambda *a, **k: None
    sys.modules.setdefault("claude_agent_sdk", _sdk)

import readthrough  # noqa: E402
from app_route_harness import load_app_symbols  # noqa: E402

pytestmark = pytest.mark.unit

# Seats mirror docs/openclaw-tester-setup-regular-users.md (ids as on the dev DB)
ALEX, BLAIR, CASEY, DREW = 349, 350, 351, 352     # role 1
ERIN = 353                                        # role 2
ADMIN = 13                                        # role 3
GROUP_A, GROUP_B = 59, 60
GROUPS = {ALEX: [GROUP_A], BLAIR: [GROUP_A], CASEY: [GROUP_B], DREW: [],
          ERIN: [GROUP_A], ADMIN: []}
ROLE = {ALEX: 1, BLAIR: 1, CASEY: 1, DREW: 1, ERIN: 2, ADMIN: 3}


def _row(rid, at, aid, title):
    return {"request_id": rid, "title": title, "description": "", "status": "Pending",
            "requested_at": "2026-09-08T10:00:00", "due_date": None, "priority": 0,
            "approval_data": "{}", "assigned_to_type": at, "assigned_to_id": aid}


DIRECT_ALEX = _row("r-alex", "user", ALEX, "Alex's own approval")
DIRECT_CASEY = _row("r-casey", "user", CASEY, "Casey's own approval")
GROUP_A_ROW = _row("r-ga", "group", GROUP_A, "Group A approval")
GROUP_B_ROW = _row("r-gb", "group", GROUP_B, "Group B approval")
POOL_NULL = _row("r-null", None, None, "Dayforce exception — NH07 (emp 3149231)")
POOL_UNASSIGNED = _row("r-unassigned", "unassigned", None, "Loan Application Review Needed")
POOL_BLANK = _row("r-blank", "", None, "blank-typed pool row")
ALL_ROWS = [DIRECT_ALEX, DIRECT_CASEY, GROUP_A_ROW, GROUP_B_ROW,
            POOL_NULL, POOL_UNASSIGNED, POOL_BLANK]
POOL_IDS = {"r-null", "r-unassigned", "r-blank"}


def _ids(rows):
    return sorted(r["request_id"] for r in rows)


# ---------------------------------------------------------------------------
# workflow_pending — HTTP path (main app answers; here a fake that IGNORES role,
# i.e. an older main app — the service must still hold the floor)
# ---------------------------------------------------------------------------

@pytest.fixture
def http_fetch(monkeypatch):
    calls = []

    def _fetch(op, **params):
        calls.append((op, dict(params)))
        return [dict(r) for r in ALL_ROWS]
    monkeypatch.setattr(readthrough, "fetch", _fetch)
    monkeypatch.setattr(readthrough, "_db",
                        lambda: (_ for _ in ()).throw(AssertionError("SQL path must not run")))
    return calls


class TestWorkflowPendingHttpPath:

    @pytest.mark.parametrize("uid", [ALEX, BLAIR, CASEY, DREW])
    def test_role1_never_receives_a_pool_row_even_from_an_old_main_app(self, http_fetch, uid):
        rows = readthrough.workflow_pending(uid, role=1)
        assert not (set(_ids(rows)) & POOL_IDS)
        assert _ids(rows) == _ids([DIRECT_ALEX, DIRECT_CASEY, GROUP_A_ROW, GROUP_B_ROW])
        assert http_fetch == [("workflow_pending", {"user_id": uid, "role": 1})]

    @pytest.mark.parametrize("uid,role", [(ERIN, 2), (ADMIN, 3)])
    def test_developer_plus_is_unchanged(self, http_fetch, uid, role):
        rows = readthrough.workflow_pending(uid, role=role)
        assert _ids(rows) == _ids(ALL_ROWS)
        assert http_fetch == [("workflow_pending", {"user_id": uid, "role": role})]

    @pytest.mark.parametrize("role", [None, 0, "", "x"])
    def test_missing_or_bad_role_fails_closed_on_both_sides(self, http_fetch, role):
        rows = readthrough.workflow_pending(ALEX, role=role)
        assert not (set(_ids(rows)) & POOL_IDS)
        assert http_fetch[0][1]["role"] == 0            # the main app is told role 0 too

    def test_role_is_required_keyword_only(self, http_fetch):
        with pytest.raises(TypeError):
            readthrough.workflow_pending(ALEX)                 # the drift we are guarding
        with pytest.raises(TypeError):
            readthrough.workflow_pending(ALEX, 2)
        assert http_fetch == []


# ---------------------------------------------------------------------------
# workflow_pending — direct-SQL path (HTTP unavailable): the pool branches must
# be absent from the query itself for role < 2
# ---------------------------------------------------------------------------

class _Cursor:
    def __init__(self, log):
        self.log = log
        self.description = [(c,) for c in ("request_id", "title", "description", "status",
                                            "requested_at", "due_date", "priority",
                                            "approval_data", "assigned_to_type",
                                            "assigned_to_id")]

    def execute(self, sql, *params):
        self.log.append((" ".join(sql.split()), params))

    def fetchall(self):
        # echo every row back; the assertion is about the SQL, and the
        # service-side re-filter is what must drop the pool rows for role < 2
        return [tuple(r[c[0]] for c in self.description) for r in ALL_ROWS]

    def close(self):
        pass


class _Conn:
    def __init__(self, log):
        self._log = log

    def cursor(self):
        return _Cursor(self._log)

    def close(self):
        pass


@pytest.fixture
def sql_only(monkeypatch):
    log = []
    monkeypatch.setattr(readthrough, "fetch",
                        lambda op, **p: (_ for _ in ()).throw(
                            readthrough.ReadthroughUnavailable("HTTP 404")))
    monkeypatch.setattr(readthrough, "_db", lambda: _Conn(log))
    return log


class TestWorkflowPendingSqlPath:

    def test_role1_sql_has_no_pool_branch_and_rows_are_refiltered(self, sql_only):
        rows = readthrough.workflow_pending(DREW, role=1)
        sql, params = sql_only[0]
        assert "assigned_to_type = 'user' AND assigned_to_id = ?" in sql
        assert "assigned_to_type = 'group' AND assigned_to_id IN" in sql
        assert "unassigned" not in sql and "IS NULL" not in sql
        assert params == (DREW, DREW)
        assert not (set(_ids(rows)) & POOL_IDS)

    @pytest.mark.parametrize("uid,role", [(ERIN, 2), (ADMIN, 3)])
    def test_developer_plus_sql_keeps_the_pool_branch(self, sql_only, uid, role):
        rows = readthrough.workflow_pending(uid, role=role)
        sql, params = sql_only[0]
        assert "OR assigned_to_type = 'unassigned' OR assigned_to_type IS NULL" in sql
        assert params == (uid, uid)
        assert _ids(rows) == _ids(ALL_ROWS)

    def test_direct_and_group_branches_are_identical_for_every_role(self, sql_only):
        for uid, role in ((ALEX, 1), (ERIN, 2), (ADMIN, 3)):
            readthrough.workflow_pending(uid, role=role)
        heads = [s.split("OR assigned_to_type = 'unassigned'")[0].split("WHERE")[1]
                 for s, _ in sql_only]
        assert heads[0].rstrip(" )") == heads[1].rstrip(" )") == heads[2].rstrip(" )")


# ---------------------------------------------------------------------------
# app._rt_workflow_pending — the SQL that actually runs on the dev tree and on
# every install (source lifted from app.py)
# ---------------------------------------------------------------------------

@pytest.fixture
def rt_op():
    log = []

    def _query(sql, params=None):
        log.append((" ".join(sql.split()), tuple(params or ())))
        return [dict(r) for r in ALL_ROWS]
    ns = {"query_app_database": _query, "_rt_date": lambda v: str(v),
          "logger": logging.getLogger("t")}
    load_app_symbols(["_rt_workflow_pending"], ns)
    return ns["_rt_workflow_pending"], log


class TestMainAppReadthroughOp:

    def test_role1_query_has_no_pool_branch(self, rt_op):
        fn, log = rt_op
        fn({"user_id": DREW, "role": 1})
        sql, params = log[0]
        assert "unassigned" not in sql and "IS NULL" not in sql
        assert "assigned_to_type = 'user' AND assigned_to_id = ?" in sql
        assert "assigned_to_type = 'group' AND assigned_to_id IN" in sql
        assert params == (DREW, DREW)

    @pytest.mark.parametrize("role", [2, 3, "3"])
    def test_developer_plus_query_is_unchanged(self, rt_op, role):
        fn, log = rt_op
        fn({"user_id": ERIN, "role": role})
        sql, params = log[0]
        assert "OR assigned_to_type = 'unassigned' OR assigned_to_type IS NULL" in sql
        assert params == (ERIN, ERIN)

    @pytest.mark.parametrize("params", [{"user_id": ALEX}, {"user_id": ALEX, "role": None},
                                        {"user_id": ALEX, "role": ""},
                                        {"user_id": ALEX, "role": "junk"}])
    def test_missing_or_bad_role_fails_closed(self, rt_op, params):
        fn, log = rt_op
        fn(params)                                     # an old agent that sends no role
        assert "unassigned" not in log[0][0] and "IS NULL" not in log[0][0]


# ---------------------------------------------------------------------------
# automation_pending — the sidecar JSON rows
# ---------------------------------------------------------------------------

def _sidecar(base, name, at, aid, title, status="Pending"):
    d = os.path.join(base, "automations", "tenant_1", "_approvals")
    os.makedirs(d, exist_ok=True)
    row = {"request_id": name, "title": title, "description": "", "status": status,
           "requested_at": "2026-09-08T10:00:00", "priority": 0,
           "assigned_to_type": at, "assigned_to_id": aid,
           "approval_data": json.dumps({"automation_name": "dayforce-doc-upload",
                                        "kind": "review"})}
    with open(os.path.join(d, name + ".json"), "w", encoding="utf-8") as f:
        json.dump(row, f)
    return row


@pytest.fixture
def sidecars(tmp_path, monkeypatch):
    base = str(tmp_path)
    monkeypatch.setattr(readthrough, "APP_ROOT", base)
    _sidecar(base, "a-alex", "user", ALEX, "Alex's review")
    _sidecar(base, "a-casey", "user", CASEY, "Casey's review")
    _sidecar(base, "a-ga", "group", GROUP_A, "Group A review")
    _sidecar(base, "a-gb", "group", GROUP_B, "Group B review")
    _sidecar(base, "a-pool", None, None, "Dayforce exception — NH07 (emp 3149231)")
    _sidecar(base, "a-pool-typed", "unassigned", None, "typed pool row")
    _sidecar(base, "a-settled", None, None, "already approved pool row", status="Approved")
    return base


def _auto(uid):
    return _ids(readthrough.automation_pending(uid, GROUPS[uid], role=ROLE[uid]))


class TestAutomationPending:

    def test_role1_matrix_direct_and_group_only(self, sidecars):
        assert _auto(ALEX) == ["a-alex", "a-ga"]
        assert _auto(BLAIR) == ["a-ga"]                 # same group as Alex
        assert _auto(CASEY) == ["a-casey", "a-gb"]
        assert _auto(DREW) == []                        # no group: fail-closed seat

    def test_developer_plus_unchanged(self, sidecars):
        assert _auto(ERIN) == ["a-ga", "a-pool", "a-pool-typed"]
        assert _auto(ADMIN) == ["a-pool", "a-pool-typed"]   # admin is in neither group

    def test_group_membership_still_gates_group_rows_for_every_role(self, sidecars):
        # a developer NOT in group B never sees B's row; a member does
        assert "a-gb" not in _ids(readthrough.automation_pending(ERIN, [GROUP_A], role=2))
        assert "a-gb" in _ids(readthrough.automation_pending(ERIN, [GROUP_B], role=2))

    @pytest.mark.parametrize("role", [None, 0, "", "x"])
    def test_missing_or_bad_role_fails_closed(self, sidecars, role):
        assert _ids(readthrough.automation_pending(ERIN, [GROUP_A], role=role)) == ["a-ga"]

    def test_role_is_required_keyword_only(self, sidecars):
        with pytest.raises(TypeError):
            readthrough.automation_pending(ALEX, [GROUP_A])
        with pytest.raises(TypeError):
            readthrough.automation_pending(ALEX, [GROUP_A], 2)
