"""
Transform Step — apply column-level transformations to a DataFrame.
"""

import logging
import time
from typing import Any, Dict, List, Tuple

import pandas as pd

from pipeline.models import StepDefinition, StepResult
from pipeline.steps.base import BaseStep

logger = logging.getLogger(__name__)


class TransformStep(BaseStep):
    """
    Apply a list of column transformation operations.

    Operations (in config.operations):
      - rename: {column, new_name}
      - cast: {column, dtype}  (int, float, str, datetime)
      - map_values: {column, mapping: {old_val: new_val}}
      - derive: {new_column, expression}  (pandas eval expression)
      - drop_columns: {columns: [...]}
      - split: {column, delimiter, into: [col1, col2, ...]}
      - merge_columns: {columns: [...], into, separator}
    """

    async def execute(
        self,
        step: StepDefinition,
        input_frames: Dict[str, pd.DataFrame],
    ) -> Tuple[pd.DataFrame, StepResult]:
        start = time.time()

        try:
            df = self._get_single_input(step, input_frames).copy()
            operations = step.config.get("operations", [])

            for op in operations:
                op_type = op.get("type")
                df = self._apply_operation(df, op_type, op)

            duration = int((time.time() - start) * 1000)
            logger.info(f"Transform step '{step.step_id}': {len(operations)} ops, {len(df)} rows")
            return df, self._build_result(step, df, duration_ms=duration)

        except Exception as e:
            duration = int((time.time() - start) * 1000)
            logger.error(f"Transform step '{step.step_id}' failed: {e}")
            return pd.DataFrame(), self._build_result(
                step, pd.DataFrame(), status="failed", duration_ms=duration, error=str(e)
            )

    def _apply_operation(self, df: pd.DataFrame, op_type: str, op: Dict[str, Any]) -> pd.DataFrame:
        if op_type == "rename":
            return self._op_rename(df, op)
        elif op_type == "cast":
            return self._op_cast(df, op)
        elif op_type == "map_values":
            return self._op_map_values(df, op)
        elif op_type == "derive":
            return self._op_derive(df, op)
        elif op_type == "drop_columns":
            return self._op_drop_columns(df, op)
        elif op_type == "split":
            return self._op_split(df, op)
        elif op_type == "merge_columns":
            return self._op_merge_columns(df, op)
        else:
            raise ValueError(f"Unknown transform operation: {op_type}")

    def _op_rename(self, df: pd.DataFrame, op: Dict) -> pd.DataFrame:
        column = op["column"]
        new_name = op["new_name"]
        return df.rename(columns={column: new_name})

    def _op_cast(self, df: pd.DataFrame, op: Dict) -> pd.DataFrame:
        column = op["column"]
        dtype = op["dtype"]
        if dtype == "int":
            df[column] = pd.to_numeric(df[column], errors="coerce").astype("Int64")
        elif dtype == "float":
            df[column] = pd.to_numeric(df[column], errors="coerce")
        elif dtype == "str":
            df[column] = df[column].astype(str)
        elif dtype == "datetime":
            fmt = op.get("format")
            df[column] = pd.to_datetime(df[column], format=fmt, errors="coerce")
        elif dtype == "bool":
            df[column] = df[column].astype(bool)
        else:
            df[column] = df[column].astype(dtype)
        return df

    def _op_map_values(self, df: pd.DataFrame, op: Dict) -> pd.DataFrame:
        column = op["column"]
        mapping = op["mapping"]
        default = op.get("default")
        if default is not None:
            df[column] = df[column].map(mapping).fillna(default)
        else:
            df[column] = df[column].map(mapping).fillna(df[column])
        return df

    def _op_derive(self, df: pd.DataFrame, op: Dict) -> pd.DataFrame:
        new_column = op["new_column"]
        expression = op["expression"]
        df[new_column] = df.eval(expression)
        return df

    def _op_drop_columns(self, df: pd.DataFrame, op: Dict) -> pd.DataFrame:
        columns = op["columns"]
        existing = [c for c in columns if c in df.columns]
        return df.drop(columns=existing)

    def _op_split(self, df: pd.DataFrame, op: Dict) -> pd.DataFrame:
        column = op["column"]
        delimiter = op.get("delimiter", ",")
        into = op["into"]
        split_df = df[column].astype(str).str.split(delimiter, expand=True)
        for i, col_name in enumerate(into):
            if i < split_df.shape[1]:
                df[col_name] = split_df[i].str.strip()
            else:
                df[col_name] = None
        return df

    def _op_merge_columns(self, df: pd.DataFrame, op: Dict) -> pd.DataFrame:
        columns = op["columns"]
        into = op["into"]
        separator = op.get("separator", " ")
        df[into] = df[columns].astype(str).agg(separator.join, axis=1)
        return df
