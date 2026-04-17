"""
Builder Data — Graph Edges
==============================
Conditional routing based on classified intent and current state.
"""

import logging

logger = logging.getLogger(__name__)


def route_by_intent(state: dict) -> str:
    """
    Route to the appropriate node based on classified intent.
    """
    intent = state.get("intent", "chat")
    has_pipeline = state.get("current_pipeline") is not None
    has_result = state.get("pipeline_result") is not None

    # User confirms a pending pipeline
    if intent == "confirm_yes" and has_pipeline and not has_result:
        logger.info("Routing -> execute_pipeline: user confirmed pipeline")
        return "execute_pipeline"

    # User rejects a pending pipeline
    if intent == "confirm_no" and has_pipeline:
        logger.info("Routing -> handle_rejection: user rejected pipeline")
        return "handle_rejection"

    # User wants to build a data pipeline
    if intent == "pipeline":
        logger.info("Routing -> design_pipeline: designing data pipeline")
        return "design_pipeline"

    # User wants quality analysis
    if intent == "quality":
        logger.info("Routing -> analyze_quality: analyzing data quality")
        return "analyze_quality"

    # Default: general conversation
    logger.info("Routing -> converse: general chat")
    return "converse"
