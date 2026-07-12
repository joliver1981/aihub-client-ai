"""Deterministic read-only SQL gate for the NLQ V3 agentic engine.

NLQ V3 plan §5 (docs/nlq-agentic-engine-plan.md). This is the SECURITY BOUNDARY
for LLM-authored SQL: the agentic `run_sql` tool must pass every query through
`validate_readonly` before execution. Read-only enforcement in the legacy engine
is prompt-only; here it is a real parser gate, so a model that ignores the prompt
(or is steered by injected content) still cannot execute anything but a SELECT.

Two independent layers, both must pass:
  1. ALLOWLIST the top-level statement — exactly one, and it must be a
     SELECT / UNION (set-op of selects) / parenthesized SELECT.
  2. DENYLIST-walk the whole AST for any embedded write/DDL/command node.
     This is what catches the nasty case a top-level check misses, e.g.
     Postgres `WITH x AS (DELETE FROM t RETURNING id) SELECT * FROM x`,
     which sqlglot parses as a top-level SELECT with a DELETE buried in a CTE.

`apply_row_cap` is a SEPARATE, non-security safety net that injects a
TOP/LIMIT/FETCH so a valid SELECT can't pull millions of rows into a DataFrame.
It fails OPEN (returns the original SQL) on any error — capping is about memory,
not safety, and the validate gate is the real boundary.

Depends on sqlglot (pure-Python; added to the aihub2.1 env + app_onedir.spec).
"""
import logging

import sqlglot
from sqlglot import exp

logger = logging.getLogger("sql_gate")

# sqlglot emits a WARNING to its own logger when it falls back to parsing
# unsupported syntax as a generic Command (e.g. EXEC). We block Command anyway,
# so keep that noise out of the app logs.
logging.getLogger("sqlglot").setLevel(logging.ERROR)


# Platform `database_type` (DataUtils) -> sqlglot dialect. Unknown/None parses
# dialect-agnostically; SELECT-only enforcement still applies.
_DIALECT_MAP = {
    "sql server": "tsql",
    "sqlserver": "tsql",
    "mssql": "tsql",
    "tsql": "tsql",
    "postgres": "postgres",
    "postgresql": "postgres",
    "mysql": "mysql",
    "mariadb": "mysql",
    "oracle": "oracle",
    "snowflake": "snowflake",
}

# Any of these appearing ANYWHERE in the parse tree fails the gate. Covers
# writes, DDL, permission changes, session/DB switches, and sqlglot's
# generic Command fallback (EXEC/EXECUTE and other unsupported syntax).
_FORBIDDEN_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.TruncateTable,
    exp.Grant,
    exp.Command,      # EXEC / EXECUTE / CALL / anything sqlglot couldn't classify
    exp.Set,          # SET ...
    exp.Use,          # USE <db>
)

# The only acceptable top-level statement kinds.
_ALLOWED_TOP_LEVEL = (exp.Select, exp.Union, exp.Subquery)


class GateResult:
    """Outcome of gate_sql(): validity, the (possibly capped) SQL, and reasons."""

    __slots__ = ("ok", "sql", "reason", "cap_applied", "dialect")

    def __init__(self, ok, sql, reason="", cap_applied=False, dialect=None):
        self.ok = ok
        self.sql = sql
        self.reason = reason
        self.cap_applied = cap_applied
        self.dialect = dialect

    def __repr__(self):
        return (f"GateResult(ok={self.ok}, cap_applied={self.cap_applied}, "
                f"dialect={self.dialect!r}, reason={self.reason!r})")


def normalize_dialect(database_type):
    """Map a platform database_type string to a sqlglot dialect, or None."""
    if not database_type:
        return None
    return _DIALECT_MAP.get(str(database_type).strip().lower())


def _has_select_into(root):
    """True if any SELECT in the tree has an INTO target (SELECT ... INTO t)."""
    for node in root.walk():
        if isinstance(node, exp.Select) and node.args.get("into") is not None:
            return True
    return False


