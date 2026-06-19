"""
schedule_logic.py - core logic for Command Center self-scheduling.

Creates/lists/cancels recurring CC-agent tasks. The actual recurring schedule is owned by the
shared scheduler engine (main-app REST API /api/scheduler, JobType 'command_center'); this
module just POSTs there and mirrors per-user task metadata into the local schedule_store.
Synchronous (uses requests) - call via asyncio.to_thread from the async graph.
"""
import logging
from typing import Any, Dict, List, Optional

import requests

from . import schedule_store as store

logger = logging.getLogger(__name__)


def _scheduler_base() -> str:
    # The scheduler REST routes live on the MAIN app (get_scheduler_api_base_url == base url).
    from cc_config import get_base_url
    return get_base_url()


def _api_key() -> str:
    try:
        from cc_config import AI_HUB_API_KEY
        if AI_HUB_API_KEY:
            return AI_HUB_API_KEY
    except Exception:
        pass
    import os
    return os.getenv("API_KEY", "")


def _param(value: Any, ptype: str = "string") -> Dict[str, Any]:
    return {"value": "" if value is None else str(value), "type": ptype}


def create_cc_schedule(user_context: Optional[Dict[str, Any]],
                       active_delegation: Optional[Dict[str, Any]],
                       task_name: str, prompt: str,
                       schedule: Dict[str, Any], schedule_desc: str) -> Dict[str, Any]:
    """Create a 'command_center' scheduled job in the shared scheduler and record it in the
    user's local store. Returns {status:'ok', job_id} or {status:'error', error}."""
    uc = user_context or {}
    ad = active_delegation or {}
    uid = uc.get("user_id")
    # Snapshot the owner's email so a scheduled "email me" works deterministically without a
    # live lookup at run time. Best-effort: the get_my_contact_info tool can still resolve it.
    owner_email = uc.get("email") or ""
    if not owner_email:
        try:
            from user_lookup import get_user_contact
            owner_email = (get_user_contact(uid) or {}).get("email", "")
        except Exception:
            owner_email = ""
    payload = {
        "name": task_name,
        "type": "command_center",
        "target_id": int(uid) if str(uid).isdigit() else 0,
        "description": f"Command Center scheduled task for user {uid}",
        "created_by": f"cc_user_{uid}",
        "parameters": {
            "prompt": _param(prompt),
            "user_id": _param(uid),
            "tenant_id": _param(uc.get("tenant_id")),
            "role": _param(uc.get("role")),
            "username": _param(uc.get("username")),
            "name": _param(uc.get("name")),
            "agent_id": _param(ad.get("agent_id")),
            "agent_name": _param(ad.get("agent_name")),
            "task_name": _param(task_name),
            "user_email": _param(owner_email),
        },
        "schedule": schedule,
    }
    try:
        r = requests.post(f"{_scheduler_base()}/api/scheduler/jobs",
                          json=payload, headers={"X-API-Key": _api_key()}, timeout=30)
    except Exception as e:
        return {"status": "error", "error": f"could not reach scheduler: {e}"}
    if r.status_code not in (200, 201):
        return {"status": "error", "error": f"scheduler returned {r.status_code}: {r.text[:200]}"}
    try:
        job_id = (r.json() or {}).get("id")
    except Exception:
        job_id = None
    if not job_id:
        return {"status": "error", "error": "scheduler did not return a job id"}
    store.add_task(uid, job_id, task_name, prompt, schedule_desc,
                   agent_id=ad.get("agent_id"), agent_name=ad.get("agent_name"))
    return {"status": "ok", "job_id": job_id}


def list_cc_schedules(user_context: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return store.list_tasks((user_context or {}).get("user_id"))


def cancel_cc_schedule(user_context: Optional[Dict[str, Any]], name_or_id: str) -> Dict[str, Any]:
    """Cancel a scheduled task by job id or task name: deactivate it in the scheduler and
    drop it from the user's local store."""
    uid = (user_context or {}).get("user_id")
    task = store.get_task(uid, name_or_id) or store.find_task_by_name(uid, name_or_id)
    if not task:
        return {"status": "error", "error": f"no scheduled task matching '{name_or_id}'"}
    job_id = task["job_id"]
    try:
        requests.delete(f"{_scheduler_base()}/api/scheduler/jobs/{job_id}",
                        headers={"X-API-Key": _api_key()}, timeout=20)
    except Exception as e:
        logger.warning(f"[schedule] delete job {job_id} failed (removing locally anyway): {e}")
    store.remove_task(uid, job_id)
    return {"status": "ok", "task_name": task.get("task_name"), "job_id": job_id}
