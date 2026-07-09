"""
Tests for email_agent_dispatcher.py
======================================
Tests EmailAgentDispatcher initialization, lifecycle, rate limiting,
filter rules matching, stats, attachment-text injection into workflows,
auto-response routing through the send_agent_email gate, and singleton functions.
"""

import sys
import os
import json
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime, date

# ---------------------------------------------------------------------------
# Module-level mocks (before import)
# ---------------------------------------------------------------------------
_mock_common_utils = MagicMock()
_mock_common_utils.get_db_connection = MagicMock()
_mock_common_utils.rotate_logs_on_startup = MagicMock()
_mock_common_utils.get_agent_api_base_url = MagicMock(return_value="http://localhost:5041")
_mock_common_utils.get_log_path = MagicMock(return_value="test_log.txt")

_saved = {}
for mod_name in ("CommonUtils",):
    _saved[mod_name] = sys.modules.get(mod_name)

sys.modules["CommonUtils"] = _mock_common_utils

if "email_agent_dispatcher" in sys.modules:
    del sys.modules["email_agent_dispatcher"]

with patch("logging.handlers.WatchedFileHandler", MagicMock()):
    from email_agent_dispatcher import (
        EmailAgentDispatcher,
        get_dispatcher,
        start_dispatcher,
        stop_dispatcher,
    )

for k, v in _saved.items():
    if v is not None:
        sys.modules[k] = v


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_dispatcher(**kwargs):
    """Create a dispatcher without starting it."""
    return EmailAgentDispatcher(**kwargs)


def _make_email(event_id=1, sender="alice@example.com", subject="Test", **extra):
    """Create a sample email dict."""
    email = {
        "event_id": event_id,
        "sender_email": sender,
        "sender_name": "Alice",
        "subject": subject,
        "body_preview": "Hello, this is a test email.",
        "recipient_email": "agent@company.com",
        "received_at": "2024-01-15T10:00:00Z",
        "message_key": "msg-key-123",
    }
    email.update(extra)
    return email


def _make_config(agent_id=1, **overrides):
    """Create a sample agent email config dict."""
    config = {
        "agent_id": agent_id,
        "email_address": "agent@company.com",
        "from_name": "AI Agent",
        "inbound_enabled": True,
        "auto_respond_enabled": False,
        "auto_respond_instructions": "",
        "auto_respond_style": "professional",
        "require_approval": True,
        "workflow_trigger_enabled": False,
        "workflow_id": None,
        "workflow_filter_rules": None,
        "max_auto_responses_per_day": 50,
        "cooldown_minutes": 15,
        "auto_responses_today": 0,
    }
    config.update(overrides)
    return config


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestEmailAgentDispatcherInit:
    """Test EmailAgentDispatcher initialization."""

    def test_default_poll_interval(self):
        d = _make_dispatcher()
        assert d.poll_interval == 60
        assert d.max_emails_per_poll == 50

    def test_custom_poll_interval(self):
        d = _make_dispatcher(poll_interval=120)
        assert d.poll_interval == 120

    @patch("email_agent_dispatcher.logger")
    def test_minimum_poll_interval(self, mock_logger):
        d = _make_dispatcher(poll_interval=10)
        assert d.poll_interval == 60  # Enforced minimum

    def test_initial_stats(self):
        d = _make_dispatcher()
        stats = d.stats
        assert stats["started_at"] is None
        assert stats["total_polls"] == 0
        assert stats["total_processed"] == 0
        assert stats["total_errors"] == 0

    def test_not_running_initially(self):
        d = _make_dispatcher()
        assert d.is_running() is False

    def test_flask_app_set(self):
        app = MagicMock()
        d = _make_dispatcher(flask_app=app)
        assert d._flask_app is app

    def test_set_flask_app(self):
        d = _make_dispatcher()
        app = MagicMock()
        d.set_flask_app(app)
        assert d._flask_app is app


@pytest.mark.unit
class TestGetStats:
    """Test get_stats method."""

    def test_includes_running_and_interval(self):
        d = _make_dispatcher(poll_interval=90)
        stats = d.get_stats()
        assert stats["running"] is False
        assert stats["poll_interval"] == 90
        assert "total_polls" in stats
        assert "total_processed" in stats


