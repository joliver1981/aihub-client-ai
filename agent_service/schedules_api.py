"""
The Agent's Schedules surface — backend logic for /api/schedules*.

One place to SEE, RUN, PAUSE, CANCEL, and CREATE every scheduled job, with the
shared JSS engine (job_scheduler.py) staying the single execution path: all
reads and writes go through the main app's scheduler REST / automations manage
seam with X-API-Key, and "Run now" queues an engine-native one-shot row
(POST /api/scheduler/jobs/<id>/run-once) rather than dispatching here — so
manual runs land in ScheduleExecutionHistory exactly like scheduled fires.

Visibility doctrine (all-users rollout): Developer+ sees every job — parity
with the classic scheduling pages, which are Developer+ and tenant-wide.
Role-1 users see ONLY jobs whose user_id parameter is theirs; a job whose
parameters cannot be fetched is hidden from them (fail closed: ownership
unproven). Acting (run/pause/delete) follows visibility.

Manual create mirrors the chat tools' bodies EXACTLY (schedule_agent_task /
schedule_portal_workflow / the automations manage 'schedule' action) including
the read-back honesty: a bounded ask that the engine did not record as bounded
is deleted and reported as NOT scheduled.
"""

import asyncio
import json
import os
from typing import Any

import httpx
from fastapi import HTTPException

from agent_config import get_base_url, logger
from platform_tools import _headers

MAX_JOBS = int(os.getenv("AGENT_SCHEDULES_MAX_JOBS", "500"))
_DETAIL_CONCURRENCY = 12
_GIST_CHARS = 300


# ---------------------------------------------------------------- helpers

def _pv(job: dict, name: str, default: str = "") -> str:
    """Job parameter value ({name: {value, type}} shape from the REST)."""
    p = (job.get("parameters") or {}).get(name)
    if isinstance(p, dict):
        v = p.get("value")
    else:
        v = p
    return default if v in (None, "") else str(v)


def _owner_id(job: dict):
    v = _pv(job, "user_id")
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _is_dev(user: dict) -> bool:
    return int(user.get("role") or 0) >= 2


def _visible(user: dict, job: dict) -> bool:
    if _is_dev(user):
        return True
    return _owner_id(job) == int(user.get("user_id") or 0)


def _cadence_text(sch: dict, tz: str) -> str:
    t = (sch or {}).get("type")
    if t == "cron":
        expr = sch.get("cron_expression") or "?"
        return f"cron {expr}" + (f" · {tz}" if tz else "")
    if t == "interval":
        secs = ((sch.get("interval_seconds") or 0)
                + (sch.get("interval_minutes") or 0) * 60
                + (sch.get("interval_hours") or 0) * 3600
                + (sch.get("interval_days") or 0) * 86400
                + (sch.get("interval_weeks") or 0) * 604800)
        if secs <= 0:
            return "interval"
        if secs % 86400 == 0:
            n = secs // 86400
            return f"every {n} day{'s' if n != 1 else ''}"
        if secs % 3600 == 0:
            n = secs // 3600
            return f"every {n} hour{'s' if n != 1 else ''}"
        if secs % 60 == 0:
            n = secs // 60
            return f"every {n} minute{'s' if n != 1 else ''}"
        return f"every {secs}s"
    if t == "date":
        return "one-time"
    return t or "—"


def _gist(job: dict) -> str:
    t = job.get("type")
    if t == "agent_session":
        return _pv(job, "prompt")[:_GIST_CHARS]
    if t == "automation":
        return f"automation {_pv(job, 'automation_id')}"
    if t == "portal_workflow":
        return f"portal workflow '{_pv(job, 'workflow_slug')}'"
    if t == "view_refresh":
        return f"view '{_pv(job, 'view_name')}'"
    if t == "view_email":
        return f"view '{_pv(job, 'view_name')}' → {_pv(job, 'to')}"
    if t == "command_center":
        return (_pv(job, "task_name") or _pv(job, "prompt"))[:_GIST_CHARS]
    return (job.get("description") or "")[:_GIST_CHARS]


