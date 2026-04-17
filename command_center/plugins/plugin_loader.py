"""
Command Center — Plugin Loader
=================================
Discovers and loads plugins from the filesystem.
"""

import importlib.util
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from command_center.plugins.base_plugin import BasePlugin

logger = logging.getLogger(__name__)


def load_plugin_manifest(plugin_dir: Path) -> Optional[Dict[str, Any]]:
    """Load and validate a plugin's manifest.json."""
    manifest_path = plugin_dir / "manifest.json"
    if not manifest_path.exists():
        logger.warning(f"No manifest.json in {plugin_dir}")
        return None

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        # Validate required fields
        required = ["id", "name", "version"]
        for field in required:
            if field not in manifest:
                logger.warning(f"Plugin manifest missing '{field}' in {plugin_dir}")
                return None

        manifest["_dir"] = str(plugin_dir)
        return manifest

    except Exception as e:
        logger.error(f"Error loading plugin manifest from {plugin_dir}: {e}")
        return None


def load_plugin_handler(plugin_dir: Path, handler_file: str = "handler.py") -> Optional[BasePlugin]:
    """Load the plugin handler module and instantiate it."""
    handler_path = plugin_dir / handler_file
    if not handler_path.exists():
        logger.warning(f"No {handler_file} in {plugin_dir}")
        return None

    try:
        spec = importlib.util.spec_from_file_location(
            f"plugin_{plugin_dir.name}",
            handler_path,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Look for a class that extends BasePlugin
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, BasePlugin)
                and attr is not BasePlugin
            ):
                instance = attr()
                instance.on_load()
                return instance

        logger.warning(f"No BasePlugin subclass found in {handler_path}")
        return None

    except Exception as e:
        logger.error(f"Error loading plugin handler from {handler_path}: {e}")
        return None


def discover_plugins(*search_dirs: Path) -> Dict[str, Dict[str, Any]]:
    """
    Discover all plugins in the given directories.
    Returns a dict of plugin_id -> {manifest, handler, dir}.
    """
    plugins = {}

    for search_dir in search_dirs:
        if not search_dir.exists():
            continue

        for item in search_dir.iterdir():
            if not item.is_dir():
                continue

            manifest = load_plugin_manifest(item)
            if manifest:
                plugin_id = manifest["id"]
                plugins[plugin_id] = {
                    "manifest": manifest,
                    "handler": None,  # Lazy load
                    "dir": item,
                }
                logger.info(f"Discovered plugin: {manifest['name']} v{manifest['version']}")

    return plugins
