"""
Command Center Agent — Conditional Routing
=============================================
Edge functions that determine which node to visit next.
"""

import logging
from graph import CommandCenterState

logger = logging.getLogger(__name__)


def route_by_intent(state: CommandCenterState) -> str:
    """Route to the appropriate handler based on classified intent."""
    intent = state.get("intent", "chat")
    logger.info(f"Routing by intent: {intent}")

    # ── Native A/B agent (CC_AGENT="native"): visual-workflow builds are
    # handled by converse's OWN deterministic workflow tools, never the
    # builder delegation. This single gate sits at the one chokepoint every
    # classify_intent branch converges on, so it covers the capability
    # router, the intent classifier, and the legacy guards alike. Classic
    # turns (agent_impl absent or != "native") fall through untouched.
    if intent == "build" and (state.get("agent_impl") or "classic") == "native":
        try:
            from graph.build_routing import looks_like_visual_workflow_build
            _msgs = state.get("messages", [])
            _last = _msgs[-1] if _msgs else None
            _text = str(getattr(_last, "content", "") or "")
            if looks_like_visual_workflow_build(_text):
                logger.info("[route_by_intent] native agent: visual-workflow build → "
                            "converse (native workflow tools)")
                return "converse"
        except Exception as _nw_err:
            logger.debug(f"[route_by_intent] native workflow gate skipped: {_nw_err}")

    route_map = {
        "chat": "converse",
        "query": "gather_data",
        "analyze": "gather_data",
        "delegate": "scan_landscape",
        "build": "build",
        "multi_step": "scan_landscape",
        "create_tool": "design_tool",
    }

    return route_map.get(intent, "converse")


def route_after_gather(state: CommandCenterState) -> str:
    """After gathering data, either analyze or render directly."""
    intent = state.get("intent", "query")
    if intent == "analyze":
        return "analyze"
    return "render_response"


def route_after_scan(state: CommandCenterState) -> str:
    """After scanning landscape, decompose tasks."""
    return "decompose_tasks"


def route_after_decompose(state: CommandCenterState) -> str:
    """After decomposition, execute delegations."""
    sub_tasks = state.get("sub_tasks", [])
    if not sub_tasks:
        return "converse"  # fallback if nothing to delegate
    return "execute_next_task"


def route_task_loop(state: CommandCenterState) -> str:
    """Check if there are more tasks to execute."""
    sub_tasks = state.get("sub_tasks", [])
    current_idx = state.get("current_task_index", 0)

    if current_idx < len(sub_tasks):
        return "execute_next_task"
    return "aggregate"


def route_after_aggregate(state: CommandCenterState) -> str:
    """After aggregating results, render response."""
    return "render_response"


def route_after_sandbox(state: CommandCenterState) -> str:
    """After sandbox testing a tool, save if successful."""
    # Check last tool test result
    results = state.get("delegation_results", {})
    tool_test = results.get("_tool_test", {})
    if tool_test.get("success", False):
        return "save_tool"
    return "converse"  # Report failure conversationally
