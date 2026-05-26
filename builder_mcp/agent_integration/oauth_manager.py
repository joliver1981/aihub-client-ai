"""
OAuth 2.0 Token Manager for MCP Servers — multi-user.

Supports two grant types:
  - client_credentials: machine-to-machine (e.g. Microsoft Graph w/ app perms).
    Tokens are app-scoped, not user-scoped — stored under a synthetic
    SERVICE_USER_ID so the same DB layout serves both flows.
  - authorization_code: delegated user permissions. Tokens are per-user.

Storage split:
  MCPServerCredentials (server-level admin config — shared across users):
      oauth_grant_type            'client_credentials' | 'authorization_code'
      oauth_token_endpoint        AAD token URL
      oauth_auth_endpoint         AAD authorize URL (auth-code only)
      oauth_client_id             public
      oauth_client_secret         secret
      oauth_scope                 space-delimited scopes
      oauth_audience              optional
      oauth_tenant_id             optional, convenience

  MCPUserTokens (per-(server,user) — runtime):
      oauth_access_token          managed
      oauth_refresh_token         managed (auth-code)
      oauth_expires_at            managed, unix seconds string

Public API:
  get_access_token(server_id, user_id)   -> str | None
  exchange_authorization_code(server_id, user_id, code, redirect_uri, code_verifier=None) -> str
  build_authorize_url(server_id, redirect_uri, state, code_challenge=None) -> str
  has_user_token(server_id, user_id) -> bool
  revoke_user_token(server_id, user_id) -> None
"""
import os
import time
import logging
import threading
from typing import Optional, Dict
from urllib.parse import urlencode

import requests

from CommonUtils import get_db_connection

logger = logging.getLogger(__name__)


# Refresh tokens this many seconds before the actual expiry to avoid races
EXPIRY_LEEWAY_SECONDS = 60

# Synthetic user_id used for client_credentials (app-only) tokens. These
# tokens are not bound to any individual user, but we keep them in the
# same per-user table by using a reserved id of 0.
SERVICE_USER_ID = 0

# Per-(server_id, user_id) refresh lock to prevent thundering-herd refreshes
# when many tool calls arrive simultaneously for the same user.
_refresh_locks: Dict[tuple, threading.Lock] = {}
_refresh_locks_guard = threading.Lock()


def _get_lock(server_id: int, user_id: int) -> threading.Lock:
    key = (int(server_id), int(user_id))
    with _refresh_locks_guard:
        lock = _refresh_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _refresh_locks[key] = lock
        return lock


def _get_encryption_key() -> str:
    try:
        from encrypt import ENCRYPTION_KEY
        return os.environ.get('MCP_ENCRYPTION_KEY', ENCRYPTION_KEY)
    except ImportError:
        return os.environ.get('MCP_ENCRYPTION_KEY', 'default_key')


# ----------------------------------------------------------------------------
# Server-level config (shared across users)
# ----------------------------------------------------------------------------

def _load_server_config(server_id: int) -> Dict[str, str]:
    """Read non-token OAuth config keys from MCPServerCredentials."""
    encryption_key = _get_encryption_key()
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        cursor.execute("""
            SELECT credential_key,
                   CONVERT(NVARCHAR(MAX), DECRYPTBYPASSPHRASE(?, credential_value)) AS v
            FROM MCPServerCredentials
            WHERE server_id = ?
        """, encryption_key, server_id)
        return {row[0]: row[1] for row in cursor.fetchall() if row[1] is not None}
    finally:
        try:
            cursor.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


# ----------------------------------------------------------------------------
# Per-user runtime tokens
# ----------------------------------------------------------------------------

_TOKEN_KEYS = ('oauth_access_token', 'oauth_refresh_token', 'oauth_expires_at')


