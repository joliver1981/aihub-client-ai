"""
Command Center — Automation Studio panel API (user-facing, CC-JWT authed).

The Studio panel docks beside the chat and needs two kinds of data:
  1. the per-session build hint (what automation is CC working on, which
     phase) — served from the in-memory studio_state seam, and
  2. authoritative automation/run data — PROXIED to the main app's internal
     manage endpoint (the browser only talks to this origin; the service hop
     carries X-API-Key + the verified user context, and the main app
     re-enforces the Developer role at its chokepoint).

Everything here is Developer+ (role >= 2), matching the CC automation tools.
"""
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/studio", tags=["studio"])


def _user(request: Request):
    """Resolve the signed-in user from the CC session JWT (same contract as /api/chat)."""
    import shared_auth
    h = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    token = h[:7].lower() == "bearer " and h[7:].strip() or (request.query_params.get("token") or "")
    verified, _err = shared_auth.verify_token(token, shared_auth.AUD_CC)
    if verified is None:
        return None
    return shared_auth.cc_user_context_from_claims(verified)


def _dev(request: Request):
    """(user_context, error_response). Developer role gate for every route."""
    uc = _user(request)
    if uc is None:
        return None, JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        role = int(uc.get("role") or 0)
    except (TypeError, ValueError):
        role = 0
    if role < 2:
        return None, JSONResponse({"error": "Developer role required"}, status_code=403)
    return uc, None


def _manage(action, uc, payload):
    from graph import automation_tools
    return automation_tools.manage(action, uc, payload, timeout=30)


def _relay(res, ok_code=200):
    """Relay the upstream status code (401/403/404/409/...) instead of
    flattening every failure — AIHUB-0031 F3 saw abort report 409 when the
    real problem was an upstream 401."""
    code = ok_code if res.get("ok") else (res.get("status_code") or 502)
    res.pop("status_code", None)
    return JSONResponse(res, status_code=code)


@router.get("/state")
async def studio_state_get(request: Request, session_id: str = ""):
    """The panel's heartbeat poll: the per-session build hint. Cheap on
    purpose — authoritative data comes from the endpoints below only when
    the hint's version changes."""
    uc, err = _dev(request)
    if err:
        return err
    import studio_state
    try:
        from cc_config import get_base_url
        main_app_url = get_base_url()
    except Exception:
        main_app_url = ""
    return {"state": studio_state.get(session_id), "main_app_url": main_app_url}


@router.get("/automation/{automation_id}")
async def studio_automation(automation_id: str, request: Request):
    """Full automation detail (manifest, code, versions) for the workbench."""
    uc, err = _dev(request)
    if err:
        return err
    res = _manage("get", uc, {"automation_id": automation_id})
    return _relay(res)


@router.get("/active")
async def studio_active(request: Request):
    uc, err = _dev(request)
    if err:
        return err
    res = _manage("active", uc, {})
    return _relay(res)


@router.get("/runs/{run_id}/events")
async def studio_run_events(run_id: str, request: Request, after: int = 0):
    """Live feed for one run: new events since `after` + pending checkpoint."""
    uc, err = _dev(request)
    if err:
        return err
    res = _manage("run_events", uc, {"run_id": run_id, "after": after})
    return _relay(res)


@router.post("/automation/{automation_id}/promote")
async def studio_promote(automation_id: str, request: Request):
    """One-click promote from the workbench's next-steps strip (james
    2026-07-22: guide the user to full completion after a dry-run). Same
    guarded manage action Mission Control's Settings panel uses."""
    uc, err = _dev(request)
    if err:
        return err
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    res = _manage("promote", uc, {"automation_id": automation_id})
    if res.get("ok"):
        # The phase rail follows the session hint, which only CC TOOLS write —
        # a panel-button promote must advance it too or the rail sits on
        # Dry-run forever (james 2026-07-22).
        try:
            import studio_state
            session_id = (body or {}).get("session_id") or ""
            if session_id:
                studio_state.update(session_id, phase="live", working=False,
                                    automation_id=automation_id,
                                    pinned_version=res.get("pinned_version"), error=None)
        except Exception as e:
            logger.warning("[studio] promote hint update failed: %s", e)
    return _relay(res)


@router.post("/runs/{run_id}/abort")
async def studio_abort(run_id: str, request: Request):
    uc, err = _dev(request)
    if err:
        return err
    res = _manage("abort", uc, {"run_id": run_id})
    return _relay(res)


@router.post("/runs/{run_id}/checkpoints/{checkpoint_id}/decision")
async def studio_checkpoint_decision(run_id: str, checkpoint_id: str, request: Request):
    uc, err = _dev(request)
    if err:
        return err
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    decision = (body or {}).get("decision")
    if decision not in ("proceed", "abort"):
        return JSONResponse({"error": "decision must be 'proceed' or 'abort'"}, status_code=400)
    res = _manage("checkpoint_decision", uc,
                  {"run_id": run_id, "checkpoint_id": checkpoint_id, "decision": decision})
    return _relay(res)
