"""
Unit Test Configuration for AI Hub
====================================

Shared fixtures for pure unit tests. These tests mock all external
dependencies (database, LLM, SMTP, HTTP, filesystem) and run fast.
"""

import pytest
import os
import sys
import json
from unittest.mock import MagicMock, patch, PropertyMock
from pathlib import Path

# Ensure the app root is importable
APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)


# =============================================================================
# DATABASE FIXTURES
# =============================================================================

@pytest.fixture
def mock_cursor():
    """A mocked database cursor that returns configurable results."""
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    cursor.fetchall.return_value = []
    cursor.description = None
    cursor.rowcount = 0
    return cursor


@pytest.fixture
def mock_db_connection(mock_cursor):
    """A mocked pyodbc database connection."""
    conn = MagicMock()
    conn.cursor.return_value = mock_cursor
    conn.close.return_value = None
    conn.commit.return_value = None
    conn.rollback.return_value = None
    return conn


@pytest.fixture
def mock_db(mock_db_connection):
    """Patches get_db_connection to return the mock connection."""
    with patch("CommonUtils.get_db_connection", return_value=mock_db_connection):
        yield mock_db_connection


# =============================================================================
# LLM / AI FIXTURES
# =============================================================================

@pytest.fixture
def mock_llm_response():
    """A factory fixture that creates configurable LLM responses."""
    def _make_response(content="This is a test response.", tool_calls=None):
        response = MagicMock()
        response.content = content
        response.tool_calls = tool_calls or []
        choice = MagicMock()
        choice.message = response
        api_response = MagicMock()
        api_response.choices = [choice]
        return api_response
    return _make_response


@pytest.fixture
def mock_llm_callable():
    """A mocked LLM callable that returns a simple string response."""
    def _llm(prompt, **kwargs):
        return "Mocked LLM response"
    return _llm


# =============================================================================
# FLASK APP FIXTURES
# =============================================================================

@pytest.fixture
def flask_app():
    """Create a minimal Flask app for route testing.

    Note: For full route tests, import the actual app.
    This fixture is for lightweight testing of utilities
    that need a Flask request context.
    """
    from flask import Flask
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-key"
    app.config["WTF_CSRF_ENABLED"] = False
    return app


@pytest.fixture
def flask_request_context(flask_app):
    """Provide a Flask request context for testing functions that use request/g."""
    with flask_app.test_request_context():
        yield


# =============================================================================
# FILESYSTEM / TEMP FIXTURES
# =============================================================================

@pytest.fixture
def temp_dir(tmp_path):
    """A temporary directory for file operations in tests."""
    return tmp_path


@pytest.fixture
def sample_csv(tmp_path):
    """Create a sample CSV file for testing."""
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text("name,age,city\nAlice,30,NYC\nBob,25,LA\nCharlie,35,Chicago\n")
    return str(csv_path)


@pytest.fixture
def sample_json(tmp_path):
    """Create a sample JSON file for testing."""
    json_path = tmp_path / "sample.json"
    data = {"users": [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]}
    json_path.write_text(json.dumps(data))
    return str(json_path)


# =============================================================================
# EMAIL FIXTURES
# =============================================================================

@pytest.fixture
def mock_smtp():
    """A mocked SMTP connection."""
    smtp = MagicMock()
    smtp.sendmail.return_value = {}
    smtp.send_message.return_value = None
    smtp.__enter__ = MagicMock(return_value=smtp)
    smtp.__exit__ = MagicMock(return_value=False)
    return smtp


# =============================================================================
# CONFIGURATION FIXTURES
# =============================================================================

@pytest.fixture
def clean_env(monkeypatch):
    """Provide a clean environment with no AI Hub env vars set.

    Use monkeypatch to set specific vars in your test:
        clean_env  # clears everything
        monkeypatch.setenv("HOST_PORT", "5001")
    """
    ai_hub_vars = [
        "HOST_PORT", "APP_ROOT", "DATABASE_SERVER", "DATABASE_NAME",
        "DATABASE_UID", "DATABASE_PWD", "API_KEY", "AZURE_OPENAI_API_KEY",
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "TAVILY_API_KEY",
        "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD",
        "AI_HUB_API_URL", "LOG_LEVEL",
    ]
    for var in ai_hub_vars:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


