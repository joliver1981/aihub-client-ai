"""
Solutions Gallery — seed data loader.

Runs a bundled `data/schema.sql` and loads `data/seeds/*.csv` into the
target database when a solution is installed. The goal is zero-touch
first-run: customers and demos can exercise the solution immediately.

Two modes:
  - Full: a target SQL Server (or other DB) connection is provided →
    execute schema.sql idempotently, then COPY-style load each CSV into
    the same-named table.
  - Sandbox: no connection provided → loader returns a success result
    with `skipped=True` so the installer proceeds (typical for
    file-based solutions like Customer Onboarding).
"""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

# Very conservative SQL statement splitter — handles the common case of
# statements separated by `;` on their own line or end-of-line. Doesn't
# try to be clever about string literals (schema.sql is authored by
# solution builders, not hostile input).
_STATEMENT_SPLIT_RE = re.compile(r";[ \t]*(?:\r?\n|$)")


@dataclass
class SeedResult:
    skipped: bool = False
    schema_statements_run: int = 0
    seeds_loaded: Dict[str, int] = field(default_factory=dict)  # {csv_filename: rows_inserted}
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skipped": self.skipped,
            "schema_statements_run": self.schema_statements_run,
            "seeds_loaded": dict(self.seeds_loaded),
            "errors": list(self.errors),
        }


def load_seed_data(
    *,
    schema_sql: Optional[bytes],
    seed_csvs: Dict[str, bytes],
    target_connection: Optional[Dict[str, Any]] = None,
) -> SeedResult:
    """Execute schema.sql then load each CSV into a same-named table.

    `target_connection` is a dict describing the DB to target. When None,
    sandbox mode kicks in and the loader skips cleanly.

    `seed_csvs` maps bare filenames (e.g. "customers.csv") to their raw
    bytes. The filename stem (without extension) is used as the target
    table name unless the CSV's first row is a header matching an
    existing table's columns.
    """
    result = SeedResult()

    if not target_connection:
        result.skipped = True
        logger.info("Seed loader: no target connection — sandbox mode")
        return result

    conn = None
    try:
        conn = _open_connection(target_connection)
        if conn is None:
            result.errors.append("Could not open target DB connection")
            return result

        # ── Schema ──────────────────────────────────────────────
        if schema_sql:
            try:
                text = schema_sql.decode("utf-8", errors="ignore")
                stmts = [s.strip() for s in _STATEMENT_SPLIT_RE.split(text) if s.strip()]
                cur = conn.cursor()
                for stmt in stmts:
                    try:
                        cur.execute(stmt)
                        result.schema_statements_run += 1
                    except Exception as e:
                        # Surface but don't abort — schemas are IF NOT EXISTS
                        result.errors.append(f"schema statement failed: {e}")
                conn.commit()
            except Exception as e:
                result.errors.append(f"schema load error: {e}")

        # ── Seeds ───────────────────────────────────────────────
        for fname, body in (seed_csvs or {}).items():
            inserted = 0
            try:
                inserted = _load_csv_into_table(conn, fname, body)
                result.seeds_loaded[fname] = inserted
            except Exception as e:
                result.errors.append(f"seed {fname}: {e}")
            finally:
                conn.commit()

    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    return result


def _open_connection(target_connection: Dict[str, Any]):
    """Open a DB connection from a target_connection dict.

    Recognises keys: driver, server, database, user, password, port.
    Returns a DB-API connection object or None.
    """
    try:
        import pyodbc  # type: ignore
    except ImportError:
        logger.warning("pyodbc not available; cannot open seed target connection")
        return None

    driver = target_connection.get("driver") or "{ODBC Driver 17 for SQL Server}"
    server = target_connection.get("server") or ""
    database = target_connection.get("database") or ""
    user = target_connection.get("user") or target_connection.get("user_name") or ""
    password = target_connection.get("password") or ""
    port = target_connection.get("port")

    server_spec = server
    if port and "," not in str(server) and ":" not in str(server):
        server_spec = f"{server},{port}"

    conn_str = (
        f"Driver={driver};"
        f"Server={server_spec};"
        f"Database={database};"
        f"UID={user};PWD={password};"
        f"Encrypt=no;TrustServerCertificate=yes;"
    )
    try:
        return pyodbc.connect(conn_str, timeout=10)
    except Exception as e:
        logger.warning("Could not open seed connection: %s", e)
        return None


def _load_csv_into_table(conn, fname: str, body: bytes) -> int:
    """Insert rows from a CSV into a table. Returns row count.

    The target table name is the filename stem. First CSV row is
    treated as a header and used to build the column list.
    """
    table = fname.rsplit(".", 1)[0]
    if not _is_safe_identifier(table):
        raise ValueError(f"unsafe table name: {table!r}")

    text = body.decode("utf-8-sig", errors="ignore")
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return 0

    cols = [h.strip() for h in header if h.strip()]
    if not cols:
        return 0
    for c in cols:
        if not _is_safe_identifier(c):
            raise ValueError(f"unsafe column name: {c!r}")

    placeholders = ",".join(["?"] * len(cols))
    col_list = ",".join(f"[{c}]" for c in cols)
    sql = f"INSERT INTO [{table}] ({col_list}) VALUES ({placeholders})"

    cur = conn.cursor()
    count = 0
    for row in reader:
        # Trim/pad to column length.
        vals = (row + [None] * len(cols))[: len(cols)]
        # Normalise empty strings to None so the DB can apply defaults.
        vals = [(v if (v is not None and v != "") else None) for v in vals]
        cur.execute(sql, vals)
        count += 1
    return count


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _is_safe_identifier(s: str) -> bool:
    return bool(_IDENTIFIER_RE.match(s or ""))
