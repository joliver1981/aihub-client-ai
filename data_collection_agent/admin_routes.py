"""
Admin routes for the data collection agent.

Phase 1: HTTPS configuration (mode selector, self-signed cert generator,
cert info inspector). Future phases can hang app-level branding admin,
voice config, etc. off the same `/data-collection/admin/*` namespace.

These routes are NOT meant for end users — they're for whoever's
deploying / running the standalone app. The standalone runner bypasses
auth by default (`run_dca.py` monkey-patches `role_decorators`), so
the routes are reachable from localhost. In a platform-embedded mode
the platform's admin role decorator should gate them.
"""

import logging
import os
from typing import Any, Dict

from flask import Blueprint, jsonify, render_template, request

from .branding import branding_to_style_block, resolve_branding, safe_url
from .identity import require_admin
from .https_config import (
    ALLOWED_MODES,
    MODE_CUSTOM_CERT,
    MODE_NONE,
    MODE_REVERSE_PROXY,
    MODE_SELF_SIGNED,
    cert_info,
    effective_runtime_settings,
    generate_self_signed,
    load_config,
    save_config,
)

logger = logging.getLogger("DataCollectionAdminRoutes")


# Auth — same fallback pattern as routes.py
try:
    from role_decorators import api_key_or_session_required as _auth_decorator
except Exception:  # pragma: no cover
    def _auth_decorator(*_args, **_kwargs):
        def wrap(fn):
            return fn
        return wrap


def _admin_branding_ctx() -> Dict[str, Any]:
    """Same branding hookup as the gallery / wizard so admin pages match."""
    b = resolve_branding(schema=None)
    return {
        'branding': b,
        'branding_style': branding_to_style_block(b),
        'safe_logo_url': safe_url(b.get('logo_url')),
        'safe_favicon_url': safe_url(b.get('favicon_url')),
    }


def register_admin_routes(bp: Blueprint):
    """Attach admin routes onto the data_collection blueprint."""

    # ------------------------------------------------------------------
    # Index page (links to each admin section). Currently just HTTPS.
    # ------------------------------------------------------------------
    @bp.route('/data-collection/admin', methods=['GET'])
    @_auth_decorator()
    @require_admin
    def admin_index():
        return render_template('admin/index.html', **_admin_branding_ctx())

    # ------------------------------------------------------------------
    # HTTPS configuration page
    # ------------------------------------------------------------------
    @bp.route('/data-collection/admin/https', methods=['GET'])
    @_auth_decorator()
    @require_admin
    def admin_https_page():
        cfg = load_config()
        runtime = effective_runtime_settings()
        ci = None
        if cfg.get('cert_path') and os.path.exists(cfg['cert_path']):
            ci = cert_info(cfg['cert_path'])
        return render_template(
            'admin/https.html',
            config=cfg,
            runtime=runtime,
            cert_info=ci,
            allowed_modes=list(ALLOWED_MODES),
            **_admin_branding_ctx(),
        )

    # ------------------------------------------------------------------
    # API: read current config + effective runtime
    # ------------------------------------------------------------------
    @bp.route('/api/data-collection/admin/https', methods=['GET'])
    @_auth_decorator()
    @require_admin
    def admin_https_get():
        cfg = load_config()
        ci = None
        if cfg.get('cert_path') and os.path.exists(cfg['cert_path']):
            ci = cert_info(cfg['cert_path'])
        return jsonify({
            'status': 'success',
            'config': cfg,
            'runtime': effective_runtime_settings(),
            'cert_info': ci,
        })

    # ------------------------------------------------------------------
    # API: save config
    # ------------------------------------------------------------------
    @bp.route('/api/data-collection/admin/https', methods=['POST'])
    @_auth_decorator()
    @require_admin
    def admin_https_save():
        data = request.get_json() or {}
        # Only accept the keys we care about
        cfg = {
            'mode': (data.get('mode') or MODE_NONE).strip(),
            'cert_path': (data.get('cert_path') or '').strip(),
            'key_path': (data.get('key_path') or '').strip(),
            'hostname': (data.get('hostname') or 'localhost').strip(),
        }
        if cfg['mode'] not in ALLOWED_MODES:
            return jsonify({
                'status': 'error',
                'error': f"invalid mode (allowed: {list(ALLOWED_MODES)})"
            }), 400
        ok, err = save_config(cfg)
        if not ok:
            return jsonify({'status': 'error', 'error': err}), 400

        return jsonify({
            'status': 'success',
            'config': load_config(),
            'runtime': effective_runtime_settings(),
            'restart_required': True,
        })

    # ------------------------------------------------------------------
    # API: generate a self-signed cert
    # ------------------------------------------------------------------
    @bp.route('/api/data-collection/admin/https/generate-cert', methods=['POST'])
    @_auth_decorator()
    @require_admin
    def admin_https_generate_cert():
        data = request.get_json() or {}
        hostname = (data.get('hostname') or 'localhost').strip()
        days = int(data.get('days_valid') or 825)
        ok, info = generate_self_signed(hostname=hostname, days_valid=days)
        if not ok:
            return jsonify({'status': 'error', 'error': info.get('error', 'failed')}), 500

        # Auto-update config to point at the new cert pair so the user
        # doesn't have to copy/paste paths
        save_config({
            'mode': MODE_SELF_SIGNED,
            'cert_path': info['cert_path'],
            'key_path': info['key_path'],
            'hostname': hostname,
        })

        return jsonify({
            'status': 'success',
            'cert': info,
            'config': load_config(),
            'runtime': effective_runtime_settings(),
            'restart_required': True,
        })

    # ------------------------------------------------------------------
    # API: inspect a cert at an arbitrary path
    # ------------------------------------------------------------------
    @bp.route('/api/data-collection/admin/https/cert-info', methods=['GET'])
    @_auth_decorator()
    @require_admin
    def admin_https_cert_info():
        path = (request.args.get('path') or '').strip()
        if not path:
            return jsonify({'status': 'error', 'error': 'path is required'}), 400
        info = cert_info(path)
        if 'error' in info:
            return jsonify({'status': 'error', 'error': info['error']}), 400
        return jsonify({'status': 'success', 'cert_info': info})