@pytest.fixture
def app_root_env(monkeypatch, tmp_path):
    """Set APP_ROOT to a temp directory for tests that need file paths."""
    monkeypatch.setenv("APP_ROOT", str(tmp_path))
    # Create standard subdirectories
    (tmp_path / "logs").mkdir(exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "secrets").mkdir(exist_ok=True)
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "exports").mkdir(exist_ok=True)
    return tmp_path


# =============================================================================
# ENCRYPTION FIXTURES
# =============================================================================

@pytest.fixture
def encryption_key():
    """A known encryption key for testing encrypt/decrypt round-trips."""
    from cryptography.fernet import Fernet
    return Fernet.generate_key()


# =============================================================================
# CONFIG DATABASE CLIENT FIXTURES
# =============================================================================

@pytest.fixture
def mock_config_db_client(mock_cursor):
    """A mocked ConfigDatabaseClient for modules that use config_db_client."""
    client = MagicMock()
    client.execute_query.return_value = mock_cursor
    client.fetch_query.return_value = []
    client.fetch_one.return_value = None
    client.close.return_value = None
    return client


# =============================================================================
# HTTP / REQUESTS FIXTURES
# =============================================================================

@pytest.fixture
def mock_requests():
    """Patches requests.request, requests.get, and requests.post globally.

    Usage:
        def test_something(mock_requests):
            mock_requests['get'].return_value.json.return_value = {"ok": True}
            mock_requests['get'].return_value.status_code = 200
    """
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {}
    mock_response.text = ""
    mock_response.content = b""
    mock_response.ok = True
    mock_response.raise_for_status.return_value = None

    with patch("requests.get", return_value=mock_response) as mock_get, \
         patch("requests.post", return_value=mock_response) as mock_post, \
         patch("requests.put", return_value=mock_response) as mock_put, \
         patch("requests.delete", return_value=mock_response) as mock_del, \
         patch("requests.request", return_value=mock_response) as mock_req:
        yield {
            "get": mock_get,
            "post": mock_post,
            "put": mock_put,
            "delete": mock_del,
            "request": mock_req,
            "response": mock_response,
        }


# =============================================================================
# LLM QUICK PROMPT FIXTURES
# =============================================================================

@pytest.fixture
def mock_llm_quick_prompt():
    """Patches AppUtils.azureQuickPrompt and azureMiniQuickPrompt.

    Usage:
        def test_something(mock_llm_quick_prompt):
            mock_llm_quick_prompt['quick'].return_value = "LLM says yes"
    """
    with patch("AppUtils.azureQuickPrompt", return_value="Mocked LLM response") as mock_quick, \
         patch("AppUtils.azureMiniQuickPrompt", return_value="Mocked mini response") as mock_mini:
        yield {
            "quick": mock_quick,
            "mini": mock_mini,
        }


# =============================================================================
# WINDOWS REGISTRY FIXTURES
# =============================================================================

@pytest.fixture
def mock_winreg():
    """Mocked winreg module for testing secure_config.py on any platform."""
    winreg = MagicMock()
    winreg.HKEY_LOCAL_MACHINE = 0x80000002
    winreg.KEY_READ = 0x20019
    winreg.KEY_WRITE = 0x20006
    winreg.REG_SZ = 1

    mock_key = MagicMock()
    mock_key.__enter__ = MagicMock(return_value=mock_key)
    mock_key.__exit__ = MagicMock(return_value=False)
    winreg.OpenKey.return_value = mock_key
    winreg.CreateKeyEx.return_value = mock_key
    winreg.QueryValueEx.return_value = ("test-api-key", 1)

    return winreg