def _normalize(job: dict, user: dict) -> dict:
    scheds = job.get("schedules") or []
    active = [s for s in scheds if s.get("is_active")]
    shown = active or scheds
    tz = _pv(job, "timezone") or _pv(job, "user_timezone")
    next_runs = sorted(s["next_run_time"] for s in active if s.get("next_run_time"))
    last_runs = sorted((s["last_run_time"] for s in scheds if s.get("last_run_time")),
                       reverse=True)
    # de-dupe cadence text preserving order (a job normally has one real row;
    # run-once leaves consumed date rows behind that we don't want to echo N times)
    cadence = ", ".join(dict.fromkeys(_cadence_text(s, tz) for s in shown))
    bound = {}
    for s in active:
        if s.get("end_date"):
            bound["end_date"] = s["end_date"]
        if s.get("max_runs") is not None:
            bound["max_runs"] = s["max_runs"]
            bound["current_runs"] = s.get("current_runs")
    return {
        "id": job.get("id"),
        "name": job.get("name"),
        "type": job.get("type"),
        "description": (job.get("description") or "")[:_GIST_CHARS],
        "gist": _gist(job),
        "is_active": bool(job.get("is_active")),
        "has_active_schedule": bool(active),
        "created_by": job.get("created_by"),
        "created_at": job.get("created_at"),
        "owner_user_id": _owner_id(job),
        "mine": _owner_id(job) == int(user.get("user_id") or 0),
        "timezone": tz,
        "cadence": cadence or "no schedule rows",
        "next_run_time": next_runs[0] if next_runs else None,
        "last_run_time": last_runs[0] if last_runs else None,
        "bound": bound or None,
        "schedule_count": len(scheds),
    }


async def _fetch_job(client: httpx.AsyncClient, base: str, hdrs: dict, job_id: int):
    r = await client.get(f"{base}/api/scheduler/jobs/{job_id}", headers=hdrs)
    if r.status_code == 404:
        return None
    if r.status_code >= 400:
        raise HTTPException(502, f"Scheduler answered HTTP {r.status_code} for job {job_id}.")
    return r.json()


async def _require_job(client, base, hdrs, user, job_id: int) -> dict:
    """Fetch + enforce visibility. 404 for both missing and not-yours (a role-1
    user must not learn which job ids exist)."""
    job = await _fetch_job(client, base, hdrs, job_id)
    if job is None or not _visible(user, job):
        raise HTTPException(404, "No such schedule (or it is not yours).")
    return job


# ---------------------------------------------------------------- read

async def list_jobs(user: dict) -> dict:
    base, hdrs = get_base_url(), _headers()
    errors: list[str] = []
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            r = await client.get(f"{base}/api/scheduler/jobs", headers=hdrs)
        except Exception as e:
            return {"schedules": [], "errors": [f"scheduler unreachable: {e}"]}
        if r.status_code >= 400:
            return {"schedules": [], "errors": [f"scheduler list HTTP {r.status_code}"]}
        rows = r.json() or []
        truncated = len(rows) > MAX_JOBS
        rows = rows[:MAX_JOBS]

        sem = asyncio.Semaphore(_DETAIL_CONCURRENCY)

        async def fetch(j):
            async with sem:
                try:
                    d = await client.get(f"{base}/api/scheduler/jobs/{j['id']}",
                                         headers=hdrs)
                    return j, (d.json() if d.status_code < 400 else None)
                except Exception:
                    return j, None
        pairs = await asyncio.gather(*(fetch(j) for j in rows))

    out, hidden_unproven = [], 0
    for j, d in pairs:
        if d is None:
            if _is_dev(user):
                # honest partial row — the list must not silently shrink
                out.append({"id": j.get("id"), "name": j.get("name"),
                            "type": j.get("type"), "is_active": bool(j.get("is_active")),
                            "created_by": j.get("created_by"),
                            "created_at": j.get("created_at"),
                            "description": (j.get("description") or "")[:_GIST_CHARS],
                            "gist": "", "mine": False, "owner_user_id": None,
                            "timezone": "", "cadence": "details unavailable",
                            "next_run_time": None, "last_run_time": None,
                            "bound": None, "schedule_count": 0,
                            "has_active_schedule": False,
                            "detail_error": "could not load schedule details"})
            else:
                hidden_unproven += 1  # fail closed: ownership unproven
            continue
        if not _visible(user, d):
            continue
        out.append(_normalize(d, user))
    if hidden_unproven:
        errors.append(f"{hidden_unproven} job(s) hidden — ownership could not be verified")
    if truncated:
        errors.append(f"showing the newest {MAX_JOBS} jobs only")
    return {"schedules": out, "truncated": truncated, "errors": errors,
            "can_see_all": _is_dev(user)}


