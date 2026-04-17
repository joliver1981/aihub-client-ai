"""
Builder Agent — Graph State
==============================
Typed state that persists across the conversation.
Every node reads from and writes to this state.
LangGraph's checkpointer keeps it across turns.
"""

from typing import Annotated, Any, Dict, List, Optional
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages


class PlanStep(TypedDict, total=False):
    """A single step in an execution plan."""
    id: str
    order: int
    capability_id: str
    description: str
    domain: str
    status: str  # pending, running, completed, failed, skipped
    inputs: dict
    outputs: dict
    error: Optional[str]


class ExecutionPlan(TypedDict, total=False):
    """A complete execution plan awaiting confirmation."""
    plan_id: str
    goal: str
    steps: list[PlanStep]
    context_needed: list[str]
    notes: Optional[str]
    status: str  # draft, confirmed, executing, completed, failed


class AgentConversationState(TypedDict, total=False):
    """
    Tracks an active conversation with another agent.

    This is a simplified view of the full AgentConversation model
    for use in the graph state (which must be JSON-serializable).
    """
    id: str
    agent_id: str
    agent_name: str
    status: str  # active, waiting_for_user, completed, failed, timeout
    task_summary: str
    pending_question: Optional[str]
    result: Optional[str]
    message_count: int
    messages: List[dict]  # List of {role: str, content: str} messages for UI display


class BuilderState(TypedDict, total=False):
    """
    The complete state of a builder agent conversation.

    The `messages` field uses LangGraph's `add_messages` reducer,
    which automatically appends new messages rather than replacing
    the list. All other fields use last-write-wins.
    """
    # Conversation (managed by add_messages reducer)
    messages: Annotated[list, add_messages]

    # Intent classification for current turn
    intent: str  # chat, build, query, confirm_yes, confirm_no, provide_context, delegate

    # Resolution results (from AIResolver)
    resolved_domains: list[dict]
    resolved_capabilities: list[dict]

    # Execution plan
    current_plan: Optional[ExecutionPlan]

    # Dynamic system context (tools, agents, connections fetched at planning time)
    system_context: Optional[Any]  # SystemContext object from context_gatherer

    # Execution tracking
    execution_results: list[dict]
    current_step_index: int

    # ═══════════════════════════════════════════════════════════════════════
    # Agent Communication State
    # ═══════════════════════════════════════════════════════════════════════
    # Agents are now first-class execution options. Agent delegations happen
    # during plan execution when a step has domain="agent".

    # Active agent conversations (conversation_id -> conversation state)
    agent_conversations: Dict[str, AgentConversationState]

    # Current agent conversation being processed (if any)
    current_agent_conversation_id: Optional[str]

    # Question from an agent that needs user input
    pending_agent_question: Optional[str]

    # Workflow edit context — set when editing an existing workflow
    workflow_edit_context: Optional[Dict[str, Any]]  # {workflow_id, workflow_name, workflow_state}

    # User context (role-based permissions)
    user_context: Optional[Dict[str, Any]]  # {user_id, role, tenant_id, username, name}

    # Self-healing correction history for current execution
    correction_history: Optional[List[dict]]  # [{step_id, strategy, success, message}]

    # Resource registry — tracks IDs of all resources created in this conversation
    # Persists across turns so the LLM can reference previously created resources
    # Format: {"connections": [{"id": 5, "name": "AIRDB2"}], "agents": [...], "schedules": [...], ...}
    created_resources: Optional[Dict[str, list]]

    # Session metadata
    session_id: str
    tier_id: Optional[str]

    # Streaming control — tells the SSE handler what type of content to emit
    stream_event: Optional[str]  # token, plan, status, executing, step_done, error, agent_update
    stream_data: Optional[dict]
