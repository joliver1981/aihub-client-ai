"""
Compare Step — compare two input DataFrames and produce a diff DataFrame.
"""

import logging
import time
from typing import Dict, List, Tuple

import pandas as pd

from pipeline.models import StepDefinition, StepResult
from pipeline.steps.base import BaseStep
from quality.comparator import DataComparator

logger = logging.getLogger(__name__)


class CompareStep(BaseStep):
    """
    Compare two DataFrames from upstream steps.

    Requires exactly 2 entries in depends_on.
    Produces a DataFrame with a _diff_status column:
      - matched: row exists in both and values match
      - mismatch: row exists in both but values differ
      - only_a: row only in first source
      - only_b: row only in second source

    Config:
        key_columns: list[str]    — columns to join on
        compare_columns: list[str] | None — columns to compare (None = all non-key)
        tolerance: float          — numeric tolerance (default 0.0)
        case_sensitive: bool      — string comparison (default True)
    """

    def __init__(self):
        self.comparator = DataComparator()

    async def execute(
        self,
        step: StepDefinition,
        input_frames: Dict[str, pd.DataFrame],
    ) -> Tuple[pd.DataFrame, StepResult]:
        start = time.time()

        try:
            if len(step.depends_on) != 2:
                raise ValueError(
                    f"Compare step requires exactly 2 dependencies, got {len(step.depends_on)}"
                )

            dep_a, dep_b = step.depends_on
            if dep_a not in input_frames:
                raise ValueError(f"Dependency '{dep_a}' not found in input frames")
            if dep_b not in input_frames:
                raise ValueError(f"Dependency '{dep_b}' not found in input frames")

            df_a = input_frames[dep_a]
            df_b = input_frames[dep_b]

            config = step.config
            key_columns = config.get("key_columns", [])
            compare_columns = config.get("compare_columns")
            tolerance = config.get("tolerance", 0.0)
            case_sensitive = config.get("case_sensitive", True)

            if not key_columns:
                raise ValueError("Compare step requires 'key_columns' in config")

            # Run comparison
            result = self.comparator.compare(
                df_a, df_b,
                key_columns=key_columns,
                compare_columns=compare_columns,
                tolerance=tolerance,
                case_sensitive=case_sensitive,
            )

            # Build the diff DataFrame
            diff_df = self.comparator.build_diff_dataframe(result, key_columns)

            duration = int((time.time() - start) * 1000)
            summary = result.summary

            logger.info(
                f"Compare step '{step.step_id}': "
                f"matched={summary.get('matched', 0)}, "
                f"mismatched={summary.get('mismatched', 0)}, "
                f"only_a={summary.get('only_in_a', 0)}, "
                f"only_b={summary.get('only_in_b', 0)}"
            )

            return diff_df, self._build_result(
                step, diff_df,
                duration_ms=duration,
                quality_score=result.quality_score,
            )

        except Exception as e:
            duration = int((time.time() - start) * 1000)
            logger.error(f"Compare step '{step.step_id}' failed: {e}")
            return pd.DataFrame(), self._build_result(
                step, pd.DataFrame(), status="failed", duration_ms=duration, error=str(e)
            )
