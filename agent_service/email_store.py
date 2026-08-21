"""
Agent Email (A6) — per-USER inbound addresses for The Agent.

Legacy general agents already receive email: Mailgun -> cloud API webhook ->
cloud SQL -> the on-prem EmailAgentDispatcher POLLS the per-tenant queue and
matches rows in AgentEmailAddresses (keyed by AGENT id). A6 adds per-USER
addresses for The Agent as a SEPARATE, additive consumer of the SAME cloud
feed:

- addresses live HERE (service-owned SQLite sidecar), NOT in
  AgentEmailAddresses — a user row there would be loaded by the legacy
  dispatcher's address map and double-processed. The legacy dispatcher
  skips our addresses harmlessly (unknown recipient -> debug log, no ack).
- address format matches the cloud parser UNCHANGED:
  {prefix}-agent.{tenant_id}@{domain} — the cloud treats the last
  dot-segment as the numeric TenantId, so "-agent" is just part of the
  prefix (live-verified 2026-08-07: tenant_id=1, domain=mail.everiai.ai).
- dedupe is OURS: the cloud poll returns everything until 3-day expiry
  (is_delivered is ignored by the poll query), so each consumer keeps its
  own processed ledger. processed_emails is that ledger.
"""

import re
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional

from workitem_store import DB_PATH  # share the mywork.db file
from agent_config import logger

_LOCK = threading.Lock()

PREFIX_RE = re.compile(r"^[a-z0-9-]{1,40}$")   # NO dots: the cloud parses the
                                               # last dot-segment as tenant id


def sanitize_prefix(raw: str) -> str:
    """Normalize any user-supplied prefix into an email-safe one (James's
    rule: fix it, don't reject it): lowercase; spaces/underscores -> hyphen;
    strip everything else including dots (the cloud router parses dots);
    collapse runs of hyphens; trim. Returns '' if nothing survives."""
    s = str(raw or "").strip().lower()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"[^a-z0-9-]", "", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s[:40]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# Per-address options (James, 2026-08-09 — parity with the legacy agent email
# config): auto_send bypasses the My Work approval; outbound_enabled is the
# outbound kill switch; notify_on_receive emails notification_email on each
# inbound; cooldown_minutes overrides the env default (NULL = env);
# reply_instructions = standing personality/instructions injected into email
# sessions. Added via additive ALTER TABLE so existing rows keep working.
_OPTION_COLUMNS = {
    "auto_send": "INTEGER NOT NULL DEFAULT 0",
    "outbound_enabled": "INTEGER NOT NULL DEFAULT 1",
    "notify_on_receive": "INTEGER NOT NULL DEFAULT 0",
    "notification_email": "TEXT DEFAULT ''",
    "cooldown_minutes": "INTEGER",
    "reply_instructions": "TEXT DEFAULT ''",
}

# Ledger columns added after v1 (same additive pattern). message_key is the
# cloud message-proxy handle — the ONLY way to re-fetch a body later (poll
# rows carry no body, and the ledger deliberately stores none): the Email
# page's expand-a-row viewer needs it. Empty for rows recorded before this
# column existed; the viewer falls back to matching the live poll feed.
_LEDGER_COLUMNS = {
    "message_key": "TEXT DEFAULT ''",
}


