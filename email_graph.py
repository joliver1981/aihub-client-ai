"""
email_graph.py — outbound notification email through Microsoft 365 (Graph API).

Provider value: EMAIL_PROVIDER=graph (the "Microsoft 365 (OAuth2)" choice on
the admin Email Settings page). OAuth 2.0 client credentials against the
client's Entra tenant, then POST /users/{sender}/sendMail with the application
permission Mail.Send. HTTPS only — no SMTP AUTH at all, so it is unaffected by
Microsoft's retirement of Basic authentication for SMTP client submission.

Scope: SYSTEM notifications only — the same sends that use the SMTP relay
today (workflow alerts, approval / review reminders, the notification routes).
Agent-mailbox mail keeps its own path (the cloud relay) and never comes here.

Contract mirrors EmailUtils.send_email_smtp: send_email_graph(...) -> bool and
never raises. send_mail(...) raises GraphMailError carrying Microsoft's own
error text (AADSTS..., ErrorAccessDenied...) so the Email Settings "send test"
can show the admin exactly what Entra / Exchange said.

Config arrives already resolved (email_settings.get_email_config):
graph_tenant_id, graph_client_id, graph_client_secret, graph_sender. Tokens
are cached in THIS process only (the main app, the executor and the DCA each
keep their own); Entra tokens live ~60-90 minutes and are refreshed
EXPIRY_LEEWAY_SECONDS early.

Limits: an inline fileAttachment must be under 3 MB (Microsoft). A larger
attachment raises GraphMailError so the caller can fall back to SMTP, which
carries it fine; the 3-150 MB upload-session path is deliberately not
implemented. No new dependencies — the token exchange is the same
requests.post the SharePoint app-only integration already makes.
"""

import base64
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Union

import requests

logger = logging.getLogger(__name__)

TOKEN_URL = 'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token'
GRAPH_SCOPE = 'https://graph.microsoft.com/.default'
SENDMAIL_URL = 'https://graph.microsoft.com/v1.0/users/{sender}/sendMail'
INLINE_ATTACHMENT_LIMIT = 3 * 1024 * 1024   # Microsoft: larger needs an upload session
EXPIRY_LEEWAY_SECONDS = 300
TIMEOUT = (10, 30)                          # connect, read (seconds)

# (tenant_id, client_id) -> (access_token, expires_at_epoch_seconds)
_token_cache: Dict[tuple, tuple] = {}
_cache_lock = threading.Lock()


class GraphMailError(RuntimeError):
    """A Microsoft 365 send failed; str(e) carries the provider's own error text."""


def _error_text(resp) -> str:
    """The most useful sentence Microsoft returned, for logs and the test-send UI."""
    try:
        data = resp.json()
    except ValueError:
        return (resp.text or '')[:300]
    err = data.get('error') if isinstance(data, dict) else None
    if isinstance(err, dict):        # Graph: {"error": {"code": ..., "message": ...}}
        return f"{err.get('code', '')}: {err.get('message', '')}".strip(': ')
    if isinstance(err, str):         # Entra token endpoint: {"error", "error_description"}
        return f"{err}: {data.get('error_description', '')}".strip(': ')
    return (resp.text or '')[:300]


def clear_token_cache() -> None:
    with _cache_lock:
        _token_cache.clear()


def get_access_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    """A client-credentials token for Graph, cached per (tenant, client) until
    EXPIRY_LEEWAY_SECONDS before it expires. Raises GraphMailError."""
    tenant_id = (tenant_id or '').strip()
    client_id = (client_id or '').strip()
    client_secret = (client_secret or '').strip()
    if not (tenant_id and client_id and client_secret):
        raise GraphMailError(
            "Microsoft 365 is not configured: tenant ID, client ID and client secret are all required")

    key = (tenant_id, client_id)
    # One fetch at a time: ten simultaneous notifications on an expired token
    # must not each hit Entra.
    with _cache_lock:
        cached = _token_cache.get(key)
        if cached and cached[1] - EXPIRY_LEEWAY_SECONDS > time.time():
            return cached[0]
        try:
            resp = requests.post(
                TOKEN_URL.format(tenant=tenant_id),
                data={
                    'grant_type': 'client_credentials',
                    'client_id': client_id,
                    'client_secret': client_secret,
                    'scope': GRAPH_SCOPE,
                },
                headers={'Accept': 'application/json'},
                timeout=TIMEOUT,
            )
        except requests.RequestException as e:
            raise GraphMailError(f"Microsoft 365 token request failed: {e}") from e
        if resp.status_code != 200:
            raise GraphMailError(
                f"Microsoft 365 token request failed (HTTP {resp.status_code}): {_error_text(resp)}")
        data = resp.json()
        token = data.get('access_token')
        if not token:
            raise GraphMailError("Microsoft 365 token response had no access_token")
        expires_in = int(data.get('expires_in') or 3600)
        _token_cache[key] = (token, time.time() + expires_in)
        return token


