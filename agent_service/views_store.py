"""
Views — deterministic dashboards (A5 + v2, docs/views-v2-spec.md).

A View pins the RECIPE, not the output: tiles of frozen SQL against named
platform connections — or, since v2, the output of a PINNED automation —
plus a title each. Refresh re-runs the pinned recipe through governed seams
(sql_gate probe / automation run) and re-renders. Zero LLM tokens anywhere
on the refresh path.

v2 scoping mirrors skills exactly (James's rule): user = private, direct;
group = direct IF the saver is a member (verified HERE, at the chokepoint
every caller converges on — not only in the tool layer); tenant = NEVER a
direct write — save() refuses, request_tenant_promotion() files an admin
approval into My Work and only the respond hook publishes.

Storage: same service-owned SQLite as My Work (single writer = agent_service).
v1 rows (install-wide) are migrated to tenant scope — their visibility today
is already "everyone", so nothing changes for them and no retroactive
approval is required.
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
SCOPES = ("user", "group", "tenant")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ns_key(scope: str, group_id: int = 0, owner_user: int = 0) -> str:
    """Namespace key: name uniqueness lives inside one of these."""
    if scope == "user":
        return f"user:{int(owner_user)}"
    if scope == "group":
        return f"group:{int(group_id)}"
    return "tenant"


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS views (
    view_id     TEXT PRIMARY KEY,
    ns          TEXT NOT NULL,
    scope       TEXT NOT NULL DEFAULT 'tenant',
    group_id    INTEGER,
    name        TEXT NOT NULL,
    description TEXT DEFAULT '',
    owner_user  INTEGER,
    tiles       TEXT NOT NULL,
    prev_tiles  TEXT,
    tile_cache  TEXT,
    cached_at   TEXT,
    version     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
)"""
_CREATE_INDEX = ("CREATE UNIQUE INDEX IF NOT EXISTS views_ns_name "
                 "ON views(ns, name)")


