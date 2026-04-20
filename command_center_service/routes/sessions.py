"""
Command Center — Session Management Routes
"""

import logging
from typing import Optional
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["sessions"])

_session_mgr = None


def init_session_routes(session_mgr):
    global _session_mgr
    _session_mgr = session_mgr


# ─── Ownership model ─────────────────────────────────────────────────────
# Every session endpoint now filters by the user_id + tenant_id the client
# sends as query params. This matches the existing /api/memory endpoint
# pattern (which filters on user_id the same way) and closes the cross-user
# and cross-tenant session-visibility bugs (BUG-R3-001/-002/-003/-005/-007).
#
# This does not by itself authenticate the caller — a sufficiently determined
# attacker with direct TCP reach to port 5091 can still forge the query
# params. True authentication requires pairing this with token validation
# on every request; that's a follow-up change. The immediate goal: stop one
# logged-in user from seeing another user's data through the normal UI flow,
# because the CC frontend now sends its own user_id/tenant_id and the server
# filters accordingly.


@router.post("/sessions")
async def create_session(
    user_id: Optional[int] = Query(None),
    role: Optional[int] = Query(None),
    tenant_id: Optional[int] = Query(None),
    username: Optional[str] = Query(None),
    name: Optional[str] = Query(None),
):
    """Create a new chat session. Owner is stamped from the query params
    so the session becomes visible only to that user."""
    ctx = None
    if user_id is not None and tenant_id is not None:
        from services import UserContext
        ctx = UserContext(
            user_id=int(user_id),
            role=int(role or 0),
            tenant_id=int(tenant_id),
            username=str(username or ""),
            name=str(name or ""),
        )
    session = _session_mgr.create_session(user_context=ctx)
    return session.to_dict()


@router.get("/sessions")
async def list_sessions(
    user_id: Optional[int] = Query(None),
    tenant_id: Optional[int] = Query(None),
    role: int = Query(0),
):
    """List sessions owned by the requesting user. Legacy sessions
    (created before user_context was persisted) are visible only to
    admin/developer roles (role >= 2)."""
    return _session_mgr.list_sessions_for(user_id, tenant_id, role)


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    user_id: Optional[int] = Query(None),
    tenant_id: Optional[int] = Query(None),
    role: int = Query(0),
):
    """Get a session with its messages, only if the requester owns it
    (or is an admin viewing a legacy session)."""
    session = _session_mgr.get_session_for(session_id, user_id, tenant_id, role)
    if not session:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    data = session.to_dict()
    data["messages"] = _session_mgr.get_messages(session_id)
    return data


@router.post("/sessions/{session_id}/pin")
async def pin_session(
    session_id: str,
    user_id: Optional[int] = Query(None),
    tenant_id: Optional[int] = Query(None),
    role: int = Query(0),
):
    """Toggle pin status of a session, only if the requester owns it."""
    session = _session_mgr.get_session_for(session_id, user_id, tenant_id, role)
    if not session:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    new_state = not session.is_pinned
    _session_mgr.pin_session(session_id, new_state)
    return {"status": "ok", "is_pinned": new_state}


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    user_id: Optional[int] = Query(None),
    tenant_id: Optional[int] = Query(None),
    role: int = Query(0),
):
    """Delete a session, only if the requester owns it."""
    session = _session_mgr.get_session_for(session_id, user_id, tenant_id, role)
    if not session:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    if _session_mgr.delete_session(session_id):
        return {"status": "deleted"}
    return JSONResponse({"error": "Session not found"}, status_code=404)


@router.post("/sessions/clear")
async def delete_all_sessions(
    user_id: Optional[int] = Query(None),
    tenant_id: Optional[int] = Query(None),
    role: int = Query(0),
):
    """Delete all sessions owned by the requesting user."""
    owned = _session_mgr.list_sessions_for(user_id, tenant_id, role)
    count = 0
    for s in owned:
        if _session_mgr.delete_session(s.get("session_id")):
            count += 1
    return {"status": "ok", "deleted": count}
