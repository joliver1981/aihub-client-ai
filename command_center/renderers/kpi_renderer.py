"""
Command Center — KPI Renderer
================================
Produces KPI card blocks for key metrics.
"""

from typing import Any, Dict, List, Optional


def create_kpi_block(cards: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Create a KPI card block.

    Each card should have:
        label: str - Metric name
        value: str - Formatted value
        trend: str (optional) - e.g., "+5%", "-2%"
        trendDirection: str (optional) - "up", "down", "flat"
        icon: str (optional) - icon name
    """
    return {"type": "kpi", "cards": cards}


def create_kpi_card(
    label: str,
    value: str,
    trend: Optional[str] = None,
    trend_direction: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a single KPI card."""
    card = {"label": label, "value": value}
    if trend:
        card["trend"] = trend
    if trend_direction:
        card["trendDirection"] = trend_direction
    return card