def init() -> None:
    with _LOCK:
        c = _connect()
        try:
            tables = {r["name"] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "views" in tables:
                cols = {r["name"] for r in c.execute("PRAGMA table_info(views)")}
                if "ns" not in cols:
                    _migrate_v1(c)
            c.execute(_CREATE_TABLE)
            c.execute(_CREATE_INDEX)
            if "views_v1" in {r["name"] for r in c.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}:
                _recover_stranded_v1(c)
            c.commit()
        finally:
            c.close()
    logger.info("views store ready (v2)")


def _copy_v1_rows(c: sqlite3.Connection) -> int:
    rows = c.execute("SELECT * FROM views_v1").fetchall()
    for r in rows:
        c.execute(
            "INSERT OR IGNORE INTO views (view_id, ns, scope, group_id, name,"
            " description, owner_user, tiles, prev_tiles, version, created_at,"
            " updated_at)"
            " VALUES (?, 'tenant', 'tenant', NULL, ?, ?, ?, ?, ?, ?, ?, ?)",
            (r["view_id"], r["name"], r["description"], r["owner_user"],
             r["tiles"], r["prev_tiles"], r["version"], r["created_at"],
             r["updated_at"]))
    return len(rows)


def _migrate_v1(c: sqlite3.Connection) -> None:
    """One-time rebuild: v1 had a table-level UNIQUE(name) (install-wide
    namespace) that SQLite cannot drop in place. Legacy rows become tenant
    scope — same visibility they already had.

    Atomicity matters (review finding, 2026-08-07): executescript() would
    implicitly COMMIT the pending rename, so a crash mid-copy would strand
    every legacy view in views_v1 forever. Everything below runs inside ONE
    explicit transaction — a kill at any point rolls back to pristine v1."""
    c.execute("BEGIN IMMEDIATE")
    try:
        c.execute("ALTER TABLE views RENAME TO views_v1")
        c.execute(_CREATE_TABLE)
        c.execute(_CREATE_INDEX)
        n = _copy_v1_rows(c)
        c.execute("DROP TABLE views_v1")
        c.commit()
    except Exception:
        c.rollback()
        raise
    logger.info(f"views store migrated v1 -> v2: {n} row(s) -> tenant scope")


def _recover_stranded_v1(c: sqlite3.Connection) -> None:
    """Belt-and-suspenders: if an OLD (pre-fix) build ever left views_v1
    behind, finish the copy idempotently instead of losing the rows."""
    c.execute("BEGIN IMMEDIATE")
    try:
        n = _copy_v1_rows(c)
        c.execute("DROP TABLE views_v1")
        c.commit()
        logger.warning(f"views store: recovered {n} stranded v1 row(s)")
    except Exception:
        c.rollback()
        raise


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_tiles(tiles) -> Optional[str]:
    """Shape check; the probe seam's sql_gate / the automation lifecycle are
    the real execution enforcers. Deeper checks (connection resolution,
    pinned-version) happen in the async tool layer before save."""
    if not isinstance(tiles, list) or not tiles:
        return "tiles must be a non-empty JSON array"
    if len(tiles) > MAX_TILES:
        return f"at most {MAX_TILES} tiles per view"
    for i, t in enumerate(tiles, 1):
        if not isinstance(t, dict):
            return f"tile {i} must be an object"
        if not str(t.get("title") or "").strip():
            return f"tile {i} needs a title"
        ttype = str(t.get("type") or "sql")
        if ttype == "sql":
            if not str(t.get("connection") or "").strip():
                return f"tile {i} needs a connection (name or id)"
            sql = str(t.get("sql") or "").strip()
            if not sql:
                return f"tile {i} needs sql"
            if not sql.lower().lstrip("(").startswith(("select", "with")):
                return f"tile {i} sql must be a single SELECT"
        elif ttype == "automation":
            if not str(t.get("automation") or "").strip():
                return f"tile {i} needs an automation (name or id)"
            if t.get("inputs") is not None and not isinstance(t["inputs"], dict):
                return f"tile {i} inputs must be an object"
        else:
            return f"tile {i} has unknown type '{ttype}' (sql | automation)"
    return None


# ---------------------------------------------------------------------------
# CRUD (scoped)
# ---------------------------------------------------------------------------

def save(name: str, description: str, tiles: list, owner_user: int,
         scope: str = "user", group_id: int = 0,
         member_check=None) -> dict:
    """
    Direct write for user/group scopes. Tenant is REFUSED here by design —
    promotion goes through request_tenant_promotion + the My Work respond
    hook (set _allow_tenant for that publisher path only).

    member_check: callable(user_id) -> iterable of group ids. Verified HERE
    for scope=group so every caller (tool, future endpoints) hits the same
    guard. Pass None to use the live UserGroups read-through.
    """
    return _save(name, description, tiles, owner_user, scope, group_id,
                 member_check, _allow_tenant=False)


def publish_tenant(name: str, description: str, tiles: list,
                   owner_user: int) -> dict:
    """The ONLY path that writes tenant scope — called by the My Work
    respond hook after a role>=3 approval (spec §3.2)."""
    return _save(name, description, tiles, owner_user, "tenant", 0,
                 None, _allow_tenant=True)


def _save(name, description, tiles, owner_user, scope, group_id,
          member_check, _allow_tenant):
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}")
    if scope == "tenant" and not _allow_tenant:
        raise ValueError("tenant views are published only through an approved "
                         "My Work promotion — file one with scope='tenant' via "
                         "the save_view tool")
    if scope == "group":
        gid = int(group_id or 0)
        if not gid:
            raise ValueError("scope=group needs group_id")
        if member_check is None:
            import readthrough
            member_check = readthrough.user_group_ids
        if gid not in set(member_check(int(owner_user or 0))):
            raise ValueError(f"user {owner_user} is not a member of group {gid} "
                             "— not saved")
    err = validate_tiles(tiles)
    if err:
        raise ValueError(err)
    name = str(name).strip()
    if not name or len(name) > 120:
        raise ValueError("view name required (max 120 chars)")

    ns = ns_key(scope, group_id, owner_user)
    now = _now()
    with _LOCK, _connect() as c:
        row = c.execute("SELECT view_id, tiles, version FROM views "
                        "WHERE ns = ? AND name = ?", (ns, name)).fetchone()
        if row:
            c.execute("UPDATE views SET description=?, tiles=?, prev_tiles=?, "
                      "version=version+1, updated_at=?, tile_cache=NULL, "
                      "cached_at=NULL WHERE view_id=?",
                      (description, json.dumps(tiles), row["tiles"], now,
                       row["view_id"]))
            vid, version = row["view_id"], row["version"] + 1
        else:
            vid, version = str(uuid.uuid4()), 1
            c.execute("INSERT INTO views (view_id, ns, scope, group_id, name, "
                      "description, owner_user, tiles, version, created_at, "
                      "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                      (vid, ns, scope, int(group_id or 0) or None, name,
                       description, int(owner_user or 0), json.dumps(tiles),
                       now, now))
    logger.info(f"view saved: [{ns}] {name} v{version} ({len(tiles)} tiles)")
    return {"view_id": vid, "name": name, "scope": scope,
            "group_id": int(group_id or 0) or None, "version": version,
            "tile_count": len(tiles)}


