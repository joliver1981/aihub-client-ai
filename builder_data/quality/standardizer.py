"""
Data Standardizer — normalize phone numbers, emails, dates, addresses, and names
to consistent formats.
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


class StandardizeOperation(Enum):
    """Available standardization operations."""
    NORMALIZE_PHONE = "normalize_phone"
    NORMALIZE_EMAIL = "normalize_email"
    NORMALIZE_DATE_FORMAT = "normalize_date_format"
    STANDARDIZE_ADDRESS = "standardize_address"
    STANDARDIZE_NAMES = "standardize_names"


@dataclass
class StandardizeRule:
    """A single standardization rule."""
    column: str
    operation: StandardizeOperation
    params: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StandardizeRule":
        return cls(
            column=data["column"],
            operation=StandardizeOperation(data["operation"]),
            params=data.get("params", {}),
        )


# Common address abbreviations
_ADDRESS_ABBREVS = {
    r"\bst\b": "Street",
    r"\bstr\b": "Street",
    r"\bave\b": "Avenue",
    r"\bblvd\b": "Boulevard",
    r"\bdr\b": "Drive",
    r"\bln\b": "Lane",
    r"\brd\b": "Road",
    r"\bct\b": "Court",
    r"\bpl\b": "Place",
    r"\bpkwy\b": "Parkway",
    r"\bhwy\b": "Highway",
    r"\bapt\b": "Apartment",
    r"\bste\b": "Suite",
    r"\bfl\b": "Floor",
}


class DataStandardizer:
    """Apply standardization rules to normalize data formats."""

    def standardize(
        self,
        df: pd.DataFrame,
        rules: List[StandardizeRule],
    ) -> Tuple[pd.DataFrame, Dict[str, int]]:
        """
        Apply standardization rules.

        Args:
            df: Input DataFrame.
            rules: List of standardization rules.

        Returns:
            Tuple of (standardized DataFrame, dict of rule_index -> changes_count).
        """
        result = df.copy()
        changes: Dict[str, int] = {}

        for i, rule in enumerate(rules):
            rule_key = f"{i}_{rule.operation.value}_{rule.column}"
            if rule.column not in result.columns:
                raise ValueError(f"Column '{rule.column}' not found in DataFrame")

            before = result[rule.column].copy()

            if rule.operation == StandardizeOperation.NORMALIZE_PHONE:
                result[rule.column] = self._normalize_phone(result[rule.column], rule.params)
            elif rule.operation == StandardizeOperation.NORMALIZE_EMAIL:
                result[rule.column] = self._normalize_email(result[rule.column])
            elif rule.operation == StandardizeOperation.NORMALIZE_DATE_FORMAT:
                result[rule.column] = self._normalize_date_format(result[rule.column], rule.params)
            elif rule.operation == StandardizeOperation.STANDARDIZE_ADDRESS:
                result[rule.column] = self._standardize_address(result[rule.column])
            elif rule.operation == StandardizeOperation.STANDARDIZE_NAMES:
                result[rule.column] = self._standardize_names(result[rule.column], rule.params)

            changed = (before.astype(str).fillna("") != result[rule.column].astype(str).fillna("")).sum()
            changes[rule_key] = int(changed)

        return result, changes

    def _normalize_phone(self, series: pd.Series, params: Dict[str, Any]) -> pd.Series:
        """
        Normalize phone numbers by extracting digits and formatting.
        Default format: +1-XXX-XXX-XXXX (US)
        """
        country_code = params.get("country_code", "1")
        fmt = params.get("format", "+{cc}-{a}-{b}-{c}")

        def normalize(val):
            if pd.isna(val):
                return val
            digits = re.sub(r"\D", "", str(val))
            # Strip leading country code if present
            if len(digits) == 11 and digits.startswith(country_code):
                digits = digits[len(country_code):]
            if len(digits) == 10:
                return fmt.format(cc=country_code, a=digits[:3], b=digits[3:6], c=digits[6:])
            return str(val)  # Return original if can't parse

        return series.apply(normalize)

    def _normalize_email(self, series: pd.Series) -> pd.Series:
        """Normalize emails: strip whitespace, lowercase."""
        if series.dtype != "object":
            return series
        return series.str.strip().str.lower()

    def _normalize_date_format(self, series: pd.Series, params: Dict[str, Any]) -> pd.Series:
        """Normalize date values to a consistent format."""
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

    def _standardize_address(self, series: pd.Series) -> pd.Series:
        """Expand common address abbreviations and title-case."""
        if series.dtype != "object":
            return series

        def normalize_addr(val):
            if pd.isna(val):
                return val
            result = str(val)
            for abbr, full in _ADDRESS_ABBREVS.items():
                result = re.sub(abbr, full, result, flags=re.IGNORECASE)
            return result.title()

        return series.apply(normalize_addr)

    def _standardize_names(self, series: pd.Series, params: Dict[str, Any]) -> pd.Series:
        """
        Standardize personal names: title-case, handle prefixes/suffixes.
        """
        if series.dtype != "object":
            return series

        trim = params.get("trim", True)
        case = params.get("case", "title")

        result = series.copy()
        if trim:
            result = result.str.strip()
            # Collapse multiple spaces
            result = result.str.replace(r"\s+", " ", regex=True)

        if case == "title":
            result = result.str.title()
        elif case == "upper":
            result = result.str.upper()
        elif case == "lower":
            result = result.str.lower()

        return result