@pytest.mark.unit
class TestLifecycle:
    """Test start/stop lifecycle."""

    @patch("email_agent_dispatcher.logger")
    @patch.object(EmailAgentDispatcher, "_poll_loop")
    def test_start_sets_running(self, mock_poll, mock_logger):
        d = _make_dispatcher()
        d.start()
        assert d.is_running() is True
        assert d.stats["started_at"] is not None
        d._running = False  # Stop for cleanup

    @patch("email_agent_dispatcher.logger")
    @patch.object(EmailAgentDispatcher, "_poll_loop")
    def test_start_twice_no_error(self, mock_poll, mock_logger):
        d = _make_dispatcher()
        d.start()
        d.start()  # Should not raise
        d._running = False

    def test_stop_when_not_running(self):
        d = _make_dispatcher()
        d.stop()  # Should not raise


@pytest.mark.unit
class TestCheckRateLimit:
    """Test the daily-cap half of _check_rate_limit (cooldown disabled)."""

    def test_within_limit(self):
        d = _make_dispatcher()
        config = _make_config(auto_responses_today=10, max_auto_responses_per_day=50, cooldown_minutes=0)
        assert d._check_rate_limit(1, config) is True

    def test_at_limit(self):
        d = _make_dispatcher()
        config = _make_config(auto_responses_today=50, max_auto_responses_per_day=50, cooldown_minutes=0)
        assert d._check_rate_limit(1, config) is False

    def test_over_limit(self):
        d = _make_dispatcher()
        config = _make_config(auto_responses_today=100, max_auto_responses_per_day=50, cooldown_minutes=0)
        assert d._check_rate_limit(1, config) is False

    def test_zero_responses(self):
        d = _make_dispatcher()
        config = _make_config(auto_responses_today=0, max_auto_responses_per_day=50, cooldown_minutes=0)
        assert d._check_rate_limit(1, config) is True


@pytest.mark.unit
class TestCooldown:
    """Test per-agent cooldown enforcement folded into _check_rate_limit."""

    @patch("email_agent_dispatcher.logger")
    def test_within_cooldown_blocked(self, mock_logger):
        d = _make_dispatcher()
        config = _make_config(cooldown_minutes=15, auto_responses_today=1, max_auto_responses_per_day=50)
        with patch.object(d, "_minutes_since_last_auto_response", return_value=5):
            assert d._check_rate_limit(1, config) is False

    def test_after_cooldown_allowed(self):
        d = _make_dispatcher()
        config = _make_config(cooldown_minutes=15, auto_responses_today=1, max_auto_responses_per_day=50)
        with patch.object(d, "_minutes_since_last_auto_response", return_value=20):
            assert d._check_rate_limit(1, config) is True

    def test_exactly_at_cooldown_allowed(self):
        d = _make_dispatcher()
        config = _make_config(cooldown_minutes=15)
        with patch.object(d, "_minutes_since_last_auto_response", return_value=15):
            # 15 is NOT < 15, so the cooldown window has elapsed
            assert d._check_rate_limit(1, config) is True

    def test_no_prior_response_allowed(self):
        d = _make_dispatcher()
        config = _make_config(cooldown_minutes=15)
        with patch.object(d, "_minutes_since_last_auto_response", return_value=None):
            assert d._check_rate_limit(1, config) is True

    def test_cooldown_zero_disables_check(self):
        d = _make_dispatcher()
        config = _make_config(cooldown_minutes=0)
        with patch.object(d, "_minutes_since_last_auto_response", return_value=1) as m:
            assert d._check_rate_limit(1, config) is True
            m.assert_not_called()  # cooldown=0 must not even query

    def test_daily_cap_takes_precedence(self):
        d = _make_dispatcher()
        config = _make_config(cooldown_minutes=5, auto_responses_today=50, max_auto_responses_per_day=50)
        # Daily cap blocks first; cooldown lookup should not be needed.
        with patch.object(d, "_minutes_since_last_auto_response", return_value=999) as m:
            assert d._check_rate_limit(1, config) is False
            m.assert_not_called()

    def test_minutes_since_helper_reads_datediff(self):
        d = _make_dispatcher()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (7,)
        with patch.object(d, "_get_db_connection", return_value=(mock_conn, mock_cursor)):
            assert d._minutes_since_last_auto_response(1) == 7

    def test_minutes_since_helper_none_when_no_history(self):
        d = _make_dispatcher()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (None,)
        with patch.object(d, "_get_db_connection", return_value=(mock_conn, mock_cursor)):
            assert d._minutes_since_last_auto_response(1) is None

    @patch("email_agent_dispatcher.logger")
    def test_minutes_since_helper_db_error_fails_open(self, mock_logger):
        d = _make_dispatcher()
        with patch.object(d, "_get_db_connection", side_effect=Exception("DB down")):
            assert d._minutes_since_last_auto_response(1) is None


