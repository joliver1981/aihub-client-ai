"""
Unit tests for EmailUtils.py - email sending utilities.

Tests cover SMTP and Azure email dispatch, error handling, recipient
normalization, and the send_email router. All network I/O is mocked.
"""

import pytest
import sys
from unittest.mock import patch, MagicMock, call


# ---------------------------------------------------------------------------
# Mock heavy dependencies before importing EmailUtils
# ---------------------------------------------------------------------------

# Ensure config mock is in place
if "config" not in sys.modules or not hasattr(sys.modules.get("config"), "SMTP_HOST"):
    _mock_cfg = MagicMock()
    _mock_cfg.SMTP_HOST = "smtp.test.com"
    _mock_cfg.SMTP_PORT = 587
    _mock_cfg.SMTP_USER = "testuser"
    _mock_cfg.SMTP_PASSWORD = "testpass"
    _mock_cfg.SMTP_USE_TLS = True
    _mock_cfg.SMTP_FROM = "sender@test.com"
    _mock_cfg.EMAIL_PROVIDER = "smtp"
    _mock_cfg.API_AZURE_EMAIL_CONN_STR = "mock_conn_str"
    _mock_cfg.API_AZURE_EMAIL_SENDER = "azure@test.com"
    sys.modules["config"] = _mock_cfg
else:
    _mock_cfg = sys.modules["config"]
    # Ensure the attributes exist on the already-loaded mock/module
    if not hasattr(_mock_cfg, "SMTP_HOST"):
        _mock_cfg.SMTP_HOST = "smtp.test.com"
        _mock_cfg.SMTP_PORT = 587
        _mock_cfg.SMTP_USER = "testuser"
        _mock_cfg.SMTP_PASSWORD = "testpass"
        _mock_cfg.SMTP_USE_TLS = True
        _mock_cfg.SMTP_FROM = "sender@test.com"
        _mock_cfg.EMAIL_PROVIDER = "smtp"
        _mock_cfg.API_AZURE_EMAIL_CONN_STR = "mock_conn_str"
        _mock_cfg.API_AZURE_EMAIL_SENDER = "azure@test.com"

# Mock azure.communication.email before importing EmailUtils
sys.modules.setdefault("azure", MagicMock())
sys.modules.setdefault("azure.communication", MagicMock())
sys.modules.setdefault("azure.communication.email", MagicMock())
sys.modules.setdefault("azure.core", MagicMock())
sys.modules.setdefault("azure.core.exceptions", MagicMock())

import EmailUtils


# =============================================================================
# SMTP Email Tests
# =============================================================================


@pytest.mark.unit
class TestSendEmailSmtp:
    """Tests for send_email_smtp()."""

    @patch("EmailUtils.smtplib.SMTP")
    def test_sends_email_successfully(self, mock_smtp_class):
        """send_email_smtp should return True when email sends without error."""
        mock_server = MagicMock()
        mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

        result = EmailUtils.send_email_smtp(
            recipients="user@example.com",
            subject="Test Subject",
            body="Test body",
            smtp_host="smtp.test.com",
            smtp_port=587,
            smtp_user="user",
            smtp_password="pass",
            smtp_use_tls=True,
            smtp_from="sender@test.com",
        )
        assert result is True

    @patch("EmailUtils.smtplib.SMTP")
    def test_calls_starttls_when_tls_enabled(self, mock_smtp_class):
        """When smtp_use_tls is True, starttls() should be called."""
        mock_server = MagicMock()
        mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

        EmailUtils.send_email_smtp(
            recipients="user@example.com",
            subject="Test",
            body="Body",
            smtp_host="smtp.test.com",
            smtp_port=587,
            smtp_user="user",
            smtp_password="pass",
            smtp_use_tls=True,
            smtp_from="sender@test.com",
        )
        mock_server.starttls.assert_called_once()

    @patch("EmailUtils.smtplib.SMTP")
    def test_calls_login_when_credentials_provided(self, mock_smtp_class):
        """When smtp_user and smtp_password are set, login() should be called."""
        mock_server = MagicMock()
        mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

        EmailUtils.send_email_smtp(
            recipients="user@example.com",
            subject="Test",
            body="Body",
            smtp_host="smtp.test.com",
            smtp_port=587,
            smtp_user="testuser",
            smtp_password="testpass",
            smtp_use_tls=False,
            smtp_from="sender@test.com",
        )
        mock_server.login.assert_called_once_with("testuser", "testpass")

    @patch("EmailUtils.smtplib.SMTP")
    def test_handles_connection_error_gracefully(self, mock_smtp_class):
        """SMTP connection failure should return False, not raise."""
        mock_smtp_class.side_effect = ConnectionRefusedError("Connection refused")

        result = EmailUtils.send_email_smtp(
            recipients="user@example.com",
            subject="Test",
            body="Body",
            smtp_host="smtp.test.com",
            smtp_port=587,
            smtp_user="user",
            smtp_password="pass",
            smtp_use_tls=True,
            smtp_from="sender@test.com",
        )
        assert result is False

    @patch("EmailUtils.smtplib.SMTP")
    def test_handles_auth_error_gracefully(self, mock_smtp_class):
        """SMTP authentication failure should return False."""
        mock_server = MagicMock()
        mock_server.login.side_effect = Exception("Authentication failed")
        mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

        result = EmailUtils.send_email_smtp(
            recipients="user@example.com",
            subject="Test",
            body="Body",
            smtp_host="smtp.test.com",
            smtp_port=587,
            smtp_user="baduser",
            smtp_password="badpass",
            smtp_use_tls=True,
            smtp_from="sender@test.com",
        )
        assert result is False

    @patch("EmailUtils.smtplib.SMTP")
    def test_list_recipients_joined_in_to_header(self, mock_smtp_class):
        """Multiple recipients should all be included."""
        mock_server = MagicMock()
        mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

        result = EmailUtils.send_email_smtp(
            recipients=["a@test.com", "b@test.com"],
            subject="Test",
            body="Body",
            smtp_host="smtp.test.com",
            smtp_port=587,
            smtp_user="user",
            smtp_password="pass",
            smtp_use_tls=False,
            smtp_from="sender@test.com",
        )
        assert result is True
        # send_message was called
        mock_server.send_message.assert_called_once()