def validate_readonly(sql, database_type=None):
    """Validate that `sql` is a single, read-only SELECT.

    Returns (ok: bool, reason: str). ok=True only when the query is exactly one
    statement, that statement is a SELECT/UNION/parenthesized-SELECT, it has no
    SELECT...INTO, and the whole tree is free of write/DDL/command nodes.

    Fails CLOSED: anything unparseable, empty, multi-statement, or unrecognized
    is rejected.
    """
    if sql is None or not str(sql).strip():
        return False, "empty query"

    dialect = normalize_dialect(database_type)

    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except Exception as e:
        return False, f"unparseable SQL ({type(e).__name__})"

    if len(statements) == 0:
        return False, "no statement found"
    if len(statements) > 1:
        return False, f"multiple statements not allowed ({len(statements)} found)"

    stmt = statements[0]

    if not isinstance(stmt, _ALLOWED_TOP_LEVEL):
        return False, f"only SELECT queries are allowed (got {type(stmt).__name__})"

    # A parenthesized/subquery top level must still wrap a SELECT or UNION.
    if isinstance(stmt, exp.Subquery):
        inner = stmt.this
        if not isinstance(inner, (exp.Select, exp.Union)):
            return False, f"only SELECT queries are allowed (subquery wraps {type(inner).__name__})"

    for node in stmt.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            return False, f"query contains a disallowed operation ({type(node).__name__})"

    if _has_select_into(stmt):
        return False, "SELECT ... INTO is not allowed (creates a table)"

    return True, "ok"


def _existing_cap(stmt):
    """Return the integer row cap already on this statement, or None."""
    try:
        limit_node = stmt.args.get("limit")
        if limit_node is None:
            return None
        expr = getattr(limit_node, "expression", None)
        if expr is None:
            return None
        return int(expr.this)
    except (ValueError, TypeError, AttributeError):
        # A non-literal LIMIT (parameter/expression) — treat as "present,
        # unknown"; don't override it.
        return -1


def apply_row_cap(sql, cap, database_type=None):
    """Inject a row cap (TOP/LIMIT/FETCH) when the query lacks a smaller one.

    Returns (sql_out, applied: bool). Respects an existing equal-or-smaller cap.
    Fails OPEN — returns the original SQL on any error — because capping is a
    memory safety net, not the security boundary (that's validate_readonly).
    """
    if not cap or int(cap) <= 0:
        return sql, False

    cap = int(cap)
    dialect = normalize_dialect(database_type)

    try:
        stmt = sqlglot.parse_one(sql, read=dialect)

        existing = _existing_cap(stmt)
        if existing is not None:
            # -1 => non-literal cap present; 0..cap => already tight enough.
            if existing == -1 or existing <= cap:
                return sql, False

        # Set operations / already-parenthesized selects: wrap so the cap
        # bounds the whole result rather than one arm of the union.
        if isinstance(stmt, (exp.Union, exp.Subquery)):
            capped = exp.select("*").from_(stmt.subquery("_capped")).limit(cap)
        else:
            capped = stmt.limit(cap)

        return capped.sql(dialect=dialect), True
    except Exception as e:
        logger.warning(f"[sql_gate] Row-cap injection failed ({type(e).__name__}: {e}); "
                       f"returning query uncapped")
        return sql, False


def gate_sql(sql, database_type=None, row_cap=None):
    """Validate read-only, then apply the row cap. One-call convenience for run_sql.

    Returns a GateResult. When ok is False, `sql` is the original input and must
    NOT be executed.
    """
    dialect = normalize_dialect(database_type)
    ok, reason = validate_readonly(sql, database_type=database_type)
    if not ok:
        return GateResult(ok=False, sql=sql, reason=reason, cap_applied=False, dialect=dialect)

    capped_sql, applied = apply_row_cap(sql, row_cap, database_type=database_type) if row_cap else (sql, False)
    return GateResult(ok=True, sql=capped_sql, reason="ok", cap_applied=applied, dialect=dialect)
