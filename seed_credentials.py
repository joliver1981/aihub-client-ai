"""
Credential Seeding Script for AI Hub Installer
================================================

Called by the Inno Setup installer after file deployment to store sensitive
credentials in the encrypted LocalSecretsManager.  Also handles migration
from legacy .env files that contain plaintext credentials.

Usage (called by installer):
    seed_credentials.py --app-root "C:\Program Files\AIHub"
                        [--wintask-user USER]
                        [--wintask-pwd PWD]
                        [--local-domain DOMAIN]
                        [--smtp-user USER]
                        [--smtp-password PWD]
                        [--migrate]          # migrate existing .env creds

Exit codes:
    0  = success
    1  = error (details printed to stderr)
"""

import os
import sys
import argparse
import re


def _parse_args():
    p = argparse.ArgumentParser(description='Seed AI Hub credentials')
    p.add_argument('--app-root', required=True, help='Installation directory')
    p.add_argument('--wintask-user', default='')
    p.add_argument('--wintask-pwd', default='')
    p.add_argument('--local-domain', default='')
    p.add_argument('--smtp-user', default='')
    p.add_argument('--smtp-password', default='')
    p.add_argument('--migrate', action='store_true',
                   help='Migrate credentials from .env then remove them')
    return p.parse_args()


def _read_env_value(env_path, key):
    """Read a single key=value from a .env file."""
    if not os.path.isfile(env_path):
        return ''
    with open(env_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line.startswith(key + '='):
                return line[len(key) + 1:]
    return ''


def _remove_sensitive_keys_from_env(env_path, keys_to_remove):
    """
    Remove lines matching any of the given keys from the .env file.
    Preserves all other content and avoids leaving blank gaps.
    """
    if not os.path.isfile(env_path):
        return

    with open(env_path, 'r') as f:
        lines = f.readlines()

    kept = []
    for line in lines:
        stripped = line.strip()
        should_remove = False
        for key in keys_to_remove:
            if stripped.startswith(key + '='):
                should_remove = True
                break
        if not should_remove:
            kept.append(line)

    # Remove trailing blank lines that may result from removal
    while kept and kept[-1].strip() == '':
        kept.pop()

    with open(env_path, 'w') as f:
        f.writelines(kept)
        if kept and not kept[-1].endswith('\n'):
            f.write('\n')


def main():
    args = _parse_args()

    # Ensure we can import local_secrets from the app root
    if args.app_root not in sys.path:
        sys.path.insert(0, args.app_root)

    # Set APP_ROOT so LocalSecretsManager uses the correct data directory
    os.environ['APP_ROOT'] = args.app_root

    from local_secrets import get_secrets_manager

    mgr = get_secrets_manager(os.path.join(args.app_root, 'data'))

    env_path = os.path.join(args.app_root, '.env')

    # Credentials to seed (from installer CLI args or migration)
    creds = {}

    if args.migrate:
        # Read from existing .env file
        _MIGRATE_KEYS = [
            'WINTASK_USER', 'WINTASK_PWD', 'LOCAL_DOMAIN',
            'WINRM_USER', 'WINRM_PWD', 'WINRM_DOMAIN',
            'SMTP_USER', 'SMTP_PASSWORD',
        ]
        for key in _MIGRATE_KEYS:
            val = _read_env_value(env_path, key)
            if val:
                creds[key] = val

        # Also migrate API_KEY to registry if present in .env
        api_key = _read_env_value(env_path, 'API_KEY')
        if api_key:
            try:
                import winreg
                reg_key = winreg.CreateKeyEx(
                    winreg.HKEY_LOCAL_MACHINE,
                    r'Software\AI Hub\Config',
                    0,
                    winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY
                )
                winreg.SetValueEx(reg_key, 'ApiKey', 0, winreg.REG_SZ, api_key)
                winreg.CloseKey(reg_key)
                print(f'Migrated API_KEY to registry')
                # Add API_KEY to keys to remove from .env
                _MIGRATE_KEYS.append('API_KEY')
            except Exception as e:
                print(f'Warning: Could not migrate API_KEY to registry: {e}',
                      file=sys.stderr)

        # Remove migrated keys from .env
        _remove_sensitive_keys_from_env(env_path, _MIGRATE_KEYS)
        print(f'Removed sensitive keys from .env')
    else:
        # Fresh install: use values passed from installer CLI
        if args.wintask_user:
            creds['WINTASK_USER'] = args.wintask_user
            creds['WINRM_USER'] = args.wintask_user  # WinRM mirrors task creds
        if args.wintask_pwd:
            creds['WINTASK_PWD'] = args.wintask_pwd
            creds['WINRM_PWD'] = args.wintask_pwd
        if args.local_domain:
            creds['LOCAL_DOMAIN'] = args.local_domain
            creds['WINRM_DOMAIN'] = args.local_domain
        if args.smtp_user:
            creds['SMTP_USER'] = args.smtp_user
        if args.smtp_password:
            creds['SMTP_PASSWORD'] = args.smtp_password

    # Store each credential in the encrypted secrets file
    for key, value in creds.items():
        if value:
            mgr.set(key, value, category='credentials')
            print(f'Stored {key} in encrypted secrets')

    print('Credential seeding complete')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        print(f'Error: {e}', file=sys.stderr)
        sys.exit(1)
