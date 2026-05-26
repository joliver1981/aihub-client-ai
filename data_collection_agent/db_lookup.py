"""
Database-backed lookup resolver.

Schema authors can declare a lookup whose values come from a SQL view
(or table) accessed through one of the platform's existing database
connections:

    "lookup_data": {
      "speakers": {
        "source": "database",
        "connection_id": 42,                 # picked from the platform's
                                             # Connections registry
        "view": "vw_compliant_speakers",     # SQL view OR table — IDENTIFIER
                                             # is validated; arbitrary SQL is
                                             # NOT allowed
        "select_columns": [                  # explicit allowlist — anything
          "speaker_id", "name", "tier",      # not listed is never queried
          "products_certified", "topics_certified",
          "city", "state"
        ],
        "filter_by": {                       # optional row filters
          "products_certified__contains": "{{collected.basics.product}}",
          "active": true                     # literal values OK too
        },
        "limit": 200,                        # safety cap
        "cache_ttl_seconds": 60              # not yet wired; reserved
      }
    }

Security model:
- The identifier slots (view name, column names) are validated against
  a strict regex (alphanumerics, underscore, single dot for schema
  qualification). Anything else is refused.
- Values are bound as SQL parameters (?), never concatenated into the
  query string. SQL injection via filter values isn't possible.
- `select_columns` is the privacy boundary. Any column not in that
  list is never read. Sensitive fields (compensation, etc.) are
  excluded by simply not naming them; the SELECT never touches them.
"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# Identifier whitelist: alphanumerics, underscore, optionally one dot
# for schema qualification ("dbo.vw_speakers"). Brackets / quotes /
# semicolons / spaces all blocked.
_IDENTIFIER_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?$')

# Filter-key suffix → SQL operator
_OP_SUFFIXES = {
    '': 'eq',
    '__eq': 'eq',
    '__ne': 'ne',
    '__gt': 'gt',
    '__lt': 'lt',
    '__gte': 'gte',
    '__lte': 'lte',
    '__contains': 'contains',
    '__startswith': 'startswith',
    '__endswith': 'endswith',
    '__in': 'in',
    '__not_in': 'not_in',
    '__isnull': 'isnull',
}


def _is_safe_identifier(s: str) -> bool:
    return bool(s and _IDENTIFIER_RE.match(s))


def _split_filter_key(key: str) -> Tuple[str, str]:
    """`products__contains` → ('products', 'contains'). Default op is 'eq'."""
    for suffix, op in sorted(_OP_SUFFIXES.items(), key=lambda kv: -len(kv[0])):
        if suffix and key.endswith(suffix):
            return key[: -len(suffix)], op
    return key, 'eq'


_TEMPLATE_RE = re.compile(r'\{\{\s*([^}]+?)\s*\}\}')


def _interpolate_value(template: Any, collected_data: Dict[str, Dict[str, Any]]) -> Any:
    """If `template` is a string with {{collected.section.field}}
    placeholders, resolve them against collected_data. Returns the
    interpolated value, or None if any placeholder couldn't be
    resolved (caller treats None as 'skip this filter')."""
    if not isinstance(template, str):
        return template  # literal pass-through (bool, int, list, etc.)
    if '{{' not in template:
        return template
    missing = [False]

    def repl(m):
        path = m.group(1).strip()
        # Support {{collected.section.field}}, {{section.field}},
        # {{collected.field}} (searches all sections), or just {{field}}
        if path.startswith('collected.'):
            path = path[len('collected.'):]
        parts = path.split('.')
        if len(parts) == 2:
            section_id, field_id = parts
            v = (collected_data.get(section_id) or {}).get(field_id)
        elif len(parts) == 1:
            field_id = parts[0]
            v = None
            for section_data in collected_data.values():
                if isinstance(section_data, dict) and field_id in section_data:
                    v = section_data[field_id]
                    break
        else:
            v = None
        if v in (None, '', []):
            missing[0] = True
            return ''
        return str(v)

    out = _TEMPLATE_RE.sub(repl, template)
    if missing[0]:
        return None
    return out


def _build_where_clause(
    filter_by: Dict[str, Any],
    collected_data: Dict[str, Dict[str, Any]],
) -> Tuple[str, List[Any]]:
    """Build a parameterized WHERE clause from filter_by + collected_data.
    Returns ('col1 = ? AND col2 LIKE ?', [val1, val2]) or ('', [])."""
    parts = []
    params: List[Any] = []
    for raw_key, raw_value in (filter_by or {}).items():
        col, op = _split_filter_key(raw_key)
        if not _is_safe_identifier(col):
            logger.warning("filter_by: skipping unsafe column name %r", col)
            continue
        value = _interpolate_value(raw_value, collected_data)
        if value is None:
            # Placeholder unresolved — skip this filter (broader query).
            continue

        if op == 'eq':
            parts.append(f"{col} = ?")
            params.append(value)
        elif op == 'ne':
            parts.append(f"{col} <> ?")
            params.append(value)
        elif op == 'gt':
            parts.append(f"{col} > ?"); params.append(value)
        elif op == 'lt':
            parts.append(f"{col} < ?"); params.append(value)
        elif op == 'gte':
            parts.append(f"{col} >= ?"); params.append(value)
        elif op == 'lte':
            parts.append(f"{col} <= ?"); params.append(value)
        elif op == 'contains':
            parts.append(f"{col} LIKE ?")
            params.append(f"%{value}%")
        elif op == 'startswith':
            parts.append(f"{col} LIKE ?")
            params.append(f"{value}%")
        elif op == 'endswith':
            parts.append(f"{col} LIKE ?")
            params.append(f"%{value}")
        elif op == 'in':
            seq = value if isinstance(value, (list, tuple)) else [value]
            if not seq:
                continue
            placeholders = ', '.join(['?'] * len(seq))
            parts.append(f"{col} IN ({placeholders})")
            params.extend(seq)
        elif op == 'not_in':
            seq = value if isinstance(value, (list, tuple)) else [value]
            if not seq:
                continue
            placeholders = ', '.join(['?'] * len(seq))
            parts.append(f"{col} NOT IN ({placeholders})")
            params.extend(seq)
        elif op == 'isnull':
            if str(value).lower() in ('1', 'true', 'yes'):
                parts.append(f"{col} IS NULL")
            else:
                parts.append(f"{col} IS NOT NULL")
        else:
            logger.warning("filter_by: unknown operator %r — skipping", op)

    return (" AND ".join(parts), params)


def query_db_lookup(
    lookup_def: Dict[str, Any],
    collected_data: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Execute the database query described by `lookup_def` and return rows
    as a list of dicts (one dict per row, keys are the select_columns).

    On any failure (bad config, query error, missing connection, etc.),
    logs a warning and returns []. Never raises into caller code.
    """
    collected_data = collected_data or {}
    connection_id = lookup_def.get('connection_id')
    view = (lookup_def.get('view') or '').strip()
    select_columns = lookup_def.get('select_columns') or []
    filter_by = lookup_def.get('filter_by') or {}
    limit = lookup_def.get('limit')

    if not connection_id:
        logger.warning("DB lookup missing connection_id")
        return []
    if not _is_safe_identifier(view):
        logger.warning("DB lookup: refusing unsafe/missing view name %r", view)
        return []

    safe_columns = [c for c in select_columns if _is_safe_identifier(c)]
    if not safe_columns:
        logger.warning(
            "DB lookup: no safe columns in select_columns (%s). At least one "
            "valid column name is required as the privacy allowlist.",
            select_columns,
        )
        return []
    rejected = [c for c in select_columns if not _is_safe_identifier(c)]
    if rejected:
        logger.warning("DB lookup: rejected unsafe column names %s", rejected)

    try:
        from DataUtils import get_database_connection_string
        import pyodbc
    except Exception as e:
        logger.warning("DB lookup: platform DB plumbing unavailable (%s)", e)
        return []

    try:
        conn_str, _conn_id, _db_type = get_database_connection_string(connection_id)
    except Exception as e:
        logger.warning("DB lookup: could not resolve connection_id=%s: %s", connection_id, e)
        return []
    if not conn_str:
        logger.warning("DB lookup: no connection string for connection_id=%s", connection_id)
        return []

    where_sql, where_params = _build_where_clause(filter_by, collected_data)
    sql = f"SELECT {', '.join(safe_columns)} FROM {view}"
    if where_sql:
        sql += f" WHERE {where_sql}"

    # Cap rows so a misconfigured filter can't yank a multi-million-row table
    # into the prompt context.
    try:
        cap = int(limit) if limit is not None else 500
    except (TypeError, ValueError):
        cap = 500
    if cap > 0:
        # SQL Server: TOP must be in SELECT. Rebuild rather than appending LIMIT.
        sql = f"SELECT TOP {cap} {', '.join(safe_columns)} FROM {view}"
        if where_sql:
            sql += f" WHERE {where_sql}"

    try:
        conn = pyodbc.connect(conn_str)
        try:
            cursor = conn.cursor()
            cursor.execute(sql, where_params)
            cols = [c[0] for c in cursor.description]
            rows = cursor.fetchall()
            return [dict(zip(cols, list(row))) for row in rows]
        finally:
            try: conn.close()
            except Exception: pass
    except Exception as e:
        logger.warning(
            "DB lookup query failed: %s\n  SQL: %s\n  PARAMS: %s",
            e, sql, where_params,
        )
        return []
