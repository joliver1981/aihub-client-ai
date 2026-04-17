"""
Outcome Tracker
=================
Records every execution outcome (success/failure/corrected) with full context.
Provides query methods for pattern analysis and failure rate calculation.

Storage: builder_service/data/resilience/outcomes.json (append-only, capped at MAX_OUTCOMES)
"""

import json
import logging
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_OUTCOMES = 1000


@dataclass
class ExecutionOutcome:
    """A single recorded execution outcome."""
    outcome_id: str = ""
    timestamp: str = ""
    session_id: str = ""
    plan_id: str = ""
    step_id: str = ""
    capability_id: str = ""
    domain: str = ""
    action: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    status: str = ""              # success, failed, corrected, skipped
    http_status: Optional[int] = None
    error: Optional[str] = None
    error_category: Optional[str] = None
    correction_applied: Optional[str] = None
    correction_result: Optional[str] = None
    duration_ms: int = 0
    user_goal: str = ""
    attempt_number: int = 1

    def __post_init__(self):
        if not self.outcome_id:
            self.outcome_id = str(uuid.uuid4())[:8]
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat() + "Z"


class OutcomeTracker:
    """
    Tracks execution outcomes to a JSON file.
    Thread-safe via atomic writes (temp file + rename).
    """

    def __init__(self, data_dir: Optional[Path] = None):
        if data_dir is None:
            data_dir = Path(__file__).parent.parent / "data"
        self.resilience_dir = data_dir / "resilience"
        self.resilience_dir.mkdir(parents=True, exist_ok=True)
        self.store_path = self.resilience_dir / "outcomes.json"

    def record(self, outcome: ExecutionOutcome) -> None:
        """Append an outcome to the persistent store."""
        try:
            data = self._load()
            data["outcomes"].append(asdict(outcome))

            # Cap at MAX_OUTCOMES — keep the most recent
            if len(data["outcomes"]) > MAX_OUTCOMES:
                data["outcomes"] = data["outcomes"][-MAX_OUTCOMES:]

            self._save(data)
            logger.debug(f"[outcome_tracker] Recorded: {outcome.capability_id} → {outcome.status}")
        except Exception as e:
            logger.warning(f"[outcome_tracker] Failed to record outcome: {e}")

    def get_recent_failures(
        self, capability_id: Optional[str] = None, limit: int = 20
    ) -> List[ExecutionOutcome]:
        """Get recent failures, optionally filtered by capability."""
        data = self._load()
        failures = [
            o for o in data["outcomes"]
            if o.get("status") in ("failed",)
            and (capability_id is None or o.get("capability_id") == capability_id)
        ]
        # Most recent first
        failures.reverse()
        return [self._dict_to_outcome(o) for o in failures[:limit]]

    def get_failure_rate(
        self, capability_id: str, window_hours: int = 168
    ) -> float:
        """Calculate failure rate for a capability over a time window."""
        data = self._load()
        cutoff = (datetime.utcnow() - timedelta(hours=window_hours)).isoformat() + "Z"

        total = 0
        failures = 0
        for o in data["outcomes"]:
            if o.get("capability_id") != capability_id:
                continue
            if o.get("timestamp", "") < cutoff:
                continue
            total += 1
            if o.get("status") in ("failed",):
                failures += 1

        return failures / total if total > 0 else 0.0

    def get_common_errors(
        self, capability_id: Optional[str] = None, limit: int = 10
    ) -> List[dict]:
        """Get most common error categories, grouped and counted."""
        data = self._load()
        counts: Dict[str, int] = {}
        examples: Dict[str, str] = {}

        for o in data["outcomes"]:
            if o.get("status") not in ("failed",):
                continue
            if capability_id and o.get("capability_id") != capability_id:
                continue
            cat = o.get("error_category") or "unknown"
            counts[cat] = counts.get(cat, 0) + 1
            if cat not in examples:
                examples[cat] = o.get("error", "")[:200]

        sorted_cats = sorted(counts.items(), key=lambda x: x[1], reverse=True)
        return [
            {"category": cat, "count": count, "example_error": examples.get(cat, "")}
            for cat, count in sorted_cats[:limit]
        ]

    def get_outcomes_for_session(self, session_id: str) -> List[ExecutionOutcome]:
        """Get all outcomes for a specific session."""
        data = self._load()
        return [
            self._dict_to_outcome(o)
            for o in data["outcomes"]
            if o.get("session_id") == session_id
        ]

    def _load(self) -> dict:
        """Load outcomes from disk."""
        if self.store_path.exists():
            try:
                with open(self.store_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                logger.warning("[outcome_tracker] Corrupt outcomes file, starting fresh")
        return {"version": "1.0", "outcomes": []}

    def _save(self, data: dict) -> None:
        """Atomic write: write to temp file then rename."""
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(self.resilience_dir), suffix=".tmp"
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
            # On Windows, need to remove target first
            if self.store_path.exists():
                self.store_path.unlink()
            os.rename(tmp_path, str(self.store_path))
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    @staticmethod
    def _dict_to_outcome(d: dict) -> ExecutionOutcome:
        """Convert a dict back to ExecutionOutcome."""
        return ExecutionOutcome(
            outcome_id=d.get("outcome_id", ""),
            timestamp=d.get("timestamp", ""),
            session_id=d.get("session_id", ""),
            plan_id=d.get("plan_id", ""),
            step_id=d.get("step_id", ""),
            capability_id=d.get("capability_id", ""),
            domain=d.get("domain", ""),
            action=d.get("action", ""),
            parameters=d.get("parameters", {}),
            status=d.get("status", ""),
            http_status=d.get("http_status"),
            error=d.get("error"),
            error_category=d.get("error_category"),
            correction_applied=d.get("correction_applied"),
            correction_result=d.get("correction_result"),
            duration_ms=d.get("duration_ms", 0),
            user_goal=d.get("user_goal", ""),
            attempt_number=d.get("attempt_number", 1),
        )
