"""Unit tests for workflow_recovery_service (one-time startup recovery).

Regression for the executor-startup error

    ERROR:WorkflowRecovery:Error processing approval timeouts:
        Expecting value: line 1 column 1 (char 0)

Root cause: ``_process_approval_timeouts`` did a strict ``json.loads`` on
``ApprovalRequests.approval_data`` inside its per-row loop.  ``approval_data``
is free-form by design (the engine stores whatever the Human Approval node's
``approvalData`` resolves to after ``${var}`` substitution -- LLM prose,
pipe-delimited summaries, file paths, ``review-me`` markers), so the first
non-JSON row raised, the loop aborted before ``commit()``, and every overdue
approval stayed Pending with its execution Paused forever.

Secondary defects fixed in the same change and pinned here:
* recovery read ``timeout_action`` from the approval_data JSON (never written
  there) and defaulted to *reject*; the engine consults the node's
  ``timeoutAction`` and defaults to *continue*;
* recovery wrote ``Timeout-Approve`` / ``Timeout-Reject`` while the engine
  writes ``Timeout-Approved`` / ``Timeout-Rejected``;
* one bad row aborted the whole batch and leaked the connection.

All DB access is faked; nothing here touches a live SQL Server.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

# workflow_recovery_service imports pyodbc at module level; keep the tests
# runnable in environments without the ODBC driver package.
if "pyodbc" not in sys.modules:
    try:
        import pyodbc  # noqa: F401
    except ImportError:  # pragma: no cover - only in stripped-down envs
        _stub = types.ModuleType("pyodbc")

        def _no_db(*_a, **_k):
            raise RuntimeError("pyodbc stub: no live DB in unit tests")

        _stub.connect = _no_db
        sys.modules["pyodbc"] = _stub

import workflow_recovery_service as wrs  # noqa: E402

_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Real-world fixtures, verbatim from the live AIHUB DB on 2026-08-21 (these are
# the exact values that were crashing startup recovery).
# ---------------------------------------------------------------------------
COLLECTIONS_TEXT = ("Export File: collections_export_currentDate.csv | Invoice Count: 2 | "
                    "Minimum Invoice Amount: 5000 | Lookback Months: 6")
BUILDER_TEXT = ("Outbound file ready for approval. File: outboundFilePath | Balance Threshold: "
                "balanceThreshold | Date Window: startDateWindow through endDateWindow")
LLM_PROSE = ("The provided content (Page 4) contains only signature/office-use information. "
             "Based on the requirements, no validation errors were found.")
MARKER = "review-me"
UNC_PATH = r"\\aihubstoragedev.file.core.windows.net\shared-files-dev\documents\chargebacks\cb.pdf"


def _definition(node_id="node-16", config=None, **extra_nodes):
    """Build a Workflows.workflow_data JSON string with one Human Approval node."""
    nodes = [{"id": node_id, "type": "Human Approval", "label": "Approval",
              "config": config if config is not None else {}}]
    for nid, cfg in extra_nodes.items():
        nodes.append({"id": nid, "type": "Human Approval", "config": cfg})
    return json.dumps({"nodes": nodes, "connections": []})


# ---------------------------------------------------------------------------
# Fake DB plumbing
# ---------------------------------------------------------------------------
class FakeCursor:
    """Records every execute(); serves the preset rows on the FIRST fetchall()."""

    def __init__(self, select_rows, fail_update_for=()):
        self._rows = list(select_rows)
        self._fail_update_for = set(fail_update_for)
        self.executed = []  # list of (normalised_sql, params)
        self.closed = False

    def execute(self, sql, *params):
        norm = " ".join(sql.split())
        self.executed.append((norm, params))
        if norm.startswith("UPDATE ApprovalRequests") and params and params[-1] in self._fail_update_for:
            raise RuntimeError(f"simulated DB failure for {params[-1]}")
        return self

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows

    def fetchone(self):
        return None

    def close(self):
        self.closed = True


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def _service(monkeypatch, rows, fail_update_for=()):
    monkeypatch.setenv("API_KEY", "TEST-API-KEY")
    cur = FakeCursor(rows, fail_update_for)
    conn = FakeConn(cur)
    svc = wrs.WorkflowRecoveryService(workflow_executor=None, connection_string="fake")
    monkeypatch.setattr(svc, "_get_db_connection", lambda: conn)
    return svc, conn, cur


def _approval_updates(cur):
    """-> {request_id: (status, comments)} for every UPDATE ApprovalRequests."""
    out = {}
    for sql, params in cur.executed:
        if sql.startswith("UPDATE ApprovalRequests"):
            status, comments, request_id = params
            out[request_id] = (status, comments)
    return out


def _timeout_row(request_id, approval_data, node_id="node-16", workflow_id=1418,
                 workflow_data="__default__", execution_id="exec-1", step_id="step-1"):
    if workflow_data == "__default__":
        workflow_data = _definition(node_id, {"timeoutAction": "fail", "dueHours": 24})
    # (request_id, approval_data, execution_id, step_execution_id, node_id, workflow_id, workflow_data)
    return (request_id, approval_data, execution_id, step_id, node_id, workflow_id, workflow_data)


# ---------------------------------------------------------------------------
# The fixture really reproduces the reported error under the old strict parse
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw", [COLLECTIONS_TEXT, BUILDER_TEXT, LLM_PROSE, MARKER, UNC_PATH])
def test_live_fixtures_reproduce_the_reported_json_error(raw):
    with pytest.raises(json.JSONDecodeError) as ei:
        json.loads(raw)
    assert "Expecting value: line 1 column 1 (char 0)" in str(ei.value)


# ---------------------------------------------------------------------------
# parse_approval_data
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw, expected", [
    (None, {}),
    ("", {}),
    ("   ", {}),
    (COLLECTIONS_TEXT, {}),
    (BUILDER_TEXT, {}),
    (LLM_PROSE, {}),
    (MARKER, {}),
    (UNC_PATH, {}),
    ('"just a JSON string"', {}),          # valid JSON but not an object
    ("[1, 2, 3]", {}),                     # valid JSON but not an object
    ("null", {}),
    ('{"vendor_name": "Adobe", "n": 2}', {"vendor_name": "Adobe", "n": 2}),
    ({"already": "parsed"}, {"already": "parsed"}),
    (b'{"from": "bytes"}', {"from": "bytes"}),
    (b"\xff\xfe not json", {}),
    (12345, {}),
])
def test_parse_approval_data_is_tolerant(raw, expected):
    assert wrs.parse_approval_data(raw) == expected


# ---------------------------------------------------------------------------
# find_node_config / resolve_timeout_action / timeout_status_for
# ---------------------------------------------------------------------------
def test_find_node_config_handles_string_dict_missing_and_garbage():
    defn = _definition("node-16", {"timeoutAction": "fail"}, **{"node-2": {"timeoutAction": "continue"}})
    assert wrs.find_node_config(defn, "node-16") == {"timeoutAction": "fail"}
    assert wrs.find_node_config(json.loads(defn), "node-2") == {"timeoutAction": "continue"}
    assert wrs.find_node_config(defn, "node-99") is None            # node not in definition
    assert wrs.find_node_config(None, "node-16") is None            # workflow deleted
    assert wrs.find_node_config("not json at all", "node-16") is None
    assert wrs.find_node_config(json.dumps({"nodes": "nope"}), "node-16") is None
    # node present but without a config block -> {} (resolvable, engine default applies)
    assert wrs.find_node_config(json.dumps({"nodes": [{"id": "n1", "type": "Human Approval"}]}), "n1") == {}


def test_timeout_action_precedence_mirrors_engine():
    # 1. node config is authoritative, even when approval_data carries a legacy hint
    assert wrs.resolve_timeout_action({"timeoutAction": "fail"}, {"timeout_action": "approve"}) == "fail"
    assert wrs.resolve_timeout_action({"timeoutAction": "continue"}, {"timeout_action": "reject"}) == "continue"
    # node found but no timeoutAction -> engine default 'continue' (NOT the old 'reject')
    assert wrs.resolve_timeout_action({}, {}) == wrs.DEFAULT_TIMEOUT_ACTION == "continue"
    assert wrs.resolve_timeout_action({"timeoutAction": ""}, {"timeout_action": "reject"}) == "continue"
    # 2. definition unresolvable -> legacy approval_data hint
    assert wrs.resolve_timeout_action(None, {"timeout_action": "reject"}) == "reject"
    # 3. nothing at all -> engine default
    assert wrs.resolve_timeout_action(None, {}) == "continue"
    assert wrs.resolve_timeout_action(None, None) == "continue"
    # normalisation
    assert wrs.resolve_timeout_action({"timeoutAction": " FAIL "}, {}) == "fail"


@pytest.mark.parametrize("action, status", [
    ("continue", "Timeout-Approved"),
    ("approve", "Timeout-Approved"),
    ("fail", "Timeout-Rejected"),
    ("reject", "Timeout-Rejected"),
    ("anything-else", "Timeout-Rejected"),
    ("", "Timeout-Rejected"),
])
def test_timeout_status_uses_engine_vocabulary(action, status):
    assert wrs.timeout_status_for(action) == status


def test_status_vocabulary_matches_the_execution_engine():
    """Drift guard: the strings recovery writes must be the ones the engine writes/reads."""
    assert wrs.STATUS_TIMEOUT_APPROVED == "Timeout-Approved"
    assert wrs.STATUS_TIMEOUT_REJECTED == "Timeout-Rejected"
    engine_src = (_ROOT / "workflow_execution.py").read_text(encoding="utf-8", errors="replace")
    assert "'Timeout-Approved'" in engine_src and "'Timeout-Rejected'" in engine_src
    recovery_src = (_ROOT / "workflow_recovery_service.py").read_text(encoding="utf-8")
    assert "f'Timeout-{" not in recovery_src, "recovery must not synthesise status names from timeout_action"
    # legacy rows written by the old recovery still count as approved in step 2
    assert "Timeout-Approve" in wrs.APPROVED_STATUSES


# ---------------------------------------------------------------------------
# _process_approval_timeouts — the actual regression
# ---------------------------------------------------------------------------
def test_free_text_approval_data_rows_are_all_processed(monkeypatch):
    rows = [
        # live shape #1: pipe text, node timeoutAction=fail  -> rejected
        _timeout_row("req-collections", COLLECTIONS_TEXT, execution_id="exec-a"),
        # live shape #2: LLM prose, node has no timeoutAction -> engine default continue -> approved
        _timeout_row("req-prose", LLM_PROSE, workflow_id=22,
                     workflow_data=_definition("node-16", {"dueHours": 4}), execution_id="exec-b"),
        # live shape #3: builder text, workflow deleted (workflow_data NULL) -> default continue
        _timeout_row("req-builder", BUILDER_TEXT, node_id="node-6", workflow_id=None,
                     workflow_data=None, execution_id="exec-c"),
        # pack-14 marker, node config present with explicit continue
        _timeout_row("req-marker", MARKER, workflow_id=77,
                     workflow_data=_definition("node-16", {"timeoutAction": "continue"}), execution_id="exec-d"),
        # genuine JSON object (the 8% case) still works
        _timeout_row("req-json", '{"vendor_name": "Adobe"}', execution_id="exec-e"),
    ]
    svc, conn, cur = _service(monkeypatch, rows)

    processed = svc._process_approval_timeouts()

    assert processed == 5
    updates = _approval_updates(cur)
    assert set(updates) == {"req-collections", "req-prose", "req-builder", "req-marker", "req-json"}
    assert updates["req-collections"][0] == "Timeout-Rejected"
    assert updates["req-prose"][0] == "Timeout-Approved"
    assert updates["req-builder"][0] == "Timeout-Approved"
    assert updates["req-marker"][0] == "Timeout-Approved"
    assert updates["req-json"][0] == "Timeout-Rejected"
    # comments explain what happened and why
    assert "timed out" in updates["req-collections"][1]
    assert "timeoutAction=fail" in updates["req-collections"][1]
    assert "Auto-approved" in updates["req-prose"][1]
    # every row committed on its own; connection + cursor released
    assert conn.commits == 5
    assert conn.rollbacks == 0
    assert conn.closed and cur.closed
    # the SELECT joins the definition so timeoutAction can be honoured
    select_sql = next(sql for sql, _ in cur.executed if sql.startswith("SELECT"))
    assert "LEFT JOIN Workflows" in select_sql and "w.workflow_data" in select_sql


def test_one_failing_row_does_not_abort_the_batch(monkeypatch):
    rows = [
        _timeout_row("req-1", COLLECTIONS_TEXT, execution_id="exec-1"),
        _timeout_row("req-2-bad", LLM_PROSE, execution_id="exec-2"),
        _timeout_row("req-3", MARKER, execution_id="exec-3"),
    ]
    svc, conn, cur = _service(monkeypatch, rows, fail_update_for={"req-2-bad"})

    processed = svc._process_approval_timeouts()

    assert processed == 2
    updates = _approval_updates(cur)
    assert "req-1" in updates and "req-3" in updates
    assert conn.commits == 2
    assert conn.rollbacks == 1          # the failed row was rolled back, others kept
    assert conn.closed and cur.closed   # no leaked connection


def test_no_overdue_approvals_is_a_quiet_noop(monkeypatch):
    svc, conn, cur = _service(monkeypatch, rows=[])
    assert svc._process_approval_timeouts() == 0
    assert _approval_updates(cur) == {}
    assert conn.closed and cur.closed


def test_definition_is_parsed_once_per_workflow(monkeypatch):
    """Seven daily runs of the same scheduled workflow share one parsed definition."""
    calls = {"n": 0}
    real_loads = wrs.json.loads

    def counting_loads(s, *a, **k):
        if isinstance(s, str) and s.startswith('{"nodes"'):
            calls["n"] += 1
        return real_loads(s, *a, **k)

    monkeypatch.setattr(wrs.json, "loads", counting_loads)
    rows = [_timeout_row(f"req-{i}", COLLECTIONS_TEXT, execution_id=f"exec-{i}") for i in range(7)]
    svc, conn, cur = _service(monkeypatch, rows)
    assert svc._process_approval_timeouts() == 7
    assert calls["n"] == 1
    assert all(s == "Timeout-Rejected" for s, _ in _approval_updates(cur).values())


# ---------------------------------------------------------------------------
# _process_pending_approval_responses — step 2 consumes what step 1 wrote
# ---------------------------------------------------------------------------
def _response_row(request_id, status, execution_id, comments="c", responded_by="System-Recovery"):
    # (request_id, status, responded_by, comments, execution_id, step_execution_id, node_id, workflow_name)
    return (request_id, status, responded_by, comments, execution_id, f"step-{execution_id}", "node-16", "WF")


def _execution_updates(cur):
    """-> {execution_id: (status, execution_data_or_None)} for UPDATE WorkflowExecutions."""
    out = {}
    for sql, params in cur.executed:
        if sql.startswith("UPDATE WorkflowExecutions"):
            if "'Completed'" in sql:
                out[params[0]] = ("Completed", None)
            else:
                out[params[1]] = ("Failed", json.loads(params[0]))
    return out


def test_step2_honours_engine_and_legacy_timeout_statuses(monkeypatch):
    rows = [
        _response_row("r1", "Timeout-Approved", "exec-ta"),   # engine spelling
        _response_row("r2", "Timeout-Approve", "exec-legacy"),  # old recovery spelling
        _response_row("r3", "Timeout-Rejected", "exec-tr", comments="Auto-rejected by startup recovery: timed out (timeoutAction=fail)"),
        _response_row("r4", "Approved", "exec-ok", responded_by="alice"),
        _response_row("r5", "Rejected", "exec-no", responded_by="bob", comments="nope"),
    ]
    svc, conn, cur = _service(monkeypatch, rows)

    assert svc._process_pending_approval_responses() == 5

    ex = _execution_updates(cur)
    assert ex["exec-ta"][0] == "Completed"
    assert ex["exec-legacy"][0] == "Completed"
    assert ex["exec-ok"][0] == "Completed"
    assert ex["exec-tr"][0] == "Failed"
    assert "timed out" in ex["exec-tr"][1]["error"]
    assert ex["exec-no"][0] == "Failed"
    assert ex["exec-no"][1]["error"] == "Approval rejected by bob: nope"
    # audit trail is explicit that recovery does not resume downstream nodes
    log_rows = [p for sql, p in cur.executed if sql.startswith("INSERT INTO ExecutionLogs")]
    assert len(log_rows) == 5
    details = {p[0]: json.loads(p[2]) for p in log_rows}
    assert details["exec-ta"]["downstream_resumed"] is False
    assert details["exec-ta"]["approval_status"] == "Timeout-Approved"
    msg_by_exec = {p[0]: p[1] for p in log_rows}
    assert "NOT resumed" in msg_by_exec["exec-ta"]
    assert "timed out" in msg_by_exec["exec-tr"]
    assert conn.commits == 1 and conn.closed and cur.closed


# ---------------------------------------------------------------------------
# run_recovery end-to-end over the fakes
# ---------------------------------------------------------------------------
def test_run_recovery_reports_counts_and_no_errors(monkeypatch):
    rows = [_timeout_row("req-1", COLLECTIONS_TEXT), _timeout_row("req-2", BUILDER_TEXT, node_id="node-6")]
    svc, conn, cur = _service(monkeypatch, rows)
    stats = svc.run_recovery()
    assert stats["timeouts_handled"] == 2
    assert stats["approvals_processed"] == 0   # fake cursor serves rows only once
    assert stats["stale_cleaned"] == 0
    assert stats["errors"] == []
