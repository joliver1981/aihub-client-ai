"""
Command Center — Health Check Route
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["health"])

# These are set by main.py during lifespan
_graph = None
_session_mgr = None
_plugin_registry = None


def init_health(graph, session_mgr, plugin_registry=None):
    global _graph, _session_mgr, _plugin_registry
    _graph = graph
    _session_mgr = session_mgr
    _plugin_registry = plugin_registry


@router.get("/health")
async def health():
    """Service health check."""
    return {
        "status": "ok",
        "service": "command_center",
        "version": "0.1.0",
        "graph_ready": _graph is not None,
        "sessions_active": len(_session_mgr.list_sessions()) if _session_mgr else 0,
        "plugins_loaded": _plugin_registry.count() if _plugin_registry else 0,
    }