@pytest.mark.unit
class TestMatchesFilterRules:
    """Test _matches_filter_rules."""

    def test_no_rules_matches_all(self):
        d = _make_dispatcher()
        email = _make_email(subject="Anything")
        assert d._matches_filter_rules(email, None) is True
        assert d._matches_filter_rules(email, []) is True

    def test_contains_subject(self):
        d = _make_dispatcher()
        email = _make_email(subject="Invoice #12345")
        rules = [{"field": "subject", "operator": "contains", "value": "invoice"}]
        assert d._matches_filter_rules(email, rules) is True

    def test_contains_subject_no_match(self):
        d = _make_dispatcher()
        email = _make_email(subject="Hello World")
        rules = [{"field": "subject", "operator": "contains", "value": "invoice"}]
        assert d._matches_filter_rules(email, rules) is False

    def test_not_contains(self):
        d = _make_dispatcher()
        email = _make_email(subject="Hello World")
        rules = [{"field": "subject", "operator": "not_contains", "value": "spam"}]
        assert d._matches_filter_rules(email, rules) is True

    def test_not_contains_fails(self):
        d = _make_dispatcher()
        email = _make_email(subject="This is spam content")
        rules = [{"field": "subject", "operator": "not_contains", "value": "spam"}]
        assert d._matches_filter_rules(email, rules) is False

    def test_equals(self):
        d = _make_dispatcher()
        email = _make_email(subject="Urgent")
        rules = [{"field": "subject", "operator": "equals", "value": "urgent"}]
        assert d._matches_filter_rules(email, rules) is True

    def test_equals_no_match(self):
        d = _make_dispatcher()
        email = _make_email(subject="Not Urgent")
        rules = [{"field": "subject", "operator": "equals", "value": "urgent"}]
        assert d._matches_filter_rules(email, rules) is False

    def test_starts_with(self):
        d = _make_dispatcher()
        email = _make_email(subject="RE: Your order")
        rules = [{"field": "subject", "operator": "starts_with", "value": "re:"}]
        assert d._matches_filter_rules(email, rules) is True

    def test_ends_with(self):
        d = _make_dispatcher()
        email = _make_email(sender="user@example.com")
        rules = [{"field": "from", "operator": "ends_with", "value": "@example.com"}]
        assert d._matches_filter_rules(email, rules) is True

    def test_sender_field_alias(self):
        d = _make_dispatcher()
        email = _make_email(sender="user@test.com")
        rules = [{"field": "sender", "operator": "contains", "value": "@test.com"}]
        assert d._matches_filter_rules(email, rules) is True

    def test_body_field(self):
        d = _make_dispatcher()
        email = _make_email(body_preview="Please find the invoice attached")
        rules = [{"field": "body", "operator": "contains", "value": "invoice"}]
        assert d._matches_filter_rules(email, rules) is True

    def test_multiple_rules_all_match(self):
        d = _make_dispatcher()
        email = _make_email(subject="Invoice #123", sender="billing@acme.com")
        rules = [
            {"field": "subject", "operator": "contains", "value": "invoice"},
            {"field": "from", "operator": "ends_with", "value": "@acme.com"},
        ]
        assert d._matches_filter_rules(email, rules) is True

    def test_multiple_rules_one_fails(self):
        d = _make_dispatcher()
        email = _make_email(subject="Invoice #123", sender="billing@other.com")
        rules = [
            {"field": "subject", "operator": "contains", "value": "invoice"},
            {"field": "from", "operator": "ends_with", "value": "@acme.com"},
        ]
        assert d._matches_filter_rules(email, rules) is False

    def test_unknown_field_skipped(self):
        d = _make_dispatcher()
        email = _make_email(subject="Test")
        rules = [{"field": "unknown_field", "operator": "contains", "value": "test"}]
        # Unknown fields are skipped (continue), so all rules pass
        assert d._matches_filter_rules(email, rules) is True

    def test_case_insensitive(self):
        d = _make_dispatcher()
        email = _make_email(subject="URGENT: Action Required")
        rules = [{"field": "subject", "operator": "contains", "value": "urgent"}]
        assert d._matches_filter_rules(email, rules) is True