async def history(user: dict, job_id: int, limit: int = 25) -> dict:
    limit = max(1, min(int(limit or 25), 100))
    base, hdrs = get_base_url(), _headers()
    async with httpx.AsyncClient(timeout=30) as client:
        job = await _require_job(client, base, hdrs, user, job_id)
        r = await client.get(f"{base}/api/scheduler/executions",
                             params={"job_id": job_id, "limit": limit},
                             headers=hdrs)
        rows = r.json() if r.status_code < 400 else []
    if not isinstance(rows, list):
        rows = []
    hist = []
    for e in rows:
        hist.append({
            "id": e.get("id"),
            "status": e.get("status"),
            "start_time": e.get("start_time"),
            "end_time": e.get("end_time"),
            "result_message": (e.get("result_message") or "")[:500],
            "error_details": (e.get("error_details") or "")[:500],
        })
    return {"job": _normalize(job, user), "history": hist}


# ---------------------------------------------------------------- act

async def run_now(user: dict, job_id: int) -> dict:
    base, hdrs = get_base_url(), _headers()
    async with httpx.AsyncClient(timeout=30) as client:
        job = await _require_job(client, base, hdrs, user, job_id)
        r = await client.post(f"{base}/api/scheduler/jobs/{job_id}/run-once",
                              headers=hdrs)
        try:
            data = r.json()
        except Exception:
            data = {}
        if r.status_code == 404 and not data.get("queued"):
            # a main app predating the run-once route answers 404 on the path
            return {"ok": False, "error": "This install's main app does not have "
                    "the run-once route yet — restart the main app on current code."}
        if r.status_code >= 400:
            return {"ok": False, "error": data.get("error") or f"HTTP {r.status_code}"}
    return {"ok": True, "job_id": job_id, "job_name": job.get("name"),
            "note": data.get("note") or "Queued — the engine fires it within about a minute."}


async def set_active(user: dict, job_id: int, active: bool) -> dict:
    base, hdrs = get_base_url(), _headers()
    async with httpx.AsyncClient(timeout=30) as client:
        await _require_job(client, base, hdrs, user, job_id)
        r = await client.put(f"{base}/api/scheduler/jobs/{job_id}",
                             json={"is_active": bool(active),
                                   "modified_by": str(user.get("username") or "agent-ui")},
                             headers=hdrs)
        if r.status_code >= 400:
            return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
        # read-back: never report a state the DB does not hold
        job = await _fetch_job(client, base, hdrs, job_id)
    if job is None or bool(job.get("is_active")) != bool(active):
        return {"ok": False, "error": "The scheduler did not record the change — "
                "the job's state is unchanged."}
    return {"ok": True, "job_id": job_id, "is_active": bool(active)}


async def delete_job(user: dict, job_id: int) -> dict:
    base, hdrs = get_base_url(), _headers()
    async with httpx.AsyncClient(timeout=30) as client:
        job = await _require_job(client, base, hdrs, user, job_id)
        r = await client.delete(f"{base}/api/scheduler/jobs/{job_id}", headers=hdrs)
        if r.status_code >= 400:
            return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
        gone = await _fetch_job(client, base, hdrs, job_id)
    if gone is not None:
        return {"ok": False, "error": "The scheduler still returns this job — "
                "it was NOT deleted."}
    return {"ok": True, "job_id": job_id, "deleted": True,
            "job_name": job.get("name")}


# ---------------------------------------------------------------- create

def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default).lower() == "true"