def request_tenant_promotion(name: str, description: str, tiles: list,
                             requested_by_user: int,
                             requested_by_name: str) -> dict:
    """File the admin approval work item for a tenant view (skills parity:
    ALWAYS an approval, no admin bypass). Returns the work item.

    Validates the NAME here too — otherwise a bad name sails through the
    request and only explodes at publish time, after the admin's approval
    (review finding, 2026-08-07)."""
    name = str(name).strip()
    if not name or len(name) > 120:
        raise ValueError("view name required (max 120 chars)")
    err = validate_tiles(tiles)
    if err:
        raise ValueError(err)
    import workitem_store
    return workitem_store.create_item(
        "approve_deny", f"Promote view '{name}' to tenant",
        summary=(f"Requested by {requested_by_name}. {description}\n\n"
                 f"{len(tiles)} tile(s): "
                 + "; ".join(f"[{t.get('type', 'sql')}] {t.get('title')}"
                             for t in tiles)),
        payload={"kind": "view_promotion", "name": str(name).strip(),
                 "description": str(description or ""), "tiles": tiles,
                 "requested_by": requested_by_name,
                 "requested_by_user": int(requested_by_user or 0)},
        from_kind="agent_session", from_ref=str(requested_by_name or ""),
        created_by=str(requested_by_name or "agent"), priority=0)


def visible_ns(user_id: int, group_ids) -> list:
    return [ns_key("user", owner_user=int(user_id or 0))] + \
           [ns_key("group", group_id=g) for g in (group_ids or [])] + \
           ["tenant"]


def list_views(user_id: int, group_ids) -> list:
    """Only what this caller can see: own + their groups + tenant."""
    nss = visible_ns(user_id, group_ids)
    q = ",".join("?" * len(nss))
    with _connect() as c:
        rows = c.execute(
            f"SELECT view_id, ns, scope, group_id, name, description, "
            f"owner_user, version, updated_at, cached_at, tiles FROM views "
            f"WHERE ns IN ({q}) ORDER BY "
            f"CASE scope WHEN 'user' THEN 0 WHEN 'group' THEN 1 ELSE 2 END, name",
            nss).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            tiles = json.loads(d.pop("tiles"))
            d["tile_count"] = len(tiles)
            d["has_automation_tiles"] = any(
                t.get("type") == "automation" for t in tiles)
        except Exception:
            d["tile_count"], d["has_automation_tiles"] = 0, False
        out.append(d)
    return out


def get(name: str, user_id: int, group_ids, scope: str = "",
        group_id: int = 0) -> Optional[dict]:
    """Resolve a view THIS CALLER can see. Explicit (scope[, group_id]) pins
    the namespace; otherwise precedence user > group > tenant (skills
    parity). Non-visible namespaces are never searched — a guessed group
    name returns None, not data."""
    name = str(name).strip()
    if scope:
        ns = ns_key(scope, group_id, user_id)
        if ns not in visible_ns(user_id, group_ids):
            return None
        candidates = [ns]
    else:
        candidates = visible_ns(user_id, group_ids)
    with _connect() as c:
        for ns in candidates:
            r = c.execute("SELECT * FROM views WHERE ns = ? AND name = ?",
                          (ns, name)).fetchone()
            if r:
                d = dict(r)
                try:
                    d["tiles"] = json.loads(d["tiles"])
                except Exception:
                    d["tiles"] = []
                try:
                    d["tile_cache"] = json.loads(d["tile_cache"]) \
                        if d.get("tile_cache") else None
                except Exception:
                    d["tile_cache"] = None
                return d
    return None


def delete(name: str, user_id: int, group_ids, role: int,
           scope: str = "", group_id: int = 0) -> tuple:
    """Permission table (spec §3.1): user = owner; group = any member;
    tenant = admin (role>=3). Returns (deleted, err)."""
    view = get(name, user_id, group_ids, scope, group_id)
    if not view:
        return False, "view not found (or not visible to you)"
    if view["scope"] == "tenant" and int(role or 0) < 3:
        return False, "deleting a tenant view requires an admin"
    if view["scope"] == "user" and int(view.get("owner_user") or 0) != int(user_id or 0):
        return False, "only the owner can delete a private view"
    with _LOCK, _connect() as c:
        c.execute("DELETE FROM views WHERE view_id = ?", (view["view_id"],))
    logger.info(f"view deleted: [{view['ns']}] {name} by user {user_id}")
    return True, None


def set_cache(view_id: str, tiles_result: list) -> None:
    """Remember the last successful tile results so slow/failed refreshes can
    honestly serve 'as of <cached_at>' instead of a blank or a lie."""
    with _LOCK, _connect() as c:
        c.execute("UPDATE views SET tile_cache=?, cached_at=? WHERE view_id=?",
                  (json.dumps(tiles_result), _now(), view_id))
