"""
Builder Service — Resilience Layer
=====================================
Self-healing framework that wraps execution failures in an
analyze-correct-retry loop and learns from outcomes over time.

Components:
  - OutcomeTracker: Records every execution outcome for analysis
  - FailureAnalyzer: Classifies failures by root cause
  - SelfCorrectionEngine: Attempts automatic fixes for failed steps
  - LearningMemory: Stores patterns to improve future planning
"""

from .outcome_tracker import OutcomeTracker, ExecutionOutcome
from .failure_analyzer import FailureAnalyzer, FailureCategory, FailureAnalysis
from .self_correction import SelfCorrectionEngine, CorrectionStrategy, CorrectionResult
from .learning_memory import LearningMemory, Pattern

__all__ = [
    'OutcomeTracker', 'ExecutionOutcome',
    'FailureAnalyzer', 'FailureCategory', 'FailureAnalysis',
    'SelfCorrectionEngine', 'CorrectionStrategy', 'CorrectionResult',
    'LearningMemory', 'Pattern',
]
