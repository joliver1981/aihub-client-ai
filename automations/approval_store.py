"""
Bridged approval rows for automation checkpoints — the "My Approvals" queue
entries that mirror a paused ``aihub.checkpoint()`` gate.

WHY FILES, NOT A TABLE (the third design, after two live failures):
  * Rows can't live in ApprovalRequests — its step_execution_id is NOT NULL
    with FK_ApprovalRequests_StepExecutions, and an automation checkpoint has
    no workflow step (verified live: FK violation 547).
  * A sibling table can't be created — the platform DB is Azure SQL and the
    app login (TenantAppUser) has no DDL at all (verified live: ALTER denied
    1088, CREATE TABLE denied 262).
  * So: one JSON file per approval row under <tenant automations dir>/_approvals/,
    the same sidecar pattern as checkpoints/events themselves (that module's
    docstring records the identical constraint).

Single-writer safety: every writer lives in the MAIN APP process — the bridge
insert (runtime_checkpoint), the queue decision (app.py approvals POST), the
Mission Control/CC mirror (_decide_checkpoint), and the dead-run cancel
(runner._db_finish_run) — so a process-wide lock + atomic replace suffices.

Row shape mirrors the ApprovalRequests columns the approvals UI consumes:
  request_id, title, description, status (Pending/Approved/Rejected/Cancelled),
  requested_at, response_at, assigned_to_type, assigned_to_id, responded_by,
  comments, approval_data (JSON string), priority, due_date.
"""

import json
import logging
import os
import threading
import uuid
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_DIRNAME = "_approvals"


def _dir(base_path: str) -> str:
    d = os.path.join(base_path, _DIRNAME)
    os.makedirs(d, exist_ok=True)
    return d


def _path(base_path: str, request_id: str) -> str:
    safe = "".join(c for c in str(request_id) if c.isalnum() or c == "-")
    return os.path.join(_dir(base_path), f"{safe}.json")


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def add_row(base_path: str, title: str, description: str,
            assigned_to_id: Optional[int], approval_data: str,
            priority: int = 0) -> Dict:
    row = {
        "request_id": str(uuid.uuid4()),
        "title": title,
        "description": description,
        "status": "Pending",
        "requested_at": _now(),
        "response_at": None,
        "assigned_to_type": "user" if assigned_to_id is not None else None,
        "assigned_to_id": assigned_to_id,
        "responded_by": None,
        "comments": None,
        "approval_data": approval_data,
        "priority": priority,
        "due_date": None,
    }
    with _LOCK:
        with open(_path(base_path, row["request_id"]), "w", encoding="utf-8") as f:
            json.dump(row, f, indent=2)
    return row


def get_row(base_path: str, request_id: str) -> Optional[Dict]:
    try:
        with open(_path(base_path, request_id), "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def settle_row(base_path: str, request_id: str, status: str,
               responded_by, comments: Optional[str] = None,
               only_if_pending: bool = True) -> Optional[Dict]:
    """Record the outcome on a row (Approved/Rejected/Cancelled). First
    decision wins when only_if_pending (mirrors the checkpoint idempotency)."""
    with _LOCK:
        row = get_row(base_path, request_id)
        if row is None:
            return None
        if only_if_pending and row.get("status") != "Pending":
            return row
        row.update(status=status, response_at=_now(),
                   responded_by=str(responded_by) if responded_by is not None else None)
        if comments is not None:
            row["comments"] = comments
        tmp = _path(base_path, request_id) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(row, f, indent=2)
        os.replace(tmp, _path(base_path, request_id))
        return row


def list_rows(base_path: str, status: Optional[str] = None,
              assigned_to_id: Optional[int] = None) -> List[Dict]:
    """All rows, newest first; optional exact-status filter and assignee
    filter (a row with no assignee matches every user — 'available to all')."""
    out = []
    try:
        names = os.listdir(_dir(base_path))
    except OSError:
        return out
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(_dir(base_path), name), "r", encoding="utf-8") as f:
                row = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if status and (row.get("status") or "").lower() != status.lower():
            continue
        if assigned_to_id is not None and row.get("assigned_to_id") is not None \
                and row.get("assigned_to_id") != assigned_to_id:
            continue
        out.append(row)
    out.sort(key=lambda r: r.get("requested_at") or "", reverse=True)
    return out
