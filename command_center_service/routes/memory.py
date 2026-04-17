"""
Command Center — Memory Routes
=================================
User preferences and route memory API endpoints.
"""

import logging
from fastapi import APIRouter, Query

from command_center.memory import user_memory

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/memory", tags=["memory"])


# ── Suggestion Chips (backed by Route Memory) ────────────────────────────

@router.get("/suggestions")
async def get_suggestions(user_id: int = Query(...), limit: int = Query(5)):
    """Get top N route-based suggestions for the current user."""
    from command_center.memory.route_memory import get_route_suggestions
    suggestions = get_route_suggestions(user_id, limit=limit)
    return suggestions


@router.delete("/suggestions")
async def delete_all_suggestions(user_id: int = Query(...)):
    """Delete all route entries for a user."""
    from command_center.memory.route_memory import delete_all_routes
    deleted = delete_all_routes(user_id)
    return {"status": "deleted", "count": deleted}


@router.delete("/suggestions/{route_id}")
async def delete_suggestion(user_id: int = Query(...), route_id: int = 0):
    """Delete a single route entry by id."""
    if not route_id:
        return await delete_all_suggestions(user_id=user_id)
    from command_center.memory.route_memory import delete_route
    deleted = delete_route(user_id, route_id)
    return {"status": "deleted" if deleted else "not_found"}


# ── Preferences ──────────────────────────────────────────────────────────

@router.get("/preferences")
async def get_preferences(user_id: int = Query(...)):
    """Get user preferences."""
    return user_memory.get_preferences(user_id)


@router.put("/preferences")
async def update_preferences(user_id: int = Query(...), key: str = Query(...), value: str = Query(...)):
    """Update a user preference."""
    user_memory.update_preference(user_id, key, value)
    return {"status": "updated"}


@router.delete("/preferences")
async def delete_all_preferences(user_id: int = Query(...)):
    """Delete all preferences for a user."""
    deleted = user_memory.delete_all_preferences(user_id)
    return {"status": "deleted", "count": deleted}


@router.delete("/preferences/{key:path}")
async def delete_preference(user_id: int = Query(...), key: str = ""):
    """Delete a single preference by key."""
    if not key:
        return await delete_all_preferences(user_id=user_id)
    deleted = user_memory.delete_preference(user_id, key)
    return {"status": "deleted" if deleted else "not_found"}


# ── Route Memory Management ──────────────────────────────────────────────

@router.get("/routes")
async def get_routes(user_id: int = Query(...), limit: int = Query(100)):
    """Get aggregated route stats for the management UI."""
    from command_center.memory.route_memory import get_all_routes
    return get_all_routes(user_id, limit=limit)


@router.delete("/routes")
async def delete_all_routes_endpoint(user_id: int = Query(...)):
    """Delete all route entries for a user."""
    from command_center.memory.route_memory import delete_all_routes
    deleted = delete_all_routes(user_id)
    return {"status": "deleted", "count": deleted}


@router.delete("/routes/canonical")
async def delete_route_by_canonical(user_id: int = Query(...), normalized_query: str = Query(...)):
    """Delete all entries for a canonical form (user clears a learned route group)."""
    from command_center.memory.route_memory import delete_routes_by_canonical
    deleted = delete_routes_by_canonical(user_id, normalized_query)
    return {"status": "deleted", "count": deleted}


@router.delete("/routes/{route_id}")
async def delete_route_endpoint(user_id: int = Query(...), route_id: int = 0):
    """Delete a single route entry by id."""
    from command_center.memory.route_memory import delete_route
    deleted = delete_route(user_id, route_id)
    return {"status": "deleted" if deleted else "not_found"}


@router.get("/routes/stats")
async def get_route_stats_endpoint(user_id: int = Query(...)):
    """Get aggregate route stats."""
    from command_center.memory.route_memory import get_route_stats
    return get_route_stats(user_id)


# ── Session Insights ────────────────────────────────────────────────────

@router.get("/insights")
async def get_insights(user_id: int = Query(...), limit: int = Query(10)):
    """Get stored session insights for a user."""
    from command_center.memory.route_memory import get_insights_for_context, _load_insights
    raw = _load_insights(user_id, limit=limit)
    return [{"topic": topic, **data} for topic, data in raw]


@router.delete("/insights")
async def delete_all_insights_endpoint(user_id: int = Query(...)):
    """Delete all session insights for a user."""
    from command_center.memory.route_memory import delete_all_insights
    deleted = delete_all_insights(user_id)
    return {"status": "deleted", "count": deleted}


# ── Backward Compatibility ───────────────────────────────────────────────

@router.get("/all")
async def get_all_memories_endpoint(user_id: int = Query(...)):
    """Get ALL memory entries (preferences) for a user."""
    memories = user_memory.get_all_memories(user_id)
    return [m.to_dict() for m in memories]
