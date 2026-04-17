"""Command Center Service — Inspector/Trace API

Read-only endpoints for execution traces.

No new DB tables: traces are stored as files under command_center_service/data/traces/.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from pathlib import Path

from services.trace_store import TraceStore

router = APIRouter(prefix="/api/inspect", tags=["inspect"])

_data_dir = Path(__file__).parent.parent / "data"
_trace_store = TraceStore(_data_dir)


@router.get("/traces")
async def list_traces(user_id: str = Query(...), session_id: str = Query(...), limit: int = Query(50)):
    return _trace_store.list_traces(user_id=user_id, session_id=session_id, limit=limit)


@router.get("/traces/{trace_id}")
async def get_trace(trace_id: str, user_id: str = Query(...), session_id: str = Query(...)):
    events = _trace_store.read_trace(user_id=user_id, session_id=session_id, trace_id=trace_id)
    return {"trace_id": trace_id, "events": events}


@router.get("/traces/{trace_id}/summary")
async def get_trace_summary(trace_id: str, user_id: str = Query(...), session_id: str = Query(...)):
    """Computed summary stats for a trace — duration, LLM calls, path taken."""
    events = _trace_store.read_trace(user_id=user_id, session_id=session_id, trace_id=trace_id)
    if not events:
        return {"trace_id": trace_id, "event_count": 0}

    llm_calls = [e for e in events if e.get("event_type") == "llm_call"]
    llm_total_ms = sum((e.get("payload") or {}).get("elapsed_ms", 0) for e in llm_calls)
    tool_calls = [e for e in events if e.get("event_type") == "tool_start"]
    delegations = [e for e in events if e.get("event_type") == "delegate_start"]
    errors = [e for e in events if e.get("level") == "error"]

    # Compute path: ordered unique node names from node_start events
    path = []
    seen = set()
    for e in events:
        if e.get("event_type") in ("node_start", "route"):
            node = e.get("node", "")
            if e.get("event_type") == "route":
                choice = (e.get("payload") or {}).get("choice", "")
                node = f"{node}:{choice}" if choice else node
            if node and node not in seen:
                path.append(node)
                seen.add(node)

    # Duration from first to last event
    total_duration_ms = 0
    try:
        from datetime import datetime
        ts_first = events[0].get("ts", "")
        ts_last = events[-1].get("ts", "")
        if ts_first and ts_last:
            t0 = datetime.fromisoformat(ts_first)
            t1 = datetime.fromisoformat(ts_last)
            total_duration_ms = int((t1 - t0).total_seconds() * 1000)
    except Exception:
        pass

    # Extract intent from first route or node_start
    intent = None
    for e in events:
        p = e.get("payload") or {}
        if e.get("event_type") == "route" and p.get("intent"):
            intent = p["intent"]
            break
        if e.get("event_type") == "node_start":
            state_p = p.get("state") or {}
            if state_p.get("intent"):
                intent = state_p["intent"]
                break

    return {
        "trace_id": trace_id,
        "total_duration_ms": total_duration_ms,
        "llm_call_count": len(llm_calls),
        "llm_total_ms": llm_total_ms,
        "tool_call_count": len(tool_calls),
        "delegation_count": len(delegations),
        "path": path,
        "intent": intent,
        "error_count": len(errors),
        "event_count": len(events),
    }
