"""
Per-user daily turn counter + optional cap (all-users rollout D2/D6, james
2026-08-24).

One row per (user_id, day) in the service-owned mywork.db (same single-writer
SQLite + WAL pattern as workitem_store — this service is the only writing
process). The counter ALWAYS increments — free usage telemetry — and the cap
only bites when the admin setting `turns_per_day` is > 0 (default OFF).

Semantics decided with james:
- One count per brain turn at the run_turn chokepoint: chat, side threads,
  scheduled /api/run, email-triggered and portal-watch turns all converge there.
- The increment is a single atomic UPSERT and the cap compares its returned
  value, so two simultaneous turns can't both sneak under the limit.
- `turns` counts turn ATTEMPTS (a refused attempt still increments): a user
  gets exactly `cap` real turns per day and later attempts are refused.
- Admins (role >= 3) are always exempt; the day boundary is the SERVER's local
  date.
- FAIL OPEN: a counter/store error must never brick chat — log and allow.
"""

import os
import sqlite3
import threading
from datetime import date

from agent_config import DATA_DIR, get_turn_cap, logger

DB_PATH = os.path.join(DATA_DIR, "mywork.db")
_LOCK = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init() -> None:
    with _LOCK, _connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS agent_usage (
                         user_id INTEGER NOT NULL,
                         day     TEXT    NOT NULL,
                         turns   INTEGER NOT NULL DEFAULT 0,
                         PRIMARY KEY (user_id, day))""")


def _increment(uid: int, day: str) -> int:
    """Atomically add one turn for (uid, day) and return the new total."""
    with _LOCK, _connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS agent_usage (
                         user_id INTEGER NOT NULL,
                         day     TEXT    NOT NULL,
                         turns   INTEGER NOT NULL DEFAULT 0,
                         PRIMARY KEY (user_id, day))""")
        if sqlite3.sqlite_version_info >= (3, 35, 0):
            row = c.execute(
                """INSERT INTO agent_usage (user_id, day, turns) VALUES (?, ?, 1)
                   ON CONFLICT(user_id, day) DO UPDATE SET turns = turns + 1
                   RETURNING turns""", (uid, day)).fetchone()
            return int(row["turns"])
        # Old-SQLite fallback: same UPSERT then read back — still serialized
        # under _LOCK, so the pair is atomic within this (single-writer) process.
        c.execute("""INSERT INTO agent_usage (user_id, day, turns) VALUES (?, ?, 1)
                     ON CONFLICT(user_id, day) DO UPDATE SET turns = turns + 1""",
                  (uid, day))
        row = c.execute("SELECT turns FROM agent_usage WHERE user_id = ? AND day = ?",
                        (uid, day)).fetchone()
        return int(row["turns"]) if row else 1


def turns_today(uid: int) -> int:
    try:
        with _connect() as c:
            row = c.execute(
                "SELECT turns FROM agent_usage WHERE user_id = ? AND day = ?",
                (int(uid), date.today().isoformat())).fetchone()
            return int(row["turns"]) if row else 0
    except Exception:
        return 0


def count_turn(user_ctx: dict) -> tuple:
    """Record one turn for this principal; return (allowed, refusal_text).

    allowed is False ONLY when the cap is ON, the principal is below admin,
    and today's total (including this attempt) exceeds the cap. Any store or
    settings error logs a warning and allows the turn (fail open)."""
    try:
        uid = int(user_ctx.get("user_id") or 0)
        role = int(user_ctx.get("role") or 0)
        turns = _increment(uid, date.today().isoformat())
        cap = get_turn_cap()
        if cap > 0 and role < 3 and turns > cap:
            logger.info(f"turn cap hit: user {uid} at {turns} attempts "
                        f"(cap {cap})")
            return False, (
                f"Daily conversation limit reached — this account has used its "
                f"{cap} agent turns for today. The limit resets at midnight "
                f"(server time). An administrator can raise or disable it in "
                f"The Agent's settings.")
        return True, ""
    except Exception as e:
        logger.warning(f"usage counter failed (allowing the turn): {e}")
        return True, ""