def _recipients(addresses: Union[str, List[str], None]) -> List[Dict[str, Any]]:
    if not addresses:
        return []
    if isinstance(addresses, str):
        addresses = addresses.replace(';', ',').split(',')
    return [{'emailAddress': {'address': a.strip()}} for a in addresses if a and a.strip()]


def build_message(recipients: Union[str, List[str]], subject: str, body: str,
                  html_content: bool = False, attachment_path: Optional[str] = None,
                  cc: Union[str, List[str], None] = None,
                  bcc: Union[str, List[str], None] = None) -> Dict[str, Any]:
    """The sendMail request body. Raises GraphMailError for a missing or
    oversized attachment (the SMTP fallback can still carry it)."""
    to = _recipients(recipients)
    if not to:
        raise GraphMailError("at least one recipient is required")
    message: Dict[str, Any] = {
        'subject': subject or '',
        'body': {'contentType': 'HTML' if html_content else 'Text', 'content': body or ''},
        'toRecipients': to,
    }
    cc_list = _recipients(cc)
    if cc_list:
        message['ccRecipients'] = cc_list
    bcc_list = _recipients(bcc)
    if bcc_list:
        message['bccRecipients'] = bcc_list
    if attachment_path:
        if not os.path.exists(attachment_path):
            raise GraphMailError(f"attachment not found: {attachment_path}")
        size = os.path.getsize(attachment_path)
        if size > INLINE_ATTACHMENT_LIMIT:
            raise GraphMailError(
                f"attachment {os.path.basename(attachment_path)} is {size / (1024 * 1024):.1f} MB; "
                f"Microsoft 365 inline attachments are limited to 3 MB")
        with open(attachment_path, 'rb') as f:
            content = f.read()
        message['attachments'] = [{
            '@odata.type': '#microsoft.graph.fileAttachment',
            'name': os.path.basename(attachment_path),
            'contentType': 'application/octet-stream',
            'contentBytes': base64.b64encode(content).decode('ascii'),
        }]
    return {'message': message, 'saveToSentItems': True}


def send_mail(conf: Dict[str, Any], recipients: Union[str, List[str]], subject: str, body: str,
              attachment_path: Optional[str] = None, html_content: bool = False,
              cc: Union[str, List[str], None] = None,
              bcc: Union[str, List[str], None] = None) -> None:
    """Send through Graph, or raise GraphMailError with Microsoft's error text."""
    sender = (conf.get('graph_sender') or '').strip()
    if not sender:
        raise GraphMailError("Microsoft 365 is not configured: a sender mailbox is required")
    payload = build_message(recipients, subject, body, html_content, attachment_path, cc, bcc)
    url = SENDMAIL_URL.format(sender=sender)
    for attempt in (1, 2):
        token = get_access_token(conf.get('graph_tenant_id'), conf.get('graph_client_id'),
                                 conf.get('graph_client_secret'))
        try:
            resp = requests.post(
                url, json=payload,
                headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
                timeout=TIMEOUT,
            )
        except requests.RequestException as e:
            raise GraphMailError(f"Microsoft 365 sendMail request failed: {e}") from e
        if resp.status_code in (200, 202):
            return
        if resp.status_code == 401 and attempt == 1:
            # A token the cache still considered valid was rejected: fetch a
            # fresh one exactly once, then report whatever comes back.
            clear_token_cache()
            continue
        raise GraphMailError(
            f"Microsoft 365 sendMail failed (HTTP {resp.status_code}): {_error_text(resp)}")


def send_email_graph(recipients: Union[str, List[str]], subject: str, body: str,
                     attachment_path: Optional[str] = None, html_content: bool = False,
                     conf: Optional[Dict[str, Any]] = None,
                     cc: Union[str, List[str], None] = None,
                     bcc: Union[str, List[str], None] = None) -> bool:
    """bool contract like EmailUtils.send_email_smtp — never raises. `conf`
    defaults to the live Email Settings resolution (UI wins, .env fallback)."""
    try:
        if conf is None:
            from email_settings import get_email_config
            conf = get_email_config()
        send_mail(conf, recipients, subject, body, attachment_path, html_content, cc, bcc)
        logger.info("Microsoft 365 (Graph) email sent successfully")
        return True
    except Exception as e:
        logger.error(f"Error sending Microsoft 365 (Graph) email: {e}")
        return False
