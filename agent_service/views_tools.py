"""
Views tools (A5 + v2) — save/list/delete deterministic dashboards.

The agent builds an analysis conversationally ONCE, then pins the exact
recipe into a View. From then on the Views surface refreshes it
deterministically — SQL tiles re-run through the read-only probe seam,
automation tiles run the automation's PINNED version through the governed
run seam — zero LLM anywhere on the refresh path.

v2 (docs/views-v2-spec.md): scopes mirror skills (user private / group
membership-verified / tenant via My Work admin approval), and automation
tiles unlock every source the platform can already reach deterministically
(scrapes, APIs-with-secrets, transforms) with no new execution paths.
"""

import asyncio
import json
import os
import time
from typing import Any

from claude_agent_sdk import tool

from platform_tools import CURRENT_USER, _text, _post, _resolve_connection
from authoring_tools import _manage, _resolve_automation
import views_store

VIEW_TILE_TIMEOUT = float(os.getenv("VIEW_TILE_TIMEOUT", "120"))
# Total wall-clock allowed for ONE serialized group of same-automation tiles
# (see _group_key). Tiles that never get their turn say so rather than
# pretending they failed.
VIEW_SERIAL_BUDGET = float(os.getenv("VIEW_SERIAL_BUDGET", "300"))
TILE_ROW_CAP = 50


# ---------------------------------------------------------------------------
# Refresh engine (shared with /api/views/run; no LLM anywhere)
# ---------------------------------------------------------------------------

def _parse_tile_stdout(stdout_tail: str):
    """Tile output contract (spec §4.2): the LAST stdout line that parses as
    JSON is the tile's data. Accepts {columns, rows}, a list of dicts, or a
    {value[, label]} stat. Returns (columns, rows) or raises ValueError."""
    for line in reversed((stdout_tail or "").strip().splitlines()):
        line = line.strip()
        if not line or line[0] not in "[{":
            continue
        try:
            data = json.loads(line)
        except Exception:
            continue
        if isinstance(data, dict) and "columns" in data and "rows" in data:
            cols = [str(c) for c in (data["columns"] or [])]
            rows = [[r.get(c) for c in cols] if isinstance(r, dict) else list(r)
                    for r in (data["rows"] or [])]
            return cols, rows[:TILE_ROW_CAP]
        if isinstance(data, list) and data and all(isinstance(r, dict) for r in data):
            cols = [str(k) for k in data[0].keys()]
            return cols, [[r.get(c) for c in cols] for r in data[:TILE_ROW_CAP]]
        if isinstance(data, dict) and "value" in data:
            return [str(data.get("label") or "value")], [[data["value"]]]
    raise ValueError(
        "no tile data found — the automation's LAST stdout line must be JSON: "
        '{"columns": [...], "rows": [[...]]}, a list of objects, or '
        '{"value": n, "label": "..."}. Note the run seam returns only the '
        "final ~2000 chars of stdout, so keep the JSON line under ~1900 chars "
        "(print fewer rows/columns — tiles are pulses, not exports)")


async def _run_sql_tile(t: dict, tile: dict) -> None:
    conn_id, err = await _resolve_connection(t.get("connection"))
    if err:
        tile["error"] = err
        return
    data, status = await _post(f"/api/discover/query/{conn_id}",
                               {"sql": str(t.get("sql") or "").strip()})
    if data.get("rejected"):
        tile["error"] = f"rejected by read-only gate: {data.get('error')}"
    elif data.get("sql_error"):
        tile["error"] = f"SQL error: {data.get('error')}"
    elif not data.get("success"):
        tile["error"] = f"HTTP {status}: {data.get('error', data)}"
    else:
        cols = data.get("columns") or []
        # The discover seam returns rows as dicts keyed by column;
        # normalize to arrays in column order for the tile renderer.
        tile["columns"] = cols
        tile["rows"] = [[r.get(c) for c in cols] if isinstance(r, dict) else r
                        for r in (data.get("rows") or [])]
        tile["row_count"] = data.get("row_count")
        tile["cap_applied"] = bool(data.get("cap_applied"))


