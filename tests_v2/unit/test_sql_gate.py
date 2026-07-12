"""Unit tests — sql_gate (NLQ V3 plan §5/§7, docs/nlq-agentic-engine-plan.md).

This is the acceptance gate for P2. sql_gate is the SECURITY BOUNDARY for
LLM-authored SQL, so the deny cases matter more than the allow cases: every
write/DDL/command/injection shape MUST be rejected, fail-closed.

NOTE: repo .gitignore ignores test*.py — commit with `git add -f`.
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import sql_gate


# ── dialect mapping ──────────────────────────────────────────────────────

@pytest.mark.parametrize("db_type,expected", [
    ("SQL Server", "tsql"),
    ("sql server", "tsql"),
    ("MSSQL", "tsql"),
    ("Postgres", "postgres"),
    ("PostgreSQL", "postgres"),
    ("MySQL", "mysql"),
    ("Oracle", "oracle"),
    ("Snowflake", "snowflake"),
    ("", None),
    (None, None),
    ("SomeFutureDB", None),
])
def test_normalize_dialect(db_type, expected):
    assert sql_gate.normalize_dialect(db_type) == expected


# ── ALLOW: valid read-only selects ───────────────────────────────────────

ALLOWED = [
    ("SQL Server", "SELECT * FROM Orders"),
    ("SQL Server", "SELECT TOP 10 name FROM Customers WHERE state = 'CA'"),
    ("SQL Server", "SELECT c.name, SUM(o.total) FROM Orders o JOIN Customers c ON o.cid = c.id GROUP BY c.name"),
    ("SQL Server", "WITH c AS (SELECT id FROM Orders) SELECT * FROM c"),
    ("SQL Server", "SELECT a FROM t UNION ALL SELECT b FROM u"),
    ("SQL Server", "SELECT a FROM (SELECT b AS a FROM u) x"),
    ("Postgres", "SELECT a FROM t LIMIT 5"),
    ("Postgres", "SELECT a FROM t WHERE b IN (SELECT b FROM u)"),
    ("Oracle", "SELECT a FROM t FETCH FIRST 5 ROWS ONLY"),
    (None, "SELECT 1"),
    ("SQL Server", "select order_id, order_date from Orders order by order_date desc"),
    ("SQL Server", "SELECT COUNT(*) FROM Orders WHERE status = 'active'"),
]


@pytest.mark.parametrize("db_type,sql", ALLOWED)
def test_valid_selects_pass(db_type, sql):
    ok, reason = sql_gate.validate_readonly(sql, database_type=db_type)
    assert ok, f"expected PASS but got: {reason} :: {sql}"


# ── DENY: writes, DDL, commands, permission/session changes ──────────────

BLOCKED = [
    # DML writes
    ("SQL Server", "INSERT INTO t VALUES (1)"),
    ("SQL Server", "UPDATE Orders SET total = 0"),
    ("SQL Server", "DELETE FROM Orders"),
    ("SQL Server", "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET a = 1"),
    # DDL
    ("SQL Server", "DROP TABLE Orders"),
    ("SQL Server", "TRUNCATE TABLE Orders"),
    ("SQL Server", "ALTER TABLE Orders ADD col INT"),
    ("SQL Server", "CREATE TABLE t (a INT)"),
    # SELECT ... INTO creates a table
    ("SQL Server", "SELECT * INTO backup_orders FROM Orders"),
    # Stored-proc / command execution
    ("SQL Server", "EXEC xp_cmdshell 'dir'"),
    ("SQL Server", "EXECUTE sp_who"),
    # Permission / session / db switching
    ("SQL Server", "GRANT SELECT ON Orders TO public"),
    ("SQL Server", "USE master"),
    ("SQL Server", "SET NOCOUNT ON"),
    # Data-modifying CTE that presents as a top-level SELECT (the key bypass)
    ("Postgres", "WITH del AS (DELETE FROM Orders RETURNING id) SELECT * FROM del"),
    ("Postgres", "WITH ins AS (INSERT INTO t VALUES (1) RETURNING id) SELECT * FROM ins"),
    # Stacked / multi-statement injection
    ("SQL Server", "SELECT 1; DROP TABLE Orders"),
    ("SQL Server", "SELECT * FROM Orders; DELETE FROM Orders"),
    # Garbage / empty
    ("SQL Server", "not a valid sql statement at all %%%"),
    ("SQL Server", ""),
    ("SQL Server", "   "),
    (None, None),
]


@pytest.mark.parametrize("db_type,sql", BLOCKED)
def test_dangerous_sql_blocked(db_type, sql):
    ok, reason = sql_gate.validate_readonly(sql, database_type=db_type)
    assert not ok, f"expected BLOCK but it PASSED :: {sql!r}"
    assert reason  # a human-readable reason is always provided


def test_comment_smuggled_second_statement_is_safe():
    # The '--' comments out the DROP; sqlglot yields a single clean SELECT.
    ok, _ = sql_gate.validate_readonly("SELECT 1 -- ; DROP TABLE Orders", database_type="SQL Server")
    assert ok


def test_block_reason_names_the_operation():
    ok, reason = sql_gate.validate_readonly("DELETE FROM Orders", database_type="SQL Server")
    assert not ok and "Delete" in reason


# ── row cap injection ────────────────────────────────────────────────────

def test_cap_injected_when_absent_tsql():
    out, applied = sql_gate.apply_row_cap("SELECT a FROM t", 10000, database_type="SQL Server")
    assert applied and "TOP" in out.upper() and "10000" in out


def test_cap_injected_when_absent_postgres():
    out, applied = sql_gate.apply_row_cap("SELECT a FROM t", 10000, database_type="Postgres")
    assert applied and "LIMIT" in out.upper() and "10000" in out


def test_existing_smaller_cap_respected():
    out, applied = sql_gate.apply_row_cap("SELECT TOP 5 a FROM t", 10000, database_type="SQL Server")
    assert not applied and out == "SELECT TOP 5 a FROM t"


def test_existing_larger_cap_is_tightened():
    out, applied = sql_gate.apply_row_cap("SELECT a FROM t LIMIT 50000", 10000, database_type="Postgres")
    assert applied and "10000" in out and "50000" not in out


def test_union_capped_by_wrapping():
    out, applied = sql_gate.apply_row_cap(
        "SELECT a FROM t UNION ALL SELECT b FROM u", 10000, database_type="SQL Server"
    )
    assert applied and "10000" in out
    # The cap must bound the whole union (wrapped), not just one arm.
    assert out.upper().count("SELECT") >= 3


def test_zero_or_none_cap_is_noop():
    assert sql_gate.apply_row_cap("SELECT a FROM t", 0, database_type="Postgres") == ("SELECT a FROM t", False)
    assert sql_gate.apply_row_cap("SELECT a FROM t", None, database_type="Postgres") == ("SELECT a FROM t", False)


def test_cap_fails_open_on_garbage():
    # Not a security path — capping garbage returns it unchanged (validate would
    # have already rejected it upstream).
    out, applied = sql_gate.apply_row_cap("%%% not sql", 10000, database_type="SQL Server")
    assert out == "%%% not sql" and not applied


# ── gate_sql convenience wrapper ─────────────────────────────────────────

def test_gate_sql_valid_with_cap():
    res = sql_gate.gate_sql("SELECT a FROM t", database_type="SQL Server", row_cap=10000)
    assert res.ok and res.cap_applied and "TOP" in res.sql.upper()
    assert res.dialect == "tsql"


def test_gate_sql_blocks_and_returns_original():
    res = sql_gate.gate_sql("DROP TABLE t", database_type="SQL Server", row_cap=10000)
    assert not res.ok and res.sql == "DROP TABLE t" and not res.cap_applied


def test_gate_sql_valid_without_cap():
    res = sql_gate.gate_sql("SELECT a FROM t", database_type="SQL Server", row_cap=None)
    assert res.ok and not res.cap_applied and res.sql == "SELECT a FROM t"
