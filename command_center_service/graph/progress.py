"""
Command Center — Progress Event Queue
========================================
Enables real-time progress streaming from graph nodes to the SSE endpoint.

Graph nodes (gather_data, build, converse) emit progress events via the queue.
The chat endpoint's SSE generator polls the queue concurrently while ainvoke() runs.

Usage in a graph node:
    from graph.progress import get_queue
    pq = get_queue(state.get("session_id", ""))
    if pq:
        await pq.emit("status", {"phase": "delegating", "message": "Asking Sales Agent..."})

Usage in chat.py:
    from graph.progress import register_queue, cleanup_queue
    pq = register_queue(session_id)
    try:
        task = asyncio.create_task(_graph.ainvoke(...))
        while not task.done():
            event = await pq.get(timeout=0.3)
            if event:
                yield _sse_event("status", event["data"])
        final_state = await task
    finally:
        cleanup_queue(session_id)
"""

import asyncio
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Module-level registry — avoids putting the queue in LangGraph state (serialization issues)
_active_queues: Dict[str, "ProgressQueue"] = {}

# TTL for stale queue cleanup (seconds)
_QUEUE_TTL = 300  # 5 minutes


class ProgressQueue:
    """Shared async queue for emitting progress events from graph nodes to the SSE stream."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._queue: asyncio.Queue = asyncio.Queue()
        self._created_at = time.time()

    async def emit(self, event_type: str, data: dict):
        """Called by graph nodes to emit a progress event."""
        await self._queue.put({"event_type": event_type, "data": data})

    def emit_sync(self, event_type: str, data: dict):
        """Non-async version for sync contexts."""
        try:
            self._queue.put_nowait({"event_type": event_type, "data": data})
        except asyncio.QueueFull:
            logger.warning(f"[progress] Queue full for session {self.session_id}, dropping event")

    async def get(self, timeout: float = 0.3) -> Optional[Dict[str, Any]]:
        """Called by the SSE generator to get the next event. Returns None on timeout."""
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    @property
    def is_stale(self) -> bool:
        return (time.time() - self._created_at) > _QUEUE_TTL


def register_queue(session_id: str) -> ProgressQueue:
    """Create and register a progress queue for a session. Cleans up stale queues."""
    # Prune stale queues
    stale = [sid for sid, q in _active_queues.items() if q.is_stale]
    for sid in stale:
        del _active_queues[sid]
        logger.debug(f"[progress] Pruned stale queue for session {sid}")

    q = ProgressQueue(session_id)
    _active_queues[session_id] = q
    return q


def get_queue(session_id: str) -> Optional[ProgressQueue]:
    """Get the active progress queue for a session (called by graph nodes)."""
    return _active_queues.get(session_id)


def cleanup_queue(session_id: str):
    """Remove a session's progress queue after the graph completes."""
    _active_queues.pop(session_id, None)