async def _run_automation_tile(t: dict, tile: dict) -> None:
    """Run the PINNED version through the governed manage seam, honesty
    branches per spec §4.3. Runs as the refreshing user (CURRENT_USER is the
    session envelope — set by the endpoint/turn before we get here)."""
    auto_ref = str(t.get("automation_id") or t.get("automation") or "")
    payload = {"automation_id": auto_ref, "inputs": dict(t.get("inputs") or {})}
    try:
        data, status = await _manage("run", payload, timeout=VIEW_TILE_TIMEOUT)
    except Exception as e:
        tile["error"] = f"run failed to start: {e}"
        return

    # A dashboard refresh cannot wait on humans: settle the checkpoint abort
    # so no orphan approval lands in My Work, and say why the tile failed.
    # _manage never raises — it returns (error_dict, status) — so success is
    # judged from the STATUS, not from the absence of an exception (review
    # finding, 2026-08-07: the old code claimed "aborted" unconditionally).
    if data.get("waiting_on_checkpoint"):
        cp = data.get("pending_checkpoint") or {}
        adata, astatus = await _manage(
            "checkpoint_decision",
            {"run_id": str(data.get("run_id")),
             "checkpoint_id": str(cp.get("checkpoint_id")),
             "decision": "abort"}, timeout=30)
        if astatus < 400 and not adata.get("timed_out"):
            aborted = "run aborted, no approval left pending"
        else:
            aborted = (f"abort FAILED (HTTP {astatus}: "
                       f"{adata.get('error', adata)}) — an approval may still "
                       "be pending in My Approvals")
        tile["run_id"] = data.get("run_id")
        tile["error"] = ("this automation pauses at a human checkpoint — a "
                         "View refresh cannot wait on approvals; remove the "
                         f"checkpoint or don't use it in a View ({aborted})")
        return

    if data.get("timed_out") or data.get("inline_wait_elapsed"):
        tile["still_running"] = True
        tile["run_id"] = data.get("run_id")
        tile["error"] = (f"still executing after {int(VIEW_TILE_TIMEOUT)}s "
                         f"(run_id {data.get('run_id', '?')}) — refresh again "
                         "later; showing the last cached result if one exists")
        return

    if status == 403:
        # Automation RUNS are Developer+ platform-wide; a role<2 viewer (the
        # AGENT_ALLOW_ALL_USERS direction) can't trigger one — say so plainly
        # and let the cache branch serve the last good result. The v2.1 answer
        # for plain-user dashboards is scheduled refresh + cache.
        tile["error"] = ("refreshing automation tiles requires a Developer "
                        "role on this install — showing the last cached "
                        "result if one exists")
        return
    run_status = str(data.get("status") or "")
    if status >= 400 or run_status not in ("success",):
        detail = data.get("error") or (str(data.get("stderr_tail") or "")[-300:])
        tile["error"] = (f"run {run_status or 'failed'} "
                         f"(exit {data.get('exit_code')}, run_id "
                         f"{data.get('run_id', '?')}): {detail}")
        return

    try:
        cols, rows = _parse_tile_stdout(str(data.get("stdout_tail") or ""))
    except ValueError as e:
        tile["error"] = str(e)
        return
    tile["columns"] = cols
    tile["rows"] = rows
    tile["row_count"] = len(rows)
    tile["run_id"] = data.get("run_id")


# Day-of-week numbers -> names, shared with the engine's root fix (2026-08-15):
# ONE mapping, two consumers. Here it is producer-side polish (stored crons are
# readable and convention-proof at rest); the engine's callsite normalization in
# job_scheduler.py is the actual fix, so if this import ever fails the identity
# fallback loses nothing — the engine still interprets numerics correctly.
# cron_dow lives at APP_ROOT, which agent_config put on sys.path.
try:
    from cron_dow import normalize_cron_dow
except Exception:  # pragma: no cover
    normalize_cron_dow = lambda expr: expr


def _group_key(t: dict, pos: int) -> str:
    """Tiles that MUST NOT run at the same time share a key.

    Automation tiles are keyed on the automation alone — deliberately matching
    how the runner's lock works, inputs and all — so two panels of the same
    automation land in one group. SQL tiles get a unique key each: the read-only
    probe seam has no such lock, and serializing them would slow every ordinary
    board for nothing.
    """
    if str(t.get("type") or "sql") == "automation":
        return "auto:" + str(t.get("automation_id") or t.get("automation") or "")
    return f"sql:{pos}"


