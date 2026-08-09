"""
Agent Email (A6) — async client for the EXISTING email seams.

Nothing new is invented here: these are the same cloud-API endpoints the
legacy EmailAgentDispatcher and GeneralAgent inbox tools already use
(GET /api/email/poll, /api/email/tenant-id, /api/email/message/<key>,
/api/email/attachments/<event_id>; POST /api/notifications/email), plus the
main app's attachment-extract route (which wraps the full
attachment_text_extractor including the OCR fallback) — all with the tenant
API key, exactly like email_receive_client.py / notification_client.py.
"""

import os
import time
from typing import Optional

import httpx

from agent_config import get_base_url, AI_HUB_API_KEY, logger

_TIMEOUT = httpx.Timeout(30.0, read=120.0)

_tenant_cache: dict = {"at": 0.0, "info": None}
_TENANT_TTL = 300


def _cloud_base() -> str:
    return (os.getenv("AI_HUB_API_URL") or "https://api.aihub.everiai.ai").rstrip("/")


def _headers() -> dict:
    return {"X-API-Key": os.getenv("API_KEY", ""), "Connection": "close"}


async def tenant_info(force: bool = False) -> Optional[dict]:
    """{tenant_id, domain, email_format} from the cloud; 5-min cache (same
    TTL as agent_email_routes)."""
    if not force and _tenant_cache["info"] and \
            time.time() - _tenant_cache["at"] < _TENANT_TTL:
        return _tenant_cache["info"]
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(f"{_cloud_base()}/api/email/tenant-id",
                                 headers=_headers())
            data = r.json()
            if r.status_code == 200 and data.get("success"):
                _tenant_cache.update(at=time.time(), info=data)
                return data
    except Exception as e:
        logger.warning(f"email tenant-info fetch failed: {e}")
    return _tenant_cache["info"]


def compose_address(prefix: str, tenant_id, domain: str) -> str:
    """James's A6 format. '-agent' is part of the prefix as far as the cloud
    parser is concerned; the last dot-segment must stay the numeric tenant."""
    return f"{prefix}-agent.{tenant_id}@{domain}"


async def poll(limit: int = 100) -> list:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(f"{_cloud_base()}/api/email/poll",
                             params={"limit": limit}, headers=_headers())
        data = r.json() if r.status_code == 200 else {}
        return data.get("emails") or data.get("events") or []


async def full_body(message_key: str) -> Optional[str]:
    """Full body via the cloud message proxy. Poll rows carry NO body at all
    (metadata only — verified live 2026-08-09), so this fetch is the ONLY
    body source and its parsing must match the proxy's real shape:
    {"success": true, "message": {body_text, body_plain, stripped_text,
    body_html, ...}} — the same envelope email_receive_client unwraps with
    result.get('message'). The original A6 code looked for a nonexistent
    'content' key and Mailgun hyphen field names, so every inbound email
    read as '(empty body)' (James's live repro, event 48)."""
    if not message_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(
                f"{_cloud_base()}/api/email/message/{message_key}",
                headers=_headers())
            if r.status_code != 200:
                return None
            data = r.json()
            content = data.get("message") or data.get("content") or data
            if not isinstance(content, dict):
                return None
            # body_text first (legacy dispatcher parity: complete content,
            # quoted thread included); stripped_text is Mailgun's
            # new-text-only heuristic and can drop inline replies.
            return (content.get("body_text") or content.get("stripped_text")
                    or content.get("body_plain") or content.get("body-plain")
                    or content.get("stripped-text") or None)
    except Exception as e:
        logger.warning(f"email full-body fetch failed: {e}")
        return None


async def attachments_for(event_id: int) -> list:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(
                f"{_cloud_base()}/api/email/attachments/{event_id}",
                headers=_headers())
            data = r.json() if r.status_code == 200 else {}
            return data.get("attachments") or []
    except Exception as e:
        logger.warning(f"email attachments list failed: {e}")
        return []


async def extract_attachment_text(attachment_id: int,
                                  max_chars: int = 20000) -> dict:
    """Extraction via the MAIN APP's existing route — it owns the extractor
    (PDF/DOCX/XLSX/CSV/... + OCR fallback) and the caps. X-API-Key works:
    the route is api_key_or_session."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=300.0)) as client:
            r = await client.get(
                f"{get_base_url()}/api/agent-email/attachment/{attachment_id}/extract",
                params={"max_chars": max_chars},
                headers={"X-API-Key": AI_HUB_API_KEY})
            data = r.json() if r.status_code == 200 else {}
            if not data.get("success", False):
                return {"success": False,
                        "error": data.get("error", f"HTTP {r.status_code}")}
            return data
    except Exception as e:
        return {"success": False, "error": str(e)}


async def send_reply(to: list, subject: str, body: str, from_address: str,
                     from_name: str) -> dict:
    """Outbound via the cloud notifications API — the SAME transport every
    legacy send path uses (notification_client payload shape, provider
    mailgun so the from address is honored). Returns the cloud's result
    verbatim; caller judges success honestly."""
    payload = {"to": [str(a) for a in to], "subject": subject, "body": body,
               "provider": "mailgun", "from_address": from_address,
               "from_name": from_name}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=60.0)) as client:
            r = await client.post(f"{_cloud_base()}/api/notifications/email",
                                  json=payload, headers=_headers())
            try:
                data = r.json()
            except Exception:
                data = {"success": False, "error": r.text[:300]}
            data.setdefault("http_status", r.status_code)
            return data
    except Exception as e:
        return {"success": False, "error": str(e)}