def _load_user_tokens(server_id: int, user_id: int) -> Dict[str, str]:
    """Read per-user runtime tokens from MCPUserTokens."""
    encryption_key = _get_encryption_key()
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        cursor.execute("""
            SELECT credential_key,
                   CONVERT(NVARCHAR(MAX), DECRYPTBYPASSPHRASE(?, credential_value)) AS v
            FROM MCPUserTokens
            WHERE server_id = ? AND user_id = ?
        """, encryption_key, server_id, user_id)
        return {row[0]: row[1] for row in cursor.fetchall() if row[1] is not None}
    finally:
        try:
            cursor.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def _save_user_token(server_id: int, user_id: int, key: str, value: str):
    """Upsert a single per-user encrypted token."""
    if value is None:
        value = ''
    encryption_key = _get_encryption_key()
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        # MERGE-style upsert: clear any existing row, then insert.
        cursor.execute("""
            DELETE FROM MCPUserTokens
            WHERE server_id = ? AND user_id = ? AND credential_key = ?
        """, server_id, user_id, key)
        # CAST required: Azure tokens are large JWTs; pyodbc binds them as NTEXT,
        # which ENCRYPTBYPASSPHRASE rejects.
        cursor.execute("""
            INSERT INTO MCPUserTokens (server_id, user_id, credential_key, credential_value, updated_date)
            VALUES (?, ?, ?, ENCRYPTBYPASSPHRASE(?, CAST(? AS NVARCHAR(MAX))), GETUTCDATE())
        """, server_id, user_id, key, encryption_key, value)
        conn.commit()
    finally:
        try:
            cursor.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def _delete_user_tokens(server_id: int, user_id: int):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        cursor.execute("DELETE FROM MCPUserTokens WHERE server_id = ? AND user_id = ?",
                       server_id, user_id)
        conn.commit()
    finally:
        try:
            cursor.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def _is_token_valid(tokens: Dict[str, str]) -> bool:
    token = tokens.get('oauth_access_token')
    if not token:
        return False
    expires_at_str = tokens.get('oauth_expires_at')
    if not expires_at_str:
        return True  # opaque token, no expiry info
    try:
        expires_at = float(expires_at_str)
    except ValueError:
        return False
    return time.time() < (expires_at - EXPIRY_LEEWAY_SECONDS)


def _store_token_response(server_id: int, user_id: int, payload: dict) -> str:
    access_token = payload.get('access_token')
    if not access_token:
        raise RuntimeError(f"OAuth token response missing access_token: {payload}")

    _save_user_token(server_id, user_id, 'oauth_access_token', access_token)

    expires_in = payload.get('expires_in')
    if expires_in:
        try:
            expires_at = int(time.time()) + int(expires_in)
            _save_user_token(server_id, user_id, 'oauth_expires_at', str(expires_at))
        except (TypeError, ValueError):
            pass

    refresh_token = payload.get('refresh_token')
    if refresh_token:
        _save_user_token(server_id, user_id, 'oauth_refresh_token', refresh_token)

    return access_token


# ----------------------------------------------------------------------------
# OAuth provider exchanges
# ----------------------------------------------------------------------------

def _exchange_client_credentials(cfg: Dict[str, str]) -> dict:
    token_endpoint = cfg.get('oauth_token_endpoint')
    client_id = cfg.get('oauth_client_id')
    client_secret = cfg.get('oauth_client_secret')
    scope = cfg.get('oauth_scope', '')
    if not (token_endpoint and client_id and client_secret):
        raise RuntimeError("OAuth client_credentials requires token_endpoint, client_id, client_secret")

    data = {
        'grant_type': 'client_credentials',
        'client_id': client_id,
        'client_secret': client_secret,
    }
    if scope:
        data['scope'] = scope
    audience = cfg.get('oauth_audience')
    if audience:
        data['audience'] = audience

    resp = requests.post(token_endpoint, data=data,
                         headers={'Accept': 'application/json', 'Connection': 'close'},
                         timeout=30)
    if not resp.ok:
        raise RuntimeError(f"OAuth client_credentials token request failed "
                           f"(HTTP {resp.status_code}): {resp.text[:300]}")
    return resp.json()


def _refresh_authorization_code(cfg: Dict[str, str], refresh_token: str) -> dict:
    token_endpoint = cfg.get('oauth_token_endpoint')
    client_id = cfg.get('oauth_client_id')
    client_secret = cfg.get('oauth_client_secret')
    if not (token_endpoint and refresh_token and client_id):
        raise RuntimeError("OAuth refresh requires token_endpoint, refresh_token, client_id")

    data = {
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token,
        'client_id': client_id,
    }
    if client_secret:
        data['client_secret'] = client_secret
    scope = cfg.get('oauth_scope')
    if scope:
        data['scope'] = scope

    resp = requests.post(token_endpoint, data=data,
                         headers={'Accept': 'application/json', 'Connection': 'close'},
                         timeout=30)
    if not resp.ok:
        raise RuntimeError(f"OAuth refresh failed (HTTP {resp.status_code}): {resp.text[:300]}")
    return resp.json()


# ----------------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------------

def exchange_authorization_code(server_id: int, user_id: int, code: str,
                                redirect_uri: str,
                                code_verifier: Optional[str] = None) -> str:
    """Exchange an authorization code for tokens and persist them under the
    given user_id. Returns the access_token."""
    if not user_id:
        raise RuntimeError("exchange_authorization_code requires a user_id (the user who authorized)")
    cfg = _load_server_config(server_id)
    token_endpoint = cfg.get('oauth_token_endpoint')
    client_id = cfg.get('oauth_client_id')
    client_secret = cfg.get('oauth_client_secret')
    if not (token_endpoint and client_id):
        raise RuntimeError("OAuth auth-code exchange requires token_endpoint and client_id")

    data = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': redirect_uri,
        'client_id': client_id,
    }
    if client_secret:
        data['client_secret'] = client_secret
    if code_verifier:
        data['code_verifier'] = code_verifier

    resp = requests.post(token_endpoint, data=data,
                         headers={'Accept': 'application/json', 'Connection': 'close'},
                         timeout=30)
    if not resp.ok:
        raise RuntimeError(f"OAuth auth-code exchange failed "
                           f"(HTTP {resp.status_code}): {resp.text[:300]}")
    return _store_token_response(server_id, user_id, resp.json())


