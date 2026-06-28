"""
Shared helper for reading INBOUND email attachment bytes/text from the CLOUD DB.

`InboundEmailAttachments` lives in the *cloud* database — the Mailgun -> cloud-relay
service (a separate codebase) writes inbound mail + attachments there; the on-prem
app only ever READS, over the cloud connection + tenant context. The agent inbox
`read_attachment` tool (agent_email_tools.read_attachment) already does this read.

This module extracts that proven read path so the email dispatcher can feed
attachment TEXT into email-triggered workflows and auto-reply drafts, WITHOUT
duplicating cloud-DB access logic in the dispatcher (which otherwise uses the LOCAL
DB connection that does not contain these rows) and WITHOUT modifying
`read_attachment` itself.

No LangChain imports here so the dispatcher / send paths can import it cleanly.
"""

import os
import logging
from typing import Dict, List, Optional, Tuple

from CommonUtils import get_cloud_db_connection
from config import MAX_ATTACHMENT_CHARS

logger = logging.getLogger("AgentEmailAttachments")


def _default_max_chars(max_chars: Optional[int]) -> int:
    if max_chars is not None:
        return max_chars
    try:
        return int(MAX_ATTACHMENT_CHARS)
    except (TypeError, ValueError):
        return 500000


def _cloud_cursor():
    """Open a CLOUD-DB connection with tenant context set (mirrors read_attachment)."""
    conn = get_cloud_db_connection()
    cursor = conn.cursor()
    cursor.execute("EXEC tenant.sp_setTenantContext ?", (os.environ.get("API_KEY", ""),))
    return conn, cursor


def fetch_attachment_bytes(attachment_id: int) -> Optional[Tuple[str, str, int, bytes]]:
    """
    Return (filename, content_type, size, content_bytes) for one attachment, or None.

    Same query/connection as the read_attachment tool — just reusable without the
    LangChain tool wrapper.
    """
    conn = None
    try:
        conn, cursor = _cloud_cursor()
        cursor.execute(
            """
            SELECT filename, content_type, size, content
            FROM InboundEmailAttachments
            WHERE attachment_id = ?
            """,
            (attachment_id,),
        )
        row = cursor.fetchone()
        cursor.close()
        if not row:
            return None
        content = bytes(row[3]) if row[3] is not None else None
        return (row[0], row[1], row[2], content)
    except Exception as e:
        logger.error(f"fetch_attachment_bytes({attachment_id}) failed: {e}")
        return None
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                return None


def get_attachment_texts_for_event(event_id, max_chars: Optional[int] = None) -> List[Dict]:
    """
    Extract text for every attachment on an inbound email event.

    Returns a list of dicts: {attachment_id, filename, content_type, size, text, error}.
    Reads bytes from the CLOUD DB (same path as read_attachment), then runs
    `attachment_text_extractor.extract_text_from_attachment`. Per-file failures are
    non-fatal (captured in `error`); a query failure returns an empty list.
    """
    max_chars = _default_max_chars(max_chars)

    results = []
    conn = None
    try:
        conn, cursor = _cloud_cursor()
        cursor.execute(
            """
            SELECT attachment_id, filename, content_type, size, content
            FROM InboundEmailAttachments
            WHERE event_id = ?
            """,
            (event_id,),
        )
        rows = cursor.fetchall()
        cursor.close()
    except Exception as e:
        logger.error(f"get_attachment_texts_for_event({event_id}) query failed: {e}")
        return results
    finally:
        if conn:
            conn.close()

    try:
        from attachment_text_extractor import extract_text_from_attachment
    except Exception as e:
        logger.error(f"attachment_text_extractor unavailable: {e}")
        return results

    for row in rows:
        attachment_id, filename, content_type, size, content = row[0], row[1], row[2], row[3], row[4]
        item = {
            "attachment_id": attachment_id,
            "filename": filename,
            "content_type": content_type,
            "size": size,
            "text": "",
            "error": None,
        }
        if not content:
            item["error"] = "content not available"
            results.append(item)
            continue
        try:
            extraction = extract_text_from_attachment(
                file_bytes=bytes(content),
                filename=filename,
                content_type=content_type,
                max_chars=max_chars,
            )
            if extraction.get("success"):
                item["text"] = extraction.get("text", "") or ""
            else:
                item["error"] = extraction.get("error", "extraction failed")
        except Exception as e:
            logger.error(f"extract failed for attachment {attachment_id} ({filename}): {e}")
            item["error"] = str(e)
        results.append(item)

    return results


def build_combined_attachment_text(items: List[Dict], max_chars: Optional[int] = None) -> str:
    """Join per-attachment text with filename headers, capped at max_chars."""
    max_chars = _default_max_chars(max_chars)
    parts = []
    for item in items:
        name = item.get("filename") or "(unknown)"
        if item.get("text"):
            parts.append(f"--- Attachment: {name} ---\n{item['text']}")
        elif item.get("error"):
            parts.append(f"--- Attachment: {name} (could not read: {item['error']}) ---")
    combined = "\n\n".join(parts)
    if len(combined) > max_chars:
        combined = combined[:max_chars] + f"\n\n[... attachment text truncated at {max_chars} chars ...]"
    return combined
