"""
My Work — the work-item store (A2).

Design constraints (learned from automations/approval_store.py, verified live
there): the platform DB is Azure SQL and the app login has NO DDL rights, so
new tables are impossible. Following the platform's own sidecar precedent,
this store is service-owned SQLite under data/agent/ — agent_service is the
single writer; anything else that wants to raise work items does it through
The Agent's REST API.

Two tables:
  work_items          — the queue (verb, payload, addressing, status, response)
  work_item_events    — the lifecycle log, one row per transition, DAY-1 by
                        design: created/claimed/released/responded/closed and
                        thread_message rows. This is the dataset the pinned
                        Flow dashboard renders later; nothing else needs to be
                        instrumented after the fact.

Verbs (from the approved design): approve_deny, review, provide_input,
edit_and_return, acknowledge, do_offline.

Visibility (one rule, the same one workflow and automation approvals use):
  * addressed to a USER  -> that user only
  * addressed to a GROUP -> members of that group only (addressed_group holds
    the platform Groups.id as text; membership is read live, so leaving the
    group removes access at once). Any member may claim it — claiming hides
    it from the rest of the group until released.
  * addressed to nobody  -> the shared pool: Developer+ only, until claimed.
Role never widens a user- or group-addressed item: an admin outside the group
does not see it (james 2026-09-24). Group routing was the A2 "documented
simplification" deferred to A3; it was finished 2026-09-24.

The Developer+ floor on the pool is enforced HERE, in list_items (role is a
required argument), not at the front door. It used to hold implicitly because
The Agent itself was Developer+ only; AGENT_ALLOW_ALL_USERS=true removed that
guarantee and regular users saw the shared pool (RU pack finding F-7,
2026-09-07). visible_to() is the same rule for ONE item — the routes that act
on an item by id (claim / release / respond / thread) check it first.
"""

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from agent_config import DATA_DIR, logger

DB_PATH = os.path.join(DATA_DIR, "mywork.db")
_LOCK = threading.Lock()

VERBS = {"approve_deny", "review", "provide_input", "edit_and_return",
         "acknowledge", "do_offline"}