# =============================================================================
# send_email Router Tests
# =============================================================================


@pytest.mark.unit
class TestSendEmail:
    """Tests for the send_email() dispatcher function."""

    @patch("EmailUtils.send_email_smtp", return_value=True)
    @patch("EmailUtils.send_email_azure", return_value=True)
    def test_dispatches_to_smtp_when_configured(self, mock_azure, mock_smtp):
        """When EMAIL_PROVIDER is 'smtp', send_email should call send_email_smtp."""
        with patch.object(sys.modules["config"], "EMAIL_PROVIDER", "smtp"):
            result = EmailUtils.send_email(
                recipients="user@example.com",
                subject="Test",
                body="Body",
            )
        assert result is True
        mock_smtp.assert_called_once()
        mock_azure.assert_not_called()

    @patch("EmailUtils.send_email_smtp", return_value=True)
    @patch("EmailUtils.send_email_azure", return_value=True)
    def test_dispatches_to_azure_when_configured(self, mock_azure, mock_smtp):
        """When EMAIL_PROVIDER is 'azure', send_email should call send_email_azure."""
        with patch.object(sys.modules["config"], "EMAIL_PROVIDER", "azure"):
            result = EmailUtils.send_email(
                recipients="user@example.com",
                subject="Test",
                body="Body",
            )
        assert result is True
        mock_azure.assert_called_once()
        mock_smtp.assert_not_called()

    @patch("EmailUtils.send_email_smtp", return_value=True)
    @patch("EmailUtils.send_email_azure", return_value=True)
    def test_string_recipient_passed_through(self, mock_azure, mock_smtp):
        """A single string recipient should be passed through correctly."""
        with patch.object(sys.modules["config"], "EMAIL_PROVIDER", "smtp"):
            EmailUtils.send_email(
                recipients="solo@example.com",
                subject="Test",
                body="Body",
            )
        # Verify the recipients argument
        call_kwargs = mock_smtp.call_args
        assert call_kwargs[1]["recipients"] == "solo@example.com" or call_kwargs[0][0] == "solo@example.com"

    @patch("EmailUtils.send_email_smtp", return_value=True)
    @patch("EmailUtils.send_email_azure", return_value=True)
    def test_list_recipients_passed_through(self, mock_azure, mock_smtp):
        """A list of recipients should be passed through correctly."""
        recipients_list = ["a@test.com", "b@test.com", "c@test.com"]
        with patch.object(sys.modules["config"], "EMAIL_PROVIDER", "smtp"):
            EmailUtils.send_email(
                recipients=recipients_list,
                subject="Test",
                body="Body",
            )
        call_kwargs = mock_smtp.call_args
        actual_recipients = call_kwargs[1].get("recipients") or call_kwargs[0][0]
        assert actual_recipients == recipients_list

    @patch("EmailUtils.send_email_smtp", side_effect=Exception("Unexpected error"))
    def test_exception_in_dispatch_returns_false(self, mock_smtp):
        """If the dispatched function raises, send_email should return False."""
        with patch.object(sys.modules["config"], "EMAIL_PROVIDER", "smtp"):
            result = EmailUtils.send_email(
                recipients="user@example.com",
                subject="Test",
                body="Body",
            )
        assert result is False


# =============================================================================
# Azure Email Tests
# =============================================================================


