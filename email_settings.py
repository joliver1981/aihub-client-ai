"""
Email Settings — admin UI configuration for OUTBOUND notification email.

Until now the sender (EmailUtils / AppUtils) was configured only by .env
(EMAIL_PROVIDER, SMTP_*, API_AZURE_EMAIL_*) and — worse — those values were
bound as function DEFAULT ARGUMENTS at import, so even a .env edit needed a
service restart. This module makes the config UI-editable and call-time
resolved, following the api_keys_config.py (BYOK) pattern exactly:

  * non-secret settings in a JSON file under the data dir
  * the SMTP password / Azure connection string in the encrypted,
    machine-bound local secrets store — never in the JSON, never echoed back
  * getters fall back to .env when nothing is configured in the UI, so
    existing installs keep working untouched
  * admin-gated blueprint + page (role >= 3), with a "send test email"
    endpoint that reports the real transport error

Resolution rule (deliberately all-or-nothing, not per-field): when the UI
config has a provider set, the UI values are authoritative — a blank SMTP
user there MEANS anonymous (IP-allowlisted relay), it does not fall back to
the .env user. With no UI provider set, everything comes from .env.
"""

import json
import logging
import os
from functools import wraps
from pathlib import Path
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, render_template, request

import app_config as cfg
from local_secrets import get_local_secret, set_local_secret, has_local_secret

logger = logging.getLogger(__name__)

email_settings_bp = Blueprint('email_settings', __name__, url_prefix='/api/email-settings')

SMTP_PASSWORD_SECRET = 'EMAIL_SMTP_PASSWORD'
AZURE_CONN_STR_SECRET = 'EMAIL_AZURE_CONN_STR'
SECRETS_CATEGORY = 'email_settings'


def _config_file() -> Path:
    data_dir = Path(os.getenv('AIHUB_DATA_DIR', './data'))
    return data_dir / 'email_settings.json'


def require_admin(f):
    """Require admin role (role >= 3) — same contract as api_keys_config."""
    @wraps(f)
    def decorated(*args, **kwargs):
        try:
            from flask_login import current_user
            if not current_user.is_authenticated:
                return jsonify({'success': False, 'error': 'Authentication required'}), 401
            if current_user.role < 3:
                return jsonify({'success': False, 'error': 'Admin access required'}), 403
        except Exception as e:
            logger.error(f"Auth check failed: {e}")
            return jsonify({'success': False, 'error': 'Authentication check failed'}), 500
        return f(*args, **kwargs)
    return decorated


def _load() -> Dict[str, Any]:
    p = _config_file()
    if p.exists():
        try:
            with open(p, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"email_settings config unreadable: {e}")
    return {}


def _save(config: Dict[str, Any]) -> bool:
    p = _config_file()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)
        return True
    except IOError as e:
        logger.error(f"email_settings config not saved: {e}")
        return False


# ---------------------------------------------------------------- effective config

def get_email_config() -> Dict[str, Any]:
    """The config the senders should use RIGHT NOW (call-time, no restart).

    Returns {source, provider, smtp_host, smtp_port, smtp_user, smtp_password,
    smtp_use_tls, smtp_from, azure_conn_str, azure_sender}. source is 'ui'
    when the admin configured a provider in the UI, else 'env'."""
    ui = _load()
    provider = (ui.get('provider') or '').strip().lower()
    if provider in ('smtp', 'azure'):
        smtp = ui.get('smtp') or {}
        azure = ui.get('azure') or {}
        return {
            'source': 'ui',
            'provider': provider,
            'smtp_host': (smtp.get('host') or '').strip(),
            'smtp_port': int(smtp.get('port') or 25),
            'smtp_user': (smtp.get('user') or '').strip(),
            'smtp_password': (get_local_secret(SMTP_PASSWORD_SECRET)
                              if has_local_secret(SMTP_PASSWORD_SECRET) else ''),
            'smtp_use_tls': bool(smtp.get('use_tls')),
            'smtp_from': (smtp.get('from') or '').strip(),
            'azure_conn_str': (get_local_secret(AZURE_CONN_STR_SECRET)
                               if has_local_secret(AZURE_CONN_STR_SECRET) else ''),
            'azure_sender': (azure.get('sender') or '').strip(),
        }
    return {
        'source': 'env',
        'provider': (getattr(cfg, 'EMAIL_PROVIDER', '') or 'azure').strip().lower(),
        'smtp_host': getattr(cfg, 'SMTP_HOST', '') or '',
        'smtp_port': int(getattr(cfg, 'SMTP_PORT', 25) or 25),
        'smtp_user': getattr(cfg, 'SMTP_USER', '') or '',
        'smtp_password': getattr(cfg, 'SMTP_PASSWORD', '') or '',
        'smtp_use_tls': bool(getattr(cfg, 'SMTP_USE_TLS', False)),
        'smtp_from': getattr(cfg, 'SMTP_FROM', '') or '',
        'azure_conn_str': getattr(cfg, 'API_AZURE_EMAIL_CONN_STR', '') or '',
        'azure_sender': getattr(cfg, 'API_AZURE_EMAIL_SENDER', '') or '',
    }


