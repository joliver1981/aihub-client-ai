"""
Command Center — Training Pipeline Interface (Phase 2 Hook)
=============================================================
Abstract interface for custom model training.
Implementations will be added in Phase 2.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class TrainingPipeline(ABC):
    """Abstract interface for custom model training pipelines."""

    @abstractmethod
    async def prepare_training_data(
        self, data_source: str, config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Prepare and validate training data from a source."""
        ...

    @abstractmethod
    async def start_training(
        self, model_type: str, training_data_id: str, hyperparameters: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Start a training job. Returns job ID and status."""
        ...

    @abstractmethod
    async def get_training_status(self, job_id: str) -> Dict[str, Any]:
        """Get the status of a training job."""
        ...

    @abstractmethod
    async def get_model_info(self, model_id: str) -> Dict[str, Any]:
        """Get information about a trained model."""
        ...

    async def list_models(self) -> List[Dict[str, Any]]:
        """List all available trained models."""
        return []

    async def predict(self, model_id: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Run inference on a trained model."""
        return {"error": "Not implemented"}
