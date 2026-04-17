"""
Command Center Agent — Graph State
======================================
Typed state that persists across the conversation.
Every node reads from and writes to this state.
"""

from typing import Annotated, Any, Dict, List, Optional
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages


class SubTask(TypedDict, total=False):
    """A single sub-task delegated to an agent or tool."""
    id: str
    description: str
    target_agent: Optional[str]      # agent_id or "self"
    target_agent_name: Optional[str]  # human-readable name
    target_tool: Optional[str]       # tool name
    status: str                       # pending, running, completed, failed
    inputs: dict
    outputs: dict
    error: Optional[str]


class ActiveDelegation(TypedDict, total=False):
    """Tracks an active delegation to another agent within a conversation.

    Lifecycle semantics (builder delegations):
    - build_status is None or "in_progress" → delegation is ACTIVE
    - build_status is "completed" / "partial" / "failed" → delegation is DONE
      The dict is preserved (not wiped) so downstream code can see what was
      built, but routing knows to stop sending messages to the builder.
    """
    agent_id: str                    # The agent currently being talked to
    agent_name: str                  # Human-readable name
    agent_type: str                  # "data" | "general" | "builder"
    started_at: str                  # ISO timestamp
    history: List[Dict[str, str]]    # Conversation history with this agent [{role, content}]
    builder_session_id: Optional[str]    # Builder service session UUID
    builder_log: Optional[List[dict]]    # Builder conversation log entries

    # Lifecycle fields
    build_status: Optional[str]                       # None | "in_progress" | "completed" | "partial" | "failed"
    created_resources: Optional[List[Dict[str, Any]]] # [{"type": "agent"|"connection"|"workflow", "id": ..., "name": ...}]
    completed_at: Optional[str]                       # ISO timestamp when build finished


class CommandCenterState(TypedDict, total=False):
    """
    Complete state of a Command Center conversation.

    The `messages` field uses LangGraph's `add_messages` reducer,
    which automatically appends new messages rather than replacing.
    All other fields use last-write-wins.

    Three context layers:
    1. Session context: messages + active_delegation (within conversation)
    2. User memory: user_preferences (across sessions, DB-backed)
    3. System knowledge: landscape + current date (always available)
    """
    # Conversation (managed by add_messages reducer)
    messages: Annotated[list, add_messages]

    # Intent classification for current turn
    intent: str  # chat, query, analyze, delegate, multi_step, create_tool

    # Active delegation — which agent the user is mid-conversation with
    active_delegation: Optional[ActiveDelegation]

    # Task decomposition
    sub_tasks: List[SubTask]
    current_task_index: int

    # Delegation results (agent_id/tool_name -> result)
    delegation_results: Dict[str, Any]

    # Rich content blocks for frontend rendering
    render_blocks: List[dict]

    # User preferences/memory (loaded from DB at session start, saved on changes)
    user_preferences: Dict[str, Any]

    # Platform landscape (agents, tools, workflows, connections)
    landscape: Dict[str, Any]

    # Active plugins for this session
    active_plugins: List[str]

    # Session metadata
    session_id: str

    # User context (from auth)
    user_context: Optional[Dict[str, Any]]  # {user_id, role, tenant_id, username, name}

    # User memory context (cross-session, loaded from DB)
    user_memory: str  # Formatted string of user's patterns/preferences

    # Pending agent selection (user was asked to pick an agent)
    pending_agent_selection: bool

    # Resources created by the builder in the most recent build.
    # Set by classify_intent when clearing a completed delegation.
    # Used by gather_data to prefer the newly created agent over the default.
    recently_created_resources: Optional[List[Dict[str, Any]]]

    # All resources created during this session (persisted across turns).
    # Accumulated by build node and classify_intent. Surfaced to LLM context
    # so the CC can always reference what was built (e.g., "the agent I just created").
    session_resources: Optional[List[Dict[str, Any]]]

    # Pending fallback context when asking user to confirm an alternative agent.
    fallback_context: Optional[Dict[str, Any]]

    # Streaming control
    stream_event: Optional[str]  # token, status, delegation, render, error
    stream_data: Optional[dict]

    # Route memory match — set by classify_intent when a confident route is found,
    # consumed by gather_data to skip the LLM agent picker.
    route_memory_match: Optional[Dict[str, Any]]

    # Reroute context — set by classify_intent when the user explicitly asks to
    # switch to a named agent during an active delegation (e.g. "ask agent X instead").
    # Contains {agent_id, agent_name, is_data_agent, original_question} so gather_data
    # can delegate directly without going through multi-step decomposition.
    reroute_context: Optional[Dict[str, Any]]

    # Trace context (per user turn) — used by Inspector
    trace: Optional[Dict[str, Any]]  # {trace_id, user_id, session_id, user_message, created_at}
