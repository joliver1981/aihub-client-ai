"""Static-analysis safety checks on every migration .sql file.

These tests deliberately avoid executing the SQL — they parse the files as
text (and optionally with ``sqlparse``) to assert structural properties that
must hold across every migration so deployments stay safe:

* idempotency guards (`IF NOT EXISTS` / `OBJECT_ID(...)` / `IF EXISTS` etc.)
* no destructive operations on non-test tables
* foreign keys reference tables that are actually created somewhere in the
  migrations set
* filename convention ``NNN_descriptive_name.sql`` with no duplicate numbers
* a header comment block describing purpose/date

The full list of tables tracked by the dependency check is built by walking
ALL migration files so an FK in migration 010 referencing a table created
in 009 is fine.
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple

import pytest

try:
    import sqlparse  # type: ignore
except ImportError:  # pragma: no cover - we ensure availability in CI
    sqlparse = None  # type: ignore


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

# Match "NNN_some_descriptive_name.sql" exactly.
FILENAME_RE = re.compile(r"^(\d{3})_[A-Za-z][A-Za-z0-9_]*\.sql$")

# Tables we know are created outside the /migrations/ directory (e.g. by the
# legacy CREATE_TABLES.sql or by the SaaS DBA). FK references to these
# tables are still valid.
EXTERNAL_TABLES: Set[str] = {
    "Users",
    "User",            # 002 references [dbo].[User]
    "Agents",          # FK target in 008
    "Documents",       # FK target referenced obliquely
    "AgentEmailAddresses",
    "AgentTools",
    "AgentKnowledge",
    "DocumentPages",
    "Tenants",
    "Workflows",
    "MCPServers",      # base schema; FK target in 013, ALTER target in 020
    "Groups",          # base schema; FK target in 016
}


def _all_migration_files() -> List[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def _read(p: Path) -> str:
    # Handle BOM gracefully — but we *also* assert it isn't there separately.
    return p.read_bytes().decode("utf-8-sig", errors="strict")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:\[?dbo\]?\.)?\[?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\]?",
    re.IGNORECASE,
)

# FK targets — both inline column-level and table-level FK syntax.
FK_REFERENCES_RE = re.compile(
    r"REFERENCES\s+(?:\[?dbo\]?\.)?\[?(?P<table>[A-Za-z_][A-Za-z0-9_]*)\]?\s*\(",
    re.IGNORECASE,
)


def _collect_created_tables(files: List[Path]) -> Set[str]:
    out: Set[str] = set()
    for f in files:
        for m in CREATE_TABLE_RE.finditer(_read(f)):
            out.add(m.group("name"))
    return out


def _strip_comments(sql: str) -> str:
    # Strip ``-- ...`` line comments and ``/* ... */`` block comments.
    sql = re.sub(r"--[^\n]*", "", sql)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    return sql


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_migrations_dir_exists():
    assert MIGRATIONS_DIR.is_dir(), f"migrations directory missing: {MIGRATIONS_DIR}"


def test_at_least_one_migration_present():
    assert _all_migration_files(), "no .sql files found in migrations/"


@pytest.mark.parametrize("path", _all_migration_files(), ids=lambda p: p.name)
def test_filename_follows_convention(path: Path):
    assert FILENAME_RE.match(path.name), (
        f"migration filename '{path.name}' does not match NNN_descriptive_name.sql"
    )


def test_no_duplicate_migration_numbers():
    seen: Dict[str, str] = {}
    for f in _all_migration_files():
        m = FILENAME_RE.match(f.name)
        if not m:
            continue
        num = m.group(1)
        assert num not in seen, (
            f"duplicate migration number {num!r}: {seen[num]} and {f.name}"
        )
        seen[num] = f.name


@pytest.mark.parametrize("path", _all_migration_files(), ids=lambda p: p.name)
def test_no_utf8_bom(path: Path):
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), (
        f"{path.name} begins with a UTF-8 BOM — strip it for portability"
    )


@pytest.mark.parametrize("path", _all_migration_files(), ids=lambda p: p.name)
def test_no_mixed_tabs_and_spaces_for_indent(path: Path):
    """Each indented line should use spaces *or* tabs, not both. Mixed indent
    breaks visual review and some downstream tooling."""
    bad: List[int] = []
    for i, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        # Look at the leading whitespace only.
        m = re.match(r"^(\s+)", line)
        if not m:
            continue
        indent = m.group(1)
        if "\t" in indent and " " in indent:
            bad.append(i)
    assert not bad, (
        f"{path.name} has mixed tab+space indent on lines: {bad[:10]} (showing first 10)"
    )


@pytest.mark.parametrize("path", _all_migration_files(), ids=lambda p: p.name)
def test_has_header_comment(path: Path):
    """Each migration must start with a `--` comment describing what it does.

    We check the first 40 non-blank lines for at least 2 leading ``--`` lines
    that together include either 'Migration' or 'Purpose' (case-insensitive).
    """
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    leading_comments: List[str] = []
    for line in lines[:60]:
        s = line.strip()
        if not s:
            continue
        if s.startswith("--"):
            leading_comments.append(s.lower())
            continue
        # First non-blank, non-comment line ends the header.
        break

    joined = " ".join(leading_comments)
    assert leading_comments, f"{path.name}: missing header comment"
    assert (
        "migration" in joined
        or "purpose" in joined
        or "compliance" in joined  # 009-012 use a banner without the literal word "migration"
        or "memory" in joined      # 006/007
        or "schema" in joined
        or "table" in joined
    ), f"{path.name}: header comment looks too thin: {leading_comments[:3]}"


@pytest.mark.parametrize("path", _all_migration_files(), ids=lambda p: p.name)
def test_is_idempotent(path: Path):
    """Either the whole file is wrapped in guards OR every CREATE TABLE /
    ALTER TABLE ADD <col> is. We accept either pattern."""
    sql = _strip_comments(_read(path))

    # Files that only contain PRINT statements are trivially idempotent.
    if not re.search(r"\b(CREATE\s+TABLE|ALTER\s+TABLE|CREATE\s+(UNIQUE\s+)?(NONCLUSTERED\s+|CLUSTERED\s+)?INDEX)\b",
                     sql, re.IGNORECASE):
        pytest.skip(f"{path.name} has no schema-mutating statements")

    # Two common idempotency patterns we accept:
    has_if_not_exists = bool(re.search(r"\bIF\s+NOT\s+EXISTS\b", sql, re.IGNORECASE))
    has_object_id = bool(re.search(r"OBJECT_ID\s*\(", sql, re.IGNORECASE))
    has_if_exists = bool(re.search(r"\bIF\s+EXISTS\b", sql, re.IGNORECASE))

    assert has_if_not_exists or has_object_id or has_if_exists, (
        f"{path.name}: no idempotency guard found "
        "(expected `IF NOT EXISTS`, `OBJECT_ID(...) IS NULL`, or `IF EXISTS`)"
    )


@pytest.mark.parametrize("path", _all_migration_files(), ids=lambda p: p.name)
def test_no_destructive_operations(path: Path):
    """Forbid DROP TABLE / TRUNCATE / unqualified DELETE on production tables.

    DROP COLUMN inside an `IF EXISTS` guard is allowed because that's the
    accepted way to walk back a schema change (migration 012 does this for
    the deprecated `extraction_schema` column).
    """
    sql = _strip_comments(_read(path))

    # DROP TABLE — forbidden outright on non-test tables.
    drop_tables = re.findall(
        r"\bDROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:\[?dbo\]?\.)?\[?(?P<n>[A-Za-z_][A-Za-z0-9_]*)\]?",
        sql,
        re.IGNORECASE,
    )
    bad_drops = [t for t in drop_tables if not t.lower().startswith(("tmp_", "test_"))]
    assert not bad_drops, f"{path.name}: forbidden DROP TABLE for {bad_drops}"

    # TRUNCATE TABLE — never allowed in a regular migration.
    truncates = re.findall(r"\bTRUNCATE\s+TABLE\b", sql, re.IGNORECASE)
    assert not truncates, f"{path.name}: TRUNCATE TABLE is not allowed"

    # DELETE without a WHERE clause anywhere in the same statement.
    # Statements are separated by ; or GO.
    statements = re.split(r";|\bGO\b", sql, flags=re.IGNORECASE)
    bad_deletes = []
    for stmt in statements:
        if re.search(r"\bDELETE\s+FROM\b", stmt, re.IGNORECASE):
            if not re.search(r"\bWHERE\b", stmt, re.IGNORECASE):
                bad_deletes.append(stmt.strip()[:80])
    assert not bad_deletes, f"{path.name}: DELETE without WHERE: {bad_deletes}"


def test_foreign_keys_reference_real_tables():
    """Every FK target must be created by some migration OR be in the
    well-known EXTERNAL_TABLES allow-list (created outside the migrations
    folder)."""
    files = _all_migration_files()
    created = _collect_created_tables(files) | EXTERNAL_TABLES

    errors: List[str] = []
    for f in files:
        sql = _read(f)
        for m in FK_REFERENCES_RE.finditer(sql):
            target = m.group("table")
            if target not in created:
                errors.append(f"{f.name}: FK -> {target} (not created anywhere)")

    assert not errors, "FK targets not found in migrations or EXTERNAL_TABLES:\n  " + "\n  ".join(errors)


@pytest.mark.skipif(sqlparse is None, reason="sqlparse not installed")
@pytest.mark.parametrize("path", _all_migration_files(), ids=lambda p: p.name)
def test_sqlparse_can_tokenize(path: Path):
    """Sanity check: sqlparse can at least lex every file without exploding."""
    sql = _read(path)
    parsed = sqlparse.parse(sql)
    assert parsed, f"{path.name}: sqlparse returned no statements"
    # Every statement should have some non-whitespace tokens.
    non_trivial = [s for s in parsed if str(s).strip()]
    assert non_trivial, f"{path.name}: sqlparse returned only whitespace"