async def run_view(view: dict, only_index: int | None = None) -> dict:
    """Refresh tiles concurrently; per-tile honest errors — one broken tile
    never fakes the others. only_index runs a single tile (per-tile
    refresh_seconds timers in the UI use this so a 30s ticker doesn't re-run
    the whole board). The cache is merged PER TILE: each success overwrites
    its own slot with a timestamp; failures serve their slot's last good
    result labeled as-of (spec §4.3)."""
    from datetime import datetime, timezone
    tiles_def = view.get("tiles") or []
    if only_index is not None and not (0 <= only_index < len(tiles_def)):
        return {"name": view.get("name"), "error": "tile_index out of range",
                "tiles": []}
    indices = [only_index] if only_index is not None else list(range(len(tiles_def)))

    results = []
    for i in indices:
        t = tiles_def[i]
        tile = {"index": i, "title": t.get("title"),
                "viz": t.get("viz") or "auto",
                "type": str(t.get("type") or "sql"),
                "refresh_seconds": t.get("refresh_seconds"),
                "layout": t.get("layout")}
        if tile["type"] == "sql":
            tile["sql"] = t.get("sql")
            tile["connection"] = t.get("connection")
        else:
            tile["automation"] = t.get("automation_name") or t.get("automation")
        results.append(tile)

    async def _one(pos: int):
        t, tile = tiles_def[indices[pos]], results[pos]
        try:
            if tile["type"] == "automation":
                await _run_automation_tile(t, tile)
            else:
                await _run_sql_tile(t, tile)
        except Exception as e:
            tile["error"] = str(e)

    # Fan out by GROUP, not by tile. Tiles backed by the same automation must
    # run one at a time: the runner's skip-if-running lock is keyed on
    # automation_id ALONE (automations/runner.py _db_has_live_run), so firing
    # them together means the first wins and the rest come back "run skipped" —
    # differing `inputs` do not help. A real View hit exactly this: six tiles,
    # one automation, six panels, five skipped on every refresh.
    #
    # Serializing (rather than relaxing the lock) keeps the platform's
    # no-concurrency guarantee intact for stateful automations. Different
    # automations, and all SQL tiles, still run fully concurrently.
    groups: dict = {}
    for pos in range(len(indices)):
        groups.setdefault(_group_key(tiles_def[indices[pos]], pos), []).append(pos)

    async def _run_group(positions: list):
        started = time.monotonic()
        for n, pos in enumerate(positions):
            # The budget is only checked BETWEEN tiles, so the first tile of a
            # group always runs and a single slow tile is never cut off midway.
            if n and time.monotonic() - started > VIEW_SERIAL_BUDGET:
                for p in positions[n:]:
                    results[p]["error"] = (
                        f"not refreshed: this tile shares an automation with "
                        f"{len(positions) - 1} other tile(s), which must run one "
                        f"at a time, and the {int(VIEW_SERIAL_BUDGET)}s budget for "
                        f"the group ran out — showing the last cached result if "
                        f"one exists")
                return
            await _one(pos)

    await asyncio.gather(*(_run_group(p) for p in groups.values()))

    # Per-tile cache merge: successes overwrite their slot; other slots keep
    # their last good data. Failed tiles get their slot attached for as-of
    # rendering.
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cache = list(view.get("tile_cache") or [])
    cache += [{}] * (len(tiles_def) - len(cache))
    legacy_at = view.get("cached_at")   # pre-per-tile cache rows lack "at"
    any_success = False
    for tile in results:
        i = tile["index"]
        if not tile.get("error"):
            cache[i] = {"columns": tile.get("columns") or [],
                        "rows": tile.get("rows") or [], "at": now}
            any_success = True
        elif cache[i].get("rows") is not None:
            tile["cache"] = {"columns": cache[i].get("columns") or [],
                             "rows": cache[i].get("rows") or [],
                             "cached_at": cache[i].get("at") or legacy_at}
    if any_success:
        try:
            views_store.set_cache(view["view_id"], cache)
        except Exception:
            pass

    return {"name": view.get("name"), "description": view.get("description"),
            "scope": view.get("scope"), "group_id": view.get("group_id"),
            "version": view.get("version"), "updated_at": view.get("updated_at"),
            "tile_index": only_index, "tiles": results}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool(
    "save_view",
    "Pin the CURRENT analysis as a saved View — a deterministic dashboard the "
    "user refreshes from the Views screen with zero AI involvement. Tiles "
    "(max 8): SQL tiles = title + connection + ONE frozen SELECT (verified "
    "via probe_connection_query FIRST — never pin SQL you haven't run); "
    "automation tiles = title + a PROMOTED automation whose last stdout line "
    "prints JSON tile data (use for scraped/API/computed data). Scopes mirror "
    "skills: 'user' (private, default), 'group' (shared with one of the "
    "user's groups — ask them to confirm first, pass group_id), 'tenant' "
    "(everyone — FILES AN ADMIN APPROVAL into My Work; not published until "
    "approved). Saving an existing name in the same scope replaces it and "
    "bumps its version. ~50 rows per tile — pulse numbers and top-N lists, "
    "not exports.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "View name (shown in the Views list)"},
            "description": {"type": "string", "description": "One line: what this shows"},
            "tiles_json": {"type": "string",
                           "description": 'JSON array. SQL tile: {"title": str, '
                                          '"connection": name-or-id, "sql": "SELECT ...", '
                                          '"viz": "stat|table|auto|ticker|line|bar"}. '
                                          'Automation tile: {"type": "automation", '
                                          '"title": str, "automation": name-or-id, '
                                          '"inputs": {...}, "viz": ...}. Optional per '
                                          'tile: "refresh_seconds" (min 15) — that tile '
                                          're-runs on its own timer while the view is '
                                          'open (pair with ticker). ticker scrolls rows '
                                          'as label/value pairs; line/bar chart col0 vs '
                                          'the first numeric column.'},
            "scope": {"type": "string", "enum": ["user", "group", "tenant"]},
            "group_id": {"type": "integer", "description": "Required for scope=group"},
        },
        "required": ["name", "tiles_json"],
        "additionalProperties": False,
    },
)
async def save_view(args: dict[str, Any]) -> dict[str, Any]:
    user = CURRENT_USER.get()
    uid = int(user.get("user_id") or 0)
    scope = str(args.get("scope") or "user")
    try:
        tiles = json.loads(args["tiles_json"])
    except Exception as e:
        return _text(f"tiles_json is not valid JSON: {e}", is_error=True)
    err = views_store.validate_tiles(tiles)
    if err:
        return _text(f"Not saved: {err}", is_error=True)

    # Read-back honesty BEFORE saving: every SQL tile's connection must
    # resolve; every automation tile must exist AND have a pinned version
    # (tiles run the pinned version — a draft can never back a tile).
    for i, t in enumerate(tiles, 1):
        if str(t.get("type") or "sql") == "automation":
            auto_id, aerr = await _resolve_automation(str(t.get("automation")))
            if aerr:
                return _text(f"Not saved — tile {i} ('{t.get('title')}'): {aerr}",
                             is_error=True)
            got, gstat = await _manage("get", {"automation_id": auto_id},
                                       timeout=30)
            a = (got.get("automation") or {}) if gstat < 400 else {}
            if not a.get("pinned_version"):
                return _text(f"Not saved — tile {i} ('{t.get('title')}'): "
                             f"automation '{a.get('name', t.get('automation'))}' "
                             "has NO promoted version. Views only run pinned "
                             "code — promote it first.", is_error=True)
            t["automation_id"] = auto_id
            t["automation_name"] = a.get("name")
        else:
            _cid, cerr = await _resolve_connection(t.get("connection"))
            if cerr:
                return _text(f"Not saved — tile {i} ('{t.get('title')}'): {cerr}",
                             is_error=True)

    name = str(args["name"]).strip()
    description = str(args.get("description") or "")

    if scope == "tenant":
        try:
            item = views_store.request_tenant_promotion(
                name, description, tiles, uid, str(user.get("username") or ""))
        except ValueError as e:
            return _text(f"Not requested: {e}", is_error=True)
        return _text(f"Tenant promotion requested — approval item "
                     f"{item['work_item_id']} is now in My Work (admin approval "
                     "required; the view is NOT published yet).")

    try:
        info = views_store.save(name, description, tiles, uid,
                                scope=scope, group_id=int(args.get("group_id") or 0))
    except ValueError as e:
        return _text(f"Not saved: {e}", is_error=True)
    verb = "updated" if info["version"] > 1 else "created"
    where = ("your private scope" if scope == "user"
             else f"group {info['group_id']} (members see it)")
    return _text(f"View '{info['name']}' {verb} in {where} (v{info['version']}, "
                 f"{info['tile_count']} tiles). It now appears on the Views "
                 "screen; every refresh re-runs the pinned recipe exactly — no "
                 "AI in the loop.")


