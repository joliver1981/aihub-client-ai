"""
Identity & access control for the data collection agent.

Single source of truth for:
  - Whether we're in test mode (DATA_COLLECTION_TEST_MODE env / config flag)
  - Who the current user is (JWT > platform > anonymous fallback)
  - Whether the current user is an admin
  - Session ownership enforcement

Test mode (`DATA_COLLECTION_TEST_MODE=True`) — default OFF.
Lets you exercise the runtime + builder + admin pages without an identity
or a JWT. Everyone is treated as admin. Session ownership checks are no-ops.
This is what the standalone runner has been doing implicitly so far.

Production mode (default) — strict.
  - All session-bound routes require an identity (JWT, platform login, or
    a stable anonymous cookie, in that priority order).
  - Builder + admin routes require Developer-or-Admin role
    (`current_user.role >= 2`).
  - JWT-authed users are NEVER admin, even if their token claims it. By
    design — JWT deep-links are for end users filling out forms, not for
    schema authoring.
  - Session-id endpoints 403 if the session doesn't belong to the caller.

Identity priority (production mode):
  1. Decoded JWT prefill claims (`jwt_claims['sub']`)
  2. Platform auth (flask_login `current_user.id`)
  3. Anonymous browser cookie (stable per browser; lets multi-user tests
     work without each user logging in)
"""

import logging
import os
import secrets
from dataclasses import dataclass
from typing import Any, Dict, Optional

from flask import g, request

logger = logging.getLogger(__name__)


# Cookie name for the anonymous browser-stable identity (production mode)
ANON_COOKIE_NAME = 'dca_anon_id'
# Anonymous IDs are prefixed so they're recognizable in logs / session files
ANON_ID_PREFIX = 'anon-'

# Roles, mirroring role_decorators.py in the main app
ROLE_USER = 1
ROLE_DEVELOPER = 2
ROLE_ADMIN = 3
# Builder + admin pages require >= this role (Developer or Admin)
MIN_ADMIN_ROLE = ROLE_DEVELOPER

# Identity sources, for diagnostics / template rendering
SOURCE_TEST = 'test_mode'
SOURCE_JWT = 'jwt'
SOURCE_PLATFORM = 'platform'
SOURCE_COOKIE = 'cookie'
SOURCE_NONE = 'none'


@dataclass
class Identity:
    """The resolved identity for the current request."""
    user_id: str               # Stable id used to scope sessions
    is_admin: bool             # Whether builder/admin features should be visible
    source: str                # Where we got the id from (for diagnostics)
    user_name: Optional[str] = None
    user_email: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'user_id': self.user_id,
            'is_admin': self.is_admin,
            'source': self.source,
            'user_name': self.user_name,
            'user_email': self.user_email,
        }


# ----------------------------------------------------------------------
# Test-mode flag
# ----------------------------------------------------------------------

def is_test_mode() -> bool:
    """
    Return True when DATA_COLLECTION_TEST_MODE is set truthy in env OR in
    the platform's config. Default: False (production-strict).

    Recognized truthy values: '1', 'true', 'yes', 'on' (case-insensitive).
    Any other value (including unset / 'False') → False.
    """
    # Highest priority: explicit env var
    env_val = os.environ.get('DATA_COLLECTION_TEST_MODE', '').strip().lower()
    if env_val in ('1', 'true', 'yes', 'on'):
        return True
    if env_val in ('0', 'false', 'no', 'off', ''):
        # Fall through to config.py / cfg attribute if env didn't decide
        pass
    else:
        # Some unrecognized value — treat as False but log
        logger.warning(
            f"DATA_COLLECTION_TEST_MODE has unrecognized value {env_val!r}; "
            "treating as False"
        )

    # Fallback: platform config attribute (config.py exposes them as cfg.X)
    try:
        import config as cfg
        if getattr(cfg, 'DATA_COLLECTION_TEST_MODE', False):
            return True
    except Exception:
        pass
    return False


# ----------------------------------------------------------------------
# Identity resolution
# ----------------------------------------------------------------------

