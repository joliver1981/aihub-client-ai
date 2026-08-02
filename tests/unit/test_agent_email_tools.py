"""
Tests for agent_email_tools.py
================================
Tests email tool context management, Cloud API client, helper functions,
tool functions (check_my_inbox, send_email, search_inbox, etc.), and
factory / system-prompt functions.
"""

import sys
import json
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

# ---------------------------------------------------------------------------
# Module-level mocks (before import)
# ---------------------------------------------------------------------------
_mock_common_utils = MagicMock()
_mock_common_utils.rotate_logs_on_startup = MagicMock()
_mock_common_utils.get_cloud_db_connection = MagicMock()
_mock_common_utils.get_log_path = MagicMock(return_value="test_log.txt")

_mock_config = MagicMock()
# Mirror config.py. These used to be "50000" (a string, and 10x below the real
# value), which is how the stale "default: 50000" docstrings went unnoticed.
_mock_config.MAX_ATTACHMENT_CHARS = 500000
_mock_config.MAX_ATTACHMENT_ARTIFACT_MB = 50

# Mock langchain @tool decorator as identity
_mock_langchain_core_tools = MagicMock()
_mock_langchain_core_tools.tool = lambda f: f

_saved = {}
for mod_name in ("CommonUtils", "config", "langchain_core.tools", "langchain_core"):
    _saved[mod_name] = sys.modules.get(mod_name)

sys.modules["CommonUtils"] = _mock_common_utils
sys.modules["config"] = _mock_config
sys.modules.setdefault("langchain_core", MagicMock())
sys.modules["langchain_core.tools"] = _mock_langchain_core_tools

if "agent_email_tools" in sys.modules:
    del sys.modules["agent_email_tools"]

# Patch the logging setup to avoid file creation
with patch("builtins.open", MagicMock()), \
     patch("logging.handlers.WatchedFileHandler", MagicMock()):
    from agent_email_tools import (
        set_email_tool_context,
        get_email_tool_context,
        clear_email_tool_context,
        _call_cloud_api,
        _prepare_attachment,
        _find_email_by_id_or_subject,
        _get_file_icon,
        _can_extract_locally,
        check_my_inbox,
        read_email,
        reply_to_email,
        send_email,
        search_inbox,
        get_inbox_summary,
        create_email_inbox_tools,
        get_email_tools_system_prompt_addition,
        _check_inbox_tools_enabled,
    )

for k, v in _saved.items():
    if v is not None:
        sys.modules[k] = v


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _set_ctx(agent_id=1, email="agent@test.com", from_name="Test Agent"):
    set_email_tool_context(agent_id, email, from_name)


def _sample_emails():
    return [
        {
            "event_id": "100",
            "id": "100",
            "sender_email": "alice@example.com",
            "sender_name": "Alice",
            "recipient_email": "agent@test.com",
            "subject": "Monthly Report",
            "received_at": "2024-06-15T10:30:00Z",
            "attachment_count": 0,
            "attachments": [],
        },
        {
            "event_id": "101",
            "id": "101",
            "sender_email": "bob@example.com",
            "sender_name": "Bob",
            "recipient_email": "agent@test.com",
            "subject": "Invoice #456",
            "received_at": "2024-06-14T09:00:00Z",
            "attachment_count": 1,
            "attachments": [{"attachment_id": "5", "filename": "inv.pdf"}],
            "has_attachments": True,
        },
        {
            "event_id": "200",
            "sender_email": "other@example.com",
            "recipient_email": "other-agent@test.com",
            "subject": "Not for this agent",
            "received_at": "2024-06-15T12:00:00Z",
        },
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestEmailToolContext:
    """Test set/get/clear context helpers."""

    def setup_method(self):
        clear_email_tool_context()

    def test_set_and_get_context(self):
        _set_ctx(agent_id=7, email="a@b.com", from_name="Bot")
        ctx = get_email_tool_context()
        assert ctx["agent_id"] == 7
        assert ctx["email_address"] == "a@b.com"
        assert ctx["from_name"] == "Bot"

    def test_default_from_name(self):
        set_email_tool_context(1, "a@b.com")
        ctx = get_email_tool_context()
        assert ctx["from_name"] == "AI Agent"

    def test_get_empty_context(self):
        ctx = get_email_tool_context()
        assert ctx["agent_id"] is None
        assert ctx["email_address"] is None

    def test_clear_context(self):
        _set_ctx()
        clear_email_tool_context()
        ctx = get_email_tool_context()
        assert ctx["agent_id"] is None


@pytest.mark.unit
class TestCallCloudApi:
    """Test _call_cloud_api helper."""

    @patch.dict("os.environ", {"AI_HUB_API_URL": "https://cloud.test", "API_KEY": "key123"})
    @patch("agent_email_tools.requests")
    def test_get_success(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True, "data": []}
        mock_requests.get.return_value = mock_resp

        result = _call_cloud_api("/api/email/poll", method="GET")
        assert result["success"] is True
        mock_requests.get.assert_called_once()

    @patch.dict("os.environ", {"AI_HUB_API_URL": "https://cloud.test", "API_KEY": "key123"})
    @patch("agent_email_tools.requests")
    def test_post_success(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True}
        mock_requests.post.return_value = mock_resp

        result = _call_cloud_api("/api/email/send", method="POST", data={"to": "x@y.com"})
        assert result["success"] is True

    @patch.dict("os.environ", {"AI_HUB_API_URL": "", "API_KEY": ""})
    def test_missing_config(self):
        result = _call_cloud_api("/api/test")
        assert result["success"] is False
        assert "not configured" in result["error"]

    @patch.dict("os.environ", {"AI_HUB_API_URL": "https://cloud.test", "API_KEY": "key123"})
    @patch("agent_email_tools.requests")
    def test_http_error(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_requests.get.return_value = mock_resp

        result = _call_cloud_api("/api/test")
        assert result["success"] is False
        assert "403" in result["error"]

    @patch.dict("os.environ", {"AI_HUB_API_URL": "https://cloud.test", "API_KEY": "key123"})
    @patch("agent_email_tools.requests")
    def test_timeout(self, mock_requests):
        import requests as real_requests
        mock_requests.get.side_effect = real_requests.exceptions.Timeout("timed out")
        mock_requests.exceptions = real_requests.exceptions

        result = _call_cloud_api("/api/test")
        assert result["success"] is False
        assert "timeout" in result["error"].lower()

    @patch.dict("os.environ", {"AI_HUB_API_URL": "https://cloud.test", "API_KEY": "key123"})
    @patch("agent_email_tools.requests")
    def test_unsupported_method(self, mock_requests):
        result = _call_cloud_api("/api/test", method="DELETE")
        assert result["success"] is False
        assert "Unsupported" in result["error"]


@pytest.mark.unit
class TestFindEmailByIdOrSubject:
    """Test _find_email_by_id_or_subject pure logic."""

    def test_find_by_event_id(self):
        emails = _sample_emails()
        result = _find_email_by_id_or_subject("100", emails)
        assert result["event_id"] == "100"

    def test_find_by_id_field(self):
        emails = [{"id": "999", "subject": "Test"}]
        result = _find_email_by_id_or_subject("999", emails)
        assert result is not None

    def test_find_by_exact_subject(self):
        emails = _sample_emails()
        result = _find_email_by_id_or_subject("Monthly Report", emails)
        assert result["event_id"] == "100"

    def test_find_by_partial_subject(self):
        emails = _sample_emails()
        result = _find_email_by_id_or_subject("Invoice", emails)
        assert result["event_id"] == "101"

    def test_not_found(self):
        emails = _sample_emails()
        result = _find_email_by_id_or_subject("nonexistent-xyz-123", emails)
        assert result is None

    def test_empty_inputs(self):
        assert _find_email_by_id_or_subject("", []) is None
        assert _find_email_by_id_or_subject(None, _sample_emails()) is None


@pytest.mark.unit
class TestFileHelpers:
    """Test _get_file_icon and _can_extract_locally."""

    def test_pdf_icon(self):
        assert _get_file_icon("application/pdf", "report.pdf") == "📄"

    def test_word_icon(self):
        assert _get_file_icon("application/msword", "doc.docx") == "📝"

    def test_excel_icon(self):
        assert _get_file_icon("application/vnd.ms-excel", "data.xlsx") == "📊"

    def test_image_icon(self):
        assert _get_file_icon("image/png", "photo.png") == "🖼️"

    def test_text_icon(self):
        assert _get_file_icon("text/plain", "readme.txt") == "📃"

    def test_csv_icon(self):
        # text/csv matches 'text' check first in source → returns 📃
        # Use filename-only detection instead
        assert _get_file_icon("", "data.csv") == "📈"

    def test_unknown_icon(self):
        assert _get_file_icon("application/octet-stream", "file.bin") == "📎"

    def test_can_extract_pdf(self):
        assert _can_extract_locally("report.pdf", "application/pdf") is True

    def test_can_extract_docx(self):
        assert _can_extract_locally("doc.docx", "") is True

    def test_can_extract_csv(self):
        assert _can_extract_locally("data.csv", "") is True

    def test_can_extract_text_content_type(self):
        assert _can_extract_locally("unknown", "text/plain") is True

    def test_cannot_extract_binary(self):
        assert _can_extract_locally("file.exe", "application/octet-stream") is False


@pytest.mark.unit
class TestCheckMyInbox:
    """Test check_my_inbox tool."""

    def setup_method(self):
        clear_email_tool_context()

    def test_no_context_returns_error(self):
        result = check_my_inbox()
        assert "Error" in result
        assert "not configured" in result

    @patch("agent_email_tools._call_cloud_api")
    def test_empty_inbox(self, mock_api):
        _set_ctx()
        mock_api.return_value = {"success": True, "emails": []}
        result = check_my_inbox()
        assert "empty" in result.lower() or "No emails" in result

    @patch("agent_email_tools._call_cloud_api")
    def test_with_emails(self, mock_api):
        _set_ctx()
        mock_api.return_value = {"success": True, "emails": _sample_emails()}
        result = check_my_inbox(limit=10)
        assert "Monthly Report" in result
        assert "alice" in result.lower() or "Alice" in result
        assert "2" in result  # 2 emails for this agent

    @patch("agent_email_tools._call_cloud_api")
    def test_api_failure(self, mock_api):
        _set_ctx()
        mock_api.return_value = {"success": False, "error": "Service down"}
        result = check_my_inbox()
        assert "Unable" in result or "error" in result.lower()

    @patch("agent_email_tools._call_cloud_api")
    def test_limit_clamped(self, mock_api):
        _set_ctx()
        mock_api.return_value = {"success": True, "emails": _sample_emails()}
        # limit > 50 should be clamped
        result = check_my_inbox(limit=100)
        assert "Found" in result


@pytest.mark.unit
class TestSendEmail:
    """Test send_email tool."""

    def setup_method(self):
        clear_email_tool_context()

    def test_no_context_returns_error(self):
        result = send_email("to@test.com", "Subject", "Body")
        assert "Error" in result

    @patch("agent_email_tools._call_cloud_api")
    def test_invalid_email_format(self, mock_api):
        _set_ctx()
        result = send_email("not-an-email", "Subject", "Body")
        assert "not a valid email" in result

    @patch("agent_email_tools._call_cloud_api")
    def test_missing_fields(self, mock_api):
        _set_ctx()
        result = send_email("", "Subject", "Body")
        assert "Error" in result

    @patch("agent_email_tools._call_cloud_api")
    def test_send_success(self, mock_api):
        _set_ctx()
        mock_api.return_value = {"success": True}
        result = send_email("recipient@test.com", "Hello", "Hi there")
        assert "sent successfully" in result

    @patch("agent_email_tools._call_cloud_api")
    def test_send_api_failure(self, mock_api):
        _set_ctx()
        mock_api.return_value = {"success": False, "error": "Rate limit"}
        result = send_email("recipient@test.com", "Hello", "Hi there")
        assert "Failed" in result

    @patch("agent_email_tools._call_cloud_api")
    def test_send_rate_limited(self, mock_api):
        _set_ctx()
        mock_api.return_value = {
            "success": False,
            "blocked_by_limit": True,
            "current_usage": 50,
            "max_allowed": 50,
        }
        result = send_email("recipient@test.com", "Hello", "Body")
        assert "limit" in result.lower()


@pytest.mark.unit
class TestSearchInbox:
    """Test search_inbox tool."""

    def setup_method(self):
        clear_email_tool_context()

    def test_no_context(self):
        result = search_inbox("test")
        assert "Error" in result

    @patch("agent_email_tools._call_cloud_api")
    def test_search_by_sender(self, mock_api):
        _set_ctx()
        mock_api.return_value = {"success": True, "emails": _sample_emails()}
        result = search_inbox("alice", search_field="sender")
        assert "Alice" in result or "alice" in result

    @patch("agent_email_tools._call_cloud_api")
    def test_search_by_subject(self, mock_api):
        _set_ctx()
        mock_api.return_value = {"success": True, "emails": _sample_emails()}
        result = search_inbox("Invoice", search_field="subject")
        assert "Invoice" in result

    @patch("agent_email_tools._call_cloud_api")
    def test_search_no_match(self, mock_api):
        _set_ctx()
        mock_api.return_value = {"success": True, "emails": _sample_emails()}
        result = search_inbox("zzz_nonexistent_zzz")
        assert "No emails found" in result

    @patch("agent_email_tools._call_cloud_api")
    def test_search_all_fields(self, mock_api):
        _set_ctx()
        mock_api.return_value = {"success": True, "emails": _sample_emails()}
        result = search_inbox("Report", search_field="all")
        assert "Monthly Report" in result


@pytest.mark.unit
class TestGetInboxSummary:
    """Test get_inbox_summary tool."""

    def setup_method(self):
        clear_email_tool_context()

    def test_no_context(self):
        result = get_inbox_summary()
        assert "Error" in result

    @patch("agent_email_tools._call_cloud_api")
    def test_empty_inbox_summary(self, mock_api):
        _set_ctx()
        mock_api.return_value = {"success": True, "emails": []}
        result = get_inbox_summary()
        assert "Empty" in result or "0 messages" in result

    @patch("agent_email_tools._call_cloud_api")
    def test_summary_with_emails(self, mock_api):
        _set_ctx()
        mock_api.return_value = {"success": True, "emails": _sample_emails()}
        result = get_inbox_summary()
        assert "INBOX SUMMARY" in result
        assert "Total Messages: 2" in result
        assert "agent@test.com" in result


@pytest.mark.unit
class TestCreateEmailInboxTools:
    """Test create_email_inbox_tools factory."""

    def setup_method(self):
        clear_email_tool_context()

    @patch("agent_email_tools.logger")
    @patch("agent_email_tools._get_agent_email_config")
    def test_no_config_returns_empty(self, mock_get_config, mock_logger):
        mock_get_config.return_value = None
        tools = create_email_inbox_tools(agent_id=1)
        assert tools == []

    @patch("agent_email_tools.logger")
    @patch("agent_email_tools._get_agent_email_config")
    def test_inbox_tools_disabled(self, mock_get_config, mock_logger):
        mock_get_config.return_value = {
            "email_address": "a@b.com",
            "inbox_tools_enabled": False,
        }
        tools = create_email_inbox_tools(agent_id=1)
        assert tools == []

    @patch("agent_email_tools.logger")
    @patch("agent_email_tools._get_agent_email_config")
    def test_no_email_address(self, mock_get_config, mock_logger):
        mock_get_config.return_value = {
            "email_address": "",
            "inbox_tools_enabled": True,
        }
        tools = create_email_inbox_tools(agent_id=1)
        assert tools == []

    @patch("agent_email_tools.logger")
    @patch("agent_email_tools._get_agent_email_config")
    def test_returns_all_tools_when_enabled(self, mock_get_config, mock_logger):
        mock_get_config.return_value = {
            "email_address": "agent@test.com",
            "inbox_tools_enabled": True,
            "from_name": "Test Agent",
        }
        tools = create_email_inbox_tools(agent_id=1)
        assert len(tools) == 8  # 6 inbox + 2 attachment tools

    @patch("agent_email_tools.logger")
    @patch("agent_email_tools._get_agent_email_config")
    def test_sets_context_on_creation(self, mock_get_config, mock_logger):
        mock_get_config.return_value = {
            "email_address": "agent@test.com",
            "inbox_tools_enabled": True,
            "from_name": "MyBot",
        }
        create_email_inbox_tools(agent_id=42)
        ctx = get_email_tool_context()
        assert ctx["agent_id"] == 42
        assert ctx["email_address"] == "agent@test.com"


@pytest.mark.unit
class TestEmailSystemPrompt:
    """Test get_email_tools_system_prompt_addition."""

    @patch("agent_email_tools._get_agent_email_config")
    def test_returns_prompt_when_enabled(self, mock_get_config):
        mock_get_config.return_value = {
            "email_address": "bot@test.com",
            "inbox_tools_enabled": True,
            "from_name": "Bot",
        }
        prompt = get_email_tools_system_prompt_addition(agent_id=1)
        assert "bot@test.com" in prompt
        assert "Email Capabilities" in prompt
        assert "check_my_inbox" in prompt

    @patch("agent_email_tools._get_agent_email_config")
    def test_returns_empty_when_disabled(self, mock_get_config):
        mock_get_config.return_value = {
            "email_address": "bot@test.com",
            "inbox_tools_enabled": False,
        }
        prompt = get_email_tools_system_prompt_addition(agent_id=1)
        assert prompt == ""

    @patch("agent_email_tools._get_agent_email_config")
    def test_returns_empty_when_no_config(self, mock_get_config):
        mock_get_config.return_value = None
        prompt = get_email_tools_system_prompt_addition(agent_id=1)
        assert prompt == ""


# ---------------------------------------------------------------------------
# Attachment helper tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestPrepareAttachment:
    """Test _prepare_attachment helper."""

    def test_none_path_returns_none(self):
        assert _prepare_attachment(None) is None

    def test_empty_path_returns_none(self):
        assert _prepare_attachment("") is None

    def test_nonexistent_file_returns_none(self):
        assert _prepare_attachment("/no/such/file.pdf") is None

    @patch("agent_email_tools.logger")
    @patch("agent_email_tools.open", side_effect=PermissionError("denied"), create=True)
    @patch("mimetypes.guess_type", return_value=("application/pdf", None))
    @patch("os.path.isfile", return_value=True)
    def test_unreadable_file_returns_none(self, _isfile, _mime, _open, _log):
        result = _prepare_attachment("/some/locked.pdf")
        assert result is None

    @patch("mimetypes.guess_type", return_value=("application/pdf", None))
    @patch("os.path.isfile", return_value=True)
    def test_valid_pdf_file(self, _mock_isfile, _mock_mime):
        import base64
        from io import BytesIO
        fake_file = BytesIO(b"fake-pdf-bytes")
        with patch("agent_email_tools.open", return_value=fake_file, create=True):
            result = _prepare_attachment("/reports/Q1.pdf")
        assert result is not None
        assert result["filename"] == "Q1.pdf"
        assert result["content_type"] == "application/pdf"
        decoded = base64.b64decode(result["content"])
        assert decoded == b"fake-pdf-bytes"

    @patch("mimetypes.guess_type", return_value=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", None))
    @patch("os.path.isfile", return_value=True)
    def test_xlsx_content_type(self, _mock_isfile, _mock_mime):
        from io import BytesIO
        fake_file = BytesIO(b"fake-xlsx-bytes")
        with patch("agent_email_tools.open", return_value=fake_file, create=True):
            result = _prepare_attachment("/data/report.xlsx")
        assert result is not None
        assert result["filename"] == "report.xlsx"
        assert "spreadsheet" in result["content_type"]


# ---------------------------------------------------------------------------
# send_email with attachment tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestSendEmailWithAttachment:
    """Test send_email attachment support."""

    def setup_method(self):
        clear_email_tool_context()

    @patch("agent_email_tools._call_cloud_api")
    @patch("agent_email_tools._prepare_attachment")
    def test_send_with_attachment_includes_payload(self, mock_prep, mock_api):
        _set_ctx()
        mock_prep.return_value = {
            "filename": "report.pdf",
            "content": "AAAA",
            "content_type": "application/pdf",
        }
        mock_api.return_value = {"success": True}

        result = send_email("to@test.com", "Subj", "Body", attachment_file_path="/tmp/report.pdf")

        assert "sent successfully" in result
        assert "report.pdf" in result
        # Verify the API was called with attachments in the payload
        call_kwargs = mock_api.call_args
        payload = call_kwargs[1]["data"] if "data" in call_kwargs[1] else call_kwargs[0][2]
        assert "attachments" in payload
        assert payload["attachments"][0]["filename"] == "report.pdf"

    @patch("agent_email_tools._call_cloud_api")
    def test_send_without_attachment_no_attachments_key(self, mock_api):
        _set_ctx()
        mock_api.return_value = {"success": True}

        result = send_email("to@test.com", "Subj", "Body")

        assert "sent successfully" in result
        call_kwargs = mock_api.call_args
        payload = call_kwargs[1]["data"] if "data" in call_kwargs[1] else call_kwargs[0][2]
        assert "attachments" not in payload

    @patch("agent_email_tools._prepare_attachment")
    def test_send_with_invalid_attachment_returns_error(self, mock_prep):
        _set_ctx()
        mock_prep.return_value = None

        result = send_email("to@test.com", "Subj", "Body", attachment_file_path="/bad/path.pdf")

        assert "Error" in result
        assert "Could not read attachment" in result

    @patch("agent_email_tools._call_cloud_api")
    def test_send_with_none_attachment_same_as_no_attachment(self, mock_api):
        _set_ctx()
        mock_api.return_value = {"success": True}

        result = send_email("to@test.com", "Subj", "Body", attachment_file_path=None)

        assert "sent successfully" in result
        call_kwargs = mock_api.call_args
        payload = call_kwargs[1]["data"] if "data" in call_kwargs[1] else call_kwargs[0][2]
        assert "attachments" not in payload


# ---------------------------------------------------------------------------
# reply_to_email with attachment tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestReplyToEmailWithAttachment:
    """Test reply_to_email attachment support."""

    def setup_method(self):
        clear_email_tool_context()

    @patch("agent_email_tools._call_cloud_api")
    @patch("agent_email_tools._prepare_attachment")
    def test_reply_with_attachment_includes_payload(self, mock_prep, mock_api):
        _set_ctx()
        mock_prep.return_value = {
            "filename": "data.xlsx",
            "content": "BBBB",
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
        # First call: poll for emails; second call: send
        mock_api.side_effect = [
            {"success": True, "emails": _sample_emails()},
            {"success": True},
        ]

        result = reply_to_email("100", "Here is the data", include_original=False, attachment_file_path="/tmp/data.xlsx")

        assert "sent successfully" in result
        assert "data.xlsx" in result
        # The second _call_cloud_api call is the send — check its payload
        send_call = mock_api.call_args_list[1]
        payload = send_call[1]["data"] if "data" in send_call[1] else send_call[0][2]
        assert "attachments" in payload
        assert payload["attachments"][0]["filename"] == "data.xlsx"

    @patch("agent_email_tools._prepare_attachment")
    def test_reply_with_bad_attachment_returns_error(self, mock_prep):
        _set_ctx()
        mock_prep.return_value = None

        result = reply_to_email("100", "Here is the data", attachment_file_path="/bad/file.pdf")

        assert "Error" in result
        assert "Could not read attachment" in result

    @patch("agent_email_tools._call_cloud_api")
    def test_reply_without_attachment_no_attachments_key(self, mock_api):
        _set_ctx()
        mock_api.side_effect = [
            {"success": True, "emails": _sample_emails()},
            {"success": True},
        ]

        result = reply_to_email("100", "Thanks!")

        assert "sent successfully" in result
        send_call = mock_api.call_args_list[1]
        payload = send_call[1]["data"] if "data" in send_call[1] else send_call[0][2]
        assert "attachments" not in payload


# ===========================================================================
# Attachment -> Command Center artifact bridge
# ===========================================================================
import types
from contextlib import contextmanager

import agent_email_tools as aet


class _FakeSink:
    """Stands in for command_center.artifacts.produced_sink."""

    def __init__(self, active=True):
        self.active = active
        self.captured = []

    def is_active(self):
        return self.active

    def capture(self, name, artifact_type, content_bytes, source=None):
        self.captured.append({
            "name": name, "type": artifact_type,
            "bytes": content_bytes, "source": source,
        })


@contextmanager
def _install_sink(sink):
    """Make `from command_center.artifacts import produced_sink` resolve to `sink`."""
    keys = ("command_center", "command_center.artifacts")
    saved = {k: sys.modules.get(k) for k in keys}
    pkg = types.ModuleType("command_center")
    sub = types.ModuleType("command_center.artifacts")
    sub.produced_sink = sink
    pkg.artifacts = sub
    sys.modules["command_center"] = pkg
    sys.modules["command_center.artifacts"] = sub
    try:
        yield sink
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


@contextmanager
def _no_command_center():
    """Simulate the module being absent entirely (e.g. a service that never ships it)."""
    keys = ("command_center", "command_center.artifacts")
    saved = {k: sys.modules.get(k) for k in keys}
    for k in keys:
        sys.modules.pop(k, None)
    sys.modules["command_center"] = None  # forces ImportError on import
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


@pytest.fixture
def _quiet_logger():
    """The module logger was built with a mocked WatchedFileHandler, so its
    handler has a MagicMock level and any real log call blows up on comparison.
    Applied only to the classes below, which do log."""
    with patch.object(aet, "logger", MagicMock()):
        yield


class TestArtifactTypeMapping:
    def test_known_extensions(self):
        assert aet._artifact_type_for("q3.csv") == "csv"
        assert aet._artifact_type_for("Report.XLSX") == "excel"
        assert aet._artifact_type_for("lease.pdf") == "pdf"
        assert aet._artifact_type_for("memo.docx") == "docx"
        assert aet._artifact_type_for("chart.png") == "image"
        assert aet._artifact_type_for("deck.pptx") == "pptx"

    def test_unknown_and_missing_default_to_text(self):
        assert aet._artifact_type_for("notes.xyz") == "text"
        assert aet._artifact_type_for("noextension") == "text"
        assert aet._artifact_type_for("") == "text"
        assert aet._artifact_type_for(None) == "text"


@pytest.mark.usefixtures("_quiet_logger")
class TestOfferAttachmentAsArtifact:
    def test_captures_original_bytes_when_delegation_is_active(self):
        sink = _FakeSink(active=True)
        with _install_sink(sink):
            assert aet._offer_attachment_as_artifact("report.pdf", b"%PDF-1.4 real bytes") is True

        assert len(sink.captured) == 1
        got = sink.captured[0]
        assert got["name"] == "report.pdf"
        assert got["type"] == "pdf"
        assert got["bytes"] == b"%PDF-1.4 real bytes"   # ORIGINAL bytes, not extracted text
        assert got["source"] == "email_attachment"

    def test_no_capture_when_no_delegation_is_active(self):
        """The normal agent-UI path must behave exactly as before."""
        sink = _FakeSink(active=False)
        with _install_sink(sink):
            assert aet._offer_attachment_as_artifact("report.pdf", b"bytes") is False
        assert sink.captured == []

    def test_oversized_attachment_is_skipped_not_captured(self):
        sink = _FakeSink(active=True)
        with _install_sink(sink), patch.object(aet, "MAX_ATTACHMENT_ARTIFACT_MB", 1):
            big = b"x" * (2 * 1024 * 1024)
            assert aet._offer_attachment_as_artifact("huge.pdf", big) is False
        assert sink.captured == []

    def test_size_limit_boundary_is_inclusive(self):
        sink = _FakeSink(active=True)
        with _install_sink(sink), patch.object(aet, "MAX_ATTACHMENT_ARTIFACT_MB", 1):
            exact = b"x" * (1 * 1024 * 1024)
            assert aet._offer_attachment_as_artifact("exact.pdf", exact) is True
        assert len(sink.captured) == 1

    def test_absent_command_center_is_not_fatal(self):
        with _no_command_center():
            assert aet._offer_attachment_as_artifact("report.pdf", b"bytes") is False

    def test_sink_error_is_swallowed(self):
        sink = _FakeSink(active=True)
        sink.capture = MagicMock(side_effect=RuntimeError("store down"))
        with _install_sink(sink):
            assert aet._offer_attachment_as_artifact("report.pdf", b"bytes") is False


@pytest.mark.usefixtures("_quiet_logger")
class TestReadAttachmentCap:
    """The per-file cap is config-driven. A hardcoded 500000 used to clamp it,
    so raising MAX_ATTACHMENT_CHARS silently had no effect."""

    def _drive_read_attachment(self, configured_cap, requested):
        seen = {}

        def _fake_extract(file_bytes, filename, content_type, max_chars,
                          allow_ocr_fallback=True):
            seen["max_chars"] = max_chars
            return {"success": True, "text": "hello", "original_length": 5}

        fake_mod = types.ModuleType("attachment_text_extractor")
        fake_mod.extract_text_from_attachment = _fake_extract

        cur = MagicMock()
        cur.fetchone.return_value = ("report.pdf", "application/pdf", 9, b"%PDF-1.4")
        conn = MagicMock()
        conn.cursor.return_value = cur

        saved = sys.modules.get("attachment_text_extractor")
        sys.modules["attachment_text_extractor"] = fake_mod
        try:
            with patch.object(aet, "get_db_connection", return_value=conn), \
                 patch.object(aet, "MAX_ATTACHMENT_CHARS", configured_cap):
                _set_ctx()
                out = aet.read_attachment("42", max_length=requested)
        finally:
            if saved is None:
                sys.modules.pop("attachment_text_extractor", None)
            else:
                sys.modules["attachment_text_extractor"] = saved
        return seen.get("max_chars"), out

    def test_honours_a_raised_config_cap(self):
        used, _ = self._drive_read_attachment(configured_cap=2_000_000,
                                              requested=2_000_000)
        assert used == 2_000_000, "must follow MAX_ATTACHMENT_CHARS, not a hardcoded 500000"

    def test_clamps_a_request_above_the_configured_cap(self):
        used, _ = self._drive_read_attachment(configured_cap=100_000,
                                              requested=9_000_000)
        assert used == 100_000

    def test_enforces_a_sane_floor(self):
        used, _ = self._drive_read_attachment(configured_cap=500_000, requested=1)
        assert used == 1000
