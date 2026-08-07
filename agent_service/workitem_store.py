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

A2 visibility (documented simplification): an item addressed to a user is
visible to that user only; an item with no user address is a group/anyone item
visible to all Developer+ users until claimed (claiming hides it from others
until released). True Groups-membership scoping arrives with the envelope
enrichment in A3.
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


def list_items(user_id: int, *, include_closed: bool = False) -> list:
    """Items this user can see (A2 visibility rules — see module docstring)."""
    q = ("SELECT * FROM work_items WHERE "
         "(addressed_user IS NULL OR addressed_user = ?) "
         "AND from_kind != 'readthrough' ")  # shadow rows exist only for threads
    if not include_closed:
        q += "AND status IN ('open', 'claimed') "
    q += "ORDER BY priority DESC, created_at DESC LIMIT 200"
    with _connect() as c:
        rows = [_row_to_dict(r) for r in c.execute(q, (int(user_id),)).fetchall()]
    # Claimed group items are hidden from everyone but the claimant.
    out = []
    for r in rows:
        if (r["status"] == "claimed" and r.get("addressed_user") is None
                and r.get("claimed_by") not in (None, int(user_id))):
            continue
        out.append(r)
    return out


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
