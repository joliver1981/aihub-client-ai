"""
Data Cleanser — apply cleansing operations to a DataFrame.
Handles null values, whitespace, case normalization, date formatting, etc.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


class CleanseOperation(Enum):
    """Available cleansing operations."""
    TRIM_WHITESPACE = "trim_whitespace"
    REMOVE_NULLS = "remove_nulls"
    FILL_NULLS = "fill_nulls"
    NORMALIZE_CASE = "normalize_case"
    REMOVE_SPECIAL_CHARS = "remove_special_chars"
    NORMALIZE_DATES = "normalize_dates"
    STRIP_HTML = "strip_html"


@dataclass
class CleanseRule:
    """A single cleansing rule to apply."""
    column: Optional[str] = None  # None = apply to all string columns
    operation: CleanseOperation = CleanseOperation.TRIM_WHITESPACE
    params: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CleanseRule":
        return cls(
            column=data.get("column"),
            operation=CleanseOperation(data["operation"]),
            params=data.get("params", {}),
        )


class DataCleanser:
    """Apply cleansing rules to a DataFrame."""

    def cleanse(
        self,
        df: pd.DataFrame,
        rules: List[CleanseRule],
    ) -> Tuple[pd.DataFrame, Dict[str, int]]:
        """
        Apply a list of cleansing rules to the DataFrame.

        Args:
            df: Input DataFrame.
            rules: List of CleanseRule to apply in order.

        Returns:
            Tuple of (cleaned DataFrame, dict of rule_index -> changes_count).
        """
        result = df.copy()
        changes: Dict[str, int] = {}

        for i, rule in enumerate(rules):
            rule_key = f"{i}_{rule.operation.value}"
            target_cols = self._resolve_columns(result, rule.column)
            total_changed = 0

            for col in target_cols:
                before = result[col].copy()

                if rule.operation == CleanseOperation.TRIM_WHITESPACE:
                    result[col] = self._trim_whitespace(result[col])

                elif rule.operation == CleanseOperation.REMOVE_NULLS:
                    before_len = len(result)
                    result = result.dropna(subset=[col])
                    total_changed += before_len - len(result)
                    continue

                elif rule.operation == CleanseOperation.FILL_NULLS:
                    result[col] = self._fill_nulls(result[col], rule.params)

                elif rule.operation == CleanseOperation.NORMALIZE_CASE:
                    result[col] = self._normalize_case(result[col], rule.params)

                elif rule.operation == CleanseOperation.REMOVE_SPECIAL_CHARS:
                    result[col] = self._remove_special_chars(result[col], rule.params)

                elif rule.operation == CleanseOperation.NORMALIZE_DATES:
                    result[col] = self._normalize_dates(result[col], rule.params)

                elif rule.operation == CleanseOperation.STRIP_HTML:
                    result[col] = self._strip_html(result[col])

                # Count changes
                changed = (before.astype(str).fillna("") != result[col].astype(str).fillna("")).sum()
                total_changed += int(changed)

            changes[rule_key] = total_changed

        return result, changes

    def _resolve_columns(self, df: pd.DataFrame, column: Optional[str]) -> List[str]:
        """Resolve target columns. If column is None, target all string/object columns."""
        if column is not None:
            if column not in df.columns:
                raise ValueError(f"Column '{column}' not found in DataFrame")
            return [column]
        return [c for c in df.columns if df[c].dtype == "object"]

    def _trim_whitespace(self, series: pd.Series) -> pd.Series:
        if series.dtype == "object":
            return series.str.strip()
        return series

    def _fill_nulls(self, series: pd.Series, params: Dict[str, Any]) -> pd.Series:
        strategy = params.get("strategy", "value")
        if strategy == "mean" and pd.api.types.is_numeric_dtype(series):
            return series.fillna(series.mean())
        elif strategy == "median" and pd.api.types.is_numeric_dtype(series):
            return series.fillna(series.median())
        elif strategy == "mode":
            mode_vals = series.mode()
            fill_val = mode_vals.iloc[0] if len(mode_vals) > 0 else ""
            return series.fillna(fill_val)
        elif strategy == "forward_fill":
            return series.ffill()
        elif strategy == "backward_fill":
            return series.bfill()
        else:
            value = params.get("value", "")
            return series.fillna(value)

    def _normalize_case(self, series: pd.Series, params: Dict[str, Any]) -> pd.Series:
        if series.dtype != "object":
            return series
        case = params.get("case", "lower")
        if case == "lower":
            return series.str.lower()
        elif case == "upper":
            return series.str.upper()
        elif case == "title":
            return series.str.title()
        return series

    def _remove_special_chars(self, series: pd.Series, params: Dict[str, Any]) -> pd.Series:
        if series.dtype != "object":
            return series
        pattern = params.get("pattern", r"[^a-zA-Z0-9\s]")
        replacement = params.get("replacement", "")
        return series.str.replace(pattern, replacement, regex=True)

    def _normalize_dates(self, series: pd.Series, params: Dict[str, Any]) -> pd.Series:
        output_format = params.get("format", "%Y-%m-%d")
        input_format = params.get("input_format")
        try:
            if input_format:
                parsed = pd.to_datetime(series, format=input_format, errors="coerce")
            else:
                parsed = pd.to_datetime(series, errors="coerce")
            return parsed.dt.strftime(output_format).fillna(series)
        except Exception:
            return series

    def _strip_html(self, series: pd.Series) -> pd.Series:
        if series.dtype != "object":
            return series
        import re
        return series.str.replace(r"<[^>]+>", "", regex=True)
