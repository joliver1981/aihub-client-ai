"""
Deduplicator — detect and remove duplicate rows using exact or fuzzy matching.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

import pandas as pd


class DeduplicationStrategy(Enum):
    """Strategy for detecting duplicates."""
    EXACT = "exact"
    FUZZY = "fuzzy"


@dataclass
class DeduplicationResult:
    """Result of a deduplication operation."""
    original_count: int = 0
    deduplicated_count: int = 0
    duplicates_found: int = 0
    duplicate_groups: Optional[pd.DataFrame] = None  # Tagged with _dup_group_id
    clean_df: Optional[pd.DataFrame] = None
    quality_score: float = 1.0

    def to_dict(self) -> Dict:
        return {
            "original_count": self.original_count,
            "deduplicated_count": self.deduplicated_count,
            "duplicates_found": self.duplicates_found,
            "quality_score": self.quality_score,
        }


class Deduplicator:
    """
    Detect and remove duplicates from a DataFrame.
    Supports exact matching and fuzzy string matching.
    """

    def deduplicate(
        self,
        df: pd.DataFrame,
        key_columns: List[str],
        strategy: DeduplicationStrategy = DeduplicationStrategy.EXACT,
        fuzzy_threshold: float = 0.85,
        keep: str = "first",
    ) -> DeduplicationResult:
        """
        Find and remove duplicates.

        Args:
            df: Input DataFrame.
            key_columns: Columns to check for duplicates.
            strategy: EXACT or FUZZY matching.
            fuzzy_threshold: Similarity threshold for fuzzy matching (0.0-1.0).
            keep: Which duplicate to keep: "first", "last".

        Returns:
            DeduplicationResult with clean DataFrame and duplicate groups.
        """
        result = DeduplicationResult(original_count=len(df))

        # Validate columns
        for col in key_columns:
            if col not in df.columns:
                raise ValueError(f"Key column '{col}' not found in DataFrame")

        if strategy == DeduplicationStrategy.EXACT:
            clean_df, groups_df = self._exact_dedup(df, key_columns, keep)
        elif strategy == DeduplicationStrategy.FUZZY:
            clean_df, groups_df = self._fuzzy_dedup(df, key_columns, fuzzy_threshold, keep)
        else:
            raise ValueError(f"Unknown deduplication strategy: {strategy}")

        result.clean_df = clean_df
        result.duplicate_groups = groups_df
        result.deduplicated_count = len(clean_df)
        result.duplicates_found = result.original_count - result.deduplicated_count
        result.quality_score = round(
            result.deduplicated_count / result.original_count, 4
        ) if result.original_count > 0 else 1.0

        return result

    def _exact_dedup(
        self,
        df: pd.DataFrame,
        key_columns: List[str],
        keep: str,
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """Exact duplicate detection using pandas drop_duplicates."""
        working = df.copy()

        # Tag duplicate groups
        working["_dup_group_id"] = (
            working.groupby(key_columns).ngroup()
        )

        # Mark duplicates
        is_dup = working.duplicated(subset=key_columns, keep=False)
        groups_df = working[is_dup].copy()

        # Deduplicate
        clean_df = working.drop_duplicates(subset=key_columns, keep=keep).copy()
        clean_df = clean_df.drop(columns=["_dup_group_id"])

        return clean_df, groups_df

    def _fuzzy_dedup(
        self,
        df: pd.DataFrame,
        key_columns: List[str],
        threshold: float,
        keep: str,
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Fuzzy duplicate detection.
        Uses rapidfuzz if available, falls back to difflib.
        """
        try:
            from rapidfuzz import fuzz as rf_fuzz
            def similarity(a: str, b: str) -> float:
                return rf_fuzz.ratio(a, b) / 100.0
        except ImportError:
            from difflib import SequenceMatcher
            def similarity(a: str, b: str) -> float:
                return SequenceMatcher(None, a, b).ratio()

        working = df.copy()

        # Build composite key strings for comparison
        composite_keys = working[key_columns].astype(str).agg(" ".join, axis=1).tolist()

        # Assign group IDs via greedy clustering
        n = len(composite_keys)
        group_ids = [-1] * n
        current_group = 0

        for i in range(n):
            if group_ids[i] != -1:
                continue
            group_ids[i] = current_group
            for j in range(i + 1, n):
                if group_ids[j] != -1:
                    continue
                sim = similarity(composite_keys[i], composite_keys[j])
                if sim >= threshold:
                    group_ids[j] = current_group
            current_group += 1

        working["_dup_group_id"] = group_ids

        # Find groups with more than one member
        group_counts = working["_dup_group_id"].value_counts()
        dup_groups = group_counts[group_counts > 1].index
        is_in_dup_group = working["_dup_group_id"].isin(dup_groups)
        groups_df = working[is_in_dup_group].copy()

        # Keep first or last from each group
        if keep == "last":
            clean_df = working.drop_duplicates(subset=["_dup_group_id"], keep="last").copy()
        else:
            clean_df = working.drop_duplicates(subset=["_dup_group_id"], keep="first").copy()

        clean_df = clean_df.drop(columns=["_dup_group_id"])

        return clean_df, groups_df
