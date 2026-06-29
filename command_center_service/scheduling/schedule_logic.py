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
                       schedule: Dict[str, Any], schedule_desc: str,
                       tz_name: Optional[str] = None) -> Dict[str, Any]:
    """Create a 'command_center' scheduled job in the shared scheduler and record it in the
    user's local store. Returns {status:'ok', job_id} or {status:'error', error}.

    `tz_name` is the canonical IANA zone (or 'UTC+HH:MM' offset) the schedule's cron should fire
    in; carried as a job parameter so the engine builds a DST-aware trigger. None -> the engine's
    default (UTC)."""
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

    # Interval triggers MUST be anchored with a StartDate. Without one, the engine re-creates
    # the IntervalTrigger every 60s poll (start_date defaults to "now") and reschedule_job
    # pushes the next fire one whole interval into the future every cycle -> the job NEVER
    # fires. Anchor at creation time (UTC, stored as-is). Cron triggers are absolute and don't
    # need this. (The legacy UI scheduling path always supplied a start_time, so it was unaffected.)
    if isinstance(schedule, dict) and schedule.get("type") == "interval" and not schedule.get("start_date"):
        from datetime import datetime, timezone
        schedule = {**schedule,
                    "start_date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")}

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
    # Carry the user's timezone as a job parameter so the engine fires the cron in that zone
    # (DST-aware). Stored alongside the other params; harmless to executors that ignore it.
    if tz_name:
        payload["parameters"]["timezone"] = _param(tz_name)
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


def create_portal_workflow_schedule(user_context: Optional[Dict[str, Any]],
                                    slug: str, task_name: str,
                                    schedule: Dict[str, Any], schedule_desc: str,
                                    email_after: bool = False,
                                    tz_name: Optional[str] = None) -> Dict[str, Any]:
    """Create a REAL recurring 'portal_workflow' scheduled job in the shared scheduler. TargetId =
    the saved portal-workflow slug; the owner user_id is a job parameter (the executor
    _execute_portal_workflow_job needs it to resolve the per-user workflow + creds). Mirrors
    create_cc_schedule (incl. the interval start_date anchor) and records the task in the user's
    local store so it shows in the Scheduled Tasks panel + list/cancel tools. Returns
    {status:'ok', job_id} or {status:'error', error} — never a fabricated success."""
    uc = user_context or {}
    uid = uc.get("user_id")
    if not uid:
        return {"status": "error", "error": "no signed-in user"}
    if not slug:
        return {"status": "error", "error": "no portal-workflow slug to schedule"}

    # Anchor interval triggers (same rationale as create_cc_schedule).
    if isinstance(schedule, dict) and schedule.get("type") == "interval" and not schedule.get("start_date"):
        from datetime import datetime, timezone
        schedule = {**schedule,
                    "start_date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")}

    # The executor resolves the per-user workflow + creds from user_id, so it MUST be a job
    # parameter (target_id is numeric and only used for the legacy per-user-id routing). The
    # workflow itself is identified by slug.
    payload = {
        "name": task_name,
        "type": "portal_workflow",
        "target_id": int(uid) if str(uid).isdigit() else 0,
        "description": f"Scheduled portal workflow '{slug}' for user {uid}",
        "created_by": f"cc_user_{uid}",
        "parameters": {
            "workflow_slug": _param(slug),
            "user_id": _param(uid),
            "tenant_id": _param(uc.get("tenant_id")),
            "email_after": _param("1" if email_after else "0"),
        },
        "schedule": schedule,
    }
    # Carry the user's timezone as a job parameter so the engine fires the cron in that zone
    # (DST-aware). The portal executor ignores it; the scheduler engine consumes it.
    if tz_name:
        payload["parameters"]["timezone"] = _param(tz_name)
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
    # Mirror into the user's local store so it shows in the Scheduled Tasks panel + list/cancel
    # tools. Best-effort: the job is already created, so a store failure must not be fatal.
    try:
        store.add_task(uid, job_id, task_name, f"Portal workflow: {slug}", schedule_desc)
    except Exception as e:
        logger.warning(f"[schedule] portal task store mirror failed (job {job_id} created): {e}")
    return {"status": "ok", "job_id": job_id}


def list_cc_schedules(user_context: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return store.list_tasks((user_context or {}).get("user_id"))


def get_next_run(job_id: Any) -> Optional[str]:
    """Best-effort: the next scheduled fire time (ISO/UTC string) for a job, read from the
    scheduler API, or None if unavailable (e.g. scheduler not yet restarted)."""
    if not job_id:
        return None
    try:
        r = requests.get(f"{_scheduler_base()}/api/scheduler/jobs/{job_id}",
                         headers={"X-API-Key": _api_key()}, timeout=8)
        if r.status_code != 200:
            return None
        scheds = (r.json() or {}).get("schedules") or []
        times = [s.get("next_run_time") for s in scheds
                 if s.get("is_active") and s.get("next_run_time")]
        return min(times) if times else None
    except Exception:
        return None


def list_cc_schedules_with_next_run(user_context: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """list_cc_schedules + a best-effort next_run per task (one scheduler call each). Used by
    the panel and the list tool; the lightweight list_cc_schedules feeds the system prompt."""
    tasks = store.list_tasks((user_context or {}).get("user_id"))
    for t in tasks:
        t["next_run"] = get_next_run(t.get("job_id"))
    return tasks


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


def run_cc_schedule_now(user_context: Optional[Dict[str, Any]], name_or_id: str) -> Dict[str, Any]:
    """Trigger an immediate, out-of-band run of a scheduled task the user owns. Ownership is
    enforced against the per-user store (only a task in THIS user's store may be triggered); the
    actual run goes through the shared scheduler's /api/scheduler/run/<job_id> (X-API-Key,
    server-side). Portal-workflow / command_center jobs run async there (HTTP 202), so this returns
    promptly and the result lands in the task's run history. Does NOT affect the recurring schedule."""
    uid = (user_context or {}).get("user_id")
    task = store.get_task(uid, name_or_id) or store.find_task_by_name(uid, name_or_id)
    if not task:
        return {"status": "error", "error": f"no scheduled task matching '{name_or_id}'"}
    job_id = task["job_id"]
    try:
        r = requests.post(f"{_scheduler_base()}/api/scheduler/run/{job_id}",
                          headers={"X-API-Key": _api_key()}, timeout=30)
    except Exception as e:
        return {"status": "error", "error": f"could not reach scheduler: {e}"}
    if r.status_code not in (200, 202):
        return {"status": "error", "error": f"scheduler returned {r.status_code}: {r.text[:200]}"}
    return {"status": "ok", "task_name": task.get("task_name"), "job_id": job_id}
