"""
Builder Data — Graph Assembly
=================================
LangGraph state machine for the data pipeline agent.

FLOW:
  START -> classify_intent -> route -> node -> END

ROUTING:
  - "converse"         -> General data Q&A with tool access
  - "design_pipeline"  -> AI designs a pipeline from description
  - "execute_pipeline" -> User confirmed, execute the pipeline
  - "analyze_quality"  -> Run quality/compare/scrub operations
  - "handle_rejection" -> User rejected proposed pipeline

EXECUTION:
  execute_pipeline -> present_results -> END
  analyze_quality  -> present_results -> END
"""

import logging
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from graph import DataAgentState
from graph.nodes import (
    classify_intent,
    converse,
    design_pipeline,
    execute_pipeline,
    analyze_quality,
    present_results,
    handle_rejection,
    init_nodes,
)
from graph.edges import route_by_intent

logger = logging.getLogger(__name__)


def create_data_graph(connection_bridge=None):
    """
    Build and compile the data agent graph.

    Args:
        connection_bridge: ConnectionBridge instance for database access.

    Returns:
        Compiled LangGraph.
    """
    # Initialize LLMs and wire them into the nodes
    from builder_data_config import get_llm
    from ai.tools import init_tools

    llm = get_llm(mini=False, streaming=True)
    llm_mini = get_llm(mini=True, streaming=False)

    init_nodes(connection_bridge, llm, llm_mini)
    init_tools(connection_bridge)

    # Build graph
    graph = StateGraph(DataAgentState)

    # ─── Core Nodes ───────────────────────────────────────────────────────
    graph.add_node("classify_intent", classify_intent)
    graph.add_node("converse", converse)
    graph.add_node("design_pipeline", design_pipeline)
    graph.add_node("execute_pipeline", execute_pipeline)
    graph.add_node("analyze_quality", analyze_quality)
    graph.add_node("present_results", present_results)
    graph.add_node("handle_rejection", handle_rejection)

    # ─── Entry ────────────────────────────────────────────────────────────
    graph.add_edge(START, "classify_intent")

    # ─── Route based on intent ────────────────────────────────────────────
    graph.add_conditional_edges(
        "classify_intent",
        route_by_intent,
        {
            "converse": "converse",
            "design_pipeline": "design_pipeline",
            "execute_pipeline": "execute_pipeline",
            "analyze_quality": "analyze_quality",
            "handle_rejection": "handle_rejection",
        },
    )

    # ─── Terminal edges ───────────────────────────────────────────────────
    graph.add_edge("converse", END)
    graph.add_edge("design_pipeline", END)
    graph.add_edge("execute_pipeline", "present_results")
    graph.add_edge("analyze_quality", "present_results")
    graph.add_edge("present_results", END)
    graph.add_edge("handle_rejection", END)

    checkpointer = MemorySaver()
    compiled = graph.compile(checkpointer=checkpointer)
    logger.info("Data agent graph compiled")
    return compiled
