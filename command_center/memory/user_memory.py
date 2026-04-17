"""
Command Center — User Memory (Preferences)
===============================================
Per-user preference CRUD operations.

Route-level memory (query→agent routing, success tracking) has been moved to
route_memory.py.  This module retains only:
  - Preference read/write/delete (simple key-value pairs, no LLM)
  - User context storage (role, department, etc.)
  - get_all_memories() for backward compatibility with the recall tool

Uses SQL Server with RLS for multi-tenant safety.
Falls back to in-memory storage when DB is unavailable.
"""

import json
import logging
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

# Add project root to path for imports
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from command_center.memory.memory_models import MemoryEntry

logger = logging.getLogger(__name__)

# In-memory fallback storage
_memory_store: Dict[int, List[MemoryEntry]] = {}
_use_db = True  # Try DB first, fall back to memory if unavailable


# ─── Database Helper ──────────────────────────────────────────────────────

def _cc_memory_db_execute(query, params=None, fetch=False):
    """
    Execute a query against cc_UserMemory with proper RLS context.

    Pattern matches DataUtils.py:
      1. get_db_connection()
      2. EXEC tenant.sp_setTenantContext with API_KEY
      3. Execute the actual query
      4. Commit + close

    Returns:
      - If fetch=True: list of row tuples
      - If fetch=False: None
    """
    try:
        from CommonUtils import get_db_connection

        conn = get_db_connection()
        cursor = conn.cursor()

        # Set RLS tenant context
        api_key = os.environ.get("API_KEY") or os.environ.get("AI_HUB_API_KEY") or ""
        cursor.execute("EXEC tenant.sp_setTenantContext ?", api_key)

        # Execute the actual query
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)

        if fetch:
            rows = cursor.fetchall()
            conn.commit()  # Commit after fetch — needed for INSERT/UPDATE with OUTPUT
            conn.close()
            return rows
        else:
            conn.commit()
            conn.close()
            return None

    except Exception as e:
        logger.error(f"Database error in _cc_memory_db_execute: {e}")
        global _use_db
        _use_db = False  # Fall back to in-memory
        raise


# ─── Preference CRUD ─────────────────────────────────────────────────────

def get_preferences(user_id: int) -> Dict[str, Any]:
    """Get user preferences from memory."""
    if not _use_db:
        entries = _memory_store.get(user_id, [])
        prefs = {}
        for entry in entries:
            if entry.memory_type == "preference":
                prefs[entry.memory_key] = entry.memory_value
        return prefs

    try:
        rows = _cc_memory_db_execute(
            "SELECT memory_key, memory_value FROM cc_UserMemory WHERE user_id = ? AND memory_type = 'preference'",
            [user_id],
            fetch=True,
        )

        prefs = {}
        for row in rows:
            memory_key, memory_value_json = row
            try:
                prefs[memory_key] = json.loads(memory_value_json) if isinstance(memory_value_json, str) else memory_value_json
            except Exception:
                prefs[memory_key] = memory_value_json
        return prefs

    except Exception as e:
        logger.error(f"Failed to get preferences from DB: {e}")
        return {}


def update_preference(user_id: int, key: str, value: Any):
    """Update a user preference."""
    memory_value = value if isinstance(value, dict) else {"value": value}

    if not _use_db:
        entries = _memory_store.setdefault(user_id, [])
        for entry in entries:
            if entry.memory_type == "preference" and entry.memory_key == key:
                entry.memory_value = memory_value
                return
        entries.append(MemoryEntry(
            user_id=user_id,
            memory_type="preference",
            memory_key=key,
            memory_value=memory_value,
            created_at=datetime.utcnow(),
            last_used=datetime.utcnow(),
        ))
        return

    try:
        rows = _cc_memory_db_execute(
            "SELECT id FROM cc_UserMemory WHERE user_id = ? AND memory_type = 'preference' AND memory_key = ?",
            [user_id, key],
            fetch=True,
        )

        if rows and len(rows) > 0:
            _cc_memory_db_execute(
                "UPDATE cc_UserMemory SET memory_value = ?, last_used = GETUTCDATE() WHERE id = ?",
                [json.dumps(memory_value), rows[0][0]],
            )
        else:
            _cc_memory_db_execute(
                "INSERT INTO cc_UserMemory (user_id, memory_type, memory_key, memory_value, usage_count, created_at, last_used) "
                "VALUES (?, 'preference', ?, ?, 0, GETUTCDATE(), GETUTCDATE())",
                [user_id, key, json.dumps(memory_value)],
            )
    except Exception as e:
        logger.error(f"Failed to update preference in DB: {e}")
        # Fall back to in-memory
        entries = _memory_store.setdefault(user_id, [])
        for entry in entries:
            if entry.memory_type == "preference" and entry.memory_key == key:
                entry.memory_value = memory_value
                return
        entries.append(MemoryEntry(
            user_id=user_id,
            memory_type="preference",
            memory_key=key,
            memory_value=memory_value,
            created_at=datetime.utcnow(),
            last_used=datetime.utcnow(),
        ))


