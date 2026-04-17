"""
Enterprise Identity Authentication Package

Provides a pluggable authentication provider chain for AI Hub.
Supports local (bcrypt), LDAP/Active Directory, and future SSO providers.

Usage:
    from auth import authenticate_user, get_enabled_providers

    # Authenticate via the provider chain (LDAP first if configured, then local)
    result = authenticate_user(username, password)
    if result['success']:
        user = result['user']  # Local User ORM object, ready for flask_login.login_user()
"""

from auth.provider_chain import authenticate_user, get_enabled_providers

__all__ = ['authenticate_user', 'get_enabled_providers']
