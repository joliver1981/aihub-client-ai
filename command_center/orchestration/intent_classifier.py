"""
Command Center — Intent Classifier
=====================================
Classifies user intent into routing categories.
"""

import logging

logger = logging.getLogger(__name__)

VALID_INTENTS = {"chat", "query", "analyze", "delegate", "multi_step", "create_tool"}


def classify_intent_from_text(text: str) -> str:
    """
    Quick heuristic intent classification (no LLM call).
    Used as fallback when LLM classification fails.
    """
    text_lower = text.lower().strip()

    # Query indicators
    query_words = ["show me", "what are", "how many", "list", "get me", "fetch", "display", "report"]
    for w in query_words:
        if text_lower.startswith(w) or w in text_lower:
            return "query"

    # Analysis indicators
    analyze_words = ["analyze", "compare", "trend", "forecast", "why did", "explain why", "pattern"]
    for w in analyze_words:
        if w in text_lower:
            return "analyze"

    # Tool creation indicators
    tool_words = ["create a tool", "build a tool", "make a tool", "new capability", "i need a tool"]
    for w in tool_words:
        if w in text_lower:
            return "create_tool"

    # Multi-step indicators
    multi_words = ["and then", "also", "after that", "first.*then", "multiple"]
    for w in multi_words:
        if w in text_lower:
            return "multi_step"

    return "chat"
