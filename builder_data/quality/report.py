"""
Quality Report — aggregates results from all quality modules into a
single quality score (0-100) and a markdown summary.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd


@dataclass
class QualityReportResult:
    """Aggregated quality report."""
    overall_score: float = 100.0
    profile: Optional[Dict[str, Dict[str, Any]]] = None
    validation: Optional[Dict[str, Any]] = None
    dedup: Optional[Dict[str, Any]] = None
    comparison: Optional[Dict[str, Any]] = None
    markdown_summary: str = ""
    breakdown: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_score": self.overall_score,
            "profile": self.profile,
            "validation": self.validation,
            "dedup": self.dedup,
            "comparison": self.comparison,
            "breakdown": self.breakdown,
            "markdown_summary": self.markdown_summary,
        }


class QualityReport:
    """
    Generate an aggregated quality report from individual module results.
    """

    def generate(
        self,
        df: pd.DataFrame,
        profile: Optional[Dict[str, Dict[str, Any]]] = None,
        validation: Optional[Dict[str, Any]] = None,
        dedup: Optional[Dict[str, Any]] = None,
        comparison: Optional[Dict[str, Any]] = None,
    ) -> QualityReportResult:
        """
        Generate a comprehensive quality report.

        Args:
            df: The DataFrame being analyzed.
            profile: Column-level profile stats (from DataComparator.profile).
            validation: Validation report dict (from DataValidator.validate().to_dict()).
            dedup: Deduplication result dict (from DeduplicationResult.to_dict()).
            comparison: Comparison result dict (from ComparisonResult.to_dict()).

        Returns:
            QualityReportResult with overall score and markdown summary.
        """
        result = QualityReportResult()
        result.profile = profile
        result.validation = validation
        result.dedup = dedup
        result.comparison = comparison

        scores: Dict[str, float] = {}
        weights: Dict[str, float] = {}

        # ── Completeness Score (from profile) ──
        if profile:
            completeness = self._calculate_completeness(profile)
            scores["completeness"] = completeness
            weights["completeness"] = 0.30

        # ── Validity Score (from validation) ──
        if validation:
            total_rules = validation.get("total_rules", 0)
            passed = validation.get("passed", 0)
            if total_rules > 0:
                scores["validity"] = round((passed / total_rules) * 100, 1)
            else:
                scores["validity"] = 100.0
            weights["validity"] = 0.30

        # ── Uniqueness Score (from dedup) ──
        if dedup:
            scores["uniqueness"] = round(dedup.get("quality_score", 1.0) * 100, 1)
            weights["uniqueness"] = 0.20

        # ── Consistency Score (from comparison) ──
        if comparison:
            scores["consistency"] = round(comparison.get("quality_score", 1.0) * 100, 1)
            weights["consistency"] = 0.20

        # ── Calculate weighted overall score ──
        if scores:
            total_weight = sum(weights[k] for k in scores)
            if total_weight > 0:
                result.overall_score = round(
                    sum(scores[k] * weights[k] for k in scores) / total_weight, 1
                )
            else:
                result.overall_score = 100.0
        result.breakdown = scores

        # ── Generate markdown summary ──
        result.markdown_summary = self._generate_markdown(df, result)

        return result

    def _calculate_completeness(self, profile: Dict[str, Dict[str, Any]]) -> float:
        """Calculate completeness score based on null percentages."""
        if not profile:
            return 100.0
        null_pcts = [col_stats.get("null_pct", 0.0) for col_stats in profile.values()]
        avg_null_pct = sum(null_pcts) / len(null_pcts) if null_pcts else 0.0
        return round((1.0 - avg_null_pct) * 100, 1)

    def _generate_markdown(self, df: pd.DataFrame, report: QualityReportResult) -> str:
        """Generate a human-readable markdown summary."""
        lines = []
        lines.append(f"## Data Quality Report")
        lines.append(f"")
        lines.append(f"**Overall Score: {report.overall_score}/100**")
        lines.append(f"")
        lines.append(f"| Dimension | Score |")
        lines.append(f"|-----------|-------|")
        for dimension, score in report.breakdown.items():
            emoji = self._score_emoji(score)
            lines.append(f"| {dimension.title()} | {emoji} {score}/100 |")
        lines.append(f"")

        # Data shape
        lines.append(f"### Dataset Overview")
        lines.append(f"- **Rows:** {len(df):,}")
        lines.append(f"- **Columns:** {len(df.columns)}")
        lines.append(f"")

        # Profile highlights
        if report.profile:
            high_null = [
                (col, stats.get("null_pct", 0))
                for col, stats in report.profile.items()
                if stats.get("null_pct", 0) > 0.05
            ]
            if high_null:
                lines.append(f"### Columns with High Null Rates")
                for col, pct in sorted(high_null, key=lambda x: -x[1]):
                    lines.append(f"- `{col}`: {pct:.1%} null")
                lines.append(f"")

        # Validation issues
        if report.validation:
            issues = report.validation.get("issues", [])
            if issues:
                lines.append(f"### Validation Issues ({len(issues)})")
                for issue in issues[:10]:
                    sev = issue.get("severity", "error")
                    icon = "X" if sev == "error" else "!"
                    lines.append(f"- [{icon}] {issue.get('message', '')}")
                lines.append(f"")

        # Dedup info
        if report.dedup:
            dups = report.dedup.get("duplicates_found", 0)
            if dups > 0:
                lines.append(f"### Duplicates")
                lines.append(f"- **{dups:,}** duplicate rows found")
                lines.append(f"")

        return "\n".join(lines)

    def _score_emoji(self, score: float) -> str:
        if score >= 90:
            return "[GOOD]"
        elif score >= 70:
            return "[OK]"
        elif score >= 50:
            return "[WARN]"
        return "[BAD]"
