"""
Command Center Agent — LangGraph Assembly
=============================================
Assembles the StateGraph with all nodes and conditional edges.
"""

import logging
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from graph import CommandCenterState
from graph.nodes import (
    classify_intent,
    converse,
    scan_landscape,
    gather_data,
    analyze,
    decompose_tasks,
    execute_next_task,
    aggregate,
    render_response,
    build,
    design_tool,
    sandbox_test,
    save_tool,
    answer_quality_gate,
)
from graph.edges import (
    route_by_intent,
    route_after_gather,
    route_after_decompose,
    route_task_loop,
    route_after_sandbox,
)
from graph.tracing import wrap_node, wrap_router

logger = logging.getLogger(__name__)


def create_command_center_graph():
    """
    Build and compile the Command Center state graph.

    Flow:
        START → classify_intent → [route_by_intent]
            → "chat"        → converse → END
            → "query"       → gather_data → render_response → END
            → "analyze"     → gather_data → analyze → render_response → END
            → "delegate"    → scan_landscape → decompose → [task loop] → aggregate → render_response → END
            → "build"       → build (→ Builder Agent SSE) → END
            → "multi_step"  → scan_landscape → decompose → [task loop] → aggregate → render_response → END
            → "create_tool" → design_tool → sandbox_test → [save_tool | converse] → END
    """

    graph = StateGraph(CommandCenterState)

    # ── Register nodes ─────────────────────────────────────────────────────
    graph.add_node("classify_intent", wrap_node("classify_intent", classify_intent))
    graph.add_node("converse", wrap_node("converse", converse))
    graph.add_node("scan_landscape", wrap_node("scan_landscape", scan_landscape))
    graph.add_node("gather_data", wrap_node("gather_data", gather_data))
    graph.add_node("analyze", wrap_node("analyze", analyze))
    graph.add_node("decompose_tasks", wrap_node("decompose_tasks", decompose_tasks))
    graph.add_node("execute_next_task", wrap_node("execute_next_task", execute_next_task))
    graph.add_node("aggregate", wrap_node("aggregate", aggregate))
    graph.add_node("render_response", wrap_node("render_response", render_response))
    graph.add_node("build", wrap_node("build", build))
    graph.add_node("design_tool", wrap_node("design_tool", design_tool))
    graph.add_node("sandbox_test", wrap_node("sandbox_test", sandbox_test))
    graph.add_node("save_tool", wrap_node("save_tool", save_tool))
    graph.add_node("answer_quality_gate", wrap_node("answer_quality_gate", answer_quality_gate))

    # ── Entry point ────────────────────────────────────────────────────────
    graph.set_entry_point("classify_intent")

    # ── Intent routing ─────────────────────────────────────────────────────
    graph.add_conditional_edges(
        "classify_intent",
        wrap_router("route_by_intent", route_by_intent),
        {
            "converse": "converse",
            "gather_data": "gather_data",
            "scan_landscape": "scan_landscape",
            "build": "build",
            "design_tool": "design_tool",
        },
    )

    # ── Chat → Gate → END ──────────────────────────────────────────────────
    graph.add_edge("converse", "answer_quality_gate")

    # ── Build → Gate → END ─────────────────────────────────────────────────
    graph.add_edge("build", "answer_quality_gate")

    # ── Data flow: gather → (analyze | render) → END ──────────────────────
    graph.add_conditional_edges(
        "gather_data",
        wrap_router("route_after_gather", route_after_gather),
        {
            "analyze": "analyze",
            "render_response": "render_response",
        },
    )
    graph.add_edge("analyze", "render_response")
    graph.add_edge("render_response", "answer_quality_gate")

    # ── Delegation flow: scan → decompose → [task loop] → aggregate → render → END
    graph.add_edge("scan_landscape", "decompose_tasks")
    graph.add_conditional_edges(
        "decompose_tasks",
        wrap_router("route_after_decompose", route_after_decompose),
        {
            "execute_next_task": "execute_next_task",
            "converse": "converse",
        },
    )
    graph.add_conditional_edges(
        "execute_next_task",
        wrap_router("route_task_loop", route_task_loop),
        {
            "execute_next_task": "execute_next_task",
            "aggregate": "aggregate",
        },
    )
    graph.add_edge("aggregate", "render_response")

    # ── Tool creation flow: design → sandbox → (save | converse) → END ────
    graph.add_edge("design_tool", "sandbox_test")
    graph.add_conditional_edges(
        "sandbox_test",
        wrap_router("route_after_sandbox", route_after_sandbox),
        {
            "save_tool": "save_tool",
            "converse": "converse",
        },
    )
    graph.add_edge("save_tool", END)

    # ── Answer Quality Gate → END ──────────────────────────────────────────
    graph.add_edge("answer_quality_gate", END)

    # ── Compile with checkpointer ──────────────────────────────────────────
    checkpointer = MemorySaver()
    compiled = graph.compile(checkpointer=checkpointer)

    logger.info("Command Center graph compiled successfully")
    return compiled