@tool(
    "get_view",
    "Read a saved View's FULL definition — every tile with its type, "
    "connection/automation, SQL, viz, and refresh_seconds. ALWAYS call this "
    "before editing a view: to change or add ONE tile you must re-save the "
    "COMPLETE tile list (save_view replaces wholesale), so fetch the current "
    "tiles, apply the change, and pass everything back. Never drop tiles the "
    "user didn't ask to remove.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "scope": {"type": "string", "enum": ["user", "group", "tenant"]},
            "group_id": {"type": "integer"},
        },
        "required": ["name"],
        "additionalProperties": False,
    },
)
async def get_view(args: dict[str, Any]) -> dict[str, Any]:
    import readthrough
    user = CURRENT_USER.get()
    uid = int(user.get("user_id") or 0)
    view = views_store.get(str(args["name"]).strip(), uid,
                           readthrough.user_group_ids(uid),
                           str(args.get("scope") or ""),
                           int(args.get("group_id") or 0))
    if not view:
        return _text(f"No view named '{args['name']}' is visible to this user.",
                     is_error=True)
    return _text(json.dumps({
        "name": view["name"], "scope": view["scope"],
        "group_id": view.get("group_id"), "version": view["version"],
        "description": view.get("description") or "",
        "tiles": view.get("tiles") or [],
    }, indent=1))


