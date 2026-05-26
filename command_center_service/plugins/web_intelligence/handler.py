"""
Web Intelligence Plugin
=========================
Provides web search and geographic data capabilities.
"""

import logging
from typing import Any, Dict, List, Optional

from command_center.plugins.base_plugin import BasePlugin

# The plugin loader (`command_center.plugins.plugin_loader.load_plugin_handler`)
# imports this file via ``importlib.util.spec_from_file_location`` under a
# synthetic module name ``plugin_web_intelligence`` — i.e. NOT as a package
# member. That means ``from .geocoder import ...`` does not resolve. We load
# the sibling geocoder module by file path instead so the import works both
# under the plugin loader and under direct ``import
# plugins.web_intelligence.handler`` (when ``command_center_service`` is on
# ``sys.path``).
try:
    from plugins.web_intelligence.geocoder import GeocodeResult, geocode as _geocode
except ImportError:  # pragma: no cover - fallback for the plugin loader
    import importlib.util as _ilu
    from pathlib import Path as _Path
    _gc_path = _Path(__file__).parent / "geocoder.py"
    _spec = _ilu.spec_from_file_location("web_intelligence_geocoder", _gc_path)
    _gc_mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_gc_mod)
    GeocodeResult = _gc_mod.GeocodeResult  # type: ignore[assignment]
    _geocode = _gc_mod.geocode  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class WebIntelligencePlugin(BasePlugin):
    """Plugin for web search, news monitoring, and geographic data."""

    def get_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "search_web",
                "description": "Search the internet for current information, news, and data",
                "parameters": {
                    "query": {"type": "str", "description": "Search query"},
                    "num_results": {"type": "int", "description": "Number of results", "default": 5},
                },
                "handler": self._search_web,
            },
            {
                "name": "geocode_address",
                "description": "Convert an address to latitude/longitude coordinates",
                "parameters": {
                    "address": {"type": "str", "description": "Address or location to geocode"},
                },
                "handler": self._geocode_address,
            },
        ]

    def get_renderers(self) -> List[Dict[str, Any]]:
        return [
            {
                "block_type": "map",
                "handler": self._render_map,
            },
        ]

    def get_data_sources(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "web_search",
                "description": "Search the internet for information",
                "handler": self._search_web,
            },
        ]

    async def _search_web(self, query: str, num_results: int = 5) -> Dict[str, Any]:
        """Web search implementation. Uses httpx to query a search API."""
        # Placeholder — will be connected to a real search API
        logger.info(f"Web search: {query}")
        return {
            "results": [
                {"title": f"Result for: {query}", "snippet": "Search integration pending", "url": ""}
            ],
            "query": query,
        }

    async def _geocode_address(self, address: str) -> Dict[str, Any]:
        """Geocode an address via the configured backend.

        Backend selection: ``CC_GEOCODER`` env var (default ``nominatim``).
        See ``geocoder.py`` for the adapter pattern.

        Returns a dict on success or a failure dict on geocoding failure.
        Callers should treat the absence of ``lat``/``lng`` (or ``status !=
        "ok"``) as "no result" — never assume ``(0.0, 0.0)`` is a valid
        coordinate from this tool.
        """
        result: Optional[GeocodeResult] = _geocode(address)
        if result is None:
            logger.warning("Geocode failed for address=%r", address)
            return {
                "address": address,
                "status": "geocoding_failed",
            }
        logger.info(
            "Geocode ok: address=%r → (%.5f, %.5f) via %s",
            address, result.lat, result.lng, result.source,
        )
        return {
            "address": address,
            "lat": result.lat,
            "lng": result.lng,
            "display_name": result.display_name,
            "confidence": result.confidence,
            "source": result.source,
            "fetched_at": result.fetched_at,
            "status": "ok",
        }

    def _render_map(self, block: Dict[str, Any]) -> Dict[str, Any]:
        """Process a map block — pass through for Leaflet.js rendering."""
        return block

    def on_load(self):
        logger.info("Web Intelligence plugin loaded")

    def on_unload(self):
        logger.info("Web Intelligence plugin unloaded")
