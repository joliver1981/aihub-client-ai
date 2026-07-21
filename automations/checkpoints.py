"""
Checkpoint gates — a running automation pauses at a human-judgment point
(``aihub.checkpoint("about to upload 1,240 rows")``) until a Developer decides
Proceed or Abort from Mission Control.

State lives as ``checkpoint_<id>.json`` files INSIDE the run's workdir — the
same sidecar pattern as events.jsonl and the artifact registry. That is a
deliberate choice: no new tables (the Azure platform DB needs an admin for
DDL), works across Flask workers because they share the filesystem, and dies
with the run folder. Only the run's ``status`` column flips in the DB
(running ⇄ waiting / aborting) so dashboards and the skip-guard see it.

Flow:
  SDK  → POST /automations/api/runtime/checkpoint (run-token auth)
       → create_checkpoint() + run status 'waiting' + notify
  Human→ POST /automations/api/runs/<rid>/checkpoints/<cid>/decision
       → decide_checkpoint() + status 'running' (proceed) or 'aborting' (abort)
  SDK  → polls GET /automations/api/runtime/checkpoint (run-token auth)
       → returns 'proceed' (script continues) or 'abort' (script raises;
         the supervision loop is killing it anyway — belt and braces)
"""

import json
import logging
import os
import uuid
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_PREFIX = "checkpoint_"
MAX_MESSAGE_CHARS = 500


def _path(workdir: str, checkpoint_id: str) -> str:
    safe = "".join(c for c in checkpoint_id if c.isalnum())
    return os.path.join(workdir, f"{_PREFIX}{safe}.json")


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def create_checkpoint(workdir: str, message: str,
                      attachments: Optional[List[Dict]] = None,
                      approval_request_id: Optional[str] = None) -> Dict:
    """attachments: [{name, relpath, size}] — files inside the run workdir the
    approver can download from the gate/queue. approval_request_id: the
    ApprovalRequests GUID bridging this gate into the My Approvals queue
    (set after the row is written via set_approval_request_id)."""
    checkpoint = {
        "checkpoint_id": uuid.uuid4().hex[:12],
        "message": (message or "")[:MAX_MESSAGE_CHARS],
        "requested_at": _now(),
        "decision": None,
        "decided_by": None,
        "decided_at": None,
        "attachments": attachments or [],
        "approval_request_id": approval_request_id,
    }
    with open(_path(workdir, checkpoint["checkpoint_id"]), "w", encoding="utf-8") as f:
        json.dump(checkpoint, f, indent=2)
    return checkpoint


def set_approval_request_id(workdir: str, checkpoint_id: str, request_id: str) -> None:
    """Record the bridged ApprovalRequests row id on the checkpoint (written
    after the row insert succeeds, so a queue-bridge failure leaves None and
    the gate still works via Mission Control alone)."""
    checkpoint = get_checkpoint(workdir, checkpoint_id)
    if checkpoint is None:
        return
    checkpoint["approval_request_id"] = request_id
    with open(_path(workdir, checkpoint_id), "w", encoding="utf-8") as f:
        json.dump(checkpoint, f, indent=2)


def get_checkpoint(workdir: str, checkpoint_id: str) -> Optional[Dict]:
    try:
        with open(_path(workdir, checkpoint_id), "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def decide_checkpoint(workdir: str, checkpoint_id: str, decision: str,
                      decided_by: Optional[int]) -> Optional[Dict]:
    """Record a decision. Idempotent: an already-decided checkpoint returns
    as-is (first decision wins)."""
    if decision not in ("proceed", "abort"):
        raise ValueError("decision must be 'proceed' or 'abort'")
    checkpoint = get_checkpoint(workdir, checkpoint_id)
    if checkpoint is None:
        return None
    if checkpoint.get("decision"):
        return checkpoint
    checkpoint.update(decision=decision, decided_by=decided_by, decided_at=_now())
    with open(_path(workdir, checkpoint_id), "w", encoding="utf-8") as f:
        json.dump(checkpoint, f, indent=2)
    return checkpoint


def list_checkpoints(workdir: str) -> List[Dict]:
    out = []
    try:
        for name in sorted(os.listdir(workdir)):
            if name.startswith(_PREFIX) and name.endswith(".json"):
                try:
                    with open(os.path.join(workdir, name), "r", encoding="utf-8") as f:
                        out.append(json.load(f))
                except (OSError, json.JSONDecodeError):
                    continue
    except FileNotFoundError:
        pass
    return out