@tool(
    "list_saved_views",
    "List the saved Views (deterministic dashboards) the current user can "
    "see: their private ones + their groups' + tenant-wide.",
    {},
)
async def list_saved_views(args: dict[str, Any]) -> dict[str, Any]:
    import readthrough
    user = CURRENT_USER.get()
    uid = int(user.get("user_id") or 0)
    views = views_store.list_views(uid, readthrough.user_group_ids(uid))
    if not views:
        return _text("No Views are visible to this user yet.")
    lines = []
    for v in views:
        scope = v["scope"] + (f" {v['group_id']}" if v.get("group_id") else "")
        auto = " ▷automation" if v.get("has_automation_tiles") else ""
        lines.append(f"- [{scope}] {v['name']} (v{v['version']}, "
                     f"{v['tile_count']} tiles{auto}) — "
                     f"{(v.get('description') or '')[:80]}")
    return _text(f"Saved Views ({len(views)}):\n" + "\n".join(lines))


@tool(
    "delete_view",
    "Delete a saved View by name (optionally scope/group_id when names "
    "collide across scopes). Permissions: private = owner; group = any "
    "member; tenant = admin. Destructive: call once with confirmed=false to "
    "preview, then ONLY after the user explicitly confirms, again with "
    "confirmed=true.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "scope": {"type": "string", "enum": ["user", "group", "tenant"]},
            "group_id": {"type": "integer"},
            "confirmed": {"type": "boolean"},
        },
        "required": ["name"],
        "additionalProperties": False,
    },
)
async def delete_view(args: dict[str, Any]) -> dict[str, Any]:
    import readthrough
    user = CURRENT_USER.get()
    uid = int(user.get("user_id") or 0)
    gids = readthrough.user_group_ids(uid)
    name = str(args["name"]).strip()
    scope = str(args.get("scope") or "")
    gid = int(args.get("group_id") or 0)
    view = views_store.get(name, uid, gids, scope, gid)
    if not view:
        return _text(f"No view named '{name}' is visible to this user.",
                     is_error=True)
    if not args.get("confirmed"):
        return _text(f"CONFIRMATION REQUIRED: view '{name}' "
                     f"[{view['scope']}{' ' + str(view.get('group_id')) if view.get('group_id') else ''}] "
                     f"(v{view['version']}, {len(view['tiles'])} tiles) would be "
                     "permanently deleted. Ask the user to confirm, then call "
                     "again with confirmed=true.")
    ok, derr = views_store.delete(name, uid, gids, int(user.get("role") or 0),
                                  view["scope"], int(view.get("group_id") or 0))
    if not ok:
        return _text(f"Not deleted: {derr}", is_error=True)
    return _text(f"View '{name}' [{view['scope']}] deleted.")


