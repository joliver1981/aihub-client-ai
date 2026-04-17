"""
Security Test Configuration for AI Hub
========================================

Shared fixtures for security-focused tests including authentication,
authorization, encryption, and data isolation.
"""

import pytest
import os
import sys
from unittest.mock import MagicMock, patch
from pathlib import Path

# Ensure the app root is importable
APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)


# =============================================================================
# USER MOCK FIXTURES
# =============================================================================

@pytest.fixture
def mock_admin_user():
    """A mock user with admin role (role=3)."""
    user = MagicMock()
    user.is_authenticated = True
    user.role = 3
    user.id = 1
    user.email = "admin@test.com"
    user.username = "admin"
    user.api_key = "test-api-key-admin"
    return user


@pytest.fixture
def mock_developer_user():
    """A mock user with developer role (role=2)."""
    user = MagicMock()
    user.is_authenticated = True
    user.role = 2
    user.id = 2
    user.email = "developer@test.com"
    user.username = "developer"
    user.api_key = "test-api-key-developer"
    return user


@pytest.fixture
def mock_basic_user():
    """A mock user with basic user role (role=1)."""
    user = MagicMock()
    user.is_authenticated = True
    user.role = 1
    user.id = 3
    user.email = "user@test.com"
    user.username = "testuser"
    user.api_key = "test-api-key-user"
    return user


@pytest.fixture
def mock_anonymous_user():
    """A mock unauthenticated user."""
    user = MagicMock()
    user.is_authenticated = False
    user.role = 0
    user.id = None
    return user


# =============================================================================
# FLASK APP FIXTURES
# =============================================================================

@pytest.fixture
def flask_app():
    """Create a Flask app for testing auth/role decorators."""
    from flask import Flask
    from flask_login import LoginManager

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-key"
    app.config["WTF_CSRF_ENABLED"] = False

    login_manager = LoginManager()
    login_manager.init_app(app)

    return app


@pytest.fixture
def mock_db_connection():
    """A mocked database connection for security tests."""
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchone.return_value = None
    cursor.fetchall.return_value = []
    return conn


# =============================================================================
# ENCRYPTION FIXTURES
# =============================================================================

@pytest.fixture
def temp_secrets_dir(tmp_path):
    """Create a temporary directory for secrets storage."""
    secrets_dir = tmp_path / "data" / "secrets"
    secrets_dir.mkdir(parents=True)
    return tmp_path


@pytest.fixture
def mock_machine_id():
    """Provide a consistent mock machine ID for encryption tests."""
    return "test-machine-id-12345"
