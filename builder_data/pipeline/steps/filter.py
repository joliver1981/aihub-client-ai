"""
Filter Step — filter rows and optionally aggregate data.
"""

import logging
import time
from typing import Any, Dict, List, Tuple

import pandas as pd

from pipeline.models import StepDefinition, StepResult
from pipeline.steps.base import BaseStep

logger = logging.getLogger(__name__)

# Operator mapping for filter conditions
_OPERATORS = {
    "==": lambda s, v: s == v,
    "!=": lambda s, v: s != v,
    ">": lambda s, v: s > v,
    "<": lambda s, v: s < v,
    ">=": lambda s, v: s >= v,
    "<=": lambda s, v: s <= v,
    "in": lambda s, v: s.isin(v if isinstance(v, list) else [v]),
    "not_in": lambda s, v: ~s.isin(v if isinstance(v, list) else [v]),
    "contains": lambda s, v: s.astype(str).str.contains(str(v), case=False, na=False),
    "is_null": lambda s, v: s.isna(),
    "not_null": lambda s, v: s.notna(),
}


class FilterStep(BaseStep):
    """
    Filter rows based on conditions and optionally aggregate.

    Config:
        conditions: list of {column, operator, value}
        logic: "and" | "or" (default: "and")
        aggregation: optional {
            group_by: list[str],
            aggregations: list[{column, function}]
        }
    """

    async def execute(
        self,
        step: StepDefinition,
        input_frames: Dict[str, pd.DataFrame],
    ) -> Tuple[pd.DataFrame, StepResult]:
        start = time.time()

        try:
            df = self._get_single_input(step, input_frames).copy()
            config = step.config

            # Apply row filters
            conditions = config.get("conditions", [])
            if conditions:
                df = self._apply_filters(df, conditions, config.get("logic", "and"))

            # Apply aggregation
            aggregation = config.get("aggregation")
            if aggregation:
                df = self._apply_aggregation(df, aggregation)

            duration = int((time.time() - start) * 1000)
            logger.info(f"Filter step '{step.step_id}': {len(df)} rows after filtering")
            return df, self._build_result(step, df, duration_ms=duration)

        except Exception as e:
            duration = int((time.time() - start) * 1000)
            logger.error(f"Filter step '{step.step_id}' failed: {e}")
            return pd.DataFrame(), self._build_result(
                step, pd.DataFrame(), status="failed", duration_ms=duration, error=str(e)
            )

    def _apply_filters(self, df: pd.DataFrame, conditions: List[Dict], logic: str) -> pd.DataFrame:
        masks = []
        for cond in conditions:
            column = cond["column"]
            operator = cond["operator"]
            value = cond.get("value")

            if column not in df.columns:
                raise ValueError(f"Filter column '{column}' not found in DataFrame")

            op_func = _OPERATORS.get(operator)
            if op_func is None:
                raise ValueError(f"Unknown filter operator: '{operator}'")

            mask = op_func(df[column], value)
            masks.append(mask)

        if not masks:
            return df

        if logic == "or":
            combined = masks[0]
            for m in masks[1:]:
                combined = combined | m
        else:
            combined = masks[0]
            for m in masks[1:]:
                combined = combined & m

        return df[combined].reset_index(drop=True)

    def _apply_aggregation(self, df: pd.DataFrame, aggregation: Dict) -> pd.DataFrame:
        group_by = aggregation.get("group_by", [])
        agg_specs = aggregation.get("aggregations", [])

        if not agg_specs:
            return df

        agg_dict: Dict[str, Any] = {}
        for spec in agg_specs:
            col = spec["column"]
            func = spec["function"]  # sum, mean, count, min, max, first, last
            agg_dict[col] = func

        if group_by:
            result = df.groupby(group_by).agg(agg_dict).reset_index()
        else:
            result = df.agg(agg_dict).to_frame().T

        return result