def _platform_identity() -> Optional[Identity]:
    """Try flask_login.current_user, then session['user_id']. Returns None if unauthenticated."""
    # flask_login
    try:
        from flask_login import current_user
        if current_user and getattr(current_user, 'is_authenticated', False):
            uid = str(getattr(current_user, 'id', None) or
                      getattr(current_user, 'get_id', lambda: '')() or '')
            if uid:
                role = int(getattr(current_user, 'role', ROLE_USER) or ROLE_USER)
                return Identity(
                    user_id=uid,
                    is_admin=role >= MIN_ADMIN_ROLE,
                    source=SOURCE_PLATFORM,
                    user_name=getattr(current_user, 'username', None) or
                              getattr(current_user, 'name', None),
                    user_email=getattr(current_user, 'email', None),
                )
    except Exception:
        pass

    # Bare flask session
    try:
        from flask import session as flask_session
        uid = flask_session.get('user_id') or flask_session.get('id')
        if uid:
            role = int(flask_session.get('role') or ROLE_USER)
            return Identity(
                user_id=str(uid),
                is_admin=role >= MIN_ADMIN_ROLE,
                source=SOURCE_PLATFORM,
                user_name=flask_session.get('username') or flask_session.get('name'),
                user_email=flask_session.get('email'),
            )
    except Exception:
        pass

    return None


def _jwt_identity(jwt_claims: Optional[Dict[str, Any]]) -> Optional[Identity]:
    """Identity derived from a validated prefill JWT. JWT users are never admin."""
    if not jwt_claims or not isinstance(jwt_claims, dict):
        return None
    sub = jwt_claims.get('sub')
    if not sub:
        return None
    return Identity(
        user_id=str(sub),
        is_admin=False,  # JWT users do NOT get admin access — by design
        source=SOURCE_JWT,
        user_name=jwt_claims.get('name'),
        user_email=jwt_claims.get('email'),
    )


def _anon_cookie_identity() -> Optional[Identity]:
    """
    Read or mint a stable anonymous browser cookie. Returns an Identity
    that's tied to the cookie value. Non-admin. Used as a fallback when
    no JWT or platform auth is available, so that multiple browsers /
    private windows hitting the same instance get distinct user_ids and
    don't share session state.

    The cookie itself is set by `apply_identity_cookie()` on the response.
    """
    cookie_val = (request.cookies.get(ANON_COOKIE_NAME) or '').strip()
    if not cookie_val.startswith(ANON_ID_PREFIX):
        return None
    return Identity(
        user_id=cookie_val,
        is_admin=False,
        source=SOURCE_COOKIE,
    )


def current_identity(jwt_claims: Optional[Dict[str, Any]] = None) -> Identity:
    """
    Resolve the current request's identity. Caches on `flask.g` so multiple
    calls within a request hit the same value.

    In TEST MODE: always returns a synthetic admin identity (user_id='test_user')
    so things keep working without auth. Session ownership checks are no-ops
    elsewhere when test mode is on.

    In PRODUCTION MODE: walks JWT → platform → cookie. If none, returns an
    Identity with empty user_id and source='none' — callers should treat as
    unauthenticated and 401.
    """
    # Cache on g so we don't redo the work multiple times per request
    cached = getattr(g, '_dca_identity', None)
    if cached is not None and cached.get('jwt_id') == id(jwt_claims):
        return cached['identity']

    if is_test_mode():
        ident = Identity(
            user_id='test_user',
            is_admin=True,
            source=SOURCE_TEST,
            user_name='Test User',
        )
    else:
        ident = (
            _jwt_identity(jwt_claims)
            or _platform_identity()
            or _anon_cookie_identity()
            or Identity(user_id='', is_admin=False, source=SOURCE_NONE)
        )

    g._dca_identity = {'jwt_id': id(jwt_claims), 'identity': ident}
    return ident