def delete_preference(user_id: int, key: str) -> bool:
    """Delete a single preference by its key. Returns True if found and deleted."""
    if not _use_db:
        entries = _memory_store.get(user_id, [])
        original_len = len(entries)
        _memory_store[user_id] = [e for e in entries if not (e.memory_type == "preference" and e.memory_key == key)]
        return len(_memory_store[user_id]) < original_len

    try:
        _cc_memory_db_execute(
            "DELETE FROM cc_UserMemory WHERE user_id = ? AND memory_type = 'preference' AND memory_key = ?",
            [user_id, key],
        )
        return True
    except Exception as e:
        logger.error(f"Failed to delete preference from DB: {e}")
        entries = _memory_store.get(user_id, [])
        original_len = len(entries)
        _memory_store[user_id] = [e for e in entries if not (e.memory_type == "preference" and e.memory_key == key)]
        return len(_memory_store[user_id]) < original_len


def delete_all_preferences(user_id: int) -> int:
    """Delete all preferences for a user. Returns count deleted."""
    if not _use_db:
        entries = _memory_store.get(user_id, [])
        prefs = [e for e in entries if e.memory_type == "preference"]
        _memory_store[user_id] = [e for e in entries if e.memory_type != "preference"]
        return len(prefs)

    try:
        rows = _cc_memory_db_execute(
            "SELECT COUNT(*) FROM cc_UserMemory WHERE user_id = ? AND memory_type = 'preference'",
            [user_id],
            fetch=True,
        )
        count = rows[0][0] if rows else 0
        _cc_memory_db_execute(
            "DELETE FROM cc_UserMemory WHERE user_id = ? AND memory_type = 'preference'",
            [user_id],
        )
        if user_id in _memory_store:
            _memory_store[user_id] = [e for e in _memory_store[user_id] if e.memory_type != "preference"]
        return count
    except Exception as e:
        logger.error(f"Failed to delete all preferences from DB: {e}")
        entries = _memory_store.get(user_id, [])
        prefs = [e for e in entries if e.memory_type == "preference"]
        _memory_store[user_id] = [e for e in entries if e.memory_type != "preference"]
        return len(prefs)


# ─── User Context ─────────────────────────────────────────────────────────

def set_user_context(user_id: int, key: str, value: Any):
    """Store user context information (role, department, etc.)."""
    update_preference(user_id, f"context:{key}", {"value": value})


def get_user_context(user_id: int) -> Dict[str, Any]:
    """Get all context entries for a user."""
    prefs = get_preferences(user_id)
    context = {}
    for key, val in prefs.items():
        if key.startswith("context:"):
            ctx_key = key[8:]  # Remove "context:" prefix
            context[ctx_key] = val.get("value", val) if isinstance(val, dict) else val
    return context


# ─── Backward Compatibility ───────────────────────────────────────────────

def get_all_memories(user_id: int) -> List[MemoryEntry]:
    """
    Fetch ALL memory entries (preferences only now) for a user.
    Kept for backward compatibility with the recall_all_memories tool.
    """
    if not _use_db:
        return list(_memory_store.get(user_id, []))

    try:
        rows = _cc_memory_db_execute(
            "SELECT id, memory_type, memory_key, memory_value, usage_count, "
            "ISNULL(success_count, 0), ISNULL(fail_count, 0), smart_label, "
            "last_used, created_at "
            "FROM cc_UserMemory WHERE user_id = ? "
            "ORDER BY memory_type, last_used DESC",
            [user_id],
            fetch=True,
        )
        if not rows:
            return []

        entries = []
        for row in rows:
            (mid, mtype, mkey, mval_json, usage, s_count, f_count,
             label, last_used, created_at) = row
            try:
                mval = json.loads(mval_json) if isinstance(mval_json, str) else (mval_json or {})
            except Exception:
                mval = {}
            entries.append(MemoryEntry(
                id=mid, user_id=user_id, memory_type=mtype,
                memory_key=mkey, memory_value=mval,
                usage_count=usage or 1, success_count=s_count or 0,
                fail_count=f_count or 0, smart_label=label,
                last_used=last_used, created_at=created_at,
            ))
        return entries
    except Exception as e:
        logger.error(f"[get_all_memories] DB error: {e}")
        return list(_memory_store.get(user_id, []))