@pytest.mark.unit
class TestSendEmailAzure:
    """Tests for send_email_azure()."""

    @patch("EmailUtils.EmailClient")
    def test_sends_azure_email_successfully(self, mock_email_client_class):
        """send_email_azure should return True when Azure send succeeds."""
        mock_client = MagicMock()
        mock_poller = MagicMock()
        mock_poller.result.return_value = MagicMock()
        mock_client.begin_send.return_value = mock_poller
        mock_email_client_class.from_connection_string.return_value = mock_client

        result = EmailUtils.send_email_azure(
            recipients="user@example.com",
            subject="Test Subject",
            body="Test body",
        )
        assert result is True
        mock_client.begin_send.assert_called_once()

    @patch("EmailUtils.EmailClient")
    def test_azure_email_failure_returns_false(self, mock_email_client_class):
        """If Azure email sending raises, should return False."""
        mock_email_client_class.from_connection_string.side_effect = Exception("Azure error")

        result = EmailUtils.send_email_azure(
            recipients="user@example.com",
            subject="Test",
            body="Body",
        )
        assert result is False

    @patch("EmailUtils.EmailClient")
    def test_azure_string_recipient_converted_to_list(self, mock_email_client_class):
        """A single string recipient should be converted to a list internally."""
        mock_client = MagicMock()
        mock_poller = MagicMock()
        mock_poller.result.return_value = MagicMock()
        mock_client.begin_send.return_value = mock_poller
        mock_email_client_class.from_connection_string.return_value = mock_client

        EmailUtils.send_email_azure(
            recipients="single@example.com",
            subject="Test",
            body="Body",
        )

        # Verify begin_send was called with recipients as a list
        call_args = mock_client.begin_send.call_args
        message = call_args[0][0] if call_args[0] else call_args[1].get("message", {})
        to_list = message.get("recipients", {}).get("to", [])
        assert len(to_list) == 1
        assert to_list[0]["address"] == "single@example.com"

    @patch("EmailUtils.EmailClient")
    def test_azure_multiple_recipients(self, mock_email_client_class):
        """Multiple recipients should be included in the Azure message."""
        mock_client = MagicMock()
        mock_poller = MagicMock()
        mock_poller.result.return_value = MagicMock()
        mock_client.begin_send.return_value = mock_poller
        mock_email_client_class.from_connection_string.return_value = mock_client

        recipients = ["a@test.com", "b@test.com"]
        EmailUtils.send_email_azure(
            recipients=recipients,
            subject="Test",
            body="Body",
        )

        call_args = mock_client.begin_send.call_args
        message = call_args[0][0] if call_args[0] else call_args[1].get("message", {})
        to_list = message.get("recipients", {}).get("to", [])
        assert len(to_list) == 2


# =============================================================================
# Microsoft 365 (Graph) provider + SMTP relay fallback
# =============================================================================


@pytest.mark.unit
class TestSendEmailGraphProvider:
    """send_email() with provider 'graph': Microsoft 365 first, the SMTP block
    as the relay fallback (Email Settings toggle, default on)."""

    @staticmethod
    def _conf(**over):
        conf = {
            "source": "ui", "provider": "graph",
            "smtp_host": "relay.test", "smtp_port": 25, "smtp_user": "", "smtp_password": "",
            "smtp_use_tls": False, "smtp_from": "alerts@x.test",
            "azure_conn_str": "", "azure_sender": "",
            "graph_tenant_id": "t", "graph_client_id": "c", "graph_client_secret": "s",
            "graph_sender": "alerts@x.test", "graph_fallback_smtp": True,
        }
        conf.update(over)
        return conf

    def _run(self, conf, graph_ok):
        import email_settings
        import email_graph
        with patch.object(email_settings, "get_email_config", return_value=conf), \
             patch.object(email_graph, "send_email_graph", return_value=graph_ok) as graph, \
             patch("EmailUtils.send_email_smtp", return_value=True) as smtp, \
             patch("EmailUtils.send_email_azure", return_value=True) as azure:
            result = EmailUtils.send_email("u@x.test", "S", "B")
        return result, graph, smtp, azure

    def test_graph_success_never_touches_smtp_or_azure(self):
        result, graph, smtp, azure = self._run(self._conf(), graph_ok=True)
        assert result is True
        graph.assert_called_once()
        assert graph.call_args[1]["conf"]["provider"] == "graph"
        smtp.assert_not_called()
        azure.assert_not_called()

    def test_graph_failure_falls_back_to_the_smtp_relay(self):
        result, graph, smtp, azure = self._run(self._conf(), graph_ok=False)
        assert result is True
        graph.assert_called_once()
        smtp.assert_called_once()
        azure.assert_not_called()

    def test_graph_failure_with_fallback_off_returns_false(self):
        result, graph, smtp, azure = self._run(self._conf(graph_fallback_smtp=False), graph_ok=False)
        assert result is False
        smtp.assert_not_called()
        azure.assert_not_called()

    def test_graph_failure_without_a_relay_host_returns_false(self):
        result, graph, smtp, azure = self._run(self._conf(smtp_host=""), graph_ok=False)
        assert result is False
        smtp.assert_not_called()