async def _post_job_verified(client, base, hdrs, plan, job_body) -> tuple:
    """POST a scheduler job and apply the tools' read-back honesty. Returns
    (job_id, None) or (None, honest error text)."""
    r = await client.post(f"{base}/api/scheduler/jobs", json=job_body, headers=hdrs)
    data = r.json() if r.status_code < 500 else {}
    if r.status_code >= 400 or not data.get("id"):
        return None, (f"Nothing was scheduled (HTTP {r.status_code}: "
                      f"{data.get('error', r.text[:200])}).")
    job_id = data["id"]
    rb = await client.get(f"{base}/api/scheduler/jobs/{job_id}", headers=hdrs)
    rbd = rb.json() if rb.status_code < 400 else {}
    if not any(s.get("is_active") for s in (rbd.get("schedules") or [])):
        return None, (f"Job #{job_id} was created but NO active schedule row "
                      "exists — treat it as NOT scheduled.")
    import work_tools
    if not work_tools._bound_was_recorded(plan, rbd.get("schedules")):
        try:
            await client.delete(f"{base}/api/scheduler/jobs/{job_id}", headers=hdrs)
        except Exception:
            pass
        return None, (f"Job #{job_id} was created but the engine did not record "
                      "the requested bound (end_date/max_runs) — it was removed "
                      "so nothing runs forever. NOT scheduled.")
    return job_id, None


