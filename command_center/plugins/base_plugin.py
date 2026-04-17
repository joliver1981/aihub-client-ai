"""
Command Center — Base Plugin
================================
Abstract base class for Command Center plugins.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List


class BasePlugin(ABC):
    """
    Abstract base class for Command Center plugins.

    Plugins provide extensible capabilities: data sources, tools, renderers.
    Each plugin has a manifest.json and a handler module implementing this class.
    """

    @abstractmethod
    def get_tools(self) -> List[Dict[str, Any]]:
        """
        Return tool definitions this plugin provides.

        Each tool dict should have:
            name: str - unique tool name
            description: str - what it does
            parameters: dict - parameter schema
            handler: callable - async function to execute
        """
        ...

    def get_renderers(self) -> List[Dict[str, Any]]:
        """
        Return renderer definitions this plugin provides.

        Each renderer dict should have:
            block_type: str - content block type this renders
            handler: callable - function that takes a block dict and returns processed block
        """
        return []

    def get_data_sources(self) -> List[Dict[str, Any]]:
        """
        Return data source definitions this plugin provides.

        Each data source dict should have:
            name: str - unique source name
            description: str - what data it provides
            handler: callable - async function to query data
        """
        return []

    def on_load(self):
        """Called when the plugin is loaded. Use for initialization."""
        pass

    def on_unload(self):
        """Called when the plugin is unloaded. Use for cleanup."""
        pass
