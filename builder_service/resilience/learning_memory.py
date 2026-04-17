"""
Learning Memory
=================
Stores successful and failure patterns from past executions.
Injected into planning prompts to improve future plans.

Patterns are lightweight records of what worked and what failed.
Over time, the agent learns to avoid known pitfalls and reuse
proven approaches.

Storage: builder_service/data/resilience/patterns.json
"""

import json
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_PATTERNS = 200
PATTERN_MIN_CONFIDENCE = 0.3


@dataclass
class Pattern:
    """A learned pattern from execution outcomes."""
    pattern_id: str = ""
    pattern_type: str = ""        # "success", "failure", "workaround"
    capability_id: str = ""
    domain: str = ""
    trigger: str = ""              # What triggered this pattern (user intent or error)
    resolution: str = ""           # What worked or what to avoid
    frequency: int = 1
    last_seen: str = ""
    confidence: float = 0.5

    def __post_init__(self):
        if not self.pattern_id:
            self.pattern_id = str(uuid.uuid4())[:8]
        if not self.last_seen:
            self.last_seen = datetime.utcnow().isoformat() + "Z"
        if not self.domain and self.capability_id:
            self.domain = self.capability_id.split(".")[0] if "." in self.capability_id else ""


class LearningMemory:
    """
    Stores and retrieves learned patterns for planning improvement.
    Patterns are pruned by frequency and age when exceeding MAX_PATTERNS.
    """

    def __init__(self, data_dir: Optional[Path] = None):
        if data_dir is None:
            data_dir = Path(__file__).parent.parent / "data"
        self.resilience_dir = data_dir / "resilience"
        self.resilience_dir.mkdir(parents=True, exist_ok=True)
        self.store_path = self.resilience_dir / "patterns.json"

    def record_success(
        self,
        capability_id: str,
        user_intent: str,
        approach: str,
        parameters_used: dict,
    ) -> None:
        """Record a successful execution pattern."""
        # Create a concise trigger from the user intent
        trigger = user_intent[:200] if user_intent else capability_id

        # Create a concise resolution from the approach and key parameters
        param_summary = ", ".join(
            f"{k}={repr(v)[:50]}" for k, v in list(parameters_used.items())[:5]
        )
        resolution = f"{approach[:200]}"
        if param_summary:
            resolution += f" (params: {param_summary})"

        self._record_pattern(
            pattern_type="success",
            capability_id=capability_id,
            trigger=trigger,
            resolution=resolution,
        )

    def record_failure(
        self,
        capability_id: str,
        error_category: str,
        root_cause: str,
        resolution: Optional[str] = None,
    ) -> None:
        """Record a failure pattern (and its resolution if one was found)."""
        trigger = f"{error_category}: {root_cause[:200]}"
        resolution_text = resolution or f"Avoid: {root_cause[:200]}"

        self._record_pattern(
            pattern_type="failure",
            capability_id=capability_id,
            trigger=trigger,
            resolution=resolution_text,
        )

    def record_workaround(
        self,
        capability_id: str,
        original_error: str,
        workaround: str,
    ) -> None:
        """Record a workaround that was found during correction."""
        self._record_pattern(
            pattern_type="workaround",
            capability_id=capability_id,
            trigger=original_error[:200],
            resolution=workaround[:300],
        )

    def get_planning_hints(self, domains: List[str]) -> str:
        """
        Generate a text block of learned patterns for the planning prompt.
        Returns patterns relevant to the given domains.
        """
        patterns = self._get_relevant_by_domain(domains)
        if not patterns:
            return ""

        # Group by type
        failures = [p for p in patterns if p.pattern_type == "failure"]
        workarounds = [p for p in patterns if p.pattern_type == "workaround"]
        successes = [p for p in patterns if p.pattern_type == "success" and p.frequency >= 3]

        lines = ["LEARNED PATTERNS FROM PAST EXECUTIONS:"]

        if failures:
            lines.append("\nKnown issues to avoid:")
            for p in failures[:5]:
                lines.append(f"  - [{p.capability_id}] {p.resolution} (seen {p.frequency}x)")

        if workarounds:
            lines.append("\nWorkarounds that have worked:")
            for p in workarounds[:5]:
                lines.append(f"  - [{p.capability_id}] {p.resolution}")

        if successes:
            lines.append("\nProven approaches:")
            for p in successes[:3]:
                lines.append(f"  - [{p.capability_id}] {p.resolution}")

        return "\n".join(lines)

    def get_known_failures(self, capability_id: str) -> List[Pattern]:
        """Get known failure patterns for a specific capability."""
        data = self._load()
        return [
            self._dict_to_pattern(p)
            for p in data["patterns"]
            if p.get("capability_id") == capability_id
            and p.get("pattern_type") in ("failure", "workaround")
            and p.get("confidence", 0) >= PATTERN_MIN_CONFIDENCE
        ]

    def _record_pattern(
        self,
        pattern_type: str,
        capability_id: str,
        trigger: str,
        resolution: str,
    ) -> None:
        """Record or update a pattern in the store."""
        try:
            data = self._load()
            patterns = data["patterns"]

            # Check for existing similar pattern (same type + capability + similar trigger)
            existing = self._find_similar(patterns, pattern_type, capability_id, trigger)

            if existing is not None:
                # Update existing: increment frequency, refresh timestamp, boost confidence
                existing["frequency"] = existing.get("frequency", 1) + 1
                existing["last_seen"] = datetime.utcnow().isoformat() + "Z"
                existing["confidence"] = min(1.0, existing.get("confidence", 0.5) + 0.1)
                # Update resolution if it's more detailed
                if len(resolution) > len(existing.get("resolution", "")):
                    existing["resolution"] = resolution
                logger.debug(
                    f"[learning_memory] Updated pattern: {capability_id}/{pattern_type} "
                    f"(freq={existing['frequency']})"
                )
            else:
                # Create new pattern
                pattern = Pattern(
                    pattern_type=pattern_type,
                    capability_id=capability_id,
                    trigger=trigger,
                    resolution=resolution,
                )
                patterns.append(asdict(pattern))
                logger.debug(f"[learning_memory] New pattern: {capability_id}/{pattern_type}")

            # Prune if needed
            if len(patterns) > MAX_PATTERNS:
                self._prune(patterns)

            self._save(data)

        except Exception as e:
            logger.warning(f"[learning_memory] Failed to record pattern: {e}")

    def _find_similar(
        self,
        patterns: List[dict],
        pattern_type: str,
        capability_id: str,
        trigger: str,
    ) -> Optional[dict]:
        """Find an existing pattern that matches this one closely enough."""
        trigger_lower = trigger.lower()
        for p in patterns:
            if p.get("pattern_type") != pattern_type:
                continue
            if p.get("capability_id") != capability_id:
                continue
            # Simple similarity: check if the triggers share significant overlap
            existing_trigger = (p.get("trigger") or "").lower()
            if self._triggers_match(trigger_lower, existing_trigger):
                return p
        return None

    @staticmethod
    def _triggers_match(trigger_a: str, trigger_b: str) -> bool:
        """Check if two triggers are similar enough to be the same pattern."""
        if not trigger_a or not trigger_b:
            return False
        # Exact match
        if trigger_a == trigger_b:
            return True
        # One contains the other
        if trigger_a in trigger_b or trigger_b in trigger_a:
            return True
        # Word overlap (>60% of words in common)
        words_a = set(trigger_a.split())
        words_b = set(trigger_b.split())
        if not words_a or not words_b:
            return False
        overlap = len(words_a & words_b) / max(len(words_a), len(words_b))
        return overlap > 0.6

    def _get_relevant_by_domain(self, domains: List[str]) -> List[Pattern]:
        """Get patterns relevant to the given domains."""
        data = self._load()
        relevant = []
        for p in data["patterns"]:
            p_domain = p.get("domain", "")
            if p_domain in domains and p.get("confidence", 0) >= PATTERN_MIN_CONFIDENCE:
                relevant.append(self._dict_to_pattern(p))
        # Sort by confidence * frequency (most reliable first)
        relevant.sort(key=lambda p: p.confidence * p.frequency, reverse=True)
        return relevant[:15]

    @staticmethod
    def _prune(patterns: List[dict]) -> None:
        """Remove low-value patterns to stay under MAX_PATTERNS."""
        # Score each pattern: frequency * confidence, weighted by recency
        now = datetime.utcnow()
        for p in patterns:
            try:
                last_seen = datetime.fromisoformat(p.get("last_seen", "").rstrip("Z"))
                age_days = (now - last_seen).days
            except (ValueError, TypeError):
                age_days = 365

            freq = p.get("frequency", 1)
            conf = p.get("confidence", 0.5)
            # Recency decay: halve the score every 90 days
            recency_factor = 0.5 ** (age_days / 90)
            p["_score"] = freq * conf * recency_factor

        # Sort by score (lowest first) and remove excess
        patterns.sort(key=lambda p: p.get("_score", 0))
        while len(patterns) > MAX_PATTERNS:
            removed = patterns.pop(0)
            logger.debug(f"[learning_memory] Pruned pattern: {removed.get('pattern_id')}")

        # Clean up temporary score field
        for p in patterns:
            p.pop("_score", None)

    def _load(self) -> dict:
        """Load patterns from disk."""
        if self.store_path.exists():
            try:
                with open(self.store_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                logger.warning("[learning_memory] Corrupt patterns file, starting fresh")
        return {"version": "1.0", "patterns": []}

    def _save(self, data: dict) -> None:
        """Atomic write: write to temp file then rename."""
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(self.resilience_dir), suffix=".tmp"
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
            if self.store_path.exists():
                self.store_path.unlink()
            os.rename(tmp_path, str(self.store_path))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    @staticmethod
    def _dict_to_pattern(d: dict) -> Pattern:
        """Convert a dict to a Pattern."""
        return Pattern(
            pattern_id=d.get("pattern_id", ""),
            pattern_type=d.get("pattern_type", ""),
            capability_id=d.get("capability_id", ""),
            domain=d.get("domain", ""),
            trigger=d.get("trigger", ""),
            resolution=d.get("resolution", ""),
            frequency=d.get("frequency", 1),
            last_seen=d.get("last_seen", ""),
            confidence=d.get("confidence", 0.5),
        )