@tool(
    "schedule_view_refresh",
    "Schedule a View's cache to refresh automatically (JSS engine, zero AI "
    "per refresh). This is how NON-developer viewers get current automation-"
    "tile data: the scheduled refresh runs as the schedule's creator and "
    "updates the cache everyone sees. Provide every_minutes (>=15) OR a cron "
    "expression. Report ONLY the ids this returns.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "The view's name"},
            "scope": {"type": "string", "enum": ["user", "group", "tenant"]},
            "group_id": {"type": "integer"},
            "every_minutes": {"type": "integer", "description": "Interval, min 15"},
            "cron_expression": {"type": "string"},
        },
        "required": ["name"],
        "additionalProperties": False,
    },
)
async def schedule_view_refresh(args: dict[str, Any]) -> dict[str, Any]:
    import datetime as _dt
    import httpx
    import readthrough
    from platform_tools import _headers
    from agent_config import get_base_url

    user = CURRENT_USER.get()
    uid = int(user.get("user_id") or 0)
    if int(user.get("role") or 0) < 2 and os.getenv(
            "AGENT_BUILD_ALLOW_ALL_USERS", "false").lower() != "true":
        return _text("Scheduling view refreshes requires a Developer role.",
                     is_error=True)
    name = str(args["name"]).strip()
    scope = str(args.get("scope") or "")
    gid = int(args.get("group_id") or 0)
    view = views_store.get(name, uid, readthrough.user_group_ids(uid), scope, gid)
    if not view:
        return _text(f"No view named '{name}' is visible to this user — "
                     "nothing scheduled.", is_error=True)

    if args.get("cron_expression"):
        schedule = {"type": "cron",
                    "cron_expression": str(args["cron_expression"])}
    elif args.get("every_minutes"):
        mins = max(int(args["every_minutes"]), 15)
        schedule = {"type": "interval", "interval_minutes": mins,
                    "start_date": _dt.datetime.utcnow().strftime(
                        "%Y-%m-%d %H:%M:%S")}
    else:
        return _text("Provide every_minutes (>=15) or cron_expression.",
                     is_error=True)

    body = {
        "name": f"View refresh: {view['name']}"[:80],
        "type": "view_refresh",
        # string "0": the route's presence check treats int 0 as missing
        "target_id": "0",
        "description": f"Deterministic cache refresh of view '{view['name']}' "
                       f"[{view['scope']}]",
        "created_by": str(user.get("username") or "agent"),
        "is_active": True,
        "parameters": {
            "view_name": {"value": view["name"], "type": "string"},
            "view_scope": {"value": view["scope"], "type": "string"},
            "view_group_id": {"value": str(view.get("group_id") or 0),
                              "type": "string"},
            "user_id": {"value": str(uid), "type": "string"},
            "role": {"value": str(int(user.get("role") or 2)), "type": "string"},
            "username": {"value": str(user.get("username") or ""),
                         "type": "string"},
        },
        "schedule": schedule,
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{get_base_url()}/api/scheduler/jobs",
                                  json=body, headers=_headers())
            data = r.json() if r.status_code < 500 else {}
            if r.status_code >= 400 or not data.get("id"):
                return _text(f"Nothing was scheduled (HTTP {r.status_code}: "
                             f"{data.get('error', r.text[:200])}). Do NOT tell "
                             "the user it was scheduled.", is_error=True)
            job_id = data["id"]
            rb = await client.get(f"{get_base_url()}/api/scheduler/jobs/{job_id}",
                                  headers=_headers())
            rbd = rb.json() if rb.status_code < 400 else {}
            active = any(s.get("is_active") for s in (rbd.get("schedules") or []))
            if not active:
                return _text(f"Job #{job_id} was created but NO active schedule "
                             "row exists — report this as NOT scheduled.",
                             is_error=True)
    except Exception as e:
        return _text(f"Scheduling failed: {e}", is_error=True)
    return _text(f"Scheduled cache refresh for view '{view['name']}' (job "
                 f"#{job_id}, verified active by read-back). Each firing "
                 f"refreshes the tiles as {user.get('username')} and updates "
                 "the cache every viewer sees — zero AI per refresh.")