@pytest.mark.unit
class TestTriggerWorkflowAttachments:
    """_trigger_workflow injects extracted attachment text as workflow variables."""

    @patch("email_agent_dispatcher.logger")
    def test_injects_attachment_text(self, mock_logger):
        d = _make_dispatcher()
        config = _make_config(workflow_id=42)
        email = _make_email(attachment_count=1, has_attachments=True)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"execution_id": "exec-aa"}
        mock_requests = MagicMock()
        mock_requests.post.return_value = mock_response

        mock_cu = MagicMock()
        mock_cu.get_executor_api_base_url = MagicMock(return_value="http://localhost:5061")

        fake_att = MagicMock()
        fake_att.get_attachment_texts_for_event.return_value = [
            {"filename": "a.pdf", "text": "INVOICE TOTAL 100", "error": None}
        ]
        fake_att.build_combined_attachment_text.return_value = "--- a.pdf ---\nINVOICE TOTAL 100"

        with patch.dict("sys.modules", {
            "requests": mock_requests,
            "CommonUtils": mock_cu,
            "agent_email_attachments": fake_att,
        }):
            result = d._trigger_workflow(config, email, {"body_text": "hi"})

        assert result["success"] is True
        posted = mock_requests.post.call_args.kwargs["json"]
        variables = posted["variables"]
        assert "email_attachment_text" in variables
        assert "INVOICE TOTAL 100" in variables["email_attachment_text"]
        assert variables["email_attachment_1_text"] == "INVOICE TOTAL 100"

    @patch("email_agent_dispatcher.logger")
    def test_no_attachments_no_text_var(self, mock_logger):
        d = _make_dispatcher()
        config = _make_config(workflow_id=42)
        email = _make_email()  # no attachments

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"execution_id": "exec-bb"}
        mock_requests = MagicMock()
        mock_requests.post.return_value = mock_response
        mock_cu = MagicMock()
        mock_cu.get_executor_api_base_url = MagicMock(return_value="http://localhost:5061")

        with patch.dict("sys.modules", {"requests": mock_requests, "CommonUtils": mock_cu}):
            result = d._trigger_workflow(config, email, None)

        assert result["success"] is True
        posted = mock_requests.post.call_args.kwargs["json"]
        assert "email_attachment_text" not in posted["variables"]


def _fake_agent_api_client(response_text="DRAFT REPLY"):
    mod = MagicMock()
    client = MagicMock()
    client.chat.return_value = {"response": response_text}
    mod.AgentAPIClient.return_value = client
    return mod


@pytest.mark.unit
class TestAutoResponseGate:
    """_trigger_auto_response routes the send through send_agent_email (the gate)."""

    @patch("email_agent_dispatcher.logger")
    def test_queued_returns_pending_approval(self, mock_logger):
        d = _make_dispatcher()
        config = _make_config(auto_respond_enabled=True)
        email = _make_email()

        fake_send = MagicMock()
        fake_send.send_agent_email.return_value = {"status": "queued", "approval_id": 11, "success": True}

        with patch.object(d, "_check_rate_limit", return_value=True), \
             patch.dict("sys.modules", {
                 "agent_api_client": _fake_agent_api_client(),
                 "agent_email_send": fake_send,
             }):
            result = d._trigger_auto_response(config, email, {"body_text": "hi"})

        assert result["success"] is True
        assert result["pending_approval"] is True
        assert result["approval_id"] == 11
        fake_send.send_agent_email.assert_called_once()

    @patch("email_agent_dispatcher.logger")
    def test_sent_increments_counter(self, mock_logger):
        d = _make_dispatcher()
        config = _make_config(auto_respond_enabled=True)
        email = _make_email()

        fake_send = MagicMock()
        fake_send.send_agent_email.return_value = {"status": "sent", "message_id": "m1", "success": True}

        with patch.object(d, "_check_rate_limit", return_value=True), \
             patch.object(d, "_increment_daily_counter") as inc, \
             patch.dict("sys.modules", {
                 "agent_api_client": _fake_agent_api_client(),
                 "agent_email_send": fake_send,
             }):
            result = d._trigger_auto_response(config, email, {"body_text": "hi"})

        assert result["success"] is True
        assert result["message_id"] == "m1"
        inc.assert_called_once()

    @patch("email_agent_dispatcher.logger")
    def test_auto_reply_prompt_is_attachment_aware(self, mock_logger):
        # an email WITH attachments -> the extracted attachment text is folded into
        # the prompt handed to the agent so the reply can reference it.
        d = _make_dispatcher()
        config = _make_config(auto_respond_enabled=True)
        email = _make_email(attachment_count=1, has_attachments=True)

        client = MagicMock()
        client.chat.return_value = {"response": "ok reply"}
        fake_api = MagicMock()
        fake_api.AgentAPIClient.return_value = client

        fake_send = MagicMock()
        fake_send.send_agent_email.return_value = {"status": "sent", "message_id": "m1", "success": True}

        fake_att = MagicMock()
        fake_att.get_attachment_texts_for_event.return_value = [
            {"filename": "a.pdf", "text": "INVOICE TOTAL 100", "error": None}]
        fake_att.build_combined_attachment_text.return_value = "--- a.pdf ---\nINVOICE TOTAL 100"

        with patch.object(d, "_check_rate_limit", return_value=True), \
             patch.object(d, "_increment_daily_counter"), \
             patch.dict("sys.modules", {
                 "agent_api_client": fake_api,
                 "agent_email_send": fake_send,
                 "agent_email_attachments": fake_att,
             }):
            result = d._trigger_auto_response(config, email, {"body_text": "hi"})

        assert result["success"] is True
        prompt = client.chat.call_args.kwargs["prompt"]
        assert "INVOICE TOTAL 100" in prompt, "auto-reply prompt is not attachment-aware"


