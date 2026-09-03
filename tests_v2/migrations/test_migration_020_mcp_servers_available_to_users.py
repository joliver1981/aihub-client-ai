"""Static checks for migrations/020_mcp_servers_available_to_users.sql.

020 adds the admin "publish" switch for My Connections
(MCPServers.available_to_users BIT NOT NULL DEFAULT 0) and backfills today's
eligible servers to 1 so existing installs keep their behaviour. These tests
never execute SQL; they pin the shape that makes the migration safe to apply
by hand on a live database and safe to re-run.
"""
from __future__ import annotations

import re

import pytest

from tests_v2.migrations.test_migration_safety import (  # type: ignore
    MIGRATIONS_DIR,
    _read,
    _strip_comments,
)

pytestmark = pytest.mark.migration

PATH = MIGRATIONS_DIR / "020_mcp_servers_available_to_users.sql"


def _sql() -> str:
    assert PATH.is_file(), f"missing {PATH}"
    return _strip_comments(_read(PATH))


def test_020_exists_and_follows_019():
    assert PATH.is_file()
    assert (MIGRATIONS_DIR / "019_platform_usage_log_covering_index.sql").is_file()


def test_020_adds_the_column_with_a_named_default_of_zero():
    sql = _sql()
    m = re.search(
        r"ALTER\s+TABLE\s+\[dbo\]\.\[MCPServers\]\s+ADD\s+available_to_users\s+BIT\s+NOT\s+NULL\s+"
        r"CONSTRAINT\s+DF_MCPServers_available_to_users\s+DEFAULT\s*\(0\)",
        sql, re.IGNORECASE)
    assert m, "column must be BIT NOT NULL with a NAMED default of 0 (new servers start unpublished)"


def test_020_column_add_is_guarded_by_sys_columns():
    sql = _sql()
    guard = re.search(
        r"IF\s+NOT\s+EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+sys\.columns\s+WHERE\s+object_id\s*=\s*"
        r"OBJECT_ID\s*\(\s*N'\[dbo\]\.\[MCPServers\]'\s*\)\s+AND\s+name\s*=\s*'available_to_users'",
        sql, re.IGNORECASE)
    assert guard, "ALTER must be wrapped in an IF NOT EXISTS on sys.columns"


def test_020_backfill_keeps_todays_eligible_servers_visible_and_runs_once():
    sql = _sql()
    update = re.search(
        r"UPDATE\s+\[dbo\]\.\[MCPServers\]\s+SET\s+available_to_users\s*=\s*1\s+"
        r"WHERE\s+auth_type\s*=\s*'oauth2'\s+AND\s+enabled\s*=\s*1",
        sql, re.IGNORECASE)
    assert update, "backfill must target exactly today's listing predicate (auth_type='oauth2' AND enabled=1)"
    # Re-running after an admin has unpublished something must not re-publish it.
    assert re.search(r"NOT\s+EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+\[dbo\]\.\[MCPServers\]\s+WHERE\s+available_to_users\s*=\s*1\s*\)",
                     sql, re.IGNORECASE), "backfill must be guarded so it only runs on first application"


def test_020_has_no_destructive_statements_outside_comments():
    sql = _sql()
    assert not re.search(r"\bDROP\s+(TABLE|COLUMN)\b", sql, re.IGNORECASE), \
        "rollback lives in the header comment only"
    assert "TRUNCATE" not in sql.upper()


def test_020_batches_are_separated_with_go():
    assert len(re.findall(r"^\s*GO\s*$", _read(PATH), re.IGNORECASE | re.MULTILINE)) >= 2
