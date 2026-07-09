"""
Tests for agent_email_send.py
=============================
The single chokepoint that gates ALL agent-as-itself outbound email by the
agent's require_approval flag (queue vs send), plus the approval-queue read/act
helpers used by the routes.
"""

import sys
import types
import json
from datetime import datetime
import pytest
from unittest.mock import patch, MagicMock

import agent_email_send as aes


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeCursor:
    def __init__(self, one=None, all_rows=None, rowcount=0):
        self._one = one
        self._all = all_rows if all_rows is not None else []
        self.rowcount = rowcount
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all

    def close(self):
        pass


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False
        self.closed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def _patch_conn(cursor):
    return patch.object(aes, "get_db_connection", return_value=FakeConn(cursor))


def _fake_notification(result):
    mod = types.ModuleType("notification_client")
    mod.send_email_notification = MagicMock(return_value=result)
    return mod


# ---------------------------------------------------------------------------
# send_agent_email — the gate
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestSendAgentEmailGate:
    def test_require_approval_true_queues_and_does_not_send(self):
        with patch.object(aes, "get_send_config", return_value={"require_approval": True}), \
             patch.object(aes, "_queue_approval", return_value={"status": "queued", "approval_id": 5, "success": True}) as q, \
             patch.object(aes, "_do_send") as send:
            result = aes.send_agent_email(1, ["x@y.com"], "Subj", "Body", source="auto_reply")

        assert result["status"] == "queued"
        q.assert_called_once()
        send.assert_not_called()

    def test_require_approval_false_sends(self):
        with patch.object(aes, "get_send_config", return_value={"require_approval": False}), \
             patch.object(aes, "_queue_approval") as q, \
             patch.object(aes, "_do_send", return_value={"status": "sent", "success": True, "message_id": "m1"}) as send:
            result = aes.send_agent_email(1, ["x@y.com"], "Subj", "Body")

        assert result["status"] == "sent"
        send.assert_called_once()
        q.assert_not_called()

    def test_no_config_defaults_to_send(self):
        with patch.object(aes, "get_send_config", return_value=None), \
             patch.object(aes, "_do_send", return_value={"status": "sent", "success": True}) as send:
            result = aes.send_agent_email(1, ["x@y.com"], "S", "B")
        assert result["status"] == "sent"
        send.assert_called_once()

    def test_no_recipient_fails(self):
        with patch.object(aes, "get_send_config", return_value={"require_approval": False}), \
             patch.object(aes, "_do_send") as send:
            result = aes.send_agent_email(1, [], "S", "B")
        assert result["status"] == "failed"
        send.assert_not_called()


# ---------------------------------------------------------------------------
# _do_send — canonical transport
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestDoSend:
    def test_success(self):
        with patch.dict(sys.modules, {"notification_client": _fake_notification({"success": True, "message_id": "m9"})}):
            result = aes._do_send(["a@b.com"], "S", "B", agent_id=1)
        assert result["status"] == "sent"
        assert result["message_id"] == "m9"

    def test_failure_preserves_blocked_by_limit(self):
        nc = _fake_notification({"success": False, "error": "limit", "blocked_by_limit": True})
        with patch.dict(sys.modules, {"notification_client": nc}):
            result = aes._do_send(["a@b.com"], "S", "B", agent_id=1)
        assert result["status"] == "failed"
        assert result["blocked_by_limit"] is True


# ---------------------------------------------------------------------------
# Queue + read/act helpers
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestQueueApproval:
    def test_inserts_pending_and_returns_id(self):
        cur = FakeCursor(one=(42,))
        with _patch_conn(cur):
            result = aes._queue_approval(1, ["x@y.com"], "S", "B", source="chat_tool")
        assert result["status"] == "queued"
        assert result["approval_id"] == 42
        assert any("INSERT INTO AgentEmailApprovals" in (sql or "") for sql, _ in cur.executed)


@pytest.mark.unit
class TestListApprovals:
    def test_empty_agent_ids_denies(self):
        # deny-all (empty accessible set) must not even hit the DB
        with patch.object(aes, "get_db_connection", side_effect=AssertionError("should not query")):
            assert aes.list_approvals(status="pending", agent_ids=[]) == []

    def test_parses_rows(self):
        now = datetime(2026, 1, 1, 12, 0, 0)
        row = (
            7, 3, "auto_reply", json.dumps(["x@y.com"]), "Alice", "Re: hi",
            "draft", None, "pending", None, None, None, None, None, None, 99, "mk", now,
        )
        cur = FakeCursor(all_rows=[row])
        with _patch_conn(cur):
            items = aes.list_approvals(status="pending", agent_ids=None)
        assert len(items) == 1
        assert items[0]["approval_id"] == 7
        assert items[0]["to_addresses"] == ["x@y.com"]
        assert items[0]["status"] == "pending"
        assert items[0]["created_at"] is not None


@pytest.mark.unit
class TestRejectApproval:
    def test_reject_success(self):
        cur = FakeCursor(rowcount=1)
        with _patch_conn(cur):
            result = aes.reject_approval(7, approver_user_id=2, comments="no")
        assert result["success"] is True
        assert result["status"] == "rejected"

    def test_reject_not_pending(self):
        cur = FakeCursor(rowcount=0)
        with _patch_conn(cur):
            result = aes.reject_approval(7, approver_user_id=2)
        assert result["success"] is False


@pytest.mark.unit
class TestSendApprovedEmail:
    def test_sends_edited_body_and_marks_sent(self):
        # read returns a pending row: (agent_id, to_json, subject, attachments_json, status)
        read_cur = FakeCursor(one=(3, json.dumps(["x@y.com"]), "Subj", None, "pending"))
        update_conn = FakeConn(FakeCursor())
        # First _local_cursor() call -> read; second -> update. Patch get_db_connection
        # to return distinct connections per call.
        conns = [FakeConn(read_cur), update_conn]
        with patch.object(aes, "get_db_connection", side_effect=conns), \
             patch.object(aes, "_do_send", return_value={"status": "sent", "success": True, "message_id": "mm"}) as send:
            result = aes.send_approved_email(7, "EDITED BODY", approver_user_id=2)

        assert result["success"] is True
        assert result["status"] == "sent"
        # the edited body is what was sent
        args, kwargs = send.call_args
        assert "EDITED BODY" in args

    def test_already_resolved_rejected(self):
        read_cur = FakeCursor(one=(3, json.dumps(["x@y.com"]), "Subj", None, "sent"))
        with patch.object(aes, "get_db_connection", return_value=FakeConn(read_cur)), \
             patch.object(aes, "_do_send") as send:
            result = aes.send_approved_email(7, "B", approver_user_id=2)
        assert result["success"] is False
        send.assert_not_called()
