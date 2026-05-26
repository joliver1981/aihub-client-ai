"""Shared fixtures for tests_v2.

These fixtures are deliberately conservative: external services (SQL Server,
SMTP, HTTP APIs, Azure/OpenAI, file system writes outside tmp_path) are
mocked. Tests that need a live system live in tests_v2/live/.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root and key services are importable.
_ROOT = Path(__file__).resolve().parents[1]
for p in (_ROOT, _ROOT / "command_center_service", _ROOT / "builder_service"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


@pytest.fixture
def fake_api_key():
    return "TEST-API-KEY-00000000-0000-0000-0000-000000000000"


@pytest.fixture
def admin_user_context():
    return {"user_id": 1, "role": 2, "tenant_id": 1, "username": "admin", "name": "Admin"}


@pytest.fixture
def viewer_user_context():
    return {"user_id": 999, "role": 0, "tenant_id": 1, "username": "viewer", "name": "Viewer"}


@pytest.fixture
def other_tenant_user_context():
    return {"user_id": 42, "role": 2, "tenant_id": 99, "username": "other", "name": "Other"}


@pytest.fixture
def mock_db_connection():
    """A mocked pyodbc-style connection with cursor that records calls."""
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchone.return_value = None
    cursor.fetchall.return_value = []
    cursor.description = []
    return conn


@pytest.fixture
def mock_requests(monkeypatch):
    """Patches the global ``requests`` module to return canned responses.

    Each test customises by setting ``mock_requests.next_response = ...``
    or using ``mock_requests.add_route(url_pattern, response_dict)``.
    """
    from collections import deque

    class _MockResponse:
        def __init__(self, status_code=200, json_data=None, text="", headers=None):
            self.status_code = status_code
            self._json = json_data if json_data is not None else {}
            self.text = text
            self.headers = headers or {}
            self.content = text.encode() if isinstance(text, str) else text

        def json(self):
            return self._json

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

        def iter_lines(self, decode_unicode=False):
            for line in (self.text.splitlines() if isinstance(self.text, str) else []):
                yield line

    class _Mocker:
        def __init__(self):
            self.calls = []
            self.queue = deque()
            self.routes = {}

        def add_route(self, url_substring, response):
            self.routes[url_substring] = response

        def enqueue(self, response):
            self.queue.append(response)

        def _respond(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            for substring, resp in self.routes.items():
                if substring in url:
                    return resp
            if self.queue:
                return self.queue.popleft()
            return _MockResponse(200, {})

    mocker = _Mocker()
    monkeypatch.setattr("requests.get", lambda url, **kw: mocker._respond("GET", url, **kw))
    monkeypatch.setattr("requests.post", lambda url, **kw: mocker._respond("POST", url, **kw))
    monkeypatch.setattr("requests.put", lambda url, **kw: mocker._respond("PUT", url, **kw))
    monkeypatch.setattr("requests.delete", lambda url, **kw: mocker._respond("DELETE", url, **kw))
    monkeypatch.setattr("requests.request", lambda method, url, **kw: mocker._respond(method, url, **kw))
    mocker.Response = _MockResponse
    return mocker


@pytest.fixture
def fixtures_dir():
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def tmp_secrets_dir(tmp_path, monkeypatch):
    """Redirect any secret-store writes to a tmp dir so tests don't touch
    the real encrypted secrets file."""
    d = tmp_path / "secrets"
    d.mkdir()
    monkeypatch.setenv("APP_ROOT", str(tmp_path))
    return d
