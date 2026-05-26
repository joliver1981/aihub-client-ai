"""
compliance_jobs.py
------------------
Lightweight in-memory tracker for asynchronous compliance import jobs.

Used by compliance_routes.upload_document() so the UI can poll for upload
progress (queued / running / done / error / duplicate) and surface in-flight
imports as ghost entries above the version list.

Notes:
- In-memory only: state is lost on app restart. This is intentional —
  persisting per-job state would require a DB table and a migration. The
  trade-off is "if we restart while a job is in flight, the job continues
  in the background and lands its version row when done, but the UI loses
  visibility into the in-flight state." Acceptable for this feature.
- Recently-finished jobs are retained for RETAIN_FINISHED_SECS so the UI
  can show a brief "just completed" state before they fall off.
- All public functions are thread-safe (single _lock guards _jobs).
"""

from __future__ import annotations

import threading
import time
import uuid as _uuid
from typing import Dict, List, Optional


# How long to keep finished jobs in the tracker so the UI can show their
# completion message briefly. Live jobs are kept indefinitely.
RETAIN_FINISHED_SECS = 90

_jobs: Dict[str, Dict] = {}
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_job(set_id: int, filename: str, retailer_id: Optional[int] = None) -> str:
    """Register a new job in the 'queued' state and return its job_id."""
    job_id = _uuid.uuid4().hex
    now = time.time()
    with _lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "set_id": int(set_id),
            "retailer_id": int(retailer_id) if retailer_id is not None else None,
            "filename": filename,
            "status": "queued",
            "started_at": now,
            "finished_at": None,
            "version_id": None,
            "version_number": None,
            "error": None,
            "message": "Queued",
        }
    return job_id


def mark_running(job_id: str, message: str = "Processing") -> None:
    """Transition queued → running and update message."""
    with _lock:
        j = _jobs.get(job_id)
        if j is not None:
            j["status"] = "running"
            j["message"] = message


def update_message(job_id: str, message: str) -> None:
    """Update the human-readable progress message without changing status."""
    with _lock:
        j = _jobs.get(job_id)
        if j is not None:
            j["message"] = message


def finish_job(
    job_id: str,
    status: str,
    version_id: Optional[int] = None,
    version_number: Optional[int] = None,
    error: Optional[str] = None,
    message: Optional[str] = None,
) -> None:
    """Mark a job as done/error/duplicate. Status must be one of those three."""
    with _lock:
        j = _jobs.get(job_id)
        if j is None:
            return
        j["status"] = status
        j["finished_at"] = time.time()
        j["version_id"] = version_id
        j["version_number"] = version_number
        j["error"] = error
        if message is not None:
            j["message"] = message
        else:
            # Sensible default messages
            if status == "done":
                j["message"] = (
                    f"Created version {version_number}"
                    if version_number is not None else "Done"
                )
            elif status == "duplicate":
                j["message"] = (
                    f"Duplicate of version {version_number}"
                    if version_number is not None else "Duplicate"
                )
            elif status == "error":
                j["message"] = error or "Failed"


def get_active_jobs(set_id: Optional[int] = None) -> List[Dict]:
    """Return active and recently-finished jobs.

    Garbage-collects finished jobs older than RETAIN_FINISHED_SECS as a
    side effect.

    Args:
        set_id: optional filter — only return jobs for this set
    """
    cutoff = time.time() - RETAIN_FINISHED_SECS
    out: List[Dict] = []
    with _lock:
        # Drop stale finished jobs first
        stale = [
            jid for jid, j in _jobs.items()
            if j["finished_at"] is not None and j["finished_at"] < cutoff
        ]
        for jid in stale:
            del _jobs[jid]

        for j in _jobs.values():
            if set_id is None or j["set_id"] == set_id:
                out.append(dict(j))

    out.sort(key=lambda j: j["started_at"], reverse=True)
    return out
