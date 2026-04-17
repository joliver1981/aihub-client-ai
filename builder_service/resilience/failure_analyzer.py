"""
Failure Analyzer
==================
Classifies execution failures by root cause and suggests remediation strategies.
Uses rule-based classification for known patterns (~80% of cases) and falls back
to LLM-based analysis for ambiguous errors.
"""

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class FailureCategory(str, Enum):
    WRONG_PARAMETERS = "wrong_parameters"
    MISSING_RESOURCE = "missing_resource"
    PERMISSION_DENIED = "permission_denied"
    EXTERNAL_DEPENDENCY = "external_dependency"
    API_ERROR = "api_error"
    VALIDATION_ERROR = "validation_error"
    MISSING_CAPABILITY = "missing_capability"
    CONTENT_FILTER = "content_filter"
    UNKNOWN = "unknown"


# Map categories to whether they are typically auto-fixable
_AUTO_FIXABLE = {
    FailureCategory.WRONG_PARAMETERS: True,
    FailureCategory.MISSING_RESOURCE: True,
    FailureCategory.VALIDATION_ERROR: True,
    FailureCategory.CONTENT_FILTER: True,
    FailureCategory.EXTERNAL_DEPENDENCY: False,
    FailureCategory.PERMISSION_DENIED: False,
    FailureCategory.MISSING_CAPABILITY: False,
    FailureCategory.API_ERROR: True,
    FailureCategory.UNKNOWN: False,
}

# Map categories to default correction strategies
_DEFAULT_STRATEGIES = {
    FailureCategory.WRONG_PARAMETERS: ["parameter_correction", "retry_with_enrichment"],
    FailureCategory.MISSING_RESOURCE: ["create_prerequisite", "ask_user"],
    FailureCategory.VALIDATION_ERROR: ["parameter_correction", "retry_with_defaults"],
    FailureCategory.CONTENT_FILTER: ["retry_with_defaults"],
    FailureCategory.API_ERROR: ["retry_with_defaults", "parameter_correction"],
    FailureCategory.EXTERNAL_DEPENDENCY: ["ask_user"],
    FailureCategory.PERMISSION_DENIED: ["ask_user"],
    FailureCategory.MISSING_CAPABILITY: ["ask_user"],
    FailureCategory.UNKNOWN: ["ask_user"],
}


@dataclass
class FailureAnalysis:
    """Result of analyzing a failure."""
    category: FailureCategory
    root_cause: str
    confidence: float = 0.0
    suggested_strategies: List[str] = field(default_factory=list)
    missing_info: List[str] = field(default_factory=list)
    auto_fixable: bool = False
    error_field: Optional[str] = None      # Specific field that caused the error
    error_details: Optional[str] = None    # Raw error details for correction engine

    def __post_init__(self):
        if not self.suggested_strategies:
            self.suggested_strategies = list(_DEFAULT_STRATEGIES.get(self.category, []))
        if self.auto_fixable is None:
            self.auto_fixable = _AUTO_FIXABLE.get(self.category, False)