def apply_identity_cookie(response):
    """
    If the current request had no existing anonymous cookie AND we're in
    production mode AND the response is being served to a browser without a
    higher-priority identity, mint a fresh anon cookie on the way out.

    Idempotent — only sets if not already present.
    """
    if is_test_mode():
        return response
    # Only set if no existing cookie
    if request.cookies.get(ANON_COOKIE_NAME):
        return response
    # Only set for real page responses (not API JSON) so we don't leak
    # cookies to webhooks / external API consumers
    if response.mimetype != 'text/html':
        return response

    new_id = ANON_ID_PREFIX + secrets.token_urlsafe(16)
    # 1-year expiry, HttpOnly so it's not readable by JS
    response.set_cookie(
        ANON_COOKIE_NAME,
        new_id,
        max_age=365 * 24 * 60 * 60,
        httponly=True,
        samesite='Lax',
    )
    return response


# ----------------------------------------------------------------------
# Decorators
# ----------------------------------------------------------------------

def require_identity(view):
    """
    Reject requests with no identity (production mode only).

    Layers ON TOP of the existing `api_key_or_session_required` — that one
    handles platform-session vs API-key auth. This one ensures we have a
    user_id we can scope sessions to. In test mode, no-op.
    """
    from functools import wraps

    @wraps(view)
    def wrapper(*args, **kwargs):
        if is_test_mode():
            return view(*args, **kwargs)
        ident = current_identity()
        if not ident.user_id or ident.source == SOURCE_NONE:
            from flask import jsonify
            return jsonify({
                'status': 'error',
                'error': 'Authentication required.',
                'code': 'NO_IDENTITY',
            }), 401
        return view(*args, **kwargs)

    return wrapper


def require_admin(view):
    """
    Gate a route to admin (Developer-or-Admin role) users only.

    In test mode: no-op (everyone is admin).
    In production mode: 403 if `current_identity().is_admin` is False.
    JWT-authed users are NEVER admin, regardless of token contents.
    """
    from functools import wraps

    @wraps(view)
    def wrapper(*args, **kwargs):
        if is_test_mode():
            return view(*args, **kwargs)
        ident = current_identity()
        if not ident.is_admin:
            from flask import jsonify, render_template, request as flask_request
            # JSON for API calls, HTML for page routes
            wants_json = (
                flask_request.is_json
                or 'application/json' in (flask_request.headers.get('Accept') or '')
                or flask_request.path.startswith('/api/')
            )
            if wants_json:
                return jsonify({
                    'status': 'error',
                    'error': 'Admin access required.',
                    'code': 'NOT_ADMIN',
                }), 403
            return render_template(
                'admin/forbidden.html',
                identity=ident,
            ), 403
        return view(*args, **kwargs)

    return wrapper


# ----------------------------------------------------------------------
# Session ownership
# ----------------------------------------------------------------------

def assert_session_owner(session, identity: Optional[Identity] = None):
    """
    Verify the calling identity owns the given session. Returns
    (allowed: bool, error_response: Optional[Tuple[Response, int]]).

    Test mode: always allowed.
    No identity: deny.
    Mismatched user_id: deny.
    Match: allow.
    """
    if is_test_mode():
        return True, None

    ident = identity or current_identity()
    if not ident.user_id or ident.source == SOURCE_NONE:
        from flask import jsonify
        return False, (jsonify({
            'status': 'error',
            'error': 'Authentication required.',
            'code': 'NO_IDENTITY',
        }), 401)

    session_owner = getattr(session, 'user_id', None)
    if not session_owner:
        # Legacy session with no owner — in production mode, refuse to expose it
        from flask import jsonify
        logger.warning(
            f"Refusing access to session {getattr(session, 'session_id', '?')} "
            f"with no recorded owner"
        )
        return False, (jsonify({
            'status': 'error',
            'error': 'Session not accessible.',
            'code': 'NO_OWNER',
        }), 403)

    if str(session_owner) != str(ident.user_id):
        from flask import jsonify
        return False, (jsonify({
            'status': 'error',
            'error': 'This session belongs to another user.',
            'code': 'NOT_OWNER',
        }), 403)

    return True, None
