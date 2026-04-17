"""
Command Center — Forecasting Engine Interface (Phase 2 Hook)
==============================================================
Abstract interface for time-series forecasting.
Implementations will be added in Phase 2.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class ForecastingEngine(ABC):
    """Abstract interface for forecasting engines."""

    @abstractmethod
    async def forecast(
        self,
        data_source: str,
        metric: str,
        horizon_days: int = 30,
        confidence_level: float = 0.95,
    ) -> Dict[str, Any]:
        """
        Generate a forecast for a metric.
        Returns: {predictions: [{date, value, lower_bound, upper_bound}], model_info: {...}}
        """
        ...

    @abstractmethod
    async def get_model_info(self, model_id: str) -> Dict[str, Any]:
        """Get information about the forecasting model used."""
        ...

    async def evaluate(
        self, data_source: str, metric: str, test_days: int = 7
    ) -> Dict[str, Any]:
        """Evaluate forecast accuracy on historical data."""
        return {"error": "Not implemented"}

    async def list_available_metrics(self, data_source: str) -> List[str]:
        """List metrics available for forecasting from a data source."""
        return []
