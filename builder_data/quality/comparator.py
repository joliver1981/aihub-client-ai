"""
Data Comparator — compare two DataFrames row-by-row and column-by-column.
Produces a ComparisonResult with detailed mismatch information, column
statistics, and a quality score.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


@dataclass
class ComparisonResult:
    """Full result of comparing two DataFrames."""
    summary: Dict[str, Any] = field(default_factory=dict)
    column_stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    mismatches: Optional[pd.DataFrame] = None
    only_in_a: Optional[pd.DataFrame] = None
    only_in_b: Optional[pd.DataFrame] = None
    matched: Optional[pd.DataFrame] = None
    quality_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary": self.summary,
            "column_stats": self.column_stats,
            "quality_score": self.quality_score,
            "mismatch_count": len(self.mismatches) if self.mismatches is not None else 0,
            "only_in_a_count": len(self.only_in_a) if self.only_in_a is not None else 0,
            "only_in_b_count": len(self.only_in_b) if self.only_in_b is not None else 0,
            "matched_count": len(self.matched) if self.matched is not None else 0,
        }


class DataComparator:
    """
    Compares two DataFrames on key columns and reports differences.
    """

    def compare(
        self,
        df_a: pd.DataFrame,
        df_b: pd.DataFrame,
        key_columns: List[str],
        compare_columns: Optional[List[str]] = None,
        tolerance: float = 0.0,
        case_sensitive: bool = True,
    ) -> ComparisonResult:
        """
        Compare two DataFrames and produce a detailed ComparisonResult.

        Args:
            df_a: First DataFrame ("source A").
            df_b: Second DataFrame ("source B").
            key_columns: Columns used to join/match rows.
            compare_columns: Columns to compare values for. None = all non-key columns.
            tolerance: Numeric tolerance for float comparisons.
            case_sensitive: Whether string comparisons are case-sensitive.

        Returns:
            ComparisonResult with summary, column stats, and diff DataFrames.
        """
        result = ComparisonResult()

        # Validate key columns exist in both
        for col in key_columns:
            if col not in df_a.columns:
                raise ValueError(f"Key column '{col}' not found in DataFrame A")
            if col not in df_b.columns:
                raise ValueError(f"Key column '{col}' not found in DataFrame B")

        # Determine compare columns
        if compare_columns is None:
            common_cols = set(df_a.columns) & set(df_b.columns)
            compare_columns = sorted(common_cols - set(key_columns))

        # Tag source for tracking
        df_a = df_a.copy()
        df_b = df_b.copy()

        # Merge on keys
        merged = pd.merge(
            df_a, df_b,
            on=key_columns,
            how="outer",
            suffixes=("_a", "_b"),
            indicator=True,
        )

        # Categorize rows
        only_in_a = merged[merged["_merge"] == "left_only"].copy()
        only_in_b = merged[merged["_merge"] == "right_only"].copy()
        both = merged[merged["_merge"] == "both"].copy()

        # Compare matched rows column-by-column
        mismatch_rows = []
        column_stats: Dict[str, Dict[str, Any]] = {}

        for col in compare_columns:
            col_a = f"{col}_a"
            col_b = f"{col}_b"

            if col_a not in both.columns or col_b not in both.columns:
                continue

            vals_a = both[col_a]
            vals_b = both[col_b]

            # Compare based on dtype
            if pd.api.types.is_numeric_dtype(vals_a) and pd.api.types.is_numeric_dtype(vals_b):
                if tolerance > 0:
                    is_match = (vals_a - vals_b).abs().fillna(float("inf")) <= tolerance
                else:
                    is_match = vals_a.fillna(np.nan).eq(vals_b.fillna(np.nan)) | (vals_a.isna() & vals_b.isna())
            elif pd.api.types.is_string_dtype(vals_a) or pd.api.types.is_string_dtype(vals_b):
                a_str = vals_a.astype(str).fillna("")
                b_str = vals_b.astype(str).fillna("")
                if not case_sensitive:
                    a_str = a_str.str.lower()
                    b_str = b_str.str.lower()
                is_match = a_str == b_str
            else:
                is_match = vals_a.fillna("__NULL__").astype(str) == vals_b.fillna("__NULL__").astype(str)

            match_count = int(is_match.sum())
            mismatch_count = int((~is_match).sum())
            total = len(both)

            column_stats[col] = {
                "match_count": match_count,
                "mismatch_count": mismatch_count,
                "match_rate": round(match_count / total, 4) if total > 0 else 1.0,
            }

            mismatch_idx = both[~is_match].index
            if len(mismatch_idx) > 0:
                mismatch_rows.append(mismatch_idx)

        # Build mismatch DataFrame
        if mismatch_rows:
            all_mismatch_idx = mismatch_rows[0]
            for idx in mismatch_rows[1:]:
                all_mismatch_idx = all_mismatch_idx.union(idx)
            mismatches_df = both.loc[all_mismatch_idx].drop(columns=["_merge"])
        else:
            mismatches_df = pd.DataFrame()

        matched_idx = both.index.difference(mismatches_df.index) if len(mismatches_df) > 0 else both.index
        matched_df = both.loc[matched_idx].drop(columns=["_merge"])

        # Clean only_in DataFrames
        only_a_cols = key_columns + [c for c in only_in_a.columns if c.endswith("_a")]
        only_b_cols = key_columns + [c for c in only_in_b.columns if c.endswith("_b")]
        only_in_a = only_in_a[only_a_cols].rename(columns={c: c.replace("_a", "") for c in only_a_cols})
        only_in_b = only_in_b[only_b_cols].rename(columns={c: c.replace("_b", "") for c in only_b_cols})

        # Summary
        total_a = len(df_a)
        total_b = len(df_b)
        matched_count = len(matched_df)
        total_comparable = max(total_a, total_b)
        quality_score = round(matched_count / total_comparable, 4) if total_comparable > 0 else 1.0

        result.summary = {
            "total_a": total_a,
            "total_b": total_b,
            "matched": matched_count,
            "mismatched": len(mismatches_df),
            "only_in_a": len(only_in_a),
            "only_in_b": len(only_in_b),
        }
        result.column_stats = column_stats
        result.mismatches = mismatches_df
        result.only_in_a = only_in_a
        result.only_in_b = only_in_b
        result.matched = matched_df
        result.quality_score = quality_score

        return result

    def profile(self, df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
        """
        Generate per-column statistics for a DataFrame.

        Returns:
            Dict mapping column name -> {dtype, null_count, null_pct, unique_count,
                                          min, max, mean, mode, sample_values}
        """
        stats: Dict[str, Dict[str, Any]] = {}
        total = len(df)

        for col in df.columns:
            series = df[col]
            null_count = int(series.isna().sum())
            unique_count = int(series.nunique(dropna=True))

            col_stats: Dict[str, Any] = {
                "dtype": str(series.dtype),
                "null_count": null_count,
                "null_pct": round(null_count / total, 4) if total > 0 else 0.0,
                "unique_count": unique_count,
            }

            # Numeric stats
            if pd.api.types.is_numeric_dtype(series):
                non_null = series.dropna()
                if len(non_null) > 0:
                    col_stats["min"] = float(non_null.min())
                    col_stats["max"] = float(non_null.max())
                    col_stats["mean"] = round(float(non_null.mean()), 4)
                    col_stats["median"] = float(non_null.median())

            # Mode (for any dtype)
            mode_vals = series.mode()
            if len(mode_vals) > 0:
                col_stats["mode"] = str(mode_vals.iloc[0])

            # Sample values (up to 5 unique non-null values)
            non_null = series.dropna().unique()
            sample = [str(v) for v in non_null[:5]]
            col_stats["sample_values"] = sample

            stats[col] = col_stats

        return stats

    def build_diff_dataframe(self, result: ComparisonResult, key_columns: List[str]) -> pd.DataFrame:
        """
        Build a single DataFrame with a _diff_status column showing the
        comparison result for each row.

        Status values: matched, mismatch, only_a, only_b
        """
        frames = []

        if result.matched is not None and len(result.matched) > 0:
            # Use _a columns for matched rows
            cols_a = key_columns + [c for c in result.matched.columns if c.endswith("_a")]
            matched = result.matched[cols_a].copy()
            matched.columns = [c.replace("_a", "") if c.endswith("_a") else c for c in matched.columns]
            matched["_diff_status"] = "matched"
            frames.append(matched)

        if result.mismatches is not None and len(result.mismatches) > 0:
            cols_a = key_columns + [c for c in result.mismatches.columns if c.endswith("_a")]
            mismatched = result.mismatches[cols_a].copy()
            mismatched.columns = [c.replace("_a", "") if c.endswith("_a") else c for c in mismatched.columns]
            mismatched["_diff_status"] = "mismatch"
            frames.append(mismatched)

        if result.only_in_a is not None and len(result.only_in_a) > 0:
            only_a = result.only_in_a.copy()
            only_a["_diff_status"] = "only_a"
            frames.append(only_a)

        if result.only_in_b is not None and len(result.only_in_b) > 0:
            only_b = result.only_in_b.copy()
            only_b["_diff_status"] = "only_b"
            frames.append(only_b)

        if frames:
            return pd.concat(frames, ignore_index=True)
        return pd.DataFrame()