# ---------------------------------------------------------------- API routes

@email_settings_bp.route('/status', methods=['GET'])
@require_admin
def status():
    """Current settings for the page — secrets reported as set/not-set only."""
    ui = _load()
    conf = get_email_config()
    return jsonify({
        'success': True,
        'source': conf['source'],
        'provider': conf['provider'],
        'ui': {
            'provider': (ui.get('provider') or ''),
            'smtp': {k: (ui.get('smtp') or {}).get(k, '') for k in ('host', 'port', 'user', 'from')}
                    | {'use_tls': bool((ui.get('smtp') or {}).get('use_tls'))},
            'azure': {'sender': (ui.get('azure') or {}).get('sender', '')},
            'smtp_password_set': has_local_secret(SMTP_PASSWORD_SECRET),
            'azure_conn_str_set': has_local_secret(AZURE_CONN_STR_SECRET),
        },
        'env_defaults': {
            'provider': (getattr(cfg, 'EMAIL_PROVIDER', '') or 'azure'),
            'smtp_host': getattr(cfg, 'SMTP_HOST', '') or '',
            'smtp_port': getattr(cfg, 'SMTP_PORT', '') or '',
            'smtp_user': getattr(cfg, 'SMTP_USER', '') or '',
            'smtp_from': getattr(cfg, 'SMTP_FROM', '') or '',
            'smtp_use_tls': bool(getattr(cfg, 'SMTP_USE_TLS', False)),
            'azure_sender': getattr(cfg, 'API_AZURE_EMAIL_SENDER', '') or '',
            'azure_conn_str_set': bool(getattr(cfg, 'API_AZURE_EMAIL_CONN_STR', '') or ''),
            'smtp_password_set': bool(getattr(cfg, 'SMTP_PASSWORD', '') or ''),
        },
    })


