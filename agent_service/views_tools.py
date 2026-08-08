"""
Views tools (A5) — save/list/delete deterministic dashboards.

The agent builds an analysis conversationally ONCE, then pins the exact SQL
into a View. From then on the Views surface refreshes it deterministically —
the pinned SELECTs re-run through the read-only probe seam with zero LLM
involvement, so a saved dashboard can never drift, hallucinate, or burn
tokens. This is the Data Explorer saved-dashboard contract James pinned in
the plan ("deterministic refresh, no rebuilding via chat").
"""

import json
from typing import Any

from claude_agent_sdk import tool

from platform_tools import CURRENT_USER, _text, _post, _resolve_connection
import views_store


async def run_view(view: dict) -> dict:
    """
    Refresh a view deterministically: run each tile's pinned SQL through the
    governed probe endpoint (sql_gate read-only + server row cap ~50). Errors
    are per-tile and honest — one broken tile never fakes the others.
    Shared by the /api/views/run endpoint and nothing else; no LLM anywhere.
    """
    results = []
    for t in view.get("tiles") or []:
        tile = {"title": t.get("title"), "viz": t.get("viz") or "auto",
                "sql": t.get("sql"), "connection": t.get("connection")}
        try:
            conn_id, err = await _resolve_connection(t.get("connection"))
            if err:
                tile["error"] = err
                results.append(tile)
                continue
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
                rows = [[r.get(c) for c in cols] if isinstance(r, dict) else r
                        for r in (data.get("rows") or [])]
                tile["columns"] = cols
                tile["rows"] = rows
                tile["row_count"] = data.get("row_count")
                tile["cap_applied"] = bool(data.get("cap_applied"))
        except Exception as e:
            tile["error"] = str(e)
        results.append(tile)
    return {"name": view.get("name"), "description": view.get("description"),
            "version": view.get("version"), "updated_at": view.get("updated_at"),
            "tiles": results}


@tool(
    "save_view",
    "Pin the CURRENT analysis as a saved View — a deterministic dashboard the "
    "user refreshes from the Views screen with zero AI involvement. Each tile "
    "= a title + connection + ONE frozen SELECT (verified via "
    "probe_connection_query FIRST — never pin SQL you haven't run). Saving an "
    "existing name replaces it and bumps its version. Tiles whose SELECT "
    "returns a single row+column render as big-number stats; anything else "
    "renders as a table. The server caps ~50 rows per tile — tell the user a "
    "View is for pulse numbers and top-N lists, not bulk export.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "View name (shown in the Views list)"},
            "description": {"type": "string", "description": "One line: what this shows"},
            "tiles_json": {"type": "string",
                           "description": 'JSON array, each: {"title": str, '
                                          '"connection": name-or-id, "sql": '
                                          '"SELECT ...", "viz": "stat|table|auto"}'},
        },
        "required": ["name", "tiles_json"],
        "additionalProperties": False,
    },
)
async def save_view(args: dict[str, Any]) -> dict[str, Any]:
    user = CURRENT_USER.get()
    try:
        tiles = json.loads(args["tiles_json"])
    except Exception as e:
        return _text(f"tiles_json is not valid JSON: {e}", is_error=True)
    err = views_store.validate_tiles(tiles)
    if err:
        return _text(f"Not saved: {err}", is_error=True)
    # Read-back honesty: verify every connection resolves BEFORE saving, so a
    # saved view never contains a tile that can only ever error.
    for i, t in enumerate(tiles, 1):
        _cid, cerr = await _resolve_connection(t.get("connection"))
        if cerr:
            return _text(f"Not saved — tile {i} ('{t.get('title')}'): {cerr}",
                         is_error=True)
    try:
        info = views_store.save(str(args["name"]), str(args.get("description") or ""),
                                tiles, int(user.get("user_id") or 0))
    except ValueError as e:
        return _text(f"Not saved: {e}", is_error=True)
    verb = "updated" if info["version"] > 1 else "created"
    return _text(f"View '{info['name']}' {verb} (v{info['version']}, "
                 f"{info['tile_count']} tiles). It now appears on the Views "
                 "screen; every refresh re-runs the pinned SQL exactly — no AI "
                 "in the loop.")


@tool(
    "list_saved_views",
    "List the saved Views (deterministic dashboards) on this install.",
    {},
)
async def list_saved_views(args: dict[str, Any]) -> dict[str, Any]:
    views = views_store.list_views()
    if not views:
        return _text("No Views are saved yet.")
    lines = [f"- {v['name']} (v{v['version']}, {v['tile_count']} tiles) — "
             f"{(v.get('description') or '')[:100]}" for v in views]
    return _text(f"Saved Views ({len(views)}):\n" + "\n".join(lines))


@tool(
    "delete_view",
    "Delete a saved View by name. Destructive: call once with confirmed=false "
    "to preview, then ONLY after the user explicitly confirms, again with "
    "confirmed=true.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "confirmed": {"type": "boolean"},
        },
        "required": ["name"],
        "additionalProperties": False,
    },
)
async def delete_view(args: dict[str, Any]) -> dict[str, Any]:
    name = str(args["name"]).strip()
    view = views_store.get(name)
    if not view:
        return _text(f"No view named '{name}' exists.", is_error=True)
    if not args.get("confirmed"):
        return _text(f"CONFIRMATION REQUIRED: view '{name}' (v{view['version']}, "
                     f"{len(view['tiles'])} tiles) would be permanently deleted. "
                     "Ask the user to confirm, then call again with confirmed=true.")
    views_store.delete(name)
    return _text(f"View '{name}' deleted.")


VIEWS_TOOLS = [save_view, list_saved_views, delete_view]