def get_access_token(server_id: int, user_id: Optional[int] = None) -> Optional[str]:
    """Return a valid access token for the (server, user), refreshing if needed.

    For grant_type=client_credentials: user_id is ignored; tokens are stored
    under SERVICE_USER_ID and shared across the whole tenant.

    For grant_type=authorization_code: user_id is required. Returns None if
    the server isn't OAuth-configured. Raises if credentials exist but the
    user hasn't yet completed authorization.
    """
    cfg = _load_server_config(server_id)
    if not cfg:
        return None

    grant_type = (cfg.get('oauth_grant_type') or '').lower()
    if not grant_type:
        return None  # not an OAuth-configured server

    effective_user_id = SERVICE_USER_ID if grant_type == 'client_credentials' else user_id
    if effective_user_id is None:
        raise RuntimeError(
            "get_access_token requires user_id for authorization_code servers "
            "(personal/delegated tokens). For service-account flows, configure "
            "the server with grant_type=client_credentials."
        )

    # Serialize concurrent refreshes for the same (server, user) — without this
    # 10 simultaneous tool calls on an expired token would all hit AAD at once,
    # wasting quota and risking refresh-token invalidation.
    lock = _get_lock(server_id, effective_user_id)
    with lock:
        tokens = _load_user_tokens(server_id, effective_user_id)

        if _is_token_valid(tokens):
            return tokens['oauth_access_token']

        if grant_type == 'client_credentials':
            payload = _exchange_client_credentials(cfg)
            return _store_token_response(server_id, effective_user_id, payload)

        if grant_type == 'authorization_code':
            refresh_token = tokens.get('oauth_refresh_token')
            if not refresh_token:
                raise RuntimeError(
                    f"No refresh token for user_id={user_id} on server_id={server_id} — "
                    f"the user must complete the OAuth authorization flow (My Connections)."
                )
            payload = _refresh_authorization_code(cfg, refresh_token)
            return _store_token_response(server_id, effective_user_id, payload)

        raise RuntimeError(f"Unsupported OAuth grant_type: {grant_type}")


def has_user_token(server_id: int, user_id: int) -> bool:
    """Cheap check used by 'My Connections' UI to indicate connected/not."""
    tokens = _load_user_tokens(server_id, user_id)
    return bool(tokens.get('oauth_access_token') or tokens.get('oauth_refresh_token'))


def revoke_user_token(server_id: int, user_id: int):
    """Delete this user's tokens for the server (Disconnect button).

    Best-effort: we don't currently call the IdP's revocation endpoint, just
    drop our copy of the tokens. The user can also revoke at the IdP itself
    (e.g. Microsoft's 'My sign-ins') if they want to fully invalidate.
    """
    _delete_user_tokens(server_id, user_id)


def build_authorize_url(server_id: int, redirect_uri: str, state: str,
                        code_challenge: Optional[str] = None) -> str:
    cfg = _load_server_config(server_id)
    auth_endpoint = cfg.get('oauth_auth_endpoint')
    client_id = cfg.get('oauth_client_id')
    scope = cfg.get('oauth_scope', '')
    if not (auth_endpoint and client_id):
        raise RuntimeError("OAuth authorize requires oauth_auth_endpoint and oauth_client_id")

    params = {
        'response_type': 'code',
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'state': state,
    }
    if scope:
        params['scope'] = scope
    if code_challenge:
        params['code_challenge'] = code_challenge
        params['code_challenge_method'] = 'S256'
    sep = '&' if '?' in auth_endpoint else '?'
    return f"{auth_endpoint}{sep}{urlencode(params)}"


# ----------------------------------------------------------------------------
# Backward-compat shim — keeps the previous routes happy until they're updated.
# Resolves to the old behavior IFF user_id can't be determined from session.
# ----------------------------------------------------------------------------

def _load_credentials(server_id: int) -> Dict[str, str]:
    """Deprecated. Returns merged server config so callers reading individual
    config keys (oauth_grant_type, oauth_client_id, etc.) keep working during
    the multi-user transition. Do NOT use for tokens — they aren't here anymore.
    """
    return _load_server_config(server_id)
