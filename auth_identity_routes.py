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

        # Validate and tidy SAML-specific config
        if provider_type == 'saml':
            if not isinstance(config, dict):
                return jsonify({'status': 'error', 'message': 'SAML settings are missing'}), 400
            config, problem = _clean_saml_config(config)
            if problem:
                return jsonify({'status': 'error', 'message': problem}), 400
            config_json = json.dumps(config)

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


# ---------------------------------------------------------------------------
# SAML (auth/saml_provider.py, auth/saml_routes.py)
# ---------------------------------------------------------------------------

_SAML_TEXT_FIELDS = ('idp_entity_id', 'idp_sso_url', 'sp_entity_id', 'username_domain',
                     'attr_username', 'attr_email', 'attr_display_name', 'attr_groups', 'attr_external_id')


def _clean_saml_config(config):
    """Return (config, problem). `problem` is a message for the Admin, or ''."""
    from auth import saml_provider as saml

    clean = {}
    for key in _SAML_TEXT_FIELDS:
        clean[key] = str(config.get(key) or '').strip()
    clean['username_domain'] = clean['username_domain'].lstrip('@')
    clean['allow_idp_initiated'] = bool(config.get('allow_idp_initiated'))

    if not clean['idp_entity_id']:
        return clean, 'The identity provider identifier is required'
    if not clean['idp_sso_url'].lower().startswith(('https://', 'http://')):
        return clean, 'The login URL must start with https://'

    try:
        clean['idp_certificates'] = saml.normalize_certificates(config.get('idp_certificates'))
    except saml.SamlError as e:
        return clean, e.detail
    if not clean['idp_certificates']:
        return clean, 'At least one signing certificate is required'

    skew = config.get('clock_skew_seconds')
    if skew not in (None, ''):
        try:
            clean['clock_skew_seconds'] = int(skew)
        except (TypeError, ValueError):
            return clean, 'The clock tolerance must be a whole number of seconds'
        if clean['clock_skew_seconds'] < 0:
            return clean, 'The clock tolerance cannot be negative'
    return clean, ''


@identity_bp.route('/saml/sp-info', methods=['GET'])
@admin_required(api=True)
def saml_sp_info():
    """The values an Admin copies into the identity provider."""
    try:
        from auth import saml_provider as saml
        from auth.saml_routes import sp_addresses

        info = sp_addresses({'sp_entity_id': request.args.get('sp_entity_id', '')})
        info['available'] = saml.SIGNXML_AVAILABLE
        info['https'] = info['acs_url'].lower().startswith('https://')
        return jsonify({'status': 'success', **info})
    except Exception as e:
        logger.error(f"Error building SAML service provider info: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@identity_bp.route('/saml/metadata', methods=['POST'])
@admin_required(api=True)
def load_saml_metadata():
    """Read an identity provider's federation metadata (from its address, or
    pasted XML) and return the identifier, login URL and signing certificates."""
    try:
        from auth import saml_provider as saml

        data = request.get_json() or {}
        url = str(data.get('url') or '').strip()
        xml = data.get('xml') or ''

        if url:
            if not url.lower().startswith('https://'):
                return jsonify({'status': 'error', 'message': 'The metadata address must start with https://'}), 400
            import requests
            try:
                fetched = requests.get(url, timeout=30)
                fetched.raise_for_status()
            except requests.RequestException as e:
                return jsonify({'status': 'error',
                                'message': f'Could not read the metadata address from this server: {e}'}), 502
            xml_bytes = fetched.content
        elif xml:
            xml_bytes = xml.encode('utf-8') if isinstance(xml, str) else bytes(xml)
        else:
            return jsonify({'status': 'error', 'message': 'Enter the metadata address or paste the metadata XML'}), 400

        try:
            parsed = saml.parse_idp_metadata(xml_bytes)
            parsed['certificate_info'] = saml.describe_certificates(parsed['idp_certificates'])
        except saml.SamlError as e:
            return jsonify({'status': 'error', 'message': e.detail}), 400
        return jsonify({'status': 'success', **parsed})

    except Exception as e:
        logger.error(f"Error loading SAML metadata: {str(e)}")
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
