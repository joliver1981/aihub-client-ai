"""Connection access for delegated regular users (Option A, 2026-09-22).

Which platform data connections may this user touch through The Agent (or any
other service that presents a signed X-AIHub-User assertion)? Exactly the ones
behind the Data Assistants (data agents) shared with the user's groups — the
rule classic mode already applies on the Data Assistants page
(DataUtils.select_user_agents_and_connections):

    user -> UserGroups -> AgentGroups -> Agents(is_data_agent=1, enabled=1)
         -> AgentConnections -> Connections

Contract — EXACTLY the DataUtils.accessible_agent_ids / doc_search_v3.acl idiom:
    None   = unrestricted (role >= CONNECTION_ACL_UNRESTRICTED_ROLE, default 2:
             Developers already see every connection on the Connections page,
             and "can list it, can ask it" is the agent-chat rule)
    [ids]  = exactly these connection ids (may be empty)
    []     = DENY ALL, and it FAILS CLOSED: any error resolving grants returns []

⚠ Never write `if allowed:` — [] is falsy and would read as "no filter".
Callers test `is None` for unrestricted and membership otherwise
(app._caller_connection_scope / _connection_access_refusal).

Spec: docs/handoff-the-agent-connection-acl.md (§5 item 1).
"""
import logging
import os
from typing import List, Optional

logger = logging.getLogger(__name__)

_FLOOR_ENV = "CONNECTION_ACL_UNRESTRICTED_ROLE"
_DEFAULT_FLOOR = 2


def unrestricted_from_role() -> int:
    """Callers whose verified role is >= this are never filtered. Default 2
    (Developer). Read at call time so a test / an install can override it."""
    try:
        return int(os.getenv(_FLOOR_ENV, str(_DEFAULT_FLOOR)) or _DEFAULT_FLOOR)
    except (TypeError, ValueError):
        return _DEFAULT_FLOOR


def _connect():
    import pyodbc
    from CommonUtils import get_db_connection_string
    conn = pyodbc.connect(get_db_connection_string())
    cur = conn.cursor()
    cur.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
    return conn, cur


_SQL = """
SELECT DISTINCT ac.connection_id
FROM dbo.AgentConnections ac
JOIN dbo.Agents a       ON a.id = ac.agent_id
JOIN dbo.AgentGroups ag ON ag.agent_id = a.id
JOIN dbo.UserGroups ug  ON ug.group_id = ag.group_id
WHERE ug.user_id = ?
  AND a.is_data_agent = 1
  AND a.enabled = 1
"""


def accessible_connection_ids(user_id, user_role=None) -> Optional[List[int]]:
    """The allow list for one user. None = unrestricted; [] = deny-all (fail closed)."""
    try:
        role = int(user_role) if user_role is not None else 0
    except (TypeError, ValueError):
        role = 0
    if role >= unrestricted_from_role():
        return None
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        logger.warning(f"[conn-acl] unusable user id {user_id!r} — deny-all")
        return []
    conn = None
    try:
        conn, cur = _connect()
        cur.execute(_SQL, uid)
        ids = sorted({int(r[0]) for r in cur.fetchall() if r[0] is not None})
        cur.close()
        return ids
    except Exception as e:
        logger.warning(f"[conn-acl] accessible_connection_ids({user_id}) failed — deny-all: {e}")
        return []  # fail closed — never fall through to all-access on error
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


def deny_all(allowed) -> bool:
    """True when the resolved allow list means NO ACCESS ([]), False for
    unrestricted (None) or a non-empty list."""
    return allowed is not None and len(allowed) == 0
