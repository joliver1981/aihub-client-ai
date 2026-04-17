"""
Builder Agent — Graph Edges
==============================
Conditional routing based on classified intent and current state.
"""

import logging

logger = logging.getLogger(__name__)


def route_by_intent(state: dict) -> str:
    """
    Route to the appropriate node based on classified intent.

    The flow treats agents as first-class execution options:
    - Build requests go to analyze_and_plan, which can choose direct APIs or agents
    - Agent delegation happens during execution, not routing
    - Agent conversations are tracked and can receive follow-up responses
    """
    intent = state.get("intent", "chat")
    has_plan = state.get("current_plan") is not None
    plan_status = ""
    if has_plan:
        plan_status = state["current_plan"].get("status", "")

    # Check if we have an active agent conversation that needs user input.
    # This covers TWO cases:
    #   1. Agent explicitly asked a question (pending_agent_question is set)
    #   2. Agent gave an update and the conversation is still active (e.g., "providing_update")
    # In both cases, the user's next message should go to the agent.
    current_agent_conv = state.get("current_agent_conversation_id")
    pending_agent_question = state.get("pending_agent_question")

    if current_agent_conv:
        # There's an active agent conversation — route to it
        if pending_agent_question:
            logger.info("Routing → handle_agent_response: responding to agent question")
            return "handle_agent_response"

        # Even without a pending question, check if the conversation is still ongoing
        agent_conversations = state.get("agent_conversations", {})
        conv_state = agent_conversations.get(current_agent_conv, {})
        conv_status = conv_state.get("status", "")

        if conv_status not in ("completed", ""):
            # Conversation is active/waiting — forward user's message to the agent
            logger.info(f"Routing → handle_agent_response: active agent conversation (status={conv_status})")
            return "handle_agent_response"

    # User confirms a pending plan
    if intent == "confirm_yes" and has_plan and plan_status == "draft":
        logger.info("Routing → execute: user confirmed plan")
        return "execute"

    # User confirms but no plan exists (step extraction failed) — re-plan
    # Route back to planner so it can generate a proper structured plan
    if intent == "confirm_yes" and not has_plan:
        logger.info("Routing → analyze_and_plan: confirm_yes with no plan, re-planning with structure")
        return "analyze_and_plan"

    # User rejects a pending plan
    if intent == "confirm_no" and has_plan:
        logger.info("Routing → handle_rejection: user rejected plan")
        return "handle_rejection"

    # User wants to query/list/view existing resources
    if intent == "query":
        logger.info("Routing → query_and_respond: fetching system data for read request")
        return "query_and_respond"

    # User wants to build something — go directly to analyze_and_plan
    # The planner will consider BOTH direct APIs and agents as execution options
    if intent == "build":
        logger.info("Routing → analyze_and_plan: planning build (agents + direct APIs)")
        return "analyze_and_plan"

    # User providing context for an in-progress build
    if intent == "provide_context":
        logger.info("Routing → analyze_and_plan: providing context")
        return "analyze_and_plan"

    # Default: general conversation
    logger.info("Routing → converse: general chat")
    return "converse"


def route_after_agent_response(state: dict) -> str:
    """
    Route after handle_agent_response completes.

    When an agent conversation finishes AND the plan has remaining pending steps,
    route back to execute to resume the plan. Otherwise, end the turn.

    This enables the multi-turn delegation flow:
      1. execute() runs Step 1 (agent delegation) → agent asks questions → PAUSE
      2. User answers questions via handle_agent_response → conversation completes
      3. THIS FUNCTION detects pending steps → routes to execute
      4. execute() resumes, skipping completed steps, running pending ones
    """
    # If conversation is still active (user still talking to agent), don't resume
    current_conv = state.get("current_agent_conversation_id")
    if current_conv:
        logger.info("Routing → end: agent conversation still active")
        return "end"

    # Check if plan has pending steps to execute
    plan = state.get("current_plan")
    if plan and plan.get("steps"):
        has_pending = any(
            s.get("status") in (None, "", "pending")
            for s in plan["steps"]
        )
        if has_pending:
            logger.info("Routing → execute: resuming plan after agent conversation completed")
            return "execute"

    return "end"


def route_after_plan(state: dict) -> str:
    """
    Route after analyze_and_plan completes.

    Auto-execute single non-destructive plans (status="confirmed").
    Otherwise, return to END so the user sees the draft plan and can approve.
    """
    plan = state.get("current_plan")
    if plan and plan.get("status") == "confirmed":
        logger.info("Routing → execute: auto-executing confirmed plan")
        return "execute"
    return "end"
