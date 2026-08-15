"""Category-based document access: which document types may this user read?

Chain (all live tables, seeded 1:1 by migration 016 so enabling this changes
nothing until an admin narrows a grant):

    user -> UserGroups -> DocumentCategoryGroups -> category
         -> DocumentTypeCategories (status='active') -> document_type

Contract — EXACTLY the platform's accessible_agent_ids idiom (DataUtils.py:1743):
    None   = unrestricted (admin role >= 3, or no identity presented — today's
             posture; identity-less internal callers keep working)
    [types]= exactly these document types
    []     = DENY ALL, and it FAILS CLOSED: any error resolving grants returns []

⚠ THE FAIL-OPEN TRAP THIS MODULE EXISTS TO GUARD:
DocUtils._build_doc_type_filter treats an EMPTY allow list as "no filter" —
`if allowed_document_types:` is falsy for [] — so handing [] to the legacy engine
grants EVERYTHING. Callers must check deny_all() and stop BEFORE the engine.
tests/unit/test_v3_acl.py locks this with the first test in the file.
"""

import logging
import os
from typing import List, Optional


def _connect():
    import pyodbc
    from CommonUtils import get_db_connection_string
    conn = pyodbc.connect(get_db_connection_string())
    cur = conn.cursor()
    cur.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
    return conn, cur


def accessible_document_types(user_id, user_role=None) -> Optional[List[str]]:
    """The allow list for one user. None = unrestricted; [] = deny-all (fail closed).

    A document_type with a PENDING category assignment (AI-assigned, awaiting
    review) is excluded here — pending means admin-only, which for non-admins is
    simply "not in the list".
    """
    try:
        role = int(user_role) if user_role is not None else None
    except (TypeError, ValueError):
        role = None
    if role is not None and role >= 3:
        return None          # admin — unrestricted, tables not consulted

    if user_id in (None, '', 0, '0'):
        # No identity presented. Today every internal caller is identity-less;
        # restricting them would break scheduler/automation flows that have no
        # user. Unrestricted UNTIL enforcement of identity is switched on.
        if os.getenv('DOC_V3_REQUIRE_IDENTITY', 'false').lower() == 'true':
            return []
        return None

    try:
        conn, cur = _connect()
    except Exception as e:
        logging.error(f"doc_search_v3.acl: DB unavailable ({e}) — DENY ALL")
        return []
    try:
        cur.execute(
            """SELECT DISTINCT tc.document_type
               FROM DocumentTypeCategories tc
               JOIN DocumentCategoryGroups cg ON cg.category_id = tc.category_id
               JOIN UserGroups             ug ON ug.group_id    = cg.group_id
               WHERE ug.user_id = ? AND tc.status = 'active'
               ORDER BY tc.document_type""", int(user_id))
        return [r[0] for r in cur.fetchall()]
    except Exception as e:
        logging.error(f"doc_search_v3.acl: grant resolution failed ({e}) — DENY ALL")
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def deny_all(allowed) -> bool:
    """True when the resolved allow list means NO ACCESS.

    This is the check every caller must make before handing `allowed` to the
    legacy engine, whose empty-list handling is fail-open.
    """
    return allowed is not None and len(allowed) == 0


def managed_category_ids(user_id) -> List[int]:
    """Categories this user's groups STEWARD (can_manage=1) — drives who receives
    My Work items for new type assignments and who may recategorise types."""
    try:
        conn, cur = _connect()
    except Exception:
        return []
    try:
        cur.execute(
            """SELECT DISTINCT cg.category_id
               FROM DocumentCategoryGroups cg
               JOIN UserGroups ug ON ug.group_id = cg.group_id
               WHERE ug.user_id = ? AND cg.can_manage = 1""", int(user_id))
        return [r[0] for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass
