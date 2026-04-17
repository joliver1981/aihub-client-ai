"""Command Center Service — Graph Tracing

Provides lightweight tracing wrappers for LangGraph node + router functions.

Goals:
- No new DB tables
- Append-only JSONL via TraceStore
- Avoid logging huge state blobs; keep payloads small
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from services.trace_store import TraceMeta, TraceStore

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent.parent / "data"
_TRACE_STORE = TraceStore(_DATA_DIR)

# Truncation limits for LLM call tracing
_SYSTEM_MSG_LIMIT = 2000
_USER_MSG_LIMIT = 1500
_RESPONSE_LIMIT = 3000
_TOOL_CALLS_LIMIT = 1000


def _trace_meta_from_state(state: dict) -> TraceMeta | None:
    trace = (state or {}).get("trace")
    if not isinstance(trace, dict):
        return None

    trace_id = trace.get("trace_id")
    user_id = trace.get("user_id")
    session_id = trace.get("session_id")
    if not trace_id or not user_id or not session_id:
        return None

    return TraceMeta(
        trace_id=str(trace_id),
        user_id=str(user_id),
        session_id=str(session_id),
        user_message=str(trace.get("user_message") or ""),
        created_at=str(trace.get("created_at") or ""),
    )


# ---- State context helpers ------------------------------------------------

def _small_state_summary(state: dict) -> dict:
    """Lightweight state summary (kept for backward compat)."""
    sub_tasks = state.get("sub_tasks") or []
    return {
        "intent": state.get("intent"),
        "has_active_delegation": bool(state.get("active_delegation")),
        "pending_agent_selection": bool(state.get("pending_agent_selection")),
        "current_task_index": state.get("current_task_index"),
        "sub_tasks_len": len(sub_tasks) if isinstance(sub_tasks, list) else None,
        "render_blocks_len": len(state.get("render_blocks") or []) if isinstance(state.get("render_blocks"), list) else None,
        "messages_len": len(state.get("messages") or []) if isinstance(state.get("messages"), list) else None,
    }


def _rich_state_context(state: dict) -> dict:
    """Rich state context for node_start events — captures fields that drive decisions."""
    sub_tasks = state.get("sub_tasks") or []
    active = state.get("active_delegation")
    route_match = state.get("route_memory_match")
    deleg_results = state.get("delegation_results") or {}
    messages = state.get("messages") or []
    render_blocks = state.get("render_blocks") or []

    ctx: dict[str, Any] = {
        "intent": state.get("intent"),
        "pending_agent_selection": bool(state.get("pending_agent_selection")),
        "messages_len": len(messages) if isinstance(messages, list) else None,
        "render_blocks_len": len(render_blocks) if isinstance(render_blocks, list) else None,
    }

    # Active delegation details (not history — too large)
    if active and isinstance(active, dict):
        ctx["active_delegation"] = {
            "agent_id": active.get("agent_id"),
            "agent_name": active.get("agent_name"),
            "agent_type": active.get("agent_type"),
            "build_status": active.get("build_status"),
            "history_len": len(active.get("history") or []),
        }
    else:
        ctx["active_delegation"] = None

    # Sub-tasks (description + status only)
    if sub_tasks and isinstance(sub_tasks, list):
        ctx["sub_tasks"] = [
            {
                "id": (t.get("id") if isinstance(t, dict) else None),
                "description": str(t.get("description", "") if isinstance(t, dict) else t)[:200],
                "status": (t.get("status") if isinstance(t, dict) else None),
                "target_agent": (t.get("target_agent") if isinstance(t, dict) else None),
            }
            for t in sub_tasks[:10]
        ]
        ctx["current_task_index"] = state.get("current_task_index")
    else:
        ctx["sub_tasks"] = None
        ctx["current_task_index"] = None

    # Route memory match
    if route_match and isinstance(route_match, dict):
        ctx["route_memory_match"] = {
            "normalized_query": route_match.get("normalized_query"),
            "agent_id": route_match.get("agent_id"),
            "intent": route_match.get("intent"),
            "success_rate": route_match.get("success_rate"),
            "usage_count": route_match.get("usage_count"),
            "is_cc_tool": route_match.get("is_cc_tool"),
        }
    else:
        ctx["route_memory_match"] = None

    # Delegation results (keys only)
    ctx["delegation_results_keys"] = sorted(deleg_results.keys()) if isinstance(deleg_results, dict) and deleg_results else None

    # Session resources count
    sess_res = state.get("session_resources") or []
    ctx["session_resources_len"] = len(sess_res) if isinstance(sess_res, list) else 0

    # Fallback context presence
    ctx["has_fallback_context"] = bool(state.get("fallback_context"))

    return ctx


def _route_context(state: dict) -> dict:
    """Targeted context for routing events — fields that routers read."""
    active = state.get("active_delegation")
    sub_tasks = state.get("sub_tasks") or []
    return {
        "intent": state.get("intent"),
        "pending_agent_selection": bool(state.get("pending_agent_selection")),
        "active_delegation_agent": (active.get("agent_id") if isinstance(active, dict) and active else None),
        "active_delegation_type": (active.get("agent_type") if isinstance(active, dict) and active else None),
        "build_status": (active.get("build_status") if isinstance(active, dict) and active else None),
        "sub_tasks_len": len(sub_tasks) if isinstance(sub_tasks, list) else None,
        "current_task_index": state.get("current_task_index"),
        "has_render_blocks": bool(state.get("render_blocks")),
        "has_fallback_context": bool(state.get("fallback_context")),
    }


# ---- Node / router wrappers -----------------------------------------------

def wrap_node(
    node_name: str,
    fn: Callable[[dict], Awaitable[dict] | dict],
) -> Callable[[dict], Awaitable[dict] | dict]:
    """Wrap a LangGraph node to emit node_start/node_end/node_error events."""

    async def _wrapped(state: dict):
        meta = _trace_meta_from_state(state)
        start = time.perf_counter()

        if meta is not None:
            _TRACE_STORE.log_event(
                meta,
                event_type="node_start",
                node=node_name,
                payload={"state": _rich_state_context(state)},
            )

        try:
            result = fn(state)
            if hasattr(result, "__await__"):
                result = await result

            if meta is not None:
                elapsed_ms = int((time.perf_counter() - start) * 1000)
                _TRACE_STORE.log_event(
                    meta,
                    event_type="node_end",
                    node=node_name,
                    payload={
                        "elapsed_ms": elapsed_ms,
                        "writes": sorted(list(result.keys())) if isinstance(result, dict) else None,
                    },
                )
            return result
        except Exception as e:
            if meta is not None:
                elapsed_ms = int((time.perf_counter() - start) * 1000)
                _TRACE_STORE.log_event(
                    meta,
                    event_type="node_error",
                    node=node_name,
                    level="error",
                    payload={"elapsed_ms": elapsed_ms, "error": str(e)},
                )
            raise

    return _wrapped


def wrap_router(
    router_name: str,
    fn: Callable[[dict], str],
) -> Callable[[dict], str]:
    """Wrap a conditional edge/router function to emit a routing event."""

    def _wrapped(state: dict) -> str:
        meta = _trace_meta_from_state(state)
        choice = fn(state)
        if meta is not None:
            _TRACE_STORE.log_event(
                meta,
                event_type="route",
                node=router_name,
                payload={"choice": choice, **_route_context(state)},
            )
        return choice

    return _wrapped


# ---- LLM call tracing -----------------------------------------------------

def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _serialize_messages(messages) -> tuple[list[dict], int]:
    """Serialize LangChain message objects to compact dicts.

    Returns (serialized_list, total_prompt_chars).
    """
    serialized = []
    total_chars = 0
    for msg in messages:
        role = getattr(msg, "type", "unknown")
        # Normalize LangChain types
        if role == "human":
            role = "user"
        elif role == "ai":
            role = "assistant"

        content = str(getattr(msg, "content", "") or "")
        total_chars += len(content)

        # Truncation limits by role
        if role == "system":
            limit = _SYSTEM_MSG_LIMIT
        else:
            limit = _USER_MSG_LIMIT

        serialized.append({
            "role": role,
            "content": _truncate(content, limit),
            "full_length": len(content),
        })

    return serialized, total_chars


def trace_llm_call(
    state: dict,
    *,
    node: str,
    step: str,
    messages,
    response,
    elapsed_ms: int,
    model_hint: str = "",
):
    """Log an llm_call event capturing the prompt, response, and timing.

    Args:
        state: Graph state (for trace metadata extraction).
        node: The graph node name making the call.
        step: Descriptive step name (e.g. 'intent_classification').
        messages: The LangChain message list sent to the LLM.
        response: The LangChain response object.
        elapsed_ms: Wall-clock duration of the call.
        model_hint: Optional label like 'mini' or 'full'.
    """
    meta = _trace_meta_from_state(state)
    if meta is None:
        return

    try:
        # Serialize prompt messages
        if isinstance(messages, list):
            ser_messages, prompt_chars = _serialize_messages(messages)
        else:
            ser_messages = []
            prompt_chars = 0

        # Serialize response
        response_content = str(getattr(response, "content", "") or "")
        response_chars = len(response_content)

        payload: dict[str, Any] = {
            "step": step,
            "model": model_hint,
            "messages": ser_messages,
            "response": _truncate(response_content, _RESPONSE_LIMIT),
            "prompt_chars": prompt_chars,
            "response_chars": response_chars,
            "elapsed_ms": elapsed_ms,
        }

        # Include tool calls if present
        tool_calls = getattr(response, "tool_calls", None)
        if tool_calls:
            tc_str = str(tool_calls)
            payload["response_tool_calls"] = _truncate(tc_str, _TOOL_CALLS_LIMIT)

        _TRACE_STORE.log_event(
            meta,
            event_type="llm_call",
            node=node,
            payload=payload,
            summary=f"{step} ({elapsed_ms}ms)",
        )
    except Exception as e:
        logger.debug(f"Failed to trace LLM call {step}: {e}")


# ---- Convenience helpers for nodes/tools ---------------------------------

def get_trace_meta(state: dict) -> TraceMeta | None:
    return _trace_meta_from_state(state)


def trace_log(
    state: dict,
    *,
    event_type: str,
    node: str,
    payload: dict | None = None,
    level: str = "info",
    summary: str | None = None,
):
    meta = _trace_meta_from_state(state)
    if meta is None:
        return
    _TRACE_STORE.log_event(meta, event_type=event_type, node=node, payload=payload, level=level, summary=summary)
