"""
system_prompts_admin_routes.py
------------------------------
Admin screen for viewing, searching, filtering and editing every system prompt
in the platform.

    Page   GET    /settings/system-prompts
    API    GET    /settings/api/system-prompts            list + facets (filtered)
           GET    /settings/api/system-prompts/detail     one prompt, full text
           POST   /settings/api/system-prompts/override   set an override
           DELETE /settings/api/system-prompts/override   revert one prompt
           DELETE /settings/api/system-prompts/overrides   revert everything
           POST   /settings/api/system-prompts/refresh    re-scan the source tree

Everything here is admin-only (role >= 3), matching the other settings screens.

Reading is powered by prompt_registry (static AST scan of the source tree);
writing goes through prompt_overrides (additive JSON file, never edits source).

Filtering is done SERVER-side and list responses carry only a short preview of
each prompt: the full catalog is ~412 KB of text, and the biggest single prompt
is ~36 KB. The detail endpoint serves full text for the one prompt being read
or edited.

Register in app.py alongside the other blueprints:
    from system_prompts_admin_routes import system_prompts_admin_bp
    app.register_blueprint(system_prompts_admin_bp)
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user

from role_decorators import admin_required

logger = logging.getLogger(__name__)

system_prompts_admin_bp = Blueprint(
    'system_prompts_admin', __name__, url_prefix='/settings'
)

# How much of a prompt to send in the list view.
_PREVIEW_CHARS = 400


# -----------------------------------------------------------------------------
# Page
# -----------------------------------------------------------------------------
@system_prompts_admin_bp.route('/system-prompts')
@admin_required()
def system_prompts_page():
    """Render the System Prompts admin screen."""
    return render_template('system_prompts_admin.html')


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _preview(text: str) -> str:
    if not text:
        return ''
    collapsed = ' '.join(text.split())
    if len(collapsed) <= _PREVIEW_CHARS:
        return collapsed
    return collapsed[:_PREVIEW_CHARS] + '…'


def _decorate(entry: dict, overrides: dict) -> dict:
    """Add override state to a catalog entry (list form — preview only)."""
    key = entry['key']
    override_text = overrides.get(key)
    is_overridden = isinstance(override_text, str) and bool(override_text.strip())
    effective = override_text if is_overridden else entry['default_text']
    return {
        'key': key,
        'name': entry['name'],
        'module': entry['module'],
        'service': entry['service'],
        'kind': entry['kind'],
        'category': entry['category'],
        'editable': entry['editable'],
        'reason': entry['reason'],
        'placeholders': entry['placeholders'],
        'source_path': entry['source_path'],
        'line': entry['line'],
        'char_count': entry['char_count'],
        'is_overridden': is_overridden,
        'preview': _preview(effective),
        'effective_chars': len(effective or ''),
    }


def _matches(entry: dict, q: str) -> bool:
    """Free-text search across name, module and the FULL prompt body."""
    if not q:
        return True
    haystack = ' '.join((
        entry.get('name', ''),
        entry.get('module', ''),
        entry.get('service', ''),
        entry.get('default_text', ''),
    )).lower()
    return all(term in haystack for term in q.lower().split())


# -----------------------------------------------------------------------------
# List
# -----------------------------------------------------------------------------
@system_prompts_admin_bp.route('/api/system-prompts', methods=['GET'])
@admin_required(api=True)
def list_system_prompts():
    """Filtered prompt list plus the facets the UI needs to build its filters."""
    try:
        from prompt_registry import build_catalog
        from prompt_overrides import get_override_status

        catalog = build_catalog()
        status = get_override_status()
        overrides = status.get('overrides', {})

        q = (request.args.get('q') or '').strip()
        service = (request.args.get('service') or '').strip()
        module = (request.args.get('module') or '').strip()
        kind = (request.args.get('kind') or '').strip()
        category = (request.args.get('category') or '').strip()
        # all | editable | readonly | overridden
        scope = (request.args.get('scope') or 'all').strip().lower()

        rows = []
        for entry in catalog['entries']:
            if service and entry['service'] != service:
                continue
            if module and entry['module'] != module:
                continue
            if kind and entry['kind'] != kind:
                continue
            if category and entry['category'] != category:
                continue
            if scope == 'editable' and not entry['editable']:
                continue
            if scope == 'readonly' and entry['editable']:
                continue
            if scope == 'overridden' and not overrides.get(entry['key']):
                continue
            if not _matches(entry, q):
                continue
            rows.append(_decorate(entry, overrides))

        return jsonify({
            'success': True,
            'entries': rows,
            'returned': len(rows),
            'stats': catalog['stats'],
            'services': catalog['services'],
            'modules': catalog['modules'],
            'override_status': {
                'active_count': status.get('active_count', 0),
                'restart_required': status.get('restart_required', False),
                'stale_keys': status.get('stale_keys', []),
                'path': status.get('path'),
                'exists': status.get('exists', False),
            },
        })
    except Exception as e:
        logger.error(f"Error listing system prompts: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


# -----------------------------------------------------------------------------
# Detail
# -----------------------------------------------------------------------------
@system_prompts_admin_bp.route('/api/system-prompts/detail', methods=['GET'])
@admin_required(api=True)
def system_prompt_detail():
    """Full text for one prompt: the code default, the override, the effective."""
    try:
        from prompt_registry import get_entry
        from prompt_overrides import load_overrides

        key = (request.args.get('key') or '').strip()
        if not key:
            return jsonify({'success': False, 'error': 'Missing key'}), 400

        entry = get_entry(key)
        if entry is None:
            return jsonify({'success': False, 'error': f'Unknown prompt: {key}'}), 404

        overrides = load_overrides()
        override_text = overrides.get(key)
        is_overridden = isinstance(override_text, str) and bool(override_text.strip())

        return jsonify({
            'success': True,
            'entry': {
                **entry,
                'is_overridden': is_overridden,
                'override_text': override_text if is_overridden else '',
                'effective_text': override_text if is_overridden else entry['default_text'],
            },
        })
    except Exception as e:
        logger.error(f"Error reading system prompt detail: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


# -----------------------------------------------------------------------------
# Write
# -----------------------------------------------------------------------------
@system_prompts_admin_bp.route('/api/system-prompts/override', methods=['POST'])
@admin_required(api=True)
def set_system_prompt_override():
    """Set (or clear, with empty text) the override for one prompt."""
    try:
        from prompt_overrides import save_overrides, get_override_status

        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify({'success': False, 'error': 'Body must be a JSON object'}), 400

        key = (data.get('key') or '').strip()
        text = data.get('text', '')
        if not key:
            return jsonify({'success': False, 'error': 'Missing key'}), 400
        if text is not None and not isinstance(text, str):
            return jsonify({'success': False, 'error': 'text must be a string'}), 400

        save_overrides({key: text})

        user_id = getattr(current_user, 'id', None)
        cleared = not (isinstance(text, str) and text.strip())
        logger.info(
            f"System prompt override {'cleared' if cleared else 'saved'}: "
            f"{key} (by user {user_id})"
        )

        status = get_override_status()
        return jsonify({
            'success': True,
            'message': (
                'Override cleared. Restart services for changes to take effect.'
                if cleared else
                'Override saved. Restart services for changes to take effect.'
            ),
            'restart_required': True,
            'active_count': status.get('active_count', 0),
        })
    except ValueError as ve:
        # Allow-list violation or a dropped {placeholder} — a user-facing error.
        return jsonify({'success': False, 'error': str(ve)}), 400
    except Exception as e:
        logger.error(f"Error saving system prompt override: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@system_prompts_admin_bp.route('/api/system-prompts/override', methods=['DELETE'])
@admin_required(api=True)
def delete_system_prompt_override():
    """Revert one prompt to its code default."""
    try:
        from prompt_overrides import clear_override, get_override_status

        data = request.get_json(silent=True) or {}
        key = (data.get('key') or request.args.get('key') or '').strip()
        if not key:
            return jsonify({'success': False, 'error': 'Missing key'}), 400

        clear_override(key)
        logger.info(f"System prompt override reverted: {key} "
                    f"(by user {getattr(current_user, 'id', None)})")

        status = get_override_status()
        return jsonify({
            'success': True,
            'message': 'Reverted to default. Restart services for changes to take effect.',
            'restart_required': True,
            'active_count': status.get('active_count', 0),
        })
    except Exception as e:
        logger.error(f"Error reverting system prompt override: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@system_prompts_admin_bp.route('/api/system-prompts/overrides', methods=['DELETE'])
@admin_required(api=True)
def clear_all_system_prompt_overrides():
    """Delete the whole override file — the kill switch, back to stock."""
    try:
        from prompt_overrides import clear_overrides

        clear_overrides()
        logger.info("ALL system prompt overrides cleared "
                    f"(by user {getattr(current_user, 'id', None)})")
        return jsonify({
            'success': True,
            'message': 'All prompt overrides cleared. Restart services for '
                       'changes to take effect.',
            'restart_required': True,
            'active_count': 0,
        })
    except Exception as e:
        logger.error(f"Error clearing system prompt overrides: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@system_prompts_admin_bp.route('/api/system-prompts/refresh', methods=['POST'])
@admin_required(api=True)
def refresh_system_prompts():
    """Force a re-scan of the source tree (after a deploy or a code edit)."""
    try:
        from prompt_registry import build_catalog
        catalog = build_catalog(force_refresh=True)
        return jsonify({'success': True, 'stats': catalog['stats']})
    except Exception as e:
        logger.error(f"Error refreshing prompt catalog: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500