def init() -> None:
    with _LOCK, _connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS user_email_addresses (
            user_id       INTEGER PRIMARY KEY,
            prefix        TEXT NOT NULL,
            email_address TEXT NOT NULL UNIQUE,
            username      TEXT DEFAULT '',
            role          INTEGER DEFAULT 2,
            is_active     INTEGER NOT NULL DEFAULT 1,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS processed_emails (
            event_id     INTEGER NOT NULL,
            address      TEXT NOT NULL,
            sender       TEXT DEFAULT '',
            subject      TEXT DEFAULT '',
            outcome      TEXT NOT NULL,
            detail       TEXT DEFAULT '',
            processed_at TEXT NOT NULL,
            PRIMARY KEY (event_id, address)
        );
        """)
        cols = {r["name"] for r in c.execute(
            "PRAGMA table_info(user_email_addresses)")}
        for name, ddl in _OPTION_COLUMNS.items():
            if name not in cols:
                c.execute(f"ALTER TABLE user_email_addresses "
                          f"ADD COLUMN {name} {ddl}")
        cols = {r["name"] for r in c.execute(
            "PRAGMA table_info(processed_emails)")}
        for name, ddl in _LEDGER_COLUMNS.items():
            if name not in cols:
                c.execute(f"ALTER TABLE processed_emails "
                          f"ADD COLUMN {name} {ddl}")
    logger.info("agent email store ready")


def set_options(user_id: int, **options) -> Optional[dict]:
    """Update per-address option columns (only known columns; ignores None)."""
    sets, vals = [], []
    for k, v in options.items():
        if k in _OPTION_COLUMNS and v is not None:
            sets.append(f"{k} = ?")
            vals.append(v)
    if not sets:
        return get_address(user_id)
    vals += [_now(), int(user_id)]
    with _LOCK, _connect() as c:
        c.execute(f"UPDATE user_email_addresses SET {', '.join(sets)}, "
                  f"updated_at = ? WHERE user_id = ?", vals)
    return get_address(user_id)


def valid_prefix(prefix: str) -> bool:
    return bool(PREFIX_RE.match(prefix or ""))


def upsert_address(user_id: int, prefix: str, email_address: str,
                   username: str, role: int, is_active: bool) -> dict:
    now = _now()
    with _LOCK, _connect() as c:
        # UNIQUE(email_address) is the DB-level collision guard the legacy
        # table never had — a duplicate prefix errors here, honestly.
        existing = c.execute(
            "SELECT user_id FROM user_email_addresses WHERE email_address = ? "
            "AND user_id != ?", (email_address, int(user_id))).fetchone()
        if existing:
            raise ValueError(f"address '{email_address}' is already taken by "
                             f"another user")
        c.execute(
            "INSERT INTO user_email_addresses (user_id, prefix, email_address,"
            " username, role, is_active, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(user_id) DO UPDATE SET prefix=excluded.prefix,"
            " email_address=excluded.email_address, username=excluded.username,"
            " role=excluded.role, is_active=excluded.is_active, updated_at=?",
            (int(user_id), prefix, email_address, username, int(role),
             1 if is_active else 0, now, now, now))
    logger.info(f"agent email address saved: user {user_id} -> {email_address} "
                f"(active={is_active})")
    return get_address(user_id)


def get_address(user_id: int) -> Optional[dict]:
    with _connect() as c:
        r = c.execute("SELECT * FROM user_email_addresses WHERE user_id = ?",
                      (int(user_id),)).fetchone()
    return dict(r) if r else None


def active_addresses() -> dict:
    """{lowercased address -> owner row} for the poller's matcher."""
    with _connect() as c:
        rows = c.execute("SELECT * FROM user_email_addresses "
                         "WHERE is_active = 1").fetchall()
    return {str(r["email_address"]).lower(): dict(r) for r in rows}


def delete_address(user_id: int) -> bool:
    with _LOCK, _connect() as c:
        cur = c.execute("DELETE FROM user_email_addresses WHERE user_id = ?",
                        (int(user_id),))
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Dedupe ledger
# ---------------------------------------------------------------------------

def already_processed(event_id: int, address: str) -> bool:
    with _connect() as c:
        r = c.execute("SELECT 1 FROM processed_emails WHERE event_id = ? "
                      "AND address = ?", (int(event_id), address.lower())).fetchone()
    return r is not None


def record(event_id: int, address: str, outcome: str, sender: str = "",
           subject: str = "", detail: str = "", message_key: str = "") -> None:
    with _LOCK, _connect() as c:
        c.execute("INSERT OR IGNORE INTO processed_emails (event_id, address,"
                  " sender, subject, outcome, detail, processed_at,"
                  " message_key)"
                  " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                  (int(event_id), address.lower(), sender[:200], subject[:300],
                   outcome[:60], detail[:500], _now(),
                   str(message_key or "")[:200]))


def get_processed(event_id: int, address: str) -> Optional[dict]:
    """One ledger row by its natural key — the expand-a-row viewer's
    ownership check (the address scoping IS the authz: callers pass the
    requesting user's own address)."""
    with _connect() as c:
        r = c.execute("SELECT * FROM processed_emails WHERE event_id = ? "
                      "AND address = ?",
                      (int(event_id), address.lower())).fetchone()
    return dict(r) if r else None


def recent(address: str = "", limit: int = 20) -> list:
    with _connect() as c:
        if address:
            rows = c.execute("SELECT * FROM processed_emails WHERE address = ? "
                             "ORDER BY processed_at DESC LIMIT ?",
                             (address.lower(), int(limit))).fetchall()
        else:
            rows = c.execute("SELECT * FROM processed_emails "
                             "ORDER BY processed_at DESC LIMIT ?",
                             (int(limit),)).fetchall()
    return [dict(r) for r in rows]


def processed_today(address: str) -> int:
    day = _now()[:10]
    with _connect() as c:
        r = c.execute("SELECT COUNT(*) AS n FROM processed_emails "
                      "WHERE address = ? AND processed_at LIKE ? "
                      "AND outcome IN ('processed', 'reply_drafted')",
                      (address.lower(), day + "%")).fetchone()
    return int(r["n"] if r else 0)


def last_processed_at(address: str) -> Optional[str]:
    with _connect() as c:
        r = c.execute("SELECT MAX(processed_at) AS t FROM processed_emails "
                      "WHERE address = ? AND outcome IN "
                      "('processed', 'reply_drafted')",
                      (address.lower(),)).fetchone()
    return r["t"] if r and r["t"] else None