async def create(user: dict, body: dict) -> dict:
    import datetime as _dt
    import work_tools

    kind = str(body.get("kind") or "").strip()
    role = int(user.get("role") or 0)
    uid = int(user.get("user_id") or 0)

    # browser zone rides along exactly like the chat contract
    tz_hint = str(body.get("browser_timezone") or "").strip()[:64]
    if tz_hint:
        canon = work_tools._zone_canonical(tz_hint)
        if canon:
            user = dict(user)
            user["browser_timezone"] = canon
    dz, dsrc = work_tools.default_zone_label(user)
    now = _dt.datetime.utcnow()
    try:
        plan = work_tools._build_schedule(body, now=now, default_tz=dz, default_src=dsrc)
    except ValueError as e:
        return {"ok": False, "error": f"Nothing was scheduled: {e}"}
    zone = plan["display_tz"]
    if plan["kind"] == "one_shot":
        cadence = (f"one-time, fires at "
                   f"{work_tools.fmt_local(plan['first_run_at'], zone)} ({zone})")
        bound = ""
    else:
        cadence = work_tools._cadence_text(plan)
        bound = work_tools._bound_text(plan, now)
    base, hdrs = get_base_url(), _headers()

    if kind == "agent_task":
        if role < 2 and not _flag("AGENT_SCHEDULE_ALLOW_ALL_USERS", "true"):
            return {"ok": False, "error": "Scheduling agent tasks requires a "
                    "Developer role on this install."}
        prompt = str(body.get("prompt") or "").strip()
        name = str(body.get("name") or "").strip()
        if not prompt or not name:
            return {"ok": False, "error": "Give the task a name and a prompt."}
        job_body = {
            "name": f"Agent: {name[:80]}",
            "type": "agent_session",
            "target_id": "0",   # string: the route treats int 0 as missing
            "description": prompt[:400],
            "created_by": str(user.get("username") or "agent-ui"),
            "is_active": True,
            "parameters": {
                "prompt": {"value": prompt, "type": "string"},
                "user_id": {"value": str(uid), "type": "string"},
                "role": {"value": str(role or 2), "type": "string"},
                "username": {"value": str(user.get("username") or ""), "type": "string"},
                "user_timezone": {"value": zone, "type": "string"},
            },
            "schedule": plan["schedule"],
        }
        for k, v in (plan.get("params") or {}).items():
            job_body["parameters"][k] = {"value": str(v), "type": "string"}
        async with httpx.AsyncClient(timeout=30) as client:
            job_id, err = await _post_job_verified(client, base, hdrs, plan, job_body)
        if err:
            return {"ok": False, "error": err}
        return {"ok": True, "job_id": job_id,
                "note": f"Scheduled agent task '{name}' (job #{job_id}, verified "
                        f"by read-back): {cadence}"
                        + (f", {bound}" if bound else "")
                        + f". Runs as {user.get('username')}; results land in "
                          "My Work." + plan["note"]}

    if kind == "portal_workflow":
        if role < 2 and not _flag("BROWSER_USE_ALLOW_ALL_USERS", "false"):
            return {"ok": False, "error": "Scheduling portal workflows requires "
                    "a Developer role on this install."}
        if plan["kind"] == "one_shot":
            return {"ok": False, "error": "Portal workflow schedules are recurring "
                    "— for a single run use ▶ Run now on an existing schedule, or "
                    "run it from the Portal Workflows page."}
        slug = str(body.get("slug") or "").strip()
        if not slug:
            return {"ok": False, "error": "Pick a portal workflow."}
        try:
            from command_center.tools import portal_workflows as _pwf
            mine = {w.get("slug"): w for w in _pwf.list_workflows(uid)}
        except Exception as e:
            return {"ok": False, "error": f"Could not read your portal workflows: {e}"}
        wf = mine.get(slug)
        if not wf:
            return {"ok": False, "error": f"No portal workflow '{slug}' in your "
                    "store — it must be one of YOURS (the run replays your saved "
                    "credentials)."}
        email_after = bool(body.get("email_after_run"))
        job_body = {
            "name": f"Portal workflow: {wf.get('name') or slug}"[:80],
            "type": "portal_workflow",
            "target_id": uid or 0,
            "description": f"Scheduled portal workflow '{slug}' for user {uid}",
            "created_by": str(user.get("username") or "agent-ui"),
            "is_active": True,
            "parameters": {
                "workflow_slug": {"value": slug, "type": "string"},
                "user_id": {"value": str(uid), "type": "string"},
                "tenant_id": {"value": str(user.get("tenant_id") or ""), "type": "string"},
                "email_after": {"value": "1" if email_after else "0", "type": "string"},
                "user_timezone": {"value": zone, "type": "string"},
            },
            "schedule": plan["schedule"],
        }
        for k, v in (plan.get("params") or {}).items():
            job_body["parameters"][k] = {"value": str(v), "type": "string"}
        async with httpx.AsyncClient(timeout=30) as client:
            job_id, err = await _post_job_verified(client, base, hdrs, plan, job_body)
        if err:
            return {"ok": False, "error": err}
        return {"ok": True, "job_id": job_id,
                "note": f"Scheduled portal workflow '{wf.get('name') or slug}' "
                        f"(job #{job_id}, verified by read-back): {cadence}"
                        + (f", {bound}" if bound else "")
                        + ". Each run replays the recorded steps headless; the "
                          "outcome lands in My Work"
                        + (" and the file is emailed." if email_after else ".")
                        + plan["note"]}

    if kind == "automation":
        if role < 2 and not _flag("AGENT_BUILD_ALLOW_ALL_USERS", "false"):
            return {"ok": False, "error": "Scheduling automations requires a "
                    "Developer role on this install."}
        automation_id = str(body.get("automation_id") or "").strip()
        if not automation_id:
            return {"ok": False, "error": "Pick an automation."}
        inputs = body.get("inputs")
        if isinstance(inputs, str) and inputs.strip():
            try:
                inputs = json.loads(inputs)
            except ValueError:
                return {"ok": False, "error": "Inputs must be a JSON object."}
        if not isinstance(inputs, dict):
            inputs = {}
        payload: dict[str, Any] = {"automation_id": automation_id,
                                   "schedule": plan["schedule"], "inputs": inputs}
        tzp = (plan.get("params") or {}).get("timezone")
        if tzp:
            payload["timezone"] = tzp
        env = {"action": "schedule",
               "user_context": {"user_id": uid, "role": role,
                                "username": str(user.get("username") or "")},
               "payload": payload}
        async with httpx.AsyncClient(timeout=60) as client:
            try:
                r = await client.post(f"{base}/automations/api/internal/manage",
                                      json=env, headers=hdrs)
                data = r.json()
            except Exception as e:
                return {"ok": False, "error": f"Could not reach the automations "
                        f"service: {e}"}
        if r.status_code >= 400 or not data.get("scheduled_job_id"):
            return {"ok": False, "error": f"Nothing was scheduled "
                    f"(HTTP {r.status_code}: {data.get('error', 'no job id returned')})."}
        return {"ok": True, "job_id": data["scheduled_job_id"],
                "note": f"Scheduled automation '{data.get('automation_name')}' "
                        f"(job #{data['scheduled_job_id']}, runs pinned "
                        f"v{data.get('pinned_version')}): {cadence}"
                        + (f", {bound}" if bound else "")
                        + f". {data.get('note', '')}" + plan["note"]}

    return {"ok": False, "error": f"Unknown schedule kind '{kind}' — use "
            "agent_task, automation, or portal_workflow."}
