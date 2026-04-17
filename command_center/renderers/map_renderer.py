"""
Command Center — Map Renderer
================================
Produces Leaflet.js map config blocks for geographic visualization.
"""

from typing import Any, Dict, List, Optional


def create_map_block(
    center: List[float],
    zoom: int = 10,
    markers: Optional[List[Dict[str, Any]]] = None,
    layers: Optional[List[Dict[str, Any]]] = None,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a Leaflet.js map content block.

    Args:
        center: [latitude, longitude]
        zoom: Zoom level (1-18)
        markers: List of markers, each with lat, lng, label, popup
        layers: Additional map layers (GeoJSON, heatmap, etc.)
        title: Map title
    """
    block = {
        "type": "map",
        "center": center,
        "zoom": zoom,
        "markers": markers or [],
    }
    if layers:
        block["layers"] = layers
    if title:
        block["title"] = title
    return block


def create_marker(
    lat: float,
    lng: float,
    label: str,
    popup: Optional[str] = None,
    color: str = "blue",
) -> Dict[str, Any]:
    """Create a single map marker."""
    marker = {"lat": lat, "lng": lng, "label": label, "color": color}
    if popup:
        marker["popup"] = popup
    return marker
