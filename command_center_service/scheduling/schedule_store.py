"""
schedule_store.py - per-user local store for Command Center scheduled tasks and their run
results. Isolated CC-native storage (no DB table), mirroring the portal-registry pattern:
one JSON file per user under command_center_service/data/cc_schedules/<user_id>.json.

  tasks:   the user's scheduled CC tasks (job_id -> metadata; never the credentials)
  results: a capped, reverse-chronological "results thread" of run outputs, each flagged
           unread until viewed (drives the panel's notification badge). Survives logout.

Written ONLY by the CC service (the schedule_task tool, the /api/scheduled/run endpoint, and
the panel routes) - a single writer process - so an atomic write + lock is enough. The
scheduler engine never touches this file; it only triggers runs over HTTP.
"""
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_LOCK = threading.Lock()
_MAX_RESULTS = 50


def _dir() -> Path:
    d = Path(__file__).resolve().parent.parent / "data" / "cc_schedules"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _uid(user_id: Any) -> str:
    return str(user_id if user_id not in (None, "") else "anon")


def _path(user_id: Any) -> Path:
    return _dir() / f"{_uid(user_id)}.json"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load(user_id: Any) -> Dict[str, Any]:
    p = _path(user_id)
    if not p.is_file():
        return {"tasks": {}, "results": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"tasks": {}, "results": []}
    except Exception:
        return {"tasks": {}, "results": []}


def _save(user_id: Any, data: Dict[str, Any]) -> None:
    p = _path(user_id)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, p)


# --- tasks -----------------------------------------------------------------

def add_task(user_id: Any, job_id: Any, task_name: str, prompt: str, schedule_desc: str,
             agent_id: Optional[str] = None, agent_name: Optional[str] = None,
             slug: Optional[str] = None, kind: Optional[str] = None) -> Dict[str, Any]:
    with _LOCK:
        data = _load(user_id)
        entry = {
            "job_id": str(job_id), "task_name": task_name, "prompt": prompt,
            "schedule_desc": schedule_desc, "agent_id": agent_id, "agent_name": agent_name,
            # kind = 'portal' (a saved portal-workflow schedule) or 'task' (a CC agent task);
            # slug links a portal schedule back to its saved workflow so a re-schedule can find
            # and REPLACE it instead of stacking a duplicate.
            "slug": slug, "kind": kind,
            "created_at": _now(), "last_run": None, "last_status": None,
        }
        data.setdefault("tasks", {})[str(job_id)] = entry
        _save(user_id, data)
        return entry


def list_tasks(user_id: Any) -> List[Dict[str, Any]]:
    return list(_load(user_id).get("tasks", {}).values())


def get_task(user_id: Any, job_id: Any) -> Optional[Dict[str, Any]]:
    return _load(user_id).get("tasks", {}).get(str(job_id))


def find_task_by_name(user_id: Any, name: str) -> Optional[Dict[str, Any]]:
    target = (name or "").strip().lower()
    for t in _load(user_id).get("tasks", {}).values():
        if (t.get("task_name") or "").strip().lower() == target:
            return t
    return None


def remove_task(user_id: Any, job_id: Any) -> bool:
    with _LOCK:
        data = _load(user_id)
        if str(job_id) in data.get("tasks", {}):
            del data["tasks"][str(job_id)]
            _save(user_id, data)
            return True
    return False


# --- results (the "results thread") ---------------------------------------

def add_result(user_id: Any, job_id: Any, task_name: str, status: str, summary: str,
               artifact_ids: Optional[List[str]] = None,
               blocks: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    with _LOCK:
        data = _load(user_id)
        result = {
            "run_id": uuid.uuid4().hex, "job_id": str(job_id), "task_name": task_name,
            "ts": _now(), "status": status, "summary": summary,
            "artifact_ids": artifact_ids or [], "blocks": blocks or [], "unread": True,
        }
        results = data.setdefault("results", [])
        results.insert(0, result)
        del results[_MAX_RESULTS:]  # cap, newest kept
        task = data.get("tasks", {}).get(str(job_id))
        if task:
            task["last_run"] = result["ts"]
            task["last_status"] = status
        _save(user_id, data)
        return result


def list_results(user_id: Any, unread_only: bool = False) -> List[Dict[str, Any]]:
    res = _load(user_id).get("results", [])
    return [r for r in res if r.get("unread")] if unread_only else res


def unread_count(user_id: Any) -> int:
    return sum(1 for r in _load(user_id).get("results", []) if r.get("unread"))


def mark_read(user_id: Any, run_ids: Optional[List[str]] = None) -> int:
    with _LOCK:
        data = _load(user_id)
        n = 0
        for r in data.get("results", []):
            if r.get("unread") and (run_ids is None or r.get("run_id") in run_ids):
                r["unread"] = False
                n += 1
        if n:
            _save(user_id, data)
        return n
