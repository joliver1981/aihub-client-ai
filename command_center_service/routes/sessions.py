"""
Command Center — Session Management Routes
"""

import logging
from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["sessions"])

_session_mgr = None


def init_session_routes(session_mgr):
    global _session_mgr
    _session_mgr = session_mgr


@router.post("/sessions")
async def create_session():
    """Create a new chat session."""
    session = _session_mgr.create_session()
    return session.to_dict()


@router.get("/sessions")
async def list_sessions():
    """List all sessions, newest first."""
    return _session_mgr.list_sessions()


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Get a session with its messages."""
    session = _session_mgr.get_session(session_id)
    if not session:
        return {"error": "Session not found"}, 404

    data = session.to_dict()
    data["messages"] = _session_mgr.get_messages(session_id)
    return data


@router.post("/sessions/{session_id}/pin")
async def pin_session(session_id: str):
    """Toggle pin status of a session."""
    session = _session_mgr.get_session(session_id)
    if not session:
        return {"error": "Session not found"}, 404
    new_state = not session.is_pinned
    _session_mgr.pin_session(session_id, new_state)
    return {"status": "ok", "is_pinned": new_state}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a session."""
    if _session_mgr.delete_session(session_id):
        return {"status": "deleted"}
    return {"error": "Session not found"}, 404


@router.post("/sessions/clear")
async def delete_all_sessions():
    """Delete all sessions."""
    _session_mgr.delete_all_sessions()
    return {"status": "all_deleted"}
