"""
My Work read-through of the EXISTING approval flows (A2).

Today's human work already lives in three places; My Work must show and act on
all of them without changing any of them:

1. Workflow approvals — ApprovalRequests rows (Azure/on-prem SQL). Listed here
   by replicating the /api/workflow/user-approvals visibility SQL read-only
   (that endpoint is session-cookie-only, so a service can't call it), using
   the platform's own service DB pattern: {SQL Server} driver + DATABASE_* env
   + EXEC tenant.sp_setTenantContext once per connection. Decisions go through
   the EXISTING generic endpoint POST /api/workflow/approvals/<request_id>,
   which updates the row the paused engine polls.

2. Automation checkpoints/reviews — the JSON-file sidecar rows under
   data/automations/**/_approvals/ (no table exists; DDL denied on Azure —
   see automations/approval_store.py). Read directly (read-only; writers use
   atomic replace). Decisions go through the SAME generic endpoint, which
   settles the sidecar row AND resumes/aborts the paused run.

3. Agent email approvals — AgentEmailApprovals via the existing
   /api/agent-email/approvals endpoints (X-API-Key accepted). The editing
   contract is preserved exactly: BODY-ONLY, the edited text posts as
   final_body; to/subject are not editable (parity with today's page).
"""

import glob
import json
import os
from typing import Optional

import httpx

from agent_config import APP_ROOT, get_base_url, AI_HUB_API_KEY, logger

_HEADERS = {"X-API-Key": AI_HUB_API_KEY, "Connection": "close"}


# ---------------------------------------------------------------------------
# SQL (read-only) — workflow ApprovalRequests + UserGroups membership
# ---------------------------------------------------------------------------

def _db():
    import pyodbc  # optional dependency; failures degrade gracefully
    server = os.getenv("DATABASE_SERVER", "localhost")
    name = os.getenv("DATABASE_NAME", "AIHUB")
    uid = os.getenv("DATABASE_UID", "")
    pwd = os.getenv("DATABASE_PWD", "")
    conn = pyodbc.connect(
        f"DRIVER={{SQL Server}};SERVER={server};DATABASE={name};UID={uid};PWD={pwd}")
    cur = conn.cursor()
    api_key = os.getenv("API_KEY", "")
    if api_key:
        cur.execute("EXEC tenant.sp_setTenantContext ?", api_key)
    cur.close()
    return conn


def user_group_ids(user_id: int) -> list:
    try:
        conn = _db()
        try:
            cur = conn.cursor()
            cur.execute("SELECT group_id FROM UserGroups WHERE user_id = ?",
                        int(user_id))
            return [int(r[0]) for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"user_group_ids unavailable: {e}")
        return []


def workflow_pending(user_id: int) -> list:
    """Pending ApprovalRequests visible to this user — the same visibility rule
    as /api/workflow/user-approvals: direct, group-member, or unassigned."""
    try:
        conn = _db()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT request_id, title, description, status, requested_at,
                       due_date, priority, approval_data, assigned_to_type,
                       assigned_to_id
                FROM ApprovalRequests
                WHERE status = 'Pending' AND (
                      (assigned_to_type = 'user'  AND assigned_to_id = ?)
                   OR (assigned_to_type = 'group' AND assigned_to_id IN
                        (SELECT group_id FROM UserGroups WHERE user_id = ?))
                   OR assigned_to_type = 'unassigned'
                   OR assigned_to_type IS NULL)
                ORDER BY priority DESC, requested_at DESC
                """, int(user_id), int(user_id))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"workflow_pending unavailable: {e}")
        return []


# ---------------------------------------------------------------------------
# Automation sidecar rows (files)
# ---------------------------------------------------------------------------

def automation_pending(user_id: int, group_ids: list) -> list:
    rows = []
    # Automations live at APP_ROOT/automations/tenant_<id>/ (CommonUtils
    # get_app_path), NOT under data/ — the sidecar sits beside each tenant dir.
    pattern = os.path.join(APP_ROOT, "automations", "tenant_*", "_approvals",
                           "*.json")
    for path in glob.glob(pattern):
        try:
            with open(path, "r", encoding="utf-8") as f:
                row = json.load(f)
        except Exception:
            continue
        if row.get("status") != "Pending":
            continue
        at, aid = row.get("assigned_to_type"), row.get("assigned_to_id")
        visible = (at is None
                   or (at == "user" and aid == int(user_id))
                   or (at == "group" and aid in (group_ids or [])))
        if visible:
            rows.append(row)
    rows.sort(key=lambda r: (-(r.get("priority") or 0),
                             r.get("requested_at") or ""), reverse=False)
    return rows


# ---------------------------------------------------------------------------
# Email approvals (REST, X-API-Key)
# ---------------------------------------------------------------------------

async def email_pending() -> list:
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"{get_base_url()}/api/agent-email/approvals",
                                 params={"status": "pending"}, headers=_HEADERS)
            if r.status_code >= 400:
                logger.warning(f"email_pending HTTP {r.status_code}")
                return []
            return (r.json() or {}).get("approvals") or []
    except Exception as e:
        logger.warning(f"email_pending unavailable: {e}")
        return []


async def decide_email(approval_id: int, action: str,
                       final_body: Optional[str], comments: str) -> tuple:
    """Approve (with the possibly-edited body-only draft) or reject. Exactly
    the current page's contract: final_body falls back to the stored draft."""
    body = {"action": action, "comments": comments or ""}
    if final_body is not None:
        body["final_body"] = final_body
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{get_base_url()}/api/agent-email/approvals/{int(approval_id)}",
                json=body, headers=_HEADERS)
            try:
                return r.json(), r.status_code
            except Exception:
                return {"error": r.text[:300]}, r.status_code
    except Exception as e:
        return {"error": str(e)}, 502


# ---------------------------------------------------------------------------
# Generic decision endpoint (workflow rows AND automation sidecar rows)
# ---------------------------------------------------------------------------

async def decide_generic(request_id: str, status: str, comments: str,
                         user_id: int, corrections: Optional[dict] = None) -> tuple:
    body = {"status": status, "comments": comments or "", "user": int(user_id)}
    if corrections:
        body["corrections"] = corrections
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{get_base_url()}/api/workflow/approvals/{request_id}",
                json=body, headers=_HEADERS)
            try:
                return r.json(), r.status_code
            except Exception:
                return {"error": r.text[:300]}, r.status_code
    except Exception as e:
        return {"error": str(e)}, 502
