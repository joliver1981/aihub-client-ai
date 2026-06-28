"""
Central chokepoint for ALL agent-as-itself outbound email + the Agent Email
Approvals queue.

Every path where an AI agent sends an email on its own behalf funnels through
send_agent_email():
  * the dispatcher auto-reply (email_agent_dispatcher._trigger_auto_response),
  * the reply_to_email / send_email inbox tools (agent_email_tools), and
  * transitively, a workflow AI Action node that drives those tools.

send_agent_email() reads the agent's require_approval flag:
  * require_approval = True  -> insert a 'pending' row into AgentEmailApprovals
                               and DO NOT send (a human approves/edits/sends it
                               later from the Agent Email Approvals UI).
  * require_approval = False -> send immediately via the canonical transport
                               (notification_client.send_email_notification).

SYSTEM notifications (admin "new email"/"auto-reply sent" alerts) deliberately do
NOT call this — they keep calling send_email_notification directly so they are
never gated.

AgentEmailAddresses + AgentEmailApprovals live in the LOCAL on-prem DB. No
LangChain imports here so tools, dispatcher and routes can all import it.
"""

import os
import json
import logging
from typing import Any, Dict, List, Optional

from CommonUtils import get_db_connection

logger = logging.getLogger("AgentEmailSend")


def _local_cursor():
    """LOCAL DB connection with tenant context (AgentEmailAddresses/Approvals live here)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("EXEC tenant.sp_setTenantContext ?", (os.environ.get("API_KEY", ""),))
    return conn, cursor


def _close(conn):
    try:
        if conn:
            conn.close()
    except Exception:
        pass


def _as_list(to) -> List[str]:
    if to is None:
        return []
    if isinstance(to, str):
        return [to]
    return [str(x) for x in to]


def get_send_config(agent_id: int) -> Optional[Dict[str, Any]]:
    """Return the agent's outbound email config (require_approval, from address)."""
    conn = None
    try:
        conn, cursor = _local_cursor()
        cursor.execute(
            """
            SELECT email_address, from_name, is_active, require_approval
            FROM AgentEmailAddresses
            WHERE agent_id = ?
            """,
            (agent_id,),
        )
        row = cursor.fetchone()
        cursor.close()
        if not row:
            return None
        return {
            "email_address": row[0],
            "from_name": row[1],
            "is_active": bool(row[2]) if row[2] is not None else False,
            "require_approval": bool(row[3]) if row[3] is not None else True,
        }
    except Exception as e:
        logger.error(f"get_send_config({agent_id}) failed: {e}")
        return None
    finally:
        _close(conn)


def _do_send(to_list, subject, body, *, html_body=None, attachments=None, agent_id=None, agent_name=None) -> Dict[str, Any]:
    """Actually send via the canonical transport. Never gated."""
    try:
        from notification_client import send_email_notification
        result = send_email_notification(
            to=to_list,
            subject=subject,
            body=body,
            html_body=html_body,
            agent_id=agent_id,
            agent_name=agent_name,
            attachments=attachments,
        )
        if result and result.get("success"):
            return {"status": "sent", "success": True, "message_id": result.get("message_id")}
        return {
            "status": "failed",
            "success": False,
            "error": (result or {}).get("error", "send failed"),
            "blocked_by_limit": (result or {}).get("blocked_by_limit"),
            "raw": result,
        }
    except Exception as e:
        logger.error(f"_do_send failed (agent={agent_id}): {e}")
        return {"status": "failed", "success": False, "error": str(e)}