@tool(
    "schedule_view_email",
    "Schedule a saved View to be REFRESHED AND EMAILED as a dashboard on a "
    "cadence — 'email me this dashboard every weekday at 9am'. Zero AI per "
    "send: the tiles are re-run deterministically and rendered by the service. "
    "It sends from the user's own agent email address and does NOT go through "
    "the approval queue — scheduling it IS the consent. ALWAYS pass timezone "
    "when the user names a clock time, in their words ('9am Eastern' -> "
    "timezone 'Eastern'); without it a cron fires in UTC and '9am' is wrong. "
    "Report ONLY the ids this returns.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "The view's name"},
            "to": {"type": "array", "items": {"type": "string"},
                   "description": "Recipient addresses"},
            "subject": {"type": "string", "description": "Optional subject line"},
            "note": {"type": "string",
                     "description": "Optional covering note above the dashboard. "
                                    "Do NOT put data in it — the tiles carry the "
                                    "numbers and they change every send."},
            "cron_expression": {"type": "string",
                                "description": "e.g. '0 9 * * 1-5' = weekdays 9am"},
            "every_hours": {"type": "integer"},
            "timezone": {"type": "string",
                         "description": "The user's zone, as they said it "
                                        "('Eastern', 'America/New_York', 'UTC+05:30')"},
            "scope": {"type": "string", "enum": ["user", "group", "tenant"]},
            "group_id": {"type": "integer"},
        },
        "required": ["name", "to"],
        "additionalProperties": False,
    },
)
async def schedule_view_email(args: dict[str, Any]) -> dict[str, Any]:
    import datetime as _dt
    import email_store
    import httpx
    import readthrough
    from platform_tools import _headers
    from agent_config import get_base_url

    user = CURRENT_USER.get()
    uid = int(user.get("user_id") or 0)
    if int(user.get("role") or 0) < 2 and os.getenv(
            "AGENT_BUILD_ALLOW_ALL_USERS", "false").lower() != "true":
        return _text("Scheduling view emails requires a Developer role.",
                     is_error=True)

    name = str(args["name"]).strip()
    scope = str(args.get("scope") or "")
    gid = int(args.get("group_id") or 0)
    view = views_store.get(name, uid, readthrough.user_group_ids(uid), scope, gid)
    if not view:
        return _text(f"No view named '{name}' is visible to this user — "
                     "nothing scheduled.", is_error=True)

    to = [str(a).strip() for a in (args.get("to") or []) if str(a).strip()]
    if not to:
        return _text("At least one recipient is required.", is_error=True)

    # Fail HERE, not at 9am every morning: a job whose sender cannot send is a
    # daily silent failure.
    addr = email_store.get_address(uid)
    if not addr or not addr.get("is_active"):
        return _text("This user has no active agent email address, so a "
                     "scheduled dashboard could never send — nothing was "
                     "scheduled. They can create one on the Email screen.",
                     is_error=True)
    if not addr.get("outbound_enabled", 1):
        return _text("Outbound email is DISABLED for this address — nothing was "
                     "scheduled.", is_error=True)

    if args.get("cron_expression"):
        # Day-of-week numbers -> names before storing: the engine reads 0 as
        # MONDAY, so a stored '1-5' fires Tue-Sat (see normalize_cron_dow).
        schedule = {"type": "cron",
                    "cron_expression": normalize_cron_dow(
                        str(args["cron_expression"]))}
    elif args.get("every_hours"):
        schedule = {"type": "interval",
                    "interval_hours": max(int(args["every_hours"]), 1),
                    "start_date": _dt.datetime.utcnow().strftime(
                        "%Y-%m-%d %H:%M:%S")}
    else:
        return _text("Provide cron_expression (e.g. '0 9 * * 1-5') or "
                     "every_hours.", is_error=True)

    # Timezone: the engine reads parameters.timezone and builds a DST-aware
    # CronTrigger (job_scheduler.py). schedule_view_refresh omits this, which is
    # why its crons fire in UTC — do not repeat that here.
    tz_note = ""
    tz_canonical = ""
    if args.get("timezone"):
        try:
            import schedule_tz
            tz_canonical, tz_display, note = schedule_tz.resolve_timezone(
                str(args["timezone"]))
            tz_note = f" Times are {tz_display}." + (f" {note}" if note else "")
        except Exception as e:
            return _text(f"Could not resolve the timezone '{args['timezone']}' "
                         f"({e}) — nothing was scheduled, because a cron with "
                         "the wrong zone fires at the wrong hour every day.",
                         is_error=True)
    elif schedule["type"] == "cron":
        tz_note = (" NOTE: no timezone was given, so this fires on the "
                   "scheduler's default zone (UTC) — tell the user, and offer "
                   "to reschedule with their zone.")

    def _p(v):
        return {"value": str(v), "type": "string"}

    params = {
        "view_name": _p(view["name"]),
        "view_scope": _p(view["scope"]),
        "view_group_id": _p(view.get("group_id") or 0),
        "user_id": _p(uid),
        "role": _p(int(user.get("role") or 2)),
        "username": _p(user.get("username") or ""),
        "to": _p(",".join(to)),
        "subject": _p(str(args.get("subject") or f"{view['name']} — dashboard")),
        "note": _p(str(args.get("note") or "")),
    }
    if tz_canonical:
        params["timezone"] = _p(tz_canonical)

    body = {
        "name": f"View email: {view['name']}"[:80],
        # string "0": the route's presence check treats int 0 as missing
        "target_id": "0",
        "type": "view_email",
        "description": f"Email the '{view['name']}' dashboard to {', '.join(to)}",
        "created_by": str(user.get("username") or "agent"),
        "is_active": True,
        "parameters": params,
        "schedule": schedule,
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{get_base_url()}/api/scheduler/jobs",
                                  json=body, headers=_headers())
            data = r.json() if r.status_code < 500 else {}
            if r.status_code >= 400 or not data.get("id"):
                return _text(f"Nothing was scheduled (HTTP {r.status_code}: "
                             f"{data.get('error', r.text[:200])}). Do NOT tell "
                             "the user it was scheduled.", is_error=True)
            job_id = data["id"]
            rb = await client.get(f"{get_base_url()}/api/scheduler/jobs/{job_id}",
                                  headers=_headers())
            rbd = rb.json() if rb.status_code < 400 else {}
            active = any(s.get("is_active") for s in (rbd.get("schedules") or []))
            if not active:
                return _text(f"Job #{job_id} was created but NO active schedule "
                             "row exists — report this as NOT scheduled.",
                             is_error=True)
    except Exception as e:
        return _text(f"Scheduling failed: {e}", is_error=True)

    return _text(f"Scheduled: the '{view['name']}' dashboard will be refreshed "
                 f"and emailed to {', '.join(to)} from {addr['email_address']} "
                 f"(job #{job_id}, verified active by read-back).{tz_note} Each "
                 "firing re-runs the tiles deterministically and sends — no "
                 "approval step, zero AI per send.")


# ---------------------------------------------------------------------------
# Rename (James 2026-08-09) — in-place, with scheduler propagation
# ---------------------------------------------------------------------------

