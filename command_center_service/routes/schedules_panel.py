"""
Command Center - Scheduled Tasks panel API (user-facing, CC-JWT authenticated).

Backs the CC-native "Scheduled Tasks" panel, scoped to the signed-in user: list their
scheduled tasks, read their results thread (with unread flags for the notification badge),
mark results read, and cancel a task. All data comes from the per-user schedule_store;
cancellation also deactivates the underlying scheduler job via schedule_logic.

Separate from the internal /api/scheduled/run endpoint (which the scheduler calls with
X-API-Key) - this one requires the user's CC session JWT, same contract as /api/chat.
"""
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/schedules", tags=["schedules"])


def _user(request: Request):
    """Resolve the signed-in user from the CC session JWT (same contract as /api/chat)."""
    import shared_auth
    h = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    token = h[7:].strip() if h[:7].lower() == "bearer " else (request.query_params.get("token") or "")
    verified, _err = shared_auth.verify_token(token, shared_auth.AUD_CC)
    if verified is None:
        return None
    return shared_auth.cc_user_context_from_claims(verified)


@router.get("")
async def get_schedules(request: Request):
    uc = _user(request)
    if uc is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    from scheduling import schedule_store as store
    uid = uc.get("user_id")
    return {"tasks": store.list_tasks(uid), "unread_count": store.unread_count(uid)}


@router.get("/results")
async def get_results(request: Request):
    uc = _user(request)
    if uc is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    from scheduling import schedule_store as store
    unread_only = request.query_params.get("unread_only") in ("1", "true")
    return {"results": store.list_results(uc.get("user_id"), unread_only=unread_only)}


@router.post("/results/read")
async def mark_results_read(request: Request):
    uc = _user(request)
    if uc is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    from scheduling import schedule_store as store
    try:
        body = await request.json()
    except Exception:
        body = {}
    run_ids = body.get("run_ids")  # None -> mark all read
    return {"marked": store.mark_read(uc.get("user_id"), run_ids)}


@router.delete("/{job_id}")
async def cancel_schedule(job_id: str, request: Request):
    uc = _user(request)
    if uc is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    from scheduling import schedule_logic as sl
    res = sl.cancel_cc_schedule(uc, job_id)
    if res.get("status") != "ok":
        return JSONResponse(res, status_code=404)
    return res
