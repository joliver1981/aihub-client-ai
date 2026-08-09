"""
Chat history (James, 2026-08-09 — CC-parity): list past Assistant
conversations and replay them.

Two sources of truth, deliberately split:
- a lightweight LEDGER here (SQLite sidecar): user_id -> session_id, a title
  (first prompt), timestamps, turn count — written on every /api/chat turn.
- the TRANSCRIPTS themselves are the Claude SDK's own session files under
  CLAUDE_CONFIG_DIR/projects/<per-user-workspace>/<session_id>.jsonl — we
  never duplicate them, we parse them on demand for replay (user lines with
  string content = real prompts; assistant lines = text blocks + tool_use
  names; everything else — queue ops, attachments, tool_result user lines —
  is transport noise and skipped).
"""

import glob
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional

from workitem_store import DB_PATH  # share the mywork.db file
from agent_config import CLAUDE_CONFIG_DIR, logger

_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init() -> None:
    with _LOCK, _connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS chat_sessions (
            session_id TEXT PRIMARY KEY,
            user_id    INTEGER NOT NULL,
            title      TEXT DEFAULT '',
            turns      INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS chat_sessions_user
            ON chat_sessions (user_id, updated_at DESC);
        """)
    logger.info("chat history ledger ready")


def touch(user_id: int, session_id: str, first_message: str) -> None:
    """Record/refresh a session after a completed turn. Never raises."""
    if not session_id:
        return
    try:
        now = _now()
        title = " ".join(str(first_message or "").split())[:120]
        with _LOCK, _connect() as c:
            c.execute(
                "INSERT INTO chat_sessions (session_id, user_id, title, turns,"
                " created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)"
                " ON CONFLICT(session_id) DO UPDATE SET"
                " turns = turns + 1, updated_at = ?",
                (session_id, int(user_id), title, now, now, now))
    except Exception as e:
        logger.warning(f"chat history touch failed (non-fatal): {e}")


def list_sessions(user_id: int, limit: int = 30) -> list:
    with _connect() as c:
        rows = c.execute(
            "SELECT session_id, title, turns, created_at, updated_at "
            "FROM chat_sessions WHERE user_id = ? "
            "ORDER BY updated_at DESC LIMIT ?",
            (int(user_id), int(limit))).fetchall()
    return [dict(r) for r in rows]


def owns_session(user_id: int, session_id: str) -> bool:
    with _connect() as c:
        r = c.execute("SELECT 1 FROM chat_sessions WHERE session_id = ? "
                      "AND user_id = ?", (session_id, int(user_id))).fetchone()
    return r is not None


def _find_transcript(session_id: str) -> Optional[str]:
    # session ids are uuids from the SDK — never trust one as a path segment
    safe = "".join(ch for ch in session_id if ch.isalnum() or ch == "-")
    if safe != session_id or not safe:
        return None
    pattern = os.path.join(CLAUDE_CONFIG_DIR, "projects", "*", f"{safe}.jsonl")
    hits = glob.glob(pattern)
    return hits[0] if hits else None


def replay(session_id: str, max_turns: int = 400) -> list:
    """Parse the SDK transcript into simple replay turns:
    [{"role": "user"|"agent", "text": str, "tools": [names]}]."""
    path = _find_transcript(session_id)
    if not path:
        return []
    turns: list = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("attachment") is not None:
                    continue
                rtype = rec.get("type")
                msg = rec.get("message") or {}
                if rtype == "user":
                    content = msg.get("content")
                    if isinstance(content, str) and content.strip():
                        turns.append({"role": "user",
                                      "text": content.strip()[:8000]})
                elif rtype == "assistant":
                    text_parts, tools = [], []
                    for block in (msg.get("content") or []):
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "text" and block.get("text"):
                            text_parts.append(block["text"])
                        elif block.get("type") == "tool_use":
                            tools.append(str(block.get("name", "?")).replace(
                                "mcp__aihub__", ""))
                    if text_parts or tools:
                        # merge consecutive assistant records of one turn
                        if turns and turns[-1]["role"] == "agent" and \
                                turns[-1].get("_open"):
                            turns[-1]["text"] = (turns[-1]["text"] + "\n\n"
                                                 + "\n\n".join(text_parts)).strip()
                            turns[-1]["tools"].extend(tools)
                        else:
                            turns.append({"role": "agent",
                                          "text": "\n\n".join(text_parts),
                                          "tools": tools, "_open": True})
                    continue
                # any USER line closes the current agent turn
                if turns and turns[-1]["role"] == "agent":
                    turns[-1].pop("_open", None)
                if len(turns) >= max_turns:
                    break
    except OSError as e:
        logger.warning(f"chat replay read failed: {e}")
        return []
    for t in turns:
        t.pop("_open", None)
    return turns
