"""
"Available to users" visibility for MCP servers on My Connections.

Migration 020 adds ``MCPServers.available_to_users BIT NOT NULL DEFAULT 0``.
Migrations on this platform are applied by hand with a DDL-capable login (the
app's own login has no ALTER rights), so a deployed build may run for a while —
or forever, on a customer install — without the column. Every read here
therefore tolerates a missing column and FALLS BACK TO VISIBLE, which is
exactly today's behaviour: a failed migration must never hide connections
that already work. Follows the precedent at mcp_agent_tools.py (the
``allow_personal_connections`` read).

Two enforcement points use this module:
  * my_connections_routes.list_my_connections  — the listing filter
  * mcp_routes.oauth_authorize                  — direct-URL authorize by a
                                                  non-admin
Filtering only the listing would not be enforcement.
"""
import logging
import os
import time
from typing import Optional

from CommonUtils import get_db_connection

logger = logging.getLogger(__name__)

COLUMN_NAME = 'available_to_users'
_CACHE_TTL_SECONDS = 300          # a freshly applied migration is noticed within 5 min
_column_cache = {'checked_at': 0.0, 'present': None}


def _set_tenant_context(cursor):
    cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))


def has_available_to_users_column(cursor=None, force: bool = False) -> bool:
    """True when MCPServers has the ``available_to_users`` column.

    Cached per process for 5 minutes. Any error → False (treated as "column
    missing", i.e. the visible fallback), never an exception.
    """
    now = time.time()
    if not force and _column_cache['present'] is not None \
            and now - _column_cache['checked_at'] < _CACHE_TTL_SECONDS:
        return bool(_column_cache['present'])

    own_conn = None
    try:
        if cursor is None:
            own_conn = get_db_connection()
            cursor = own_conn.cursor()
            _set_tenant_context(cursor)
        cursor.execute("""
            SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'MCPServers' AND COLUMN_NAME = ?
        """, COLUMN_NAME)
        row = cursor.fetchone()
        present = bool(row and int(row[0]) > 0)
    except Exception as e:
        logger.warning(f"Could not check MCPServers.{COLUMN_NAME} (treating as missing): {e}")
        present = False
    finally:
        if own_conn is not None:
            try:
                own_conn.close()
            except Exception:
                pass

    _column_cache['present'] = present
    _column_cache['checked_at'] = now
    return present


def reset_cache():
    """Test hook."""
    _column_cache['present'] = None
    _column_cache['checked_at'] = 0.0


def server_available_to_users(server_id: int, cursor=None) -> bool:
    """Is this server published to users on My Connections?

    Missing column, missing row, or any error → True (visible), so a failed
    migration preserves today's behaviour instead of hiding everyone's
    working connections. Row-missing → True keeps the decision on the
    existing 404/config checks rather than a misleading "not published".
    """
    own_conn = None
    try:
        if cursor is None:
            own_conn = get_db_connection()
            cursor = own_conn.cursor()
            _set_tenant_context(cursor)
        if not has_available_to_users_column(cursor):
            return True
        cursor.execute(f"SELECT {COLUMN_NAME} FROM MCPServers WHERE server_id = ?", server_id)
        row = cursor.fetchone()
        if not row:
            return True
        return bool(row[0])
    except Exception as e:
        logger.warning(f"Could not read {COLUMN_NAME} for server {server_id} (treating as visible): {e}")
        return True
    finally:
        if own_conn is not None:
            try:
                own_conn.close()
            except Exception:
                pass


def coerce_flag(value) -> Optional[int]:
    """Normalise a JSON/form flag to 1/0, or None when absent/unrecognised."""
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return 1 if int(value) else 0
    text = str(value).strip().lower()
    if text in ('1', 'true', 'yes', 'on'):
        return 1
    if text in ('0', 'false', 'no', 'off', ''):
        return 0
    return None