@pytest.mark.unit
class TestIsAlreadyProcessed:
    """Test _is_already_processed with mocked DB."""

    def test_not_processed(self):
        d = _make_dispatcher()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = None

        with patch.object(d, "_get_db_connection", return_value=(mock_conn, mock_cursor)):
            result = d._is_already_processed(1, 100)
        assert result is False

    def test_already_processed(self):
        d = _make_dispatcher()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (1,)

        with patch.object(d, "_get_db_connection", return_value=(mock_conn, mock_cursor)):
            result = d._is_already_processed(1, 100)
        assert result is True

    @patch("email_agent_dispatcher.logger")
    def test_db_error_returns_false(self, mock_logger):
        d = _make_dispatcher()
        with patch.object(d, "_get_db_connection", side_effect=Exception("DB down")):
            result = d._is_already_processed(1, 100)
        assert result is False


@pytest.mark.unit
class TestTriggerWorkflow:
    """Test _trigger_workflow."""

    @patch("email_agent_dispatcher.logger")
    def test_no_workflow_id(self, mock_logger):
        d = _make_dispatcher()
        config = _make_config(workflow_id=None)
        result = d._trigger_workflow(config, _make_email(), None)
        assert result["success"] is False
        assert "No workflow" in result["error"]

    @patch("email_agent_dispatcher.logger")
    def test_successful_trigger(self, mock_logger):
        d = _make_dispatcher()
        config = _make_config(workflow_id=42)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"execution_id": "exec-001"}

        mock_requests = MagicMock()
        mock_requests.post.return_value = mock_response

        mock_cu = MagicMock()
        mock_cu.get_executor_api_base_url = MagicMock(return_value="http://localhost:5061")

        with patch.dict("sys.modules", {"requests": mock_requests, "CommonUtils": mock_cu}):
            result = d._trigger_workflow(config, _make_email(), {"body_text": "Test"})

        assert result["success"] is True
        assert result["execution_id"] == "exec-001"

    @patch("email_agent_dispatcher.logger")
    def test_api_error(self, mock_logger):
        d = _make_dispatcher()
        config = _make_config(workflow_id=42)

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {"message": "Server error"}

        mock_requests = MagicMock()
        mock_requests.post.return_value = mock_response

        mock_cu = MagicMock()
        mock_cu.get_executor_api_base_url = MagicMock(return_value="http://localhost:5061")

        with patch.dict("sys.modules", {"requests": mock_requests, "CommonUtils": mock_cu}):
            result = d._trigger_workflow(config, _make_email(), None)

        assert result["success"] is False


@pytest.mark.unit
class TestIsEnterpriseEnabled:
    """Test _is_enterprise_enabled."""

    def test_returns_true_when_import_fails(self):
        d = _make_dispatcher()
        with patch.dict("sys.modules", {"admin_tier_usage": None}):
            result = d._is_enterprise_enabled()
        assert result is True

    def test_uses_cache(self):
        d = _make_dispatcher()
        d._enterprise_enabled_cache = True
        d._enterprise_cache_time = datetime.now()
        result = d._is_enterprise_enabled()
        assert result is True


@pytest.mark.unit
class TestSingletonFunctions:
    """Test get_dispatcher, start_dispatcher, stop_dispatcher."""

    def test_get_dispatcher_creates_instance(self):
        import email_agent_dispatcher as mod
        mod._dispatcher_instance = None

        with patch.dict("os.environ", {"EMAIL_POLL_INTERVAL": "120"}):
            d = get_dispatcher()
        assert d is not None
        assert d.poll_interval == 120

        # Cleanup
        mod._dispatcher_instance = None

    def test_get_dispatcher_returns_same_instance(self):
        import email_agent_dispatcher as mod
        mod._dispatcher_instance = None

        d1 = get_dispatcher()
        d2 = get_dispatcher()
        assert d1 is d2

        mod._dispatcher_instance = None
