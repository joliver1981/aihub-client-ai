"""
Email completion action.

Sends a formatted email summary of the collected data via either:

  - "smtp"      — the platform's configured SMTP relay (cfg.SMTP_*),
                  loaded from the encrypted LocalSecretsManager at startup.
                  Use this when the deploying customer wants emails sent
                  from THEIR own mail server with their own headers /
                  branding / DKIM. This is the default.

  - "cloud_api" — the platform's hosted email service
                  (`/api/notifications/email`), which uses the platform's
                  shared sender domain. Use this for hosted SaaS
                  deployments where the customer hasn't configured their
                  own SMTP server.

The transport is selectable per-schema via `config.transport`. If the
chosen transport isn't configured, the action falls back to whichever
transport IS configured (with a warning) so a misconfigured schema
doesn't silently swallow submissions.

Schema config:
    {
      "type": "email",
      "label": "Notify recipient",
      "transport": "smtp",                  // OR "cloud_api". Default: "smtp"
      "transport_fallback": true,           // when true (default), if `transport`
                                            //   isn't configured, fall back to
                                            //   the other transport instead of
                                            //   hard-failing. Set false for
                                            //   strict-transport requirements.
      "to": ["recipient@example.com"],
      "to_from_field": "submitter_email",   // optional: pull recipient from a collected field
      "cc": ["cc@example.com"],
      "cc_from_field": "submitter_email",   // optional: pull CC from a collected field
      "cc_from_identity": "email",          // optional: auto-CC the authenticated user
      "from_address": "noreply@example.com",  // optional override (else cfg.SMTP_FROM)
      "from_name": "Data Collection",         // optional display name
      "subject_template": "New Submission: {{title}}",
      "body_template": "templates/x.html",     // optional explicit HTML template path
      "body_format": "auto_summary",           // OR provide a body_html string
      "body_html": "<p>...</p>",               // explicit HTML (with substitutions)
      "include_json_attachment": false,
      "continue_on_error": false
    }
"""

import base64
import json
import logging
import os
import re
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from typing import Any, Dict, List, Optional

from . import ActionHandler, ActionResult, render_template

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# SMTP transport — uses platform-wide cfg.SMTP_* values
# ----------------------------------------------------------------------

def _smtp_settings() -> Dict[str, Any]:
    """Pull SMTP settings from the platform config. Loaded from the
    encrypted LocalSecretsManager via secure_config at startup, so the
    deploying customer's own SMTP server is used automatically.

    Falls back to environment variables if config import fails (e.g.
    in tests or standalone runs without the full platform)."""
    out = {
        'host':    os.environ.get('SMTP_HOST', ''),
        'port':    int(os.environ.get('SMTP_PORT', '587') or 587),
        'user':    os.environ.get('SMTP_USER', ''),
        'password': os.environ.get('SMTP_PASSWORD', ''),
        'use_tls': os.environ.get('SMTP_USE_TLS', 'true').lower() == 'true',
        'from':    os.environ.get('SMTP_FROM', ''),
    }
    try:
        import config as cfg  # platform's central config
        out.update({
            'host':    getattr(cfg, 'SMTP_HOST', out['host']),
            'port':    int(getattr(cfg, 'SMTP_PORT', out['port'])),
            'user':    getattr(cfg, 'SMTP_USER', out['user']),
            'password': getattr(cfg, 'SMTP_PASSWORD', out['password']),
            'use_tls': bool(getattr(cfg, 'SMTP_USE_TLS', out['use_tls'])),
            'from':    getattr(cfg, 'SMTP_FROM', out['from']),
        })
    except Exception as e:
        logger.debug("config import unavailable for SMTP settings: %s", e)
    return out


