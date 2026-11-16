"""
get_connection_string_by_name must resolve {{LOCAL_SECRET:...}} passwords.
==========================================================================
Automations resolve a platform connection BY NAME (automations/runner.py
_resolve_connection -> DataUtils.get_connection_string_by_name), and so do
{CONN:name} placeholders in code strings. Connections saved through the
current UI/API store the password as a {{LOCAL_SECRET:CONN_PWD_<id>}}
reference. Before the fix the reference went into the ODBC string verbatim,
so pyodbc failed with "Neither DSN nor SERVER keyword supplied / Invalid
connection string attribute" on every such connection, while the Test button
(which resolves it) still passed.

These tests pin: references are resolved in both the built string and a stored
connection string; legacy plain-text passwords pass through unchanged.
"""

import sys
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import connection_secrets  # noqa: E402
import DataUtils  # noqa: E402

REAL_PASSWORD = "s3cret-pass"
REF = "{{LOCAL_SECRET:CONN_PWD_636}}"


def _row(password, connection_string=None, database_type="SQL Server"):
    # server, database_name, user_name, password, connection_string, database_type, port, id
    return ("10.0.0.6", "DemoDB", "ai_user", password, connection_string, database_type, 1433, 636)


def _call(row, secrets=None):
    """Run get_connection_string_by_name against one fake Connections row."""
    secrets = {"CONN_PWD_636": REAL_PASSWORD} if secrets is None else secrets
    cursor = mock.MagicMock()
    cursor.fetchall.return_value = [row]
    conn = mock.MagicMock()
    conn.cursor.return_value = cursor
    with mock.patch.object(DataUtils.pyodbc, "connect", return_value=conn), \
         mock.patch.object(connection_secrets, "get_local_secret",
                           side_effect=lambda name, default=None: secrets.get(name, default)):
        return DataUtils.get_connection_string_by_name("DemoDB")


def test_secret_reference_in_password_column_is_resolved():
    cs = _call(_row(REF))
    assert f"PWD={REAL_PASSWORD};" in cs
    assert "LOCAL_SECRET" not in cs and "{{" not in cs
    assert "Server=10.0.0.6,1433;" in cs and "Database=DemoDB;" in cs


def test_secret_reference_inside_stored_connection_string_is_resolved():
    stored = f"DRIVER={{ODBC Driver 17 for SQL Server}};Server=10.0.0.6;Database=DemoDB;UID=ai_user;PWD={REF};"
    cs = _call(_row(REF, connection_string=stored))
    assert f"PWD={REAL_PASSWORD};" in cs
    assert "LOCAL_SECRET" not in cs
    # the driver braces of a stored string are left alone
    assert cs.startswith("DRIVER={ODBC Driver 17 for SQL Server};")


def test_plain_text_password_passes_through_unchanged():
    cs = _call(_row("legacy-plain"))
    assert "PWD=legacy-plain;" in cs


def test_other_database_types_resolve_too():
    cs = _call(_row(REF, database_type="Postgres"))
    assert f"PWD={REAL_PASSWORD};" in cs and "LOCAL_SECRET" not in cs


def test_unknown_connection_returns_none():
    cursor = mock.MagicMock()
    cursor.fetchall.return_value = []
    conn = mock.MagicMock()
    conn.cursor.return_value = cursor
    with mock.patch.object(DataUtils.pyodbc, "connect", return_value=conn):
        assert DataUtils.get_connection_string_by_name("NoSuchConnection") is None
