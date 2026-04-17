"""
Identity Provider Admin Routes

Blueprint for managing enterprise identity provider configurations (LDAP, etc.).
All routes are admin-only (role >= 3).
"""

from flask import Blueprint, jsonify, request, render_template
from flask_login import login_required
import logging
import json

from role_decorators import admin_required
from DataUtils import (
    Get_Identity_Providers,
    Save_Identity_Provider,
    Delete_Identity_Provider,
)

logger = logging.getLogger(__name__)

identity_bp = Blueprint('identity', __name__, url_prefix='/admin/identity')


@identity_bp.route('/settings')
@admin_required()
def identity_settings_page():
    """Render the identity provider settings page."""
    return render_template('admin/identity_settings.html')


@identity_bp.route('/providers', methods=['GET'])
@admin_required(api=True)
def get_providers():
    """List all configured identity providers."""
    try:
        provider_type = request.args.get('type')
        enabled_only = request.args.get('enabled_only', 'false').lower() == 'true'

        df = Get_Identity_Providers(
            provider_type=provider_type,
            enabled_only=enabled_only
        )

        if df is None or df.empty:
            return jsonify({'status': 'success', 'providers': []})

        providers = df.to_dict(orient='records')

        # Parse JSON fields for client consumption
        for p in providers:
            if 'config_json' in p and p['config_json']:
                try:
                    p['config'] = json.loads(p['config_json'])
                except (json.JSONDecodeError, TypeError):
                    p['config'] = {}
            else:
                p['config'] = {}

            if 'group_role_mapping' in p and p['group_role_mapping']:
                try:
                    p['group_role_mapping'] = json.loads(p['group_role_mapping'])
                except (json.JSONDecodeError, TypeError):
                    p['group_role_mapping'] = {}

        return jsonify({'status': 'success', 'providers': providers})

    except Exception as e:
        logger.error(f"Error fetching identity providers: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@identity_bp.route('/providers', methods=['POST'])
@admin_required(api=True)
def save_provider():
    """Create or update an identity provider configuration."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'status': 'error', 'message': 'No data provided'}), 400

        provider_id = data.get('id', 0)
        provider_type = data.get('provider_type', '')
        provider_name = data.get('provider_name', '')
        is_enabled = data.get('is_enabled', False)
        is_default = data.get('is_default', False)
        auto_provision = data.get('auto_provision', True)
        default_role = data.get('default_role', 1)

        # Validate required fields
        if not provider_type:
            return jsonify({'status': 'error', 'message': 'provider_type is required'}), 400
        if not provider_name:
            return jsonify({'status': 'error', 'message': 'provider_name is required'}), 400

        # Config can be passed as a dict (from JSON body) or as a string
        config = data.get('config', {})
        if isinstance(config, dict):
            config_json = json.dumps(config)
        else:
            config_json = str(config)

        # Group role mapping
        group_mapping = data.get('group_role_mapping', {})
        if isinstance(group_mapping, dict):
            group_role_mapping = json.dumps(group_mapping)
        else:
            group_role_mapping = str(group_mapping)

        # Validate LDAP-specific config
        if provider_type == 'ldap':
            if isinstance(config, dict):
                if not config.get('server'):
                    return jsonify({'status': 'error', 'message': 'LDAP server hostname is required'}), 400
                if not config.get('base_dn'):
                    return jsonify({'status': 'error', 'message': 'LDAP base DN is required'}), 400

        result = Save_Identity_Provider(
            provider_id=provider_id,
            provider_type=provider_type,
            provider_name=provider_name,
            is_enabled=is_enabled,
            is_default=is_default,
            config_json=config_json,
            auto_provision=auto_provision,
            default_role=default_role,
            group_role_mapping=group_role_mapping
        )

        # Invalidate provider cache so changes take effect immediately
        try:
            from auth.provider_chain import invalidate_provider_cache
            invalidate_provider_cache()
        except ImportError:
            pass

        if result:
            return jsonify({'status': 'success', 'message': 'Provider saved successfully'})
        else:
            return jsonify({'status': 'error', 'message': 'Failed to save provider'}), 500

    except Exception as e:
        logger.error(f"Error saving identity provider: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@identity_bp.route('/providers/<int:provider_id>', methods=['DELETE'])
@admin_required(api=True)
def delete_provider(provider_id):
    """Delete an identity provider configuration."""
    try:
        result = Delete_Identity_Provider(provider_id)

        # Invalidate provider cache
        try:
            from auth.provider_chain import invalidate_provider_cache
            invalidate_provider_cache()
        except ImportError:
            pass

        if result:
            return jsonify({'status': 'success', 'message': 'Provider deleted successfully'})
        else:
            return jsonify({'status': 'error', 'message': 'Failed to delete provider'}), 500

    except Exception as e:
        logger.error(f"Error deleting identity provider: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@identity_bp.route('/providers/<int:provider_id>/test', methods=['POST'])
@admin_required(api=True)
def test_provider_connection(provider_id):
    """Test connectivity to an identity provider."""
    try:
        # Allow testing with either saved config (by ID) or ad-hoc config from request body
        data = request.get_json() or {}

        if data.get('config'):
            # Use config from request body (for testing before saving)
            config = data['config']
            provider_type = data.get('provider_type', 'ldap')
        else:
            # Load saved config from database
            df = Get_Identity_Providers(provider_id=provider_id)
            if df is None or df.empty:
                return jsonify({'status': 'error', 'message': 'Provider not found'}), 404

            provider = df.iloc[0]
            provider_type = provider['provider_type']
            try:
                config = json.loads(provider['config_json'])
            except (json.JSONDecodeError, TypeError):
                return jsonify({'status': 'error', 'message': 'Invalid provider configuration'}), 400

        if provider_type == 'ldap':
            return _test_ldap_connection(config)
        else:
            return jsonify({'status': 'error', 'message': f'Test not supported for provider type: {provider_type}'}), 400

    except Exception as e:
        logger.error(f"Error testing provider connection: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@identity_bp.route('/providers/test', methods=['POST'])
@admin_required(api=True)
def test_provider_connection_adhoc():
    """Test connectivity with an ad-hoc config (before saving)."""
    try:
        data = request.get_json()
        if not data or not data.get('config'):
            return jsonify({'status': 'error', 'message': 'Config is required'}), 400

        provider_type = data.get('provider_type', 'ldap')
        config = data['config']

        if provider_type == 'ldap':
            return _test_ldap_connection(config)
        else:
            return jsonify({'status': 'error', 'message': f'Test not supported for provider type: {provider_type}'}), 400

    except Exception as e:
        logger.error(f"Error testing provider connection: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


def _test_ldap_connection(config):
    """Test LDAP server connectivity."""
    try:
        from auth.ldap_provider import LdapAuthProvider, LDAP3_AVAILABLE

        if not LDAP3_AVAILABLE:
            return jsonify({
                'status': 'error',
                'message': 'ldap3 library is not installed. Install with: pip install ldap3'
            }), 500

        provider = LdapAuthProvider(config=config)
        success, message = provider.test_connection()

        return jsonify({
            'status': 'success' if success else 'error',
            'connected': success,
            'message': message
        })

    except ImportError:
        return jsonify({
            'status': 'error',
            'message': 'ldap3 library is not installed'
        }), 500
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Connection test failed: {str(e)}'
        }), 500