def _send_via_smtp(
    recipients: List[str],
    subject: str,
    body_html: str,
    cc: Optional[List[str]] = None,
    from_address: Optional[str] = None,
    from_name: Optional[str] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """
    Build a MIME message and ship it via the platform's SMTP relay.
    Returns {'success': bool, 'error': str?, ...}.

    `attachments` is a list of dicts with keys:
        filename, content (base64-encoded str), content_type
    """
    settings = _smtp_settings()
    if not settings['host']:
        return {'success': False, 'error': (
            'SMTP not configured. Set SMTP_HOST/SMTP_USER/SMTP_PASSWORD/SMTP_FROM '
            'in the platform credential store (or as env vars).'
        )}

    sender = from_address or settings['from']
    if not sender:
        return {'success': False, 'error': 'No sender address (config.from_address or cfg.SMTP_FROM)'}
    if from_name:
        sender_header = f'{from_name} <{sender}>'
    else:
        sender_header = sender

    msg = MIMEMultipart('mixed')
    msg['Subject'] = subject
    msg['From'] = sender_header
    msg['To'] = ', '.join(recipients)
    if cc:
        msg['Cc'] = ', '.join(cc)
    msg['Date'] = formatdate(localtime=True)
    msg.attach(MIMEText(body_html or '', 'html'))

    for att in (attachments or []):
        try:
            raw = base64.b64decode(att.get('content') or '')
            part = MIMEApplication(raw, _subtype=(att.get('content_type') or 'octet-stream').split('/')[-1])
            part.add_header(
                'Content-Disposition', 'attachment',
                filename=att.get('filename') or 'attachment.bin',
            )
            msg.attach(part)
        except Exception as e:
            logger.warning("Skipping bad attachment: %s", e)

    all_recipients = list(recipients) + list(cc or [])
    try:
        with smtplib.SMTP(settings['host'], settings['port'], timeout=timeout) as server:
            if settings['use_tls']:
                server.starttls()
            if settings['user'] and settings['password']:
                server.login(settings['user'], settings['password'])
            server.sendmail(sender, all_recipients, msg.as_string())
        return {'success': True, 'transport': 'smtp', 'host': settings['host']}
    except smtplib.SMTPException as e:
        return {'success': False, 'error': f'SMTP error: {e}'}
    except OSError as e:
        return {'success': False, 'error': f'SMTP connection failed: {e}'}
    except Exception as e:
        logger.exception("Unexpected SMTP error")
        return {'success': False, 'error': f'Unexpected SMTP failure: {e}'}


# ----------------------------------------------------------------------
# Cloud-API transport — uses the platform's hosted email service
# ----------------------------------------------------------------------

def _send_via_cloud_api(
    recipients: List[str],
    subject: str,
    body_html: str,
    cc: Optional[List[str]] = None,
    from_address: Optional[str] = None,
    from_name: Optional[str] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Ship the message through the platform's hosted email endpoint
    (`/api/notifications/email`). Mirrors the contract used by
    agent_email_tools.send_email — same endpoint, same auth, same
    rate-limit behavior.
    """
    try:
        from agent_email_tools import _call_cloud_api
    except Exception as e:
        return {'success': False, 'error': f'Cloud API transport unavailable: {e}'}

    payload = {
        'to': list(recipients),
        'subject': subject,
        'body': body_html,
    }
    if cc:
        payload['cc'] = list(cc)
    if from_address:
        payload['from_address'] = from_address
    if from_name:
        payload['from_name'] = from_name
    if attachments:
        payload['attachments'] = attachments

    result = _call_cloud_api('/api/notifications/email', method='POST', data=payload)
    if not result:
        return {'success': False, 'error': 'No response from cloud API'}
    if result.get('success'):
        return {'success': True, 'transport': 'cloud_api', 'response': result}
    # Pass the error through with a hint when rate-limited
    if result.get('blocked_by_limit'):
        return {
            'success': False,
            'transport': 'cloud_api',
            'error': (
                f"Daily email limit reached "
                f"({result.get('current_usage', 0)}/{result.get('max_allowed', 0)})."
            ),
            'response': result,
        }
    return {
        'success': False,
        'transport': 'cloud_api',
        'error': result.get('error', 'Cloud API send failed'),
        'response': result,
    }


def _smtp_configured() -> bool:
    s = _smtp_settings()
    return bool(s.get('host'))


def _cloud_api_configured() -> bool:
    """The platform's cloud API key + URL are set at install time. We
    do a cheap sanity check so we don't try to use a transport that
    obviously isn't wired up."""
    try:
        from agent_email_tools import _get_cloud_api_url, _get_api_key
        return bool(_get_cloud_api_url()) and bool(_get_api_key())
    except Exception:
        return False


# ----------------------------------------------------------------------
# Action handler
# ----------------------------------------------------------------------

EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')


class EmailAction(ActionHandler):
    action_type = 'email'

    def execute(self, collected_data: Dict, session, config: Dict, schema: Dict) -> ActionResult:
        label = config.get('label') or 'Send email'

        # Build recipients
        recipients = self._collect_recipients(
            config.get('to'),
            config.get('to_from_field'),
            collected_data,
            session,
            schema,
        )
        if not recipients:
            return ActionResult(
                action_type=self.action_type,
                label=label,
                success=False,
                message="No valid recipient addresses (config.to or to_from_field).",
            )

        cc = self._collect_recipients(
            config.get('cc'),
            config.get('cc_from_field'),
            collected_data,
            session,
            schema,
        )
        # Auto-CC the authenticated user when configured. Common case:
        # the SAML / parent-system identity is the person submitting; CC
        # them so they have a copy in their own inbox without forcing
        # the schema author to know the email field's name.
        cc_from_identity = config.get('cc_from_identity')
        if cc_from_identity:
            ident_value = self._lookup_identity_attr(session, cc_from_identity)
            if ident_value and EMAIL_RE.match(str(ident_value)):
                if str(ident_value) not in cc and str(ident_value) not in recipients:
                    cc.append(str(ident_value))

        # Subject + body
        subject_template = config.get('subject_template') or 'New submission'
        subject = render_template(subject_template, collected_data, session, schema)

        body_html = self._build_body_html(config, collected_data, session, schema)

        # From address — schema can override; otherwise the platform-wide
        # SMTP_FROM (set in the credential store at install time) is used.
        from_address = config.get('from_address')  # may be None — _send_via_smtp falls back
        from_name = config.get('from_name') or 'Data Collection Agent'

        attachments = []
        if config.get('include_json_attachment'):
            json_blob = json.dumps(
                {'session_id': session.session_id, 'data': collected_data},
                default=str,
                indent=2,
            ).encode('utf-8')
            attachments.append({
                'filename': f"submission_{session.session_id[:8]}.json",
                'content': base64.b64encode(json_blob).decode('utf-8'),
                'content_type': 'application/json',
            })

        # Per-schema transport selection. Default is "smtp" (existing
        # behavior). If the chosen transport isn't configured on this
        # deployment, fall back to whichever IS configured rather than
        # silently failing — unless transport_fallback is explicitly
        # disabled. The fallback is on by default because most schema
        # authors want "the email went out" over "the send hard-failed."
        # Authors that need transport-strictness (e.g. compliance
        # requires SMTP only, never the hosted relay) set
        # `transport_fallback: false` to make a misconfiguration loud.
        requested = (config.get('transport') or 'smtp').strip().lower()
        if requested not in ('smtp', 'cloud_api'):
            logger.warning(
                "Unknown email transport %r — falling back to 'smtp'.",
                requested,
            )
            requested = 'smtp'

        # Default is True (fallback ON). Accept explicit booleans only.
        fallback_enabled = config.get('transport_fallback', True)
        if isinstance(fallback_enabled, str):
            fallback_enabled = fallback_enabled.strip().lower() not in (
                'false', '0', 'no', 'off',
            )

        chosen = requested
        if chosen == 'smtp' and not _smtp_configured():
            if fallback_enabled and _cloud_api_configured():
                logger.warning(
                    "Email schema requested SMTP but SMTP is not configured "
                    "(no SMTP_HOST). Falling back to cloud_api transport."
                )
                chosen = 'cloud_api'
            elif not fallback_enabled:
                return ActionResult(
                    action_type=self.action_type,
                    label=label,
                    success=False,
                    message=(
                        "Email transport 'smtp' is not configured on this "
                        "deployment, and transport_fallback is disabled."
                    ),
                    details={
                        'requested_transport': requested,
                        'transport_fallback': False,
                    },
                )
        elif chosen == 'cloud_api' and not _cloud_api_configured():
            if fallback_enabled and _smtp_configured():
                logger.warning(
                    "Email schema requested cloud_api but the cloud API is "
                    "not configured (no API URL/key). Falling back to SMTP."
                )
                chosen = 'smtp'
            elif not fallback_enabled:
                return ActionResult(
                    action_type=self.action_type,
                    label=label,
                    success=False,
                    message=(
                        "Email transport 'cloud_api' is not configured on "
                        "this deployment, and transport_fallback is disabled."
                    ),
                    details={
                        'requested_transport': requested,
                        'transport_fallback': False,
                    },
                )

        if chosen == 'cloud_api':
            send_result = _send_via_cloud_api(
                recipients=recipients,
                subject=subject,
                body_html=body_html,
                cc=cc or None,
                from_address=from_address,
                from_name=from_name,
                attachments=attachments or None,
            )
        else:
            send_result = _send_via_smtp(
                recipients=recipients,
                subject=subject,
                body_html=body_html,
                cc=cc or None,
                from_address=from_address,
                from_name=from_name,
                attachments=attachments or None,
            )

        # Annotate when we ended up on a different transport than asked
        if chosen != requested:
            send_result['transport_fallback_from'] = requested

        if send_result.get('success'):
            return ActionResult(
                action_type=self.action_type,
                label=label,
                success=True,
                message=f"Sent to {len(recipients)} recipient(s)"
                        + (f", CC {len(cc)}" if cc else "") + ".",
                details={
                    'recipients': recipients, 'cc': cc, 'subject': subject,
                    'transport': send_result.get('transport'),
                    'host': send_result.get('host'),
                },
            )
        return ActionResult(
            action_type=self.action_type,
            label=label,
            success=False,
            message=send_result.get('error', 'Email send failed'),
            details={'response': send_result, 'recipients': recipients, 'cc': cc},
        )

    def _lookup_identity_attr(self, session, attr_name: str) -> Optional[str]:
        """Pull a named attribute off the session's identity (e.g. 'email',
        'user_id', 'name'). Used by cc_from_identity. Read-only; never
        mutates session state."""
        # The session carries identity-derived fields. We check both
        # well-known top-level attrs and the user_email shortcut.
        if attr_name == 'email':
            return getattr(session, 'user_email', None)
        if attr_name in ('name', 'user_name'):
            return getattr(session, 'user_name', None)
        if attr_name in ('id', 'user_id'):
            return getattr(session, 'user_id', None)
        return getattr(session, attr_name, None)

    # ------------------------------------------------------------------
    def validate_config(self, config: Dict) -> List[str]:
        errors = []
        if not config.get('to') and not config.get('to_from_field'):
            errors.append("email action requires 'to' or 'to_from_field'")
        if not config.get('subject_template'):
            errors.append("email action requires 'subject_template'")
        if not (config.get('body_format') or config.get('body_html') or config.get('body_template')):
            errors.append("email action requires 'body_format', 'body_html', or 'body_template'")
        if config.get('cc_from_identity'):
            allowed = {'email', 'name', 'user_name', 'id', 'user_id'}
            if config['cc_from_identity'] not in allowed:
                errors.append(
                    f"cc_from_identity must be one of {sorted(allowed)}, "
                    f"got {config['cc_from_identity']!r}"
                )
        # Transport: per-schema selection. Default is 'smtp'.
        if 'transport' in config:
            allowed_transports = {'smtp', 'cloud_api'}
            if config['transport'] not in allowed_transports:
                errors.append(
                    f"transport must be one of {sorted(allowed_transports)}, "
                    f"got {config['transport']!r}"
                )
        if 'transport_fallback' in config:
            if not isinstance(config['transport_fallback'], bool):
                errors.append(
                    "transport_fallback must be a boolean (true/false), "
                    f"got {type(config['transport_fallback']).__name__}"
                )
        return errors

    # ------------------------------------------------------------------
    def _collect_recipients(self, static_list, from_field: Optional[str],
                            collected_data: Dict, session, schema: Dict) -> List[str]:
        out = []
        if static_list:
            if isinstance(static_list, str):
                static_list = [static_list]
            for raw in static_list:
                resolved = render_template(raw, collected_data, session, schema)
                if resolved and EMAIL_RE.match(str(resolved)):
                    out.append(str(resolved))
        if from_field:
            value = self._lookup_field_value(from_field, collected_data)
            if value:
                if isinstance(value, list):
                    for v in value:
                        if EMAIL_RE.match(str(v)):
                            out.append(str(v))
                elif EMAIL_RE.match(str(value)):
                    out.append(str(value))
        # Deduplicate, preserve order
        seen = set()
        uniq = []
        for addr in out:
            if addr.lower() in seen:
                continue
            seen.add(addr.lower())
            uniq.append(addr)
        return uniq

    def _lookup_field_value(self, field_id: str, collected_data: Dict):
        # Allow "section.field" or bare "field"
        if '.' in field_id:
            section_id, fid = field_id.split('.', 1)
            return (collected_data.get(section_id) or {}).get(fid)
        for sec_data in collected_data.values():
            if isinstance(sec_data, dict) and field_id in sec_data:
                return sec_data[field_id]
        return None

    def _build_body_html(self, config: Dict, collected_data: Dict, session, schema: Dict) -> str:
        # Explicit HTML wins
        if config.get('body_html'):
            return render_template(config['body_html'], collected_data, session, schema)

        # External template file
        body_template_path = config.get('body_template')
        if body_template_path:
            try:
                full_path = body_template_path
                if not os.path.isabs(body_template_path):
                    # Resolve relative to data_collection_agent/configs/
                    from ..schema_loader import CONFIGS_DIR
                    full_path = os.path.join(CONFIGS_DIR, body_template_path)
                with open(full_path, 'r', encoding='utf-8') as f:
                    template = f.read()
                return render_template(template, collected_data, session, schema)
            except Exception as e:
                logger.warning(
                    f"Could not read body_template '{body_template_path}': {e}; "
                    "falling back to auto_summary"
                )

        # Default: auto-generate from collected data
        return _build_auto_summary_html(schema, collected_data, session)


def _build_auto_summary_html(schema: Dict, collected_data: Dict, session) -> str:
    """Render an HTML summary of the collected data, organized by section."""
    from ..schema_loader import get_section_order, get_section, get_lookup_values

    rows_html_parts = []
    for sid in get_section_order(schema):
        section = get_section(schema, sid)
        if not section:
            continue
        sec_data = collected_data.get(sid) or {}
        if not sec_data:
            continue

        rows_html_parts.append(
            f"<h3 style='margin-top:24px;font-family:Arial,sans-serif;color:#0f172a'>"
            f"{_escape(section.get('title', sid))}</h3>"
        )
        rows_html_parts.append(
            "<table style='border-collapse:collapse;width:100%;font-family:Arial,sans-serif;font-size:14px'>"
        )
        for fld in section.get('fields', []):
            fid = fld.get('id')
            if not fid or fid not in sec_data:
                continue
            value = sec_data[fid]
            display = _format_value(value, fld, schema, get_lookup_values)
            rows_html_parts.append(
                f"<tr>"
                f"<td style='padding:8px;border-bottom:1px solid #e5e7eb;color:#475569;width:200px'>"
                f"{_escape(fld.get('label', fid))}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #e5e7eb;color:#0f172a'>"
                f"{_escape(display)}</td>"
                f"</tr>"
            )
        rows_html_parts.append("</table>")

    body = (
        "<div style='max-width:720px;margin:0 auto;padding:24px;font-family:Arial,sans-serif;color:#0f172a'>"
        f"<h2 style='margin:0 0 8px 0'>{_escape(schema.get('name', 'Submission'))}</h2>"
        f"<p style='color:#64748b;margin:0 0 16px 0;font-size:13px'>"
        f"Submission ID: {session.session_id} &middot; Submitted: {session.submitted_at or session.updated_at}</p>"
        + ''.join(rows_html_parts)
        + "</div>"
    )
    return body


def _escape(value) -> str:
    if value is None:
        return ''
    s = str(value)
    return (
        s.replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
    )


def _format_value(value, field: Dict, schema: Dict, lookup_fn) -> str:
    if value is None or value == '':
        return '—'
    field_type = field.get('type', 'text')
    if field_type == 'boolean':
        return 'Yes' if value else 'No'
    if field_type == 'multi_select' and isinstance(value, list):
        return ', '.join(str(v) for v in value)
    if field_type == 'lookup' and field.get('lookup_ref'):
        items = lookup_fn(schema, field['lookup_ref'])
        for item in items:
            if isinstance(item, dict) and str(item.get('id')) == str(value):
                return str(item.get('label') or item.get('name') or value)
    if field_type == 'select' and field.get('options_ref'):
        items = lookup_fn(schema, field['options_ref'])
        for item in items:
            if isinstance(item, dict) and str(item.get('id')) == str(value):
                return str(item.get('label') or value)
    if isinstance(value, list):
        return ', '.join(str(v) for v in value)
    return str(value)
