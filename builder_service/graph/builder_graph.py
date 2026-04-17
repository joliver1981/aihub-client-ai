"""
Builder Agent — Graph Assembly
=================================
Unified planning flow where agents and direct APIs are equal execution options.

FLOW:
  START → classify_intent → route → node → END

ROUTING (from classify_intent):
  - "converse" → General chat
  - "query_and_respond" → Read/list/query existing resources (fetches real data first)
  - "analyze_and_plan" → Build request (planner chooses APIs or agents)
  - "execute" → User confirmed plan (executes API calls + agent delegations)
  - "handle_rejection" → User rejected plan
  - "handle_agent_response" → User responding to agent question

FIRST-CLASS AGENTS:
  - analyze_and_plan sees available agents alongside available APIs
  - Plans can include agent delegation steps: {domain: "agent", action: "workflow_agent"}
  - execute handles both API calls and agent delegations
  - Agent conversations can ask follow-up questions (handle_agent_response)

This treats specialized agents as equal to direct API actions.
"""

import logging
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from graph import BuilderState
from graph.nodes import (
    classify_intent,
    converse,
    query_and_respond,
    analyze_and_plan,
    execute,
    handle_rejection,
    handle_agent_response,
)
from graph.edges import route_by_intent, route_after_plan, route_after_agent_response

logger = logging.getLogger(__name__)


def create_builder_graph():
    """
    Build and compile the builder agent graph.

    The graph treats agents as first-class execution options:
    - Planner considers agents alongside direct APIs
    - Plans can mix API calls and agent delegations
    - Agent delegations happen during execute node
    """
    graph = StateGraph(BuilderState)

    # ─── Core Nodes ───────────────────────────────────────────────────────
    graph.add_node("classify_intent", classify_intent)
    graph.add_node("converse", converse)
    graph.add_node("query_and_respond", query_and_respond)
    graph.add_node("analyze_and_plan", analyze_and_plan)
    graph.add_node("execute", execute)
    graph.add_node("handle_rejection", handle_rejection)

    # ─── Agent Response Handler ───────────────────────────────────────────
    # Handles user responses to agent follow-up questions
    graph.add_node("handle_agent_response", handle_agent_response)

    # ─── Entry ────────────────────────────────────────────────────────────
    graph.add_edge(START, "classify_intent")

    # ─── Route based on intent ────────────────────────────────────────────
    graph.add_conditional_edges(
        "classify_intent",
        route_by_intent,
        {
            "converse": "converse",
            "query_and_respond": "query_and_respond",
            "analyze_and_plan": "analyze_and_plan",
            "execute": "execute",
            "handle_rejection": "handle_rejection",
            "handle_agent_response": "handle_agent_response",
        },
    )

    # ─── Terminal edges ───────────────────────────────────────────────────
    # All nodes terminate after one execution (multi-turn is handled by re-entry)
    graph.add_edge("converse", END)
    graph.add_edge("query_and_respond", END)
    graph.add_conditional_edges(
        "analyze_and_plan",
        route_after_plan,
        {
            "execute": "execute",
            "end": END,
        },
    )
    graph.add_edge("execute", END)
    graph.add_edge("handle_rejection", END)
    graph.add_conditional_edges(
        "handle_agent_response",
        route_after_agent_response,
        {
            "execute": "execute",
            "end": END,
        },
    )

    checkpointer = MemorySaver()
    compiled = graph.compile(checkpointer=checkpointer)
    logger.info("Builder graph compiled (agents as first-class execution options)")
    return compiled