class FailureAnalyzer:
    """
    Analyzes execution failures to determine root cause and remediation.
    Rule-based first, LLM fallback for ambiguous cases.
    """

    def analyze(
        self,
        http_status: Optional[int],
        error_message: Optional[str],
        response_data: Optional[Dict[str, Any]],
        capability_id: str,
        parameters: dict,
        system_context: Optional[Any] = None,
    ) -> FailureAnalysis:
        """
        Classify a failure and suggest remediation strategies.

        Args:
            http_status: HTTP status code from the failed request (may be None for connection errors)
            error_message: Error message string
            response_data: Full response data dict from the API
            capability_id: The capability that was attempted (e.g., "agents.create")
            parameters: The parameters that were sent
            system_context: Optional SystemContext for resource validation

        Returns:
            FailureAnalysis with category, root cause, and suggested strategies
        """
        error_str = (error_message or "").lower()
        resp_str = str(response_data or {}).lower()
        combined = f"{error_str} {resp_str}"

        # Try rule-based classification first
        analysis = self._classify_by_rules(
            http_status, error_str, resp_str, combined,
            capability_id, parameters, system_context
        )
        if analysis:
            logger.info(
                f"[failure_analyzer] {capability_id}: {analysis.category.value} "
                f"(confidence={analysis.confidence:.2f}, auto_fixable={analysis.auto_fixable})"
            )
            return analysis

        # Fallback: unknown
        logger.info(f"[failure_analyzer] {capability_id}: could not classify, marking as unknown")
        return FailureAnalysis(
            category=FailureCategory.UNKNOWN,
            root_cause=error_message or "Unknown error",
            confidence=0.3,
            auto_fixable=False,
            error_details=error_message,
        )

    def _classify_by_rules(
        self,
        http_status: Optional[int],
        error_str: str,
        resp_str: str,
        combined: str,
        capability_id: str,
        parameters: dict,
        system_context: Optional[Any],
    ) -> Optional[FailureAnalysis]:
        """Rule-based classification for known error patterns."""

        # ── Permission denied ──
        if http_status == 403 or "permission" in combined or "forbidden" in combined or "unauthorized" in combined:
            return FailureAnalysis(
                category=FailureCategory.PERMISSION_DENIED,
                root_cause="Insufficient permissions for this operation",
                confidence=0.95,
                auto_fixable=False,
                missing_info=["Required role or permissions"],
                error_details=error_str,
            )

        # ── Authentication failure ──
        if http_status == 401 or "authentication" in combined or "not authenticated" in combined:
            return FailureAnalysis(
                category=FailureCategory.PERMISSION_DENIED,
                root_cause="Authentication failed — invalid or missing API key",
                confidence=0.95,
                auto_fixable=False,
                error_details=error_str,
            )

        # ── Auth redirect (302 to /login) ──
        # Flask's @login_required returns 302 redirect to /login when there's
        # no session.  This means the endpoint needs @api_key_or_session_required
        # instead, or the API key was not sent.
        if http_status in (301, 302, 303, 307, 308) or "/login" in combined or "redirecting" in combined:
            return FailureAnalysis(
                category=FailureCategory.PERMISSION_DENIED,
                root_cause=(
                    "Request was redirected to login page — the endpoint uses "
                    "@login_required instead of @api_key_or_session_required, "
                    "so API key authentication is not accepted."
                ),
                confidence=0.95,
                auto_fixable=False,
                missing_info=["Endpoint decorator needs to be updated to support API key auth"],
                error_details=error_str,
            )

        # ── Content filter ──
        if "content management policy" in combined or "content_filter" in combined or "content filter" in combined:
            return FailureAnalysis(
                category=FailureCategory.CONTENT_FILTER,
                root_cause="Request was blocked by the LLM content filter",
                confidence=0.95,
                auto_fixable=True,
                suggested_strategies=["retry_with_defaults"],
                error_details=error_str,
            )

        # ── Missing resource (404) ──
        if http_status == 404 or "not found" in combined:
            # Try to identify which resource is missing
            missing = self._detect_missing_resource(combined, capability_id, parameters, system_context)
            return FailureAnalysis(
                category=FailureCategory.MISSING_RESOURCE,
                root_cause=missing or "Referenced resource not found",
                confidence=0.90,
                auto_fixable=True,
                suggested_strategies=["create_prerequisite", "ask_user"],
                missing_info=[missing] if missing else [],
                error_details=error_str,
            )

        # ── Validation error (422) ──
        if http_status == 422 or "validation" in combined or "invalid" in combined:
            error_field = self._extract_error_field(combined)
            return FailureAnalysis(
                category=FailureCategory.VALIDATION_ERROR,
                root_cause=f"Validation failed{f' for field: {error_field}' if error_field else ''}",
                confidence=0.85,
                auto_fixable=True,
                suggested_strategies=["parameter_correction", "retry_with_defaults"],
                error_field=error_field,
                error_details=error_str,
            )

        # ── Wrong parameters (400) ──
        if http_status == 400 or "bad request" in combined:
            error_field = self._extract_error_field(combined)
            return FailureAnalysis(
                category=FailureCategory.WRONG_PARAMETERS,
                root_cause=f"Invalid parameters{f': {error_field}' if error_field else ''}",
                confidence=0.85,
                auto_fixable=True,
                suggested_strategies=["parameter_correction", "retry_with_enrichment"],
                error_field=error_field,
                error_details=error_str,
            )

        # ── Missing required fields ──
        if "required" in combined and ("missing" in combined or "field" in combined):
            error_field = self._extract_error_field(combined)
            return FailureAnalysis(
                category=FailureCategory.WRONG_PARAMETERS,
                root_cause=f"Missing required field{f': {error_field}' if error_field else ''}",
                confidence=0.90,
                auto_fixable=True,
                suggested_strategies=["retry_with_enrichment", "retry_with_defaults"],
                error_field=error_field,
                error_details=error_str,
            )

        # ── External dependency (connection errors, timeouts) ──
        if any(kw in combined for kw in ["timeout", "connection refused", "connection error",
                                          "unreachable", "dns", "socket", "econnrefused"]):
            return FailureAnalysis(
                category=FailureCategory.EXTERNAL_DEPENDENCY,
                root_cause="Service unreachable or timed out",
                confidence=0.90,
                auto_fixable=False,
                error_details=error_str,
            )

        # ── Server error (5xx) ──
        if http_status and 500 <= http_status < 600:
            return FailureAnalysis(
                category=FailureCategory.API_ERROR,
                root_cause=f"Server error (HTTP {http_status})",
                confidence=0.80,
                auto_fixable=True,
                suggested_strategies=["retry_with_defaults"],
                error_details=error_str,
            )

        # ── Capability not found in registry ──
        if "no action found" in combined or "unknown capability" in combined or "not registered" in combined:
            return FailureAnalysis(
                category=FailureCategory.MISSING_CAPABILITY,
                root_cause=f"Capability '{capability_id}' is not registered in the action registry",
                confidence=0.95,
                auto_fixable=False,
                error_details=error_str,
            )

        # ── Duplicate / conflict (409) ──
        if http_status == 409 or "already exists" in combined or "duplicate" in combined:
            return FailureAnalysis(
                category=FailureCategory.VALIDATION_ERROR,
                root_cause="Resource already exists (duplicate)",
                confidence=0.85,
                auto_fixable=True,
                suggested_strategies=["parameter_correction"],
                error_details=error_str,
            )

        return None

    def _detect_missing_resource(
        self,
        error_combined: str,
        capability_id: str,
        parameters: dict,
        system_context: Optional[Any],
    ) -> Optional[str]:
        """Try to identify specifically which resource is missing."""
        # Check for common reference fields
        for field_name in ("connection_id", "agent_id", "workflow_id", "tool_name"):
            value = parameters.get(field_name)
            if value is not None:
                if str(value).lower() in error_combined:
                    return f"{field_name}={value} does not exist"

        # Data agent needs a connection
        if "agents.create_data_agent" in capability_id and "connection" in error_combined:
            conn_id = parameters.get("connection_id", "unknown")
            return f"Connection '{conn_id}' not found — create it first"

        return None

    @staticmethod
    def _extract_error_field(error_text: str) -> Optional[str]:
        """Extract the specific field name from an error message."""
        # Pattern: "field 'X' is required" or "missing field: X" or "'X' is invalid"
        patterns = [
            r"field\s+['\"](\w+)['\"]",
            r"missing\s+(?:field:?\s*)?['\"]?(\w+)['\"]?",
            r"['\"](\w+)['\"]\s+is\s+(?:required|invalid|missing)",
            r"parameter\s+['\"](\w+)['\"]",
        ]
        for pattern in patterns:
            match = re.search(pattern, error_text, re.IGNORECASE)
            if match:
                return match.group(1)
        return None
