"""
Scrub Step — deduplicate and cleanse data in a single step.
"""

import logging
import time
from typing import Dict, List, Tuple

import pandas as pd

from pipeline.models import StepDefinition, StepResult
from pipeline.steps.base import BaseStep
from quality.deduplicator import Deduplicator, DeduplicationStrategy
from quality.cleanser import DataCleanser, CleanseRule

logger = logging.getLogger(__name__)


class ScrubStep(BaseStep):
    """
    Combines deduplication and cleansing in a single pipeline step.

    Config:
        dedup_columns: list[str] | None   — columns for dedup (None = skip dedup)
        dedup_strategy: str               — "exact" or "fuzzy"
        fuzzy_threshold: float            — fuzzy match threshold (0.0-1.0)
        keep: str                         — "first" or "last"
        cleanse_rules: list[dict]         — list of {column, operation, params}
    """

    def __init__(self):
        self.deduplicator = Deduplicator()
        self.cleanser = DataCleanser()

    async def execute(
        self,
        step: StepDefinition,
        input_frames: Dict[str, pd.DataFrame],
    ) -> Tuple[pd.DataFrame, StepResult]:
        start = time.time()

        try:
            df = self._get_single_input(step, input_frames).copy()
            config = step.config
            quality_score = 1.0

            # Phase 1: Deduplication
            dedup_columns = config.get("dedup_columns")
            if dedup_columns:
                strategy_str = config.get("dedup_strategy", "exact")
                strategy = DeduplicationStrategy(strategy_str)
                threshold = config.get("fuzzy_threshold", 0.85)
                keep = config.get("keep", "first")

                dedup_result = self.deduplicator.deduplicate(
                    df,
                    key_columns=dedup_columns,
                    strategy=strategy,
                    fuzzy_threshold=threshold,
                    keep=keep,
                )
                df = dedup_result.clean_df
                quality_score = min(quality_score, dedup_result.quality_score)
                logger.info(
                    f"Scrub step '{step.step_id}' dedup: "
                    f"{dedup_result.original_count} -> {dedup_result.deduplicated_count} rows "
                    f"({dedup_result.duplicates_found} duplicates removed)"
                )

            # Phase 2: Cleansing
            cleanse_rules_raw = config.get("cleanse_rules", [])
            if cleanse_rules_raw:
                rules = [CleanseRule.from_dict(r) for r in cleanse_rules_raw]
                df, changes = self.cleanser.cleanse(df, rules)
                total_changes = sum(changes.values())
                logger.info(
                    f"Scrub step '{step.step_id}' cleanse: "
                    f"{total_changes} changes across {len(rules)} rules"
                )

            duration = int((time.time() - start) * 1000)
            return df, self._build_result(
                step, df,
                duration_ms=duration,
                quality_score=quality_score,
            )

        except Exception as e:
            duration = int((time.time() - start) * 1000)
            logger.error(f"Scrub step '{step.step_id}' failed: {e}")
            return pd.DataFrame(), self._build_result(
                step, pd.DataFrame(), status="failed", duration_ms=duration, error=str(e)
            )
