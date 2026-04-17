"""
Command Center — Chart Renderer
==================================
Produces Chart.js config blocks from data.
"""

from typing import Any, Dict, List

# Default color palette
COLORS = [
    "#3b82f6", "#10b981", "#f59e0b", "#8b5cf6",
    "#ef4444", "#06b6d4", "#ec4899", "#f97316",
    "#84cc16", "#6366f1", "#14b8a6", "#e11d48",
    "#a855f7", "#0ea5e9", "#22c55e",
]


def create_chart_block(
    chart_type: str,
    title: str,
    data: List[Dict[str, Any]],
    x_key: str = "label",
    y_keys: List[str] = None,
    colors: List[str] = None,
) -> Dict[str, Any]:
    """
    Create a Chart.js compatible content block.

    Args:
        chart_type: bar, line, pie, doughnut, area
        title: Chart title
        data: List of data points
        x_key: Key for x-axis labels
        y_keys: Keys for y-axis values
        colors: Custom color list
    """
    if y_keys is None:
        y_keys = ["value"]
    if colors is None:
        colors = COLORS[:len(y_keys)]

    return {
        "type": "chart",
        "chartType": chart_type,
        "title": title,
        "data": data,
        "xKey": x_key,
        "yKeys": y_keys,
        "colors": colors,
    }


def create_kpi_block(cards: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Create a KPI card block.

    Each card: {"label": "Metric Name", "value": "$1.2M", "trend": "+5%", "trendDirection": "up|down|flat"}
    """
    return {"type": "kpi", "cards": cards}