def _queue_approval(agent_id, to_list, subject, body, *, source, attachments=None, event_id=None, message_key=None, recipient_name=None, created_by=None) -> Dict[str, Any]:
    conn = None
    try:
        conn, cursor = _local_cursor()
        cursor.execute(
            """
            INSERT INTO AgentEmailApprovals (
                agent_id, source, to_addresses, recipient_name, subject,
                draft_body, attachments, event_id, message_key, status, created_by
            )
            OUTPUT INSERTED.approval_id
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                agent_id,
                source,
                json.dumps(to_list),
                recipient_name,
                subject,
                body,
                json.dumps(attachments) if attachments else None,
                event_id,
                message_key,
                created_by,
            ),
        )
        row = cursor.fetchone()
        approval_id = row[0] if row else None
        conn.commit()
        cursor.close()
        logger.info(f"Queued agent email for approval: agent={agent_id} id={approval_id} source={source}")
        return {"status": "queued", "success": True, "pending_approval": True, "approval_id": approval_id}
    except Exception as e:
        logger.error(f"Failed to queue agent email approval (agent={agent_id}): {e}")
        return {"status": "failed", "success": False, "error": f"Could not queue for approval: {e}"}
    finally:
        _close(conn)


def send_agent_email(agent_id, to, subject, body, *, html_body=None, attachments=None, source="chat_tool", event_id=None, message_key=None, recipient_name=None, created_by=None, agent_name=None) -> Dict[str, Any]:
    """
    Gate + send an agent-originated email. THE single chokepoint.

    Returns {'status': 'queued'|'sent'|'failed', 'success': bool, ...}:
      queued -> {'approval_id': N, 'pending_approval': True}
      sent   -> {'message_id': ...}
      failed -> {'error': ...}
    """
    to_list = _as_list(to)
    if not to_list:
        return {"status": "failed", "success": False, "error": "no recipient"}

    config = get_send_config(agent_id) if agent_id else None
    require_approval = config.get("require_approval", True) if config else False

    if require_approval:
        return _queue_approval(
            agent_id, to_list, subject, body,
            source=source, attachments=attachments, event_id=event_id,
            message_key=message_key, recipient_name=recipient_name, created_by=created_by,
        )

    return _do_send(
        to_list, subject, body, html_body=html_body, attachments=attachments,
        agent_id=agent_id, agent_name=agent_name,
    )


_LIST_COLS = "approval_id, agent_id, source, to_addresses, recipient_name, subject, draft_body, final_body, status, created_by, approver_user_id, approver_comments, responded_at, sent_message_id, error_message, event_id, message_key, created_at"


def _row_to_dict(row) -> Dict[str, Any]:
    d = {
        "approval_id": row[0],
        "agent_id": row[1],
        "source": row[2],
        "to_addresses": json.loads(row[3]) if row[3] else [],
        "recipient_name": row[4],
        "subject": row[5],
        "draft_body": row[6],
        "final_body": row[7],
        "status": row[8],
        "created_by": row[9],
        "approver_user_id": row[10],
        "approver_comments": row[11],
        "responded_at": row[12].isoformat() if row[12] else None,
        "sent_message_id": row[13],
        "error_message": row[14],
        "event_id": row[15],
        "message_key": row[16],
        "created_at": row[17].isoformat() if row[17] else None,
    }
    return d


def list_approvals(status: Optional[str] = None, agent_ids: Optional[List[int]] = None, limit: int = 200) -> List[Dict[str, Any]]:
    """
    List approvals, optionally filtered by status and a set of agent ids.

    agent_ids=None means no agent filter (admin / all-access). An EMPTY list
    means deny-all (returns []), matching DataUtils.accessible_agent_ids fail-closed.
    """
    if agent_ids is not None and len(agent_ids) == 0:
        return []

    where = []
    params = []
    if status and status != "all":
        where.append("status = ?")
        params.append(status)
    if agent_ids is not None:
        placeholders = ",".join("?" * len(agent_ids))
        where.append(f"agent_id IN ({placeholders})")
        params.extend(agent_ids)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    conn = None
    try:
        conn, cursor = _local_cursor()
        cursor.execute(
            f"SELECT TOP ({int(limit)}) {_LIST_COLS} FROM AgentEmailApprovals{where_sql} ORDER BY created_at DESC",
            params,
        )
        rows = cursor.fetchall()
        cursor.close()
        return [_row_to_dict(r) for r in rows]
    except Exception as e:
        logger.error(f"list_approvals failed: {e}")
        return []
    finally:
        _close(conn)


def get_approval(approval_id) -> Optional[Dict[str, Any]]:
    conn = None
    try:
        conn, cursor = _local_cursor()
        cursor.execute(
            f"SELECT {_LIST_COLS} FROM AgentEmailApprovals WHERE approval_id = ?",
            (approval_id,),
        )
        row = cursor.fetchone()
        cursor.close()
        return _row_to_dict(row) if row else None
    except Exception as e:
        logger.error(f"get_approval({approval_id}) failed: {e}")
        return None
    finally:
        _close(conn)


def reject_approval(approval_id, approver_user_id, comments=None) -> Dict[str, Any]:
    conn = None
    try:
        conn, cursor = _local_cursor()
        cursor.execute(
            """
            UPDATE AgentEmailApprovals
            SET status='rejected', approver_user_id=?, approver_comments=?, responded_at=GETUTCDATE()
            WHERE approval_id=? AND status='pending'
            """,
            (approver_user_id, comments, approval_id),
        )
        affected = cursor.rowcount
        conn.commit()
        cursor.close()
        if affected and affected > 0:
            return {"success": True, "status": "rejected"}
        return {"success": False, "error": "approval not pending or not found"}
    except Exception as e:
        logger.error(f"reject_approval({approval_id}) failed: {e}")
        return {"success": False, "error": str(e)}
    finally:
        _close(conn)


def send_approved_email(approval_id, final_body, *, approver_user_id, comments=None) -> Dict[str, Any]:
    """
    Send a previously-queued approval (used by the approve route). Sends final_body
    (the possibly-edited text) via the canonical transport and marks the row
    sent/failed. Authorization (agent access) is the CALLER's responsibility.
    """
    conn = None
    try:
        conn, cursor = _local_cursor()
        cursor.execute(
            """
            SELECT agent_id, to_addresses, subject, attachments, status
            FROM AgentEmailApprovals
            WHERE approval_id = ?
            """,
            (approval_id,),
        )
        row = cursor.fetchone()
        cursor.close()
        if not row:
            return {"success": False, "error": "approval not found"}
        agent_id, to_json, subject, attachments_json, status = row[0], row[1], row[2], row[3], row[4]
        if status != "pending":
            return {"success": False, "error": f"approval already {status}"}
        to_list = json.loads(to_json) if to_json else []
        attachments = json.loads(attachments_json) if attachments_json else None
    except Exception as e:
        logger.error(f"send_approved_email read failed (id={approval_id}): {e}")
        _close(conn)
        return {"success": False, "error": str(e)}
    finally:
        _close(conn)

    send_result = _do_send(to_list, subject, final_body, attachments=attachments, agent_id=agent_id)

    conn2 = None
    try:
        conn2, cursor2 = _local_cursor()
        if send_result.get("success"):
            cursor2.execute(
                """
                UPDATE AgentEmailApprovals
                SET status='sent', final_body=?, sent_message_id=?, approver_user_id=?,
                    approver_comments=?, responded_at=GETUTCDATE(), error_message=NULL
                WHERE approval_id=?
                """,
                (final_body, send_result.get("message_id"), approver_user_id, comments, approval_id),
            )
        else:
            cursor2.execute(
                """
                UPDATE AgentEmailApprovals
                SET status='failed', final_body=?, approver_user_id=?, approver_comments=?,
                    responded_at=GETUTCDATE(), error_message=?
                WHERE approval_id=?
                """,
                (final_body, approver_user_id, comments, send_result.get("error"), approval_id),
            )
        conn2.commit()
        cursor2.close()
    except Exception as e:
        logger.error(f"send_approved_email update failed (id={approval_id}): {e}")
    finally:
        _close(conn2)

    return {
        "success": bool(send_result.get("success")),
        "status": send_result.get("status"),
        "message_id": send_result.get("message_id"),
        "error": send_result.get("error"),
        "agent_id": agent_id,
    }
