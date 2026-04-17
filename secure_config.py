"""
Secure Configuration Loader for AI Hub
========================================

Loads sensitive configuration values from secure storage (Windows Registry,
encrypted LocalSecretsManager) into os.environ so that all existing
os.getenv() calls work unchanged.

On first run after an upgrade, if credentials are still in .env but NOT
yet in the secure stores, this module automatically migrates them (one-time).

Usage:
    Call load_secure_config() early in your service startup, AFTER load_dotenv()
    but BEFORE any code reads os.getenv('API_KEY') or other sensitive vars.

    from dotenv import load_dotenv
    load_dotenv()

    from secure_config import load_secure_config
    load_secure_config()

This module is used by:
    - config.py (main app and most services)
    - app_mcp_gateway.py (MCP gateway)
    - builder_service/builder_config.py
    - builder_data/builder_data_config.py
"""

import os
import logging

logger = logging.getLogger(__name__)

# Credentials that should live in encrypted LocalSecretsManager
_SECRET_KEYS = [
    'WINTASK_USER',
    'WINTASK_PWD',
    'LOCAL_DOMAIN',
    'WINRM_USER',
    'WINRM_PWD',
    'WINRM_DOMAIN',
    'SMTP_USER',
    'SMTP_PASSWORD',
]


def _load_api_key_from_registry():
    """Read API_KEY from Windows Registry (HKLM\\Software\\AI Hub\\Config)."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r'Software\AI Hub\Config',
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY
        )
        value, _ = winreg.QueryValueEx(key, 'ApiKey')
        winreg.CloseKey(key)
        return value
    except Exception:
        return None


def _write_api_key_to_registry(api_key):
    """Write API_KEY to Windows Registry."""
    try:
        import winreg
        key = winreg.CreateKeyEx(
            winreg.HKEY_LOCAL_MACHINE,
            r'Software\AI Hub\Config',
            0,
            winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY
        )
        winreg.SetValueEx(key, 'ApiKey', 0, winreg.REG_SZ, api_key)
        winreg.CloseKey(key)
        return True
    except Exception as e:
        logger.debug(f'Could not write API_KEY to registry: {e}')
        return False


def cleanup_env_file():
    """
    Remove sensitive keys from the .env file AFTER verifying that
    secure storage is working correctly.

    This is a manual/explicit operation — NOT called automatically.
    Call this only after confirming that the new executables (which
    know how to read from registry/LocalSecretsManager) are deployed
    and services are starting correctly.

    Can be called from seed_credentials.py --cleanup or from admin UI.
    """
    keys_to_remove = ['API_KEY'] + list(_SECRET_KEYS)

    try:
        env_path = os.path.join(
            os.getenv('APP_ROOT', os.path.dirname(os.path.abspath(__file__))),
            '.env'
        )
        if not os.path.isfile(env_path):
            return

        with open(env_path, 'r') as f:
            lines = f.readlines()

        kept = []
        removed = []
        for line in lines:
            stripped = line.strip()
            should_remove = any(stripped.startswith(k + '=') for k in keys_to_remove)
            if should_remove:
                removed.append(stripped.split('=')[0])
            else:
                kept.append(line)

        # Clean up trailing blank lines
        while kept and kept[-1].strip() == '':
            kept.pop()

        if removed:
            with open(env_path, 'w') as f:
                f.writelines(kept)
                if kept and not kept[-1].endswith('\n'):
                    f.write('\n')
            logger.info(f'Removed sensitive keys from .env: {removed}')
        else:
            logger.info('No sensitive keys found in .env — already clean')
    except Exception as e:
        logger.warning(f'Could not clean .env file: {e}')


def _migrate_env_credentials():
    """
    One-time migration: if credentials exist in os.environ (from .env)
    but NOT in the encrypted secrets store, copy them over.

    This handles the upgrade scenario where the installer wrote credentials
    to .env in a prior version and they haven't been migrated yet.

    IMPORTANT: We only COPY to secure storage — we do NOT remove from .env.
    The .env values remain as a fallback for backward compatibility.
    To clean up .env after verifying secure storage works, call
    cleanup_env_file() or run seed_credentials.py --migrate manually.
    """
    try:
        from local_secrets import get_local_secret, set_local_secret, has_local_secret
    except Exception:
        return

    migrated_keys = []
    for key in _SECRET_KEYS:
        env_val = os.getenv(key)
        if env_val and not has_local_secret(key):
            set_local_secret(key, env_val, category='credentials')
            migrated_keys.append(key)

    # Also migrate API_KEY to registry if it's in .env but not in registry
    env_api_key = os.getenv('API_KEY')
    if env_api_key and not _load_api_key_from_registry():
        if _write_api_key_to_registry(env_api_key):
            migrated_keys.append('API_KEY')

    if migrated_keys:
        logger.info(f'Migrated credentials to secure storage: {migrated_keys}')


def _load_secrets_into_env():
    """
    Load sensitive credentials from LocalSecretsManager into os.environ.

    Values already present in os.environ (e.g. from .env for backward
    compatibility) are NOT overwritten.
    """
    try:
        from local_secrets import get_local_secret
    except Exception:
        return

    for key in _SECRET_KEYS:
        if not os.getenv(key):
            val = get_local_secret(key)
            if val:
                os.environ[key] = val


def load_secure_config():
    """
    Load all secure configuration into os.environ.

    Safe to call multiple times — only populates values that are missing
    from the environment.
    """
    # API_KEY: registry > .env (already loaded by load_dotenv)
    if not os.getenv('API_KEY'):
        api_key = _load_api_key_from_registry()
        if api_key:
            os.environ['API_KEY'] = api_key
            logger.debug('Loaded API_KEY from Windows Registry')

    # One-time migration: .env credentials → secure stores
    _migrate_env_credentials()

    # Sensitive credentials: encrypted secrets > .env
    _load_secrets_into_env()
