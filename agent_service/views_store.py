"""
Views — deterministic dashboards (A5, plan §5).

A View pins the RECIPE, not the output: tiles of frozen SQL against named
platform connections plus a title each. Refresh = re-run the pinned SQL
through the governed read-only probe seam (sql_gate enforced, row-capped) and
re-render — zero LLM tokens, fully deterministic, exactly the Data Explorer
save-a-dashboard contract carried into The Agent.

Storage: same service-owned SQLite as My Work (single writer = agent_service).
Saving over an existing name bumps the version (append-style history kept in
the row's previous_tiles for now; full version pinning can follow playbooks'
model later without a schema break).
"""

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

from workitem_store import DB_PATH  # share the mywork.db file
from agent_config import logger

_LOCK = threading.Lock()

MAX_TILES = 8


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
        CREATE TABLE IF NOT EXISTS views (
            view_id     TEXT PRIMARY KEY,
            name        TEXT NOT NULL UNIQUE,
            description TEXT DEFAULT '',
            owner_user  INTEGER,
            tiles       TEXT NOT NULL,
            prev_tiles  TEXT,
            version     INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );
        """)
    logger.info("views store ready")


def validate_tiles(tiles) -> Optional[str]:
    """Shape check; the probe seam's sql_gate is the real read-only enforcer."""
    if not isinstance(tiles, list) or not tiles:
        return "tiles must be a non-empty JSON array"
    if len(tiles) > MAX_TILES:
        return f"at most {MAX_TILES} tiles per view"
    for i, t in enumerate(tiles, 1):
        if not isinstance(t, dict):
            return f"tile {i} must be an object"
        if not str(t.get("title") or "").strip():
            return f"tile {i} needs a title"
        if not str(t.get("connection") or "").strip():
            return f"tile {i} needs a connection (name or id)"
        sql = str(t.get("sql") or "").strip()
        if not sql:
            return f"tile {i} needs sql"
        if not sql.lower().lstrip("(").startswith(("select", "with")):
            return f"tile {i} sql must be a single SELECT"
    return None


def save(name: str, description: str, tiles: list, owner_user: int) -> dict:
    err = validate_tiles(tiles)
    if err:
        raise ValueError(err)
    name = str(name).strip()
    if not name or len(name) > 120:
        raise ValueError("view name required (max 120 chars)")
    now = _now()
    with _LOCK, _connect() as c:
        row = c.execute("SELECT view_id, tiles, version FROM views WHERE name = ?",
                        (name,)).fetchone()
        if row:
            c.execute("UPDATE views SET description=?, tiles=?, prev_tiles=?, "
                      "version=version+1, updated_at=? WHERE view_id=?",
                      (description, json.dumps(tiles), row["tiles"], now,
                       row["view_id"]))
            vid, version = row["view_id"], row["version"] + 1
        else:
            vid, version = str(uuid.uuid4()), 1
            c.execute("INSERT INTO views (view_id, name, description, owner_user, "
                      "tiles, version, created_at, updated_at) "
                      "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                      (vid, name, description, int(owner_user or 0),
                       json.dumps(tiles), now, now))
    logger.info(f"view saved: {name} v{version} ({len(tiles)} tiles)")
    return {"view_id": vid, "name": name, "version": version,
            "tile_count": len(tiles)}


def list_views() -> list:
    with _connect() as c:
        rows = c.execute("SELECT view_id, name, description, owner_user, version, "
                         "updated_at, tiles FROM views ORDER BY name").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["tile_count"] = len(json.loads(d.pop("tiles")))
        except Exception:
            d["tile_count"] = 0
        out.append(d)
    return out


def get(name: str) -> Optional[dict]:
    with _connect() as c:
        r = c.execute("SELECT * FROM views WHERE name = ?",
                      (str(name).strip(),)).fetchone()
    if not r:
        return None
    d = dict(r)
    try:
        d["tiles"] = json.loads(d["tiles"])
    except Exception:
        d["tiles"] = []
    return d


def delete(name: str) -> bool:
    with _LOCK, _connect() as c:
        cur = c.execute("DELETE FROM views WHERE name = ?", (str(name).strip(),))
        return cur.rowcount > 0
