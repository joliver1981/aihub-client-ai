"""
Command Center — Plugin Registry
===================================
Manage loaded plugins: enable, disable, list, get tools/renderers.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from command_center.plugins.base_plugin import BasePlugin
from command_center.plugins.plugin_loader import discover_plugins, load_plugin_handler

logger = logging.getLogger(__name__)


class PluginRegistry:
    """Manages the lifecycle of Command Center plugins."""

    def __init__(self):
        self._plugins: Dict[str, Dict[str, Any]] = {}
        self._handlers: Dict[str, BasePlugin] = {}
        self._enabled: Dict[str, bool] = {}

    def discover(self, *search_dirs: Path):
        """Discover plugins from filesystem directories."""
        discovered = discover_plugins(*search_dirs)
        for pid, info in discovered.items():
            if pid not in self._plugins:
                self._plugins[pid] = info
                self._enabled[pid] = True
                logger.info(f"Registered plugin: {pid}")

    def load_handler(self, plugin_id: str) -> Optional[BasePlugin]:
        """Lazy-load a plugin's handler."""
        if plugin_id in self._handlers:
            return self._handlers[plugin_id]

        info = self._plugins.get(plugin_id)
        if not info:
            return None

        handler = load_plugin_handler(
            info["dir"],
            info["manifest"].get("handler", "handler.py"),
        )
        if handler:
            self._handlers[plugin_id] = handler
            info["handler"] = handler
        return handler

    def enable(self, plugin_id: str) -> bool:
        if plugin_id in self._plugins:
            self._enabled[plugin_id] = True
            return True
        return False

    def disable(self, plugin_id: str) -> bool:
        if plugin_id in self._plugins:
            self._enabled[plugin_id] = False
            # Unload handler
            handler = self._handlers.pop(plugin_id, None)
            if handler:
                handler.on_unload()
            return True
        return False

    def is_enabled(self, plugin_id: str) -> bool:
        return self._enabled.get(plugin_id, False)

    def count(self) -> int:
        return len(self._plugins)

    def list_plugins(self) -> List[Dict[str, Any]]:
        """List all plugins with their enabled status."""
        result = []
        for pid, info in self._plugins.items():
            manifest = info["manifest"]
            result.append({
                "id": pid,
                "name": manifest.get("name", pid),
                "version": manifest.get("version", "0.0.0"),
                "description": manifest.get("description", ""),
                "enabled": self._enabled.get(pid, False),
                "provides": manifest.get("provides", {}),
            })
        return result

    def get_all_tools(self) -> List[Dict[str, Any]]:
        """Get tools from all enabled plugins."""
        tools = []
        for pid, enabled in self._enabled.items():
            if not enabled:
                continue
            handler = self.load_handler(pid)
            if handler:
                try:
                    plugin_tools = handler.get_tools()
                    for t in plugin_tools:
                        t["_plugin_id"] = pid
                    tools.extend(plugin_tools)
                except Exception as e:
                    logger.error(f"Error getting tools from plugin {pid}: {e}")
        return tools

    def get_all_renderers(self) -> List[Dict[str, Any]]:
        """Get renderers from all enabled plugins."""
        renderers = []
        for pid, enabled in self._enabled.items():
            if not enabled:
                continue
            handler = self.load_handler(pid)
            if handler:
                try:
                    renderers.extend(handler.get_renderers())
                except Exception as e:
                    logger.error(f"Error getting renderers from plugin {pid}: {e}")
        return renderers
