"""ODBC driver selection (config.py) — regression for the 2026-08 large-Excel
ingest failure.

The legacy 'SQL Server' ODBC driver streams big values through
WRITETEXT/SQLPutData, which SQL Server rejects on RLS-protected tables
(error 7152, surfacing to users as 7125 'partial insert' when large
DocumentPages.full_text values are written). config.py therefore resolves the
driver once at import:

* DATABASE_DRIVER from the environment wins when that driver is installed —
  tolerating the '+'-encoded form that shipped in .env.template
  ('ODBC+Driver+17+for+SQL+Server') and stray {braces};
* otherwise 'ODBC Driver 17 for SQL Server' when installed;
* otherwise the legacy 'SQL Server' driver — a wrong config value or a
  machine without Driver 17 must never lose its DB connections (the fail-safe
  rollout guard).

Every connection string in the app is built by config.build_connection_string
so the choice applies everywhere. pyodbc.drivers is monkeypatched here — no
real driver enumeration and no DB access.
"""
from __future__ import annotations

import pyodbc
import pytest

import config


MODERN = 'ODBC Driver 17 for SQL Server'
LEGACY = 'SQL Server'


def _resolve(monkeypatch, configured, installed):
    if configured is None:
        monkeypatch.delenv('DATABASE_DRIVER', raising=False)
    else:
        monkeypatch.setenv('DATABASE_DRIVER', configured)
    monkeypatch.setattr(pyodbc, 'drivers', lambda: list(installed))
    return config._resolve_db_driver()


# --- normalization -----------------------------------------------------------

@pytest.mark.parametrize('raw, expected', [
    ('ODBC Driver 17 for SQL Server', MODERN),
    ('ODBC+Driver+17+for+SQL+Server', MODERN),          # .env.template legacy form
    ('{ODBC Driver 17 for SQL Server}', MODERN),
    ('  {ODBC+Driver+17+for+SQL+Server}  ', MODERN),
    ('SQL Server', LEGACY),
    ('', ''),
    (None, ''),
])
def test_normalize_db_driver(raw, expected):
    assert config._normalize_db_driver(raw) == expected


# --- resolution --------------------------------------------------------------

def test_unset_prefers_driver_17_when_installed(monkeypatch):
    assert _resolve(monkeypatch, None, [LEGACY, MODERN]) == MODERN


def test_unset_falls_back_to_legacy_without_driver_17(monkeypatch):
    assert _resolve(monkeypatch, None, [LEGACY]) == LEGACY


def test_configured_driver_used_when_installed(monkeypatch):
    assert _resolve(monkeypatch, 'ODBC Driver 18 for SQL Server',
                    [LEGACY, MODERN, 'ODBC Driver 18 for SQL Server']) \
        == 'ODBC Driver 18 for SQL Server'


def test_plus_encoded_configured_value_resolves(monkeypatch):
    assert _resolve(monkeypatch, 'ODBC+Driver+17+for+SQL+Server',
                    [LEGACY, MODERN]) == MODERN


def test_explicit_legacy_opt_out_is_honored(monkeypatch):
    # Setting DATABASE_DRIVER=SQL Server pins the legacy driver even when 17
    # is installed — the escape hatch if Driver 17 misbehaves on a client.
    assert _resolve(monkeypatch, LEGACY, [LEGACY, MODERN]) == LEGACY


def test_bogus_configured_value_never_bricks(monkeypatch, capsys):
    # The rollout guard: an uninstalled driver name is ignored with a warning.
    assert _resolve(monkeypatch, 'Totally Bogus Driver 99',
                    [LEGACY, MODERN]) == MODERN
    assert 'not an installed' in capsys.readouterr().err


def test_bogus_configured_value_without_driver_17(monkeypatch):
    # Client machine without Driver 17 AND a bad config value: legacy driver,
    # exactly the pre-change behavior.
    assert _resolve(monkeypatch, 'Totally Bogus Driver 99', [LEGACY]) == LEGACY


def test_driver_enumeration_failure_keeps_configured_value(monkeypatch):
    def boom():
        raise RuntimeError('driver enumeration failed')
    monkeypatch.setenv('DATABASE_DRIVER', MODERN)
    monkeypatch.setattr(pyodbc, 'drivers', boom)
    assert config._resolve_db_driver() == MODERN


def test_driver_enumeration_failure_defaults_to_legacy(monkeypatch):
    def boom():
        raise RuntimeError('driver enumeration failed')
    monkeypatch.delenv('DATABASE_DRIVER', raising=False)
    monkeypatch.setattr(pyodbc, 'drivers', boom)
    assert config._resolve_db_driver() == LEGACY


def test_case_insensitive_match_returns_installed_casing(monkeypatch):
    assert _resolve(monkeypatch, 'odbc driver 17 for sql server',
                    [LEGACY, MODERN]) == MODERN


# --- the single builder ------------------------------------------------------

def test_build_connection_string_uses_resolved_driver():
    s = config.build_connection_string('srv', 'db', 'user', 'pw')
    assert s == f"DRIVER={{{config.DB_DRIVER}}};SERVER=srv;DATABASE=db;UID=user;PWD=pw"


def test_module_connection_string_matches_builder():
    assert config.CONNECTION_STRING == config.build_connection_string(
        config.DB_SERVER, config.DB_NAME, config.DB_USER, config.DB_PWD)
