"""
Authentication Provider Chain

Orchestrates authentication across configured providers.
Tries LDAP/SSO first (if configured), then falls back to local auth.
"""

import logging
import json
import os
from typing import Dict, Any, List, Optional

from auth.base_provider import AuthResult

logger = logging.getLogger(__name__)

# Cache for provider configurations to avoid querying DB on every login
_provider_cache = {
    'configs': None,
    'last_loaded': None
}


def get_enabled_providers() -> List[Dict[str, Any]]:
    """
    Fetch enabled identity provider configurations from the database.

    Returns a list of provider config dicts, or empty list if none configured.
    Caches results for the lifetime of the process (reloaded on settings save).
    """
    from CommonUtils import get_db_connection

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        cursor.execute("""
            SELECT id, provider_type, provider_name, config_json,
                   auto_provision, default_role, group_role_mapping
            FROM [dbo].[IdentityProviderConfig]
            WHERE is_enabled = 1
            ORDER BY is_default DESC, id ASC
        """)

        providers = []
        for row in cursor.fetchall():
            config_json = row[3] if row[3] else '{}'
            group_mapping = row[6] if row[6] else '{}'

            try:
                config = json.loads(config_json)
            except json.JSONDecodeError:
                logger.error(f"Invalid config_json for provider {row[1]} (id={row[0]})")
                continue

            try:
                group_role_map = json.loads(group_mapping)
            except json.JSONDecodeError:
                group_role_map = {}

            providers.append({
                'id': row[0],
                'provider_type': row[1],
                'provider_name': row[2],
                'config': config,
                'auto_provision': bool(row[4]),
                'default_role': row[5],
                'group_role_mapping': group_role_map,
            })

        cursor.close()
        conn.close()

        return providers

    except Exception as e:
        logger.error(f"Error loading identity provider configs: {str(e)}")
        return []


def invalidate_provider_cache():
    """Clear the provider cache (call after saving provider settings)."""
    _provider_cache['configs'] = None
    _provider_cache['last_loaded'] = None


def authenticate_user(username: str, password: str, bcrypt_instance=None,
                      user_class=None, db=None) -> Dict[str, Any]:
    """
    Authenticate a user through the provider chain.

    1. Check if any external providers (LDAP, etc.) are configured and enabled
    2. If so, try each external provider in order
    3. If external auth succeeds, find/create local user via UserProvisioner
    4. If all external providers fail (or none configured), fall through to local auth
    5. Local auth uses existing bcrypt password check

    Args:
        username: The login username
        password: The login password
        bcrypt_instance: Flask-Bcrypt instance
        user_class: User ORM class
        db: SQLAlchemy db instance

    Returns:
        Dict with keys:
            - 'success': bool
            - 'user': User object (if success)
            - 'error': str (if failure)
            - 'provider': str (which provider authenticated, e.g., 'ldap', 'local')
    """
    # Load enabled external providers
    providers = get_enabled_providers()

    # Try each external provider
    for provider_config in providers:
        provider_type = provider_config['provider_type']

        if provider_type == 'ldap':
            result = _try_ldap_auth(username, password, provider_config, db, user_class, bcrypt_instance)
            if result['success']:
                return result
            # LDAP failed — continue to next provider or fall through to local

    # Fall through to local authentication
    return _try_local_auth(username, password, bcrypt_instance, user_class)


def _try_ldap_auth(username: str, password: str, provider_config: Dict[str, Any],
                   db, user_class, bcrypt_instance) -> Dict[str, Any]:
    """Attempt LDAP authentication and user provisioning."""
    try:
        from auth.ldap_provider import LdapAuthProvider, LDAP3_AVAILABLE

        if not LDAP3_AVAILABLE:
            logger.warning("ldap3 library not installed, skipping LDAP authentication")
            return {'success': False, 'error': 'LDAP library not available', 'provider': 'ldap'}

        ldap_provider = LdapAuthProvider(
            config=provider_config['config'],
            group_role_mapping=provider_config.get('group_role_mapping', {})
        )

        auth_result = ldap_provider.authenticate(username, password)

        if not auth_result.success:
            return {'success': False, 'error': auth_result.error, 'provider': 'ldap'}

        # LDAP auth succeeded — find or create local user
        from auth.user_provisioner import UserProvisioner

        provisioner = UserProvisioner(db, user_class, bcrypt_instance)
        user = provisioner.find_or_create_user(
            auth_provider='ldap',
            external_id=auth_result.user_attributes.get('external_id', username),
            username=auth_result.user_attributes.get('username', username),
            name=auth_result.user_attributes.get('name', username),
            email=auth_result.user_attributes.get('email'),
            groups=auth_result.user_attributes.get('groups', []),
            auto_provision=provider_config.get('auto_provision', True),
            default_role=provider_config.get('default_role', 1),
            group_role_mapping=provider_config.get('group_role_mapping', {}),
        )

        if user is None:
            return {
                'success': False,
                'error': 'LDAP authentication succeeded but user provisioning is disabled for unknown users',
                'provider': 'ldap'
            }

        logger.info(f"LDAP authentication successful for user: {username}")
        return {'success': True, 'user': user, 'provider': 'ldap'}

    except ImportError:
        logger.warning("ldap3 not available, skipping LDAP auth")
        return {'success': False, 'error': 'LDAP library not available', 'provider': 'ldap'}
    except Exception as e:
        logger.error(f"LDAP authentication error: {str(e)}")
        return {'success': False, 'error': str(e), 'provider': 'ldap'}


def _try_local_auth(username: str, password: str, bcrypt_instance, user_class) -> Dict[str, Any]:
    """Attempt local bcrypt authentication (existing behavior)."""
    try:
        from auth.local_provider import LocalAuthProvider

        local_provider = LocalAuthProvider(bcrypt_instance, user_class)
        auth_result = local_provider.authenticate(username, password)

        if auth_result.success:
            return {
                'success': True,
                'user': auth_result.user_attributes.get('local_user'),
                'provider': 'local'
            }

        return {'success': False, 'error': auth_result.error, 'provider': 'local'}

    except Exception as e:
        logger.error(f"Local authentication error: {str(e)}")
        return {'success': False, 'error': str(e), 'provider': 'local'}