@email_settings_bp.route('/save', methods=['POST'])
@require_admin
def save():
    """Persist UI config. Secrets are write-only: saved when a non-empty value
    arrives, left untouched when the field comes back blank."""
    data = request.get_json(silent=True) or {}
    provider = (data.get('provider') or '').strip().lower()
    if provider not in ('smtp', 'azure'):
        return jsonify({'success': False, 'error': "provider must be 'smtp' or 'azure'"}), 400

    smtp_in = data.get('smtp') or {}
    azure_in = data.get('azure') or {}

    if provider == 'smtp':
        host = (smtp_in.get('host') or '').strip()
        if not host:
            return jsonify({'success': False, 'error': 'SMTP host is required'}), 400
        try:
            port = int(smtp_in.get('port') or 0)
            if not (1 <= port <= 65535):
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'SMTP port must be 1-65535'}), 400
        if not (smtp_in.get('from') or '').strip():
            return jsonify({'success': False, 'error': 'From address is required'}), 400
    else:
        if not (azure_in.get('sender') or '').strip():
            return jsonify({'success': False, 'error': 'Azure sender address is required'}), 400
        conn = (azure_in.get('connection_string') or '').strip()
        if not conn and not has_local_secret(AZURE_CONN_STR_SECRET):
            return jsonify({'success': False,
                            'error': 'Azure connection string is required (none stored yet)'}), 400

    pw = (smtp_in.get('password') or '').strip()
    if pw:
        set_local_secret(SMTP_PASSWORD_SECRET, pw,
                         description='Outbound notification email — SMTP password',
                         category=SECRETS_CATEGORY)
    conn = (azure_in.get('connection_string') or '').strip()
    if conn:
        set_local_secret(AZURE_CONN_STR_SECRET, conn,
                         description='Outbound notification email — Azure Communication Services connection string',
                         category=SECRETS_CATEGORY)

    ok = _save({
        'provider': provider,
        'smtp': {
            'host': (smtp_in.get('host') or '').strip(),
            'port': int(smtp_in.get('port') or 25) if str(smtp_in.get('port') or '').strip() else 25,
            'user': (smtp_in.get('user') or '').strip(),
            'from': (smtp_in.get('from') or '').strip(),
            'use_tls': bool(smtp_in.get('use_tls')),
        },
        'azure': {'sender': (azure_in.get('sender') or '').strip()},
    })
    if not ok:
        return jsonify({'success': False, 'error': 'could not write the config file'}), 500
    logger.info(f"email settings saved: provider={provider} (UI mode active)")
    return jsonify({'success': True, 'message': 'Email settings saved — active immediately (no restart).'})


@email_settings_bp.route('/revert', methods=['POST'])
@require_admin
def revert():
    """Back to .env defaults. Stored secrets are kept (write-only store)."""
    p = _config_file()
    try:
        if p.exists():
            p.unlink()
    except OSError as e:
        return jsonify({'success': False, 'error': f'could not remove config: {e}'}), 500
    return jsonify({'success': True, 'message': 'Reverted to server .env configuration.'})


@email_settings_bp.route('/test', methods=['POST'])
@require_admin
def test_send():
    """Send a test email with the CURRENT effective config and report the real
    transport error on failure (the senders in EmailUtils swallow exceptions,
    which is right for batch jobs and useless for diagnostics)."""
    data = request.get_json(silent=True) or {}
    to = (data.get('to') or '').strip()
    if not to or '@' not in to:
        return jsonify({'success': False, 'error': 'a recipient address is required'}), 400

    conf = get_email_config()
    subject = 'AI Hub — test email (Email Settings)'
    body = (f"This is a test email from AI Hub's Email Settings page.\n"
            f"Provider: {conf['provider']}  ·  config source: {conf['source']}")
    try:
        if conf['provider'] == 'smtp':
            import smtplib
            from email.mime.text import MIMEText
            from email.utils import formatdate
            msg = MIMEText(body, 'plain')
            msg['Subject'] = subject
            msg['From'] = conf['smtp_from']
            msg['To'] = to
            msg['Date'] = formatdate(localtime=True)
            with smtplib.SMTP(conf['smtp_host'], conf['smtp_port'], timeout=20) as server:
                if conf['smtp_use_tls']:
                    server.starttls()
                if conf['smtp_user'] and conf['smtp_password']:
                    server.login(conf['smtp_user'], conf['smtp_password'])
                server.send_message(msg)
        else:
            from azure.communication.email import EmailClient
            client = EmailClient.from_connection_string(conf['azure_conn_str'])
            poller = client.begin_send({
                'senderAddress': conf['azure_sender'],
                'recipients': {'to': [{'address': to}]},
                'content': {'subject': subject, 'plainText': body},
            })
            poller.result()
        return jsonify({'success': True,
                        'message': f"Test email sent to {to} via {conf['provider']} "
                                   f"({conf['source']} config)."})
    except Exception as e:
        logger.warning(f"email settings test send failed: {e}")
        return jsonify({'success': False, 'error': f'{type(e).__name__}: {e}'}), 502


# ---------------------------------------------------------------- page route

def register_email_settings_page(app):
    """Call from app.py, next to api_keys_config.register_page_route(app)."""
    from flask_login import login_required, current_user

    @app.route('/admin/email-settings')
    @login_required
    def email_settings_page():
        if current_user.role < 3:
            from flask import abort
            abort(403)
        return render_template('email_settings.html')
