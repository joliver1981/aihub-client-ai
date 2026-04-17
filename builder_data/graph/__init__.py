"""
Builder Data — Graph State
==============================
Typed state that persists across the conversation.
Every node reads from and writes to this state.
LangGraph's checkpointer keeps it across turns.
"""

from typing import Annotated, Any, Dict, List, Optional
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages


class DataAgentState(TypedDict, total=False):
    """
    The complete state of a data agent conversation.

    The `messages` field uses LangGraph's `add_messages` reducer,
    which automatically appends new messages rather than replacing
    the list. All other fields use last-write-wins.
    """
    # Conversation (managed by add_messages reducer)
    messages: Annotated[list, add_messages]

    # Intent classification for current turn
    intent: str  # pipeline, quality, chat, confirm_yes, confirm_no

    # Pipeline state
    current_pipeline: Optional[Dict[str, Any]]   # PipelineDefinition as dict
    pipeline_result: Optional[Dict[str, Any]]     # PipelineResult as dict

    # Working data references
    dataframe_refs: Dict[str, str]  # step_id -> DataFrameStore key

    # Quality state
    quality_report: Optional[Dict[str, Any]]
    comparison_result: Optional[Dict[str, Any]]

    # Connection cache (refreshed at pipeline design time)
    available_connections: Optional[List[Dict[str, Any]]]

    # Session metadata
    session_id: str

    # Streaming control
    stream_event: Optional[str]   # token, pipeline, status, step_done, error
    stream_data: Optional[dict]
