"""
Base Step — abstract base class for all pipeline step handlers.
"""

from abc import ABC, abstractmethod
from typing import Dict, Tuple

import pandas as pd

from pipeline.models import StepDefinition, StepResult


class BaseStep(ABC):
    """
    Abstract base class for pipeline step handlers.

    Each step receives:
    - step: StepDefinition with configuration
    - input_frames: Dict of step_id -> DataFrame from upstream dependencies

    Each step returns:
    - Tuple of (output DataFrame, StepResult)
    """

    @abstractmethod
    async def execute(
        self,
        step: StepDefinition,
        input_frames: Dict[str, pd.DataFrame],
    ) -> Tuple[pd.DataFrame, StepResult]:
        """
        Execute this step and return the output DataFrame + result metadata.
        """
        ...

    def _get_single_input(self, step: StepDefinition, input_frames: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        """Helper to get the single input DataFrame when a step has one dependency."""
        if not step.depends_on:
            raise ValueError(f"Step '{step.step_id}' has no dependencies but requires input")
        dep_id = step.depends_on[0]
        if dep_id not in input_frames:
            raise ValueError(f"Step '{step.step_id}' dependency '{dep_id}' not found in input frames")
        return input_frames[dep_id]

    def _build_result(
        self,
        step: StepDefinition,
        df: pd.DataFrame,
        status: str = "success",
        duration_ms: int = 0,
        error: str = None,
        quality_score: float = None,
        preview_rows: int = 10,
    ) -> StepResult:
        """Helper to build a StepResult from the output DataFrame."""
        preview = None
        if df is not None and len(df) > 0:
            preview = df.head(preview_rows).to_dict(orient="records")

        return StepResult(
            step_id=step.step_id,
            status=status,
            row_count=len(df) if df is not None else 0,
            columns=list(df.columns) if df is not None else [],
            preview=preview,
            quality_score=quality_score,
            duration_ms=duration_ms,
            error=error,
        )
