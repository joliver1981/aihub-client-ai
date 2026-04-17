"""
Command Center — Anomaly Detector Interface (Phase 2 Hook)
============================================================
Abstract interface for anomaly detection.
Implementations will be added in Phase 2.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class AnomalyDetector(ABC):
    """Abstract interface for anomaly detection engines."""

    @abstractmethod
    async def configure(
        self, data_source: str, metrics: List[str], sensitivity: float = 0.5
    ) -> Dict[str, Any]:
        """Configure anomaly detection for a data source."""
        ...

    @abstractmethod
    async def detect(
        self, data_source: str, time_range: Optional[Dict[str, str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Run anomaly detection. Returns list of detected anomalies.
        Each anomaly: {metric, timestamp, value, expected_range, severity}
        """
        ...

    @abstractmethod
    async def get_alerts(
        self, severity_min: str = "low"
    ) -> List[Dict[str, Any]]:
        """Get active alerts from anomaly detection."""
        ...

    async def train_baseline(self, data_source: str, historical_days: int = 30) -> Dict[str, Any]:
        """Train a baseline model from historical data."""
        return {"error": "Not implemented"}