async def rewrite_view_refresh_jobs(old_name: str, new_name: str,
                                    scope: str, group_id: int,
                                    modified_by: str = "agent") -> dict:
    """view_refresh JSS jobs reference their view BY NAME (parameters.view_name
    — there is no id in the job), so a rename must rewrite every matching job
    or its schedule 404s forever and the shared cache silently goes stale.
    Best-effort per job; returns {updated: [ids], failed: [{id, error}]} so
    callers can report honestly."""
    import httpx
    from platform_tools import _headers
    from agent_config import get_base_url

    updated, failed = [], []
    base = get_base_url()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{base}/api/scheduler/jobs",
                                 headers=_headers())
            jobs = r.json() if r.status_code < 400 else []
            if not isinstance(jobs, list):
                jobs = []
            for j in jobs:
                # view_email jobs key off the name the same way, so a rename
                # must re-point them too or the daily dashboard 404s forever.
                if str(j.get("type")) not in ("view_refresh", "view_email"):
                    continue
                jid = j.get("id")
                try:
                    jr = await client.get(f"{base}/api/scheduler/jobs/{jid}",
                                          headers=_headers())
                    job = jr.json() if jr.status_code < 400 else {}
                    params = job.get("parameters") or {}

                    def _pv(key):
                        v = params.get(key)
                        return str(v.get("value")) if isinstance(v, dict) \
                            else str(v or "")
                    if _pv("view_name") != old_name:
                        continue
                    if _pv("view_scope") != str(scope):
                        continue
                    if int(_pv("view_group_id") or 0) != int(group_id or 0):
                        continue
                    params["view_name"] = {"value": new_name, "type": "string"}
                    pr = await client.put(
                        f"{base}/api/scheduler/jobs/{jid}",
                        json={"name": f"View refresh: {new_name}"[:80],
                              "description": (f"Deterministic cache refresh of "
                                              f"view '{new_name}' [{scope}]"),
                              "parameters": params,
                              "modified_by": modified_by},
                        headers=_headers())
                    if pr.status_code < 400:
                        updated.append(jid)
                    else:
                        failed.append({"id": jid,
                                       "error": f"HTTP {pr.status_code}"})
                except Exception as e:
                    failed.append({"id": jid, "error": str(e)})
    except Exception as e:
        failed.append({"id": None, "error": f"job listing failed: {e}"})
    return {"updated": updated, "failed": failed}


def rename_summary(view: dict, sched: dict) -> str:
    """Shared honest wording for the tool + HTTP paths."""
    parts = [f"View '{view['old_name']}' renamed to '{view['name']}' "
             f"[{view['scope']}] — same view, version v{view['version']} and "
             "cached data preserved."]
    if sched["updated"]:
        parts.append(f"{len(sched['updated'])} scheduled refresh job(s) "
                     f"re-pointed at the new name "
                     f"(#{', #'.join(str(i) for i in sched['updated'])}).")
    if sched["failed"]:
        parts.append(f"WARNING: {len(sched['failed'])} scheduled refresh "
                     "job(s) could NOT be updated and will fail until fixed: "
                     + "; ".join(f"#{f['id']}: {f['error']}"
                                 for f in sched["failed"]))
    parts.append("Old bookmarks/deep links to the previous name no longer "
                 "resolve.")
    return " ".join(parts)


@tool(
    "rename_view",
    "Rename a saved View IN PLACE — keeps its id, version, cached tiles and "
    "scope, and re-points any scheduled cache-refresh jobs at the new name. "
    "ALWAYS use this to rename; never save_view under a different name (that "
    "creates a disconnected copy and strands the original). Permissions "
    "mirror delete: private = owner, group = any member, tenant = admin.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Current view name"},
            "new_name": {"type": "string", "description": "New name (max 120 chars)"},
            "scope": {"type": "string", "enum": ["user", "group", "tenant"]},
            "group_id": {"type": "integer"},
        },
        "required": ["name", "new_name"],
        "additionalProperties": False,
    },
)
async def rename_view(args: dict[str, Any]) -> dict[str, Any]:
    import readthrough
    user = CURRENT_USER.get()
    uid = int(user.get("user_id") or 0)
    view, err = views_store.rename(
        str(args["name"]).strip(), str(args["new_name"]).strip(), uid,
        readthrough.user_group_ids(uid), int(user.get("role") or 0),
        str(args.get("scope") or ""), int(args.get("group_id") or 0))
    if err:
        return _text(f"Not renamed: {err}", is_error=True)
    sched = await rewrite_view_refresh_jobs(
        view["old_name"], view["name"], view["scope"],
        int(view.get("group_id") or 0),
        modified_by=str(user.get("username") or "agent"))
    return _text(rename_summary(view, sched))


VIEWS_TOOLS = [save_view, get_view, list_saved_views, delete_view,
               schedule_view_refresh, schedule_view_email, rename_view]
