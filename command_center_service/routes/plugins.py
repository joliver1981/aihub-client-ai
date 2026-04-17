"""
Command Center — Plugin Routes
=================================
Plugin management API endpoints.
"""

import logging
from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/plugins", tags=["plugins"])

# Set by main.py during lifespan
_plugin_registry = None


def init_plugin_routes(plugin_registry):
    global _plugin_registry
    _plugin_registry = plugin_registry


def _get_registry():
    global _plugin_registry
    if _plugin_registry is None:
        from command_center.plugins.plugin_registry import PluginRegistry
        _plugin_registry = PluginRegistry()
    return _plugin_registry


@router.get("")
async def list_plugins():
    """List all plugins with enabled status."""
    return _get_registry().list_plugins()


@router.post("/{plugin_id}/enable")
async def enable_plugin(plugin_id: str):
    """Enable a plugin."""
    if _get_registry().enable(plugin_id):
        return {"status": "enabled", "plugin_id": plugin_id}
    return {"error": "Plugin not found"}, 404


@router.post("/{plugin_id}/disable")
async def disable_plugin(plugin_id: str):
    """Disable a plugin."""
    if _get_registry().disable(plugin_id):
        return {"status": "disabled", "plugin_id": plugin_id}
    return {"error": "Plugin not found"}, 404