OPEN_STATUSES = ("open", "claimed")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init() -> None:
    with _LOCK, _connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS work_items (
            work_item_id   TEXT PRIMARY KEY,
            verb           TEXT NOT NULL,
            title          TEXT NOT NULL,
            summary        TEXT DEFAULT '',
            payload        TEXT DEFAULT '{}',
            addressed_user INTEGER,
            addressed_group TEXT,
            from_kind      TEXT NOT NULL,
            from_ref       TEXT DEFAULT '',
            blocks_kind    TEXT,
            blocks_ref     TEXT,
            status         TEXT NOT NULL DEFAULT 'open',
            priority       INTEGER NOT NULL DEFAULT 0,
            due_at         TEXT,
            created_at     TEXT NOT NULL,
            created_by     TEXT DEFAULT '',
            claimed_by     INTEGER,
            claimed_at     TEXT,
            responded_by   INTEGER,
            responded_at   TEXT,
            response       TEXT,
            thread_session TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_items_status
            ON work_items(status, addressed_user);
        CREATE TABLE IF NOT EXISTS work_item_events (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            work_item_id TEXT NOT NULL REFERENCES work_items(work_item_id),
            event        TEXT NOT NULL,
            actor        TEXT DEFAULT '',
            at           TEXT NOT NULL,
            data         TEXT DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS ix_events_item
            ON work_item_events(work_item_id, id);
        """)
    logger.info(f"work-item store ready at {DB_PATH}")


def _event(c: sqlite3.Connection, item_id: str, event: str, actor,
           data: Optional[dict] = None) -> None:
    c.execute("INSERT INTO work_item_events (work_item_id, event, actor, at, data) "
              "VALUES (?, ?, ?, ?, ?)",
              (item_id, event, str(actor or ""), _now(),
               json.dumps(data or {}, default=str)))


def _row_to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in ("payload", "response"):
        if d.get(k):
            try:
                d[k] = json.loads(d[k])
            except Exception:
                pass
    return d


def create_item(verb: str, title: str, *, summary: str = "",
                payload: Optional[dict] = None, addressed_user=None,
                addressed_group: Optional[str] = None, from_kind: str = "agent",
                from_ref: str = "", blocks_kind: Optional[str] = None,
                blocks_ref: Optional[str] = None, priority: int = 0,
                due_at: Optional[str] = None, created_by: str = "") -> dict:
    if verb not in VERBS:
        raise ValueError(f"unknown verb '{verb}' (valid: {sorted(VERBS)})")
    if addressed_group not in (None, ""):
        if addressed_user is not None:
            raise ValueError("address an item to a user OR a group, not both")
        try:
            addressed_group = str(int(addressed_group))
        except (TypeError, ValueError):
            raise ValueError(f"addressed_group must be a platform group id, "
                             f"got '{addressed_group}'")
    else:
        addressed_group = None
    item_id = str(uuid.uuid4())
    with _LOCK, _connect() as c:
        c.execute(
            "INSERT INTO work_items (work_item_id, verb, title, summary, payload, "
            "addressed_user, addressed_group, from_kind, from_ref, blocks_kind, "
            "blocks_ref, status, priority, due_at, created_at, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)",
            (item_id, verb, title, summary,
             json.dumps(payload or {}, default=str),
             int(addressed_user) if addressed_user is not None else None,
             addressed_group, from_kind, from_ref, blocks_kind, blocks_ref,
             int(priority), due_at, _now(), created_by))
        _event(c, item_id, "created", created_by,
               {"verb": verb, "title": title,
                "addressed_user": addressed_user,
                "addressed_group": addressed_group})
    return get_item(item_id)


def get_item(item_id: str) -> Optional[dict]:
    with _connect() as c:
        r = c.execute("SELECT * FROM work_items WHERE work_item_id = ?",
                      (item_id,)).fetchone()
        return _row_to_dict(r) if r else None


def _group_keys(group_ids) -> list:
    """Group ids as the text addressed_group stores ('7'); junk dropped."""
    out = []
    for g in group_ids or []:
        try:
            out.append(str(int(g)))
        except (TypeError, ValueError):
            continue
    return out


# The shared pool = addressed to NOBODY: no user AND no group. A group item
# also has addressed_user NULL, so "addressed_user IS NULL" alone would leak
# every group's items to every Developer+.
_POOL_SQL = ("(addressed_user IS NULL AND "
             "(addressed_group IS NULL OR addressed_group = ''))")


def list_items(user_id: int, *, role: int, group_ids=None,
               include_closed: bool = False) -> list:
    """Items this user can see (visibility rules — see module docstring).

    `role` is the caller's platform role (1 user, 2 developer, 3 admin) and is
    REQUIRED, not defaulted: the unaddressed "anyone" pool is a Developer+
    audience, and every caller (GET /api/work/list, the list_my_work tool)
    converges here, so this is the one place the rule cannot drift from.
    role < 2 -> no shared-pool items at all.
    `group_ids` = the caller's platform group memberships; items addressed to
    one of those groups are included for ANY role. Omitted -> no group items
    (fails closed).
    """
    gkeys = _group_keys(group_ids)
    clauses, params = ["addressed_user = ?"], [int(user_id)]
    # An item the caller HOLDS stays theirs until they release or answer it —
    # even after leaving the group (or a role change): the claim hides it from
    # everyone else, so without this it would be stranded with no one able to
    # act on it.
    clauses.append("(addressed_user IS NULL AND status = 'claimed' AND claimed_by = ?)")
    params.append(int(user_id))
    if gkeys:
        clauses.append("(addressed_user IS NULL AND addressed_group IN ("
                       + ",".join("?" * len(gkeys)) + "))")
        params += gkeys
    if int(role or 0) >= 2:
        clauses.append(_POOL_SQL)
    q = "SELECT * FROM work_items WHERE (" + " OR ".join(clauses) + ") "
    q += "AND from_kind != 'readthrough' "  # shadow rows exist only for threads
    if not include_closed:
        q += "AND status IN ('open', 'claimed') "
    q += "ORDER BY priority DESC, created_at DESC LIMIT 200"
    with _connect() as c:
        rows = [_row_to_dict(r) for r in c.execute(q, params).fetchall()]
    # Claimed group / pool items are hidden from everyone but the claimant.
    out = []
    for r in rows:
        if (r["status"] == "claimed" and r.get("addressed_user") is None
                and r.get("claimed_by") not in (None, int(user_id))):
            continue
        out.append(r)
    return out


def visible_to(item: Optional[dict], user_id, *, role, group_ids=None) -> bool:
    """list_items' rule for ONE item: may this user see (and so act on) it?
    Routes that take an item id (claim, release, respond, thread) check this
    first, so an id alone never reaches another user's or group's item."""
    if not item:
        return False
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return False
    if item.get("addressed_user") is not None:
        return int(item["addressed_user"]) == uid
    # the claimant keeps what they hold (see list_items)
    if item.get("status") == "claimed" and item.get("claimed_by") is not None:
        try:
            if int(item["claimed_by"]) == uid:
                return True
        except (TypeError, ValueError):
            pass
    group = str(item.get("addressed_group") or "").strip()
    if group:
        return group in _group_keys(group_ids)
    try:
        return int(role or 0) >= 2
    except (TypeError, ValueError):
        return False


def claim(item_id: str, user_id: int) -> tuple:
    with _LOCK, _connect() as c:
        r = c.execute("SELECT status, addressed_user, claimed_by FROM work_items "
                      "WHERE work_item_id = ?", (item_id,)).fetchone()
        if not r:
            return None, "not found"
        if r["addressed_user"] is not None:
            return None, "personal items don't need claiming"
        if r["status"] == "claimed" and r["claimed_by"] != int(user_id):
            return None, "already claimed by someone else"
        if r["status"] not in OPEN_STATUSES:
            return None, f"item is {r['status']}"
        c.execute("UPDATE work_items SET status='claimed', claimed_by=?, "
                  "claimed_at=? WHERE work_item_id=?",
                  (int(user_id), _now(), item_id))
        _event(c, item_id, "claimed", user_id)
    return get_item(item_id), None


def release(item_id: str, user_id: int) -> tuple:
    with _LOCK, _connect() as c:
        r = c.execute("SELECT status, claimed_by FROM work_items "
                      "WHERE work_item_id = ?", (item_id,)).fetchone()
        if not r:
            return None, "not found"
        if r["status"] != "claimed" or r["claimed_by"] != int(user_id):
            return None, "you don't hold the claim on this item"
        c.execute("UPDATE work_items SET status='open', claimed_by=NULL, "
                  "claimed_at=NULL WHERE work_item_id=?", (item_id,))
        _event(c, item_id, "released", user_id)
    return get_item(item_id), None


def respond(item_id: str, user_id: int, response: dict) -> tuple:
    """Record the human's response and close the item. First response wins."""
    with _LOCK, _connect() as c:
        r = c.execute("SELECT status, addressed_user, claimed_by FROM work_items "
                      "WHERE work_item_id = ?", (item_id,)).fetchone()
        if not r:
            return None, "not found"
        if r["status"] not in OPEN_STATUSES:
            return None, f"item already {r['status']}"
        if (r["addressed_user"] is None and r["status"] == "claimed"
                and r["claimed_by"] != int(user_id)):
            return None, "claimed by someone else"
        now = _now()
        c.execute("UPDATE work_items SET status='closed', responded_by=?, "
                  "responded_at=?, response=? WHERE work_item_id=?",
                  (int(user_id), now, json.dumps(response, default=str), item_id))
        _event(c, item_id, "responded", user_id, response)
        _event(c, item_id, "closed", user_id)
    return get_item(item_id), None


def log_decision(item_id: str, actor, decision: str, comments: str = "",
                 via: str = "my_work") -> None:
    """Record a decision made through My Work on the lifecycle log (used for
    read-through items whose row-of-record lives elsewhere)."""
    with _LOCK, _connect() as c:
        _event(c, item_id, "responded", actor,
               {"decision": decision, "comments": comments, "via": via})
        _event(c, item_id, "closed", actor)


def shadow_item(source: str, ref: str, title: str) -> dict:
    """Get-or-create the thread-anchor row for a read-through item (workflow/
    automation/email rows live elsewhere; this row exists only so side-thread
    messages and lifecycle mirrors have somewhere to attach)."""
    with _connect() as c:
        r = c.execute("SELECT work_item_id FROM work_items WHERE "
                      "from_kind='readthrough' AND blocks_kind=? AND blocks_ref=?",
                      (source, str(ref))).fetchone()
    if r:
        return get_item(r["work_item_id"])
    return create_item("review", title, from_kind="readthrough",
                       blocks_kind=source, blocks_ref=str(ref),
                       created_by="readthrough")


def set_thread_session(item_id: str, session_id: str) -> None:
    with _LOCK, _connect() as c:
        c.execute("UPDATE work_items SET thread_session=? WHERE work_item_id=?",
                  (session_id, item_id))


def append_thread(item_id: str, role: str, text: str, actor="") -> None:
    with _LOCK, _connect() as c:
        _event(c, item_id, "thread_message", actor, {"role": role, "text": text})


def list_events(item_id: str) -> list:
    with _connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, event, actor, at, data FROM work_item_events "
            "WHERE work_item_id = ? ORDER BY id", (item_id,)).fetchall()]


def thread(item_id: str) -> list:
    out = []
    for ev in list_events(item_id):
        if ev["event"] == "thread_message":
            try:
                d = json.loads(ev["data"])
            except Exception:
                d = {}
            out.append({"role": d.get("role", "?"), "text": d.get("text", ""),
                        "at": ev["at"]})
    return out
