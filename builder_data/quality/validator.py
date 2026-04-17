"""
Data Validator — validate DataFrame against schema rules.
Checks column existence, data types, ranges, patterns, nullability, and uniqueness.
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import pandas as pd


class ValidationType(Enum):
    """Types of validation checks."""
    COLUMN_EXISTS = "column_exists"
    DTYPE = "dtype"
    NOT_NULL = "not_null"
    UNIQUE = "unique"
    RANGE = "range"
    PATTERN = "pattern"
    IN_SET = "in_set"
    MAX_LENGTH = "max_length"


@dataclass
class ValidationRule:
    """A single validation rule."""
    column: str
    validation_type: ValidationType
    params: Dict[str, Any] = field(default_factory=dict)
    severity: str = "error"  # error, warning, info

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ValidationRule":
        return cls(
            column=data["column"],
            validation_type=ValidationType(data["validation_type"]),
            params=data.get("params", {}),
            severity=data.get("severity", "error"),
        )


@dataclass
class ValidationIssue:
    """A single validation issue found."""
    rule: ValidationRule
    message: str
    affected_rows: int = 0
    sample_values: Optional[List[str]] = None


@dataclass
class ValidationReport:
    """Full validation report."""
    total_rules: int = 0
    passed: int = 0
    failed: int = 0
    warnings: int = 0
    issues: List[ValidationIssue] = field(default_factory=list)
    is_valid: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_rules": self.total_rules,
            "passed": self.passed,
            "failed": self.failed,
            "warnings": self.warnings,
            "is_valid": self.is_valid,
            "issues": [
                {
                    "column": issue.rule.column,
                    "type": issue.rule.validation_type.value,
                    "severity": issue.rule.severity,
                    "message": issue.message,
                    "affected_rows": issue.affected_rows,
                    "sample_values": issue.sample_values,
                }
                for issue in self.issues
            ],
        }


class DataValidator:
    """Validate a DataFrame against a set of rules."""

    def validate(
        self,
        df: pd.DataFrame,
        rules: List[ValidationRule],
    ) -> ValidationReport:
        """
        Validate DataFrame against all rules.

        Args:
            df: Input DataFrame.
            rules: List of validation rules to check.

        Returns:
            ValidationReport with all issues found.
        """
        report = ValidationReport(total_rules=len(rules))

        for rule in rules:
            issue = self._check_rule(df, rule)
            if issue is None:
                report.passed += 1
            else:
                report.issues.append(issue)
                if rule.severity == "error":
                    report.failed += 1
                    report.is_valid = False
                elif rule.severity == "warning":
                    report.warnings += 1

        return report

    def _check_rule(self, df: pd.DataFrame, rule: ValidationRule) -> Optional[ValidationIssue]:
        """Check a single rule. Returns None if passed, ValidationIssue if failed."""
        if rule.validation_type == ValidationType.COLUMN_EXISTS:
            return self._check_column_exists(df, rule)
        elif rule.validation_type == ValidationType.DTYPE:
            return self._check_dtype(df, rule)
        elif rule.validation_type == ValidationType.NOT_NULL:
            return self._check_not_null(df, rule)
        elif rule.validation_type == ValidationType.UNIQUE:
            return self._check_unique(df, rule)
        elif rule.validation_type == ValidationType.RANGE:
            return self._check_range(df, rule)
        elif rule.validation_type == ValidationType.PATTERN:
            return self._check_pattern(df, rule)
        elif rule.validation_type == ValidationType.IN_SET:
            return self._check_in_set(df, rule)
        elif rule.validation_type == ValidationType.MAX_LENGTH:
            return self._check_max_length(df, rule)
        return None

    def _check_column_exists(self, df: pd.DataFrame, rule: ValidationRule) -> Optional[ValidationIssue]:
        if rule.column not in df.columns:
            return ValidationIssue(
                rule=rule,
                message=f"Column '{rule.column}' does not exist",
            )
        return None

    def _check_dtype(self, df: pd.DataFrame, rule: ValidationRule) -> Optional[ValidationIssue]:
        if rule.column not in df.columns:
            return ValidationIssue(rule=rule, message=f"Column '{rule.column}' does not exist")

        expected = rule.params.get("dtype", "object")
        actual = str(df[rule.column].dtype)

        # Flexible matching
        dtype_groups = {
            "int": ["int64", "int32", "int16", "int8", "Int64", "Int32"],
            "float": ["float64", "float32", "Float64"],
            "str": ["object", "string"],
            "bool": ["bool", "boolean"],
            "datetime": ["datetime64[ns]", "datetime64"],
        }

        expected_lower = expected.lower()
        if expected_lower in dtype_groups:
            if actual not in dtype_groups[expected_lower]:
                return ValidationIssue(
                    rule=rule,
                    message=f"Column '{rule.column}' has dtype '{actual}', expected '{expected}'",
                )
        elif actual != expected:
            return ValidationIssue(
                rule=rule,
                message=f"Column '{rule.column}' has dtype '{actual}', expected '{expected}'",
            )
        return None

    def _check_not_null(self, df: pd.DataFrame, rule: ValidationRule) -> Optional[ValidationIssue]:
        if rule.column not in df.columns:
            return ValidationIssue(rule=rule, message=f"Column '{rule.column}' does not exist")

        null_count = int(df[rule.column].isna().sum())
        if null_count > 0:
            samples = df[df[rule.column].isna()].index[:5].tolist()
            return ValidationIssue(
                rule=rule,
                message=f"Column '{rule.column}' has {null_count} null values",
                affected_rows=null_count,
                sample_values=[f"row {idx}" for idx in samples],
            )
        return None

    def _check_unique(self, df: pd.DataFrame, rule: ValidationRule) -> Optional[ValidationIssue]:
        if rule.column not in df.columns:
            return ValidationIssue(rule=rule, message=f"Column '{rule.column}' does not exist")

        dup_count = int(df[rule.column].duplicated().sum())
        if dup_count > 0:
            dups = df[df[rule.column].duplicated(keep=False)][rule.column].unique()[:5]
            return ValidationIssue(
                rule=rule,
                message=f"Column '{rule.column}' has {dup_count} duplicate values",
                affected_rows=dup_count,
                sample_values=[str(v) for v in dups],
            )
        return None

    def _check_range(self, df: pd.DataFrame, rule: ValidationRule) -> Optional[ValidationIssue]:
        if rule.column not in df.columns:
            return ValidationIssue(rule=rule, message=f"Column '{rule.column}' does not exist")

        series = df[rule.column].dropna()
        if not pd.api.types.is_numeric_dtype(series):
            return ValidationIssue(
                rule=rule,
                message=f"Column '{rule.column}' is not numeric, cannot check range",
            )

        min_val = rule.params.get("min")
        max_val = rule.params.get("max")
        violations = pd.Series([False] * len(series), index=series.index)

        if min_val is not None:
            violations |= series < min_val
        if max_val is not None:
            violations |= series > max_val

        viol_count = int(violations.sum())
        if viol_count > 0:
            bad_vals = series[violations].head(5)
            return ValidationIssue(
                rule=rule,
                message=f"Column '{rule.column}' has {viol_count} values outside range [{min_val}, {max_val}]",
                affected_rows=viol_count,
                sample_values=[str(v) for v in bad_vals],
            )
        return None

    def _check_pattern(self, df: pd.DataFrame, rule: ValidationRule) -> Optional[ValidationIssue]:
        if rule.column not in df.columns:
            return ValidationIssue(rule=rule, message=f"Column '{rule.column}' does not exist")

        pattern = rule.params.get("pattern", "")
        if not pattern:
            return None

        series = df[rule.column].dropna().astype(str)
        matches = series.str.match(pattern)
        fail_count = int((~matches).sum())

        if fail_count > 0:
            bad_vals = series[~matches].head(5)
            return ValidationIssue(
                rule=rule,
                message=f"Column '{rule.column}' has {fail_count} values not matching pattern '{pattern}'",
                affected_rows=fail_count,
                sample_values=[str(v) for v in bad_vals],
            )
        return None

    def _check_in_set(self, df: pd.DataFrame, rule: ValidationRule) -> Optional[ValidationIssue]:
        if rule.column not in df.columns:
            return ValidationIssue(rule=rule, message=f"Column '{rule.column}' does not exist")

        allowed = set(rule.params.get("values", []))
        if not allowed:
            return None

        series = df[rule.column].dropna()
        invalid = ~series.isin(allowed)
        fail_count = int(invalid.sum())

        if fail_count > 0:
            bad_vals = series[invalid].unique()[:5]
            return ValidationIssue(
                rule=rule,
                message=f"Column '{rule.column}' has {fail_count} values not in allowed set",
                affected_rows=fail_count,
                sample_values=[str(v) for v in bad_vals],
            )
        return None

    def _check_max_length(self, df: pd.DataFrame, rule: ValidationRule) -> Optional[ValidationIssue]:
        if rule.column not in df.columns:
            return ValidationIssue(rule=rule, message=f"Column '{rule.column}' does not exist")

        max_len = rule.params.get("max_length", 255)
        series = df[rule.column].dropna().astype(str)
        too_long = series.str.len() > max_len
        fail_count = int(too_long.sum())

        if fail_count > 0:
            bad_vals = series[too_long].head(5)
            return ValidationIssue(
                rule=rule,
                message=f"Column '{rule.column}' has {fail_count} values exceeding max length {max_len}",
                affected_rows=fail_count,
                sample_values=[f"{str(v)[:50]}... (len={len(str(v))})" for v in bad_vals],
            )
        return None
