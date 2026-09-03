"""
Signed OAuth ``state`` for the My Connections redirect broker.

WHY THIS EXISTS
    Client installs are HTTP-only and browsed by LAN address, so no OAuth
    provider will accept their callback as a redirect URI. The provider is
    therefore given a stable HTTPS endpoint on the cloud API (the "broker"),
    which 302s the browser back to the install. The install's own callback
    address (the "return address") travels inside ``state``.

    An unvalidated return address would be an open redirect. This module binds
    it to the secret both sides already share — the tenant API key (on-prem
    ``API_KEY`` == cloud ``Tenants.LicenseKey``) — with an HMAC-SHA256.

WIRE FORMAT (both repos MUST agree — a known-answer vector pins it in tests)
    payload = {"e": <unix expiry>, "n": "<nonce>", "r": "<return_url>",
               "t": <tenant_id>, "v": 1}          # JSON, compact, sorted keys
    body    = base64url(payload, no padding)
    sig     = base64url(HMAC_SHA256(key=API_KEY, msg=body), no padding)
    state   = body + "." + sig

TWIN FILE
    C:/src/aihub-api/project/api/mcp_oauth_state.py is a byte-for-byte copy of
    this module (different repos, no shared package). Change both together and
    keep the known-answer test green in each repo.

This module has no Flask or database dependencies on purpose: it is pure
functions over strings, so it is trivially testable and portable.
"""
import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import urlencode, urlsplit

STATE_VERSION = 1
DEFAULT_TTL_SECONDS = 600            # 10 minutes — the provider round trip
CALLBACK_PATH_SUFFIX = '/api/mcp/oauth/callback'
MAX_STATE_LENGTH = 4096
MAX_RETURN_URL_LENGTH = 2048
_NONCE_MIN, _NONCE_MAX = 16, 128
_NONCE_ALPHABET = frozenset(
    'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_')
_REQUIRED_FIELDS = ('t', 'r', 'n', 'e')


class StateError(ValueError):
    """Raised on any verification failure.

    ``reason`` is a short machine code for logs:
        malformed        not "<body>.<sig>" / too long / not a string
        bad_payload      body is not base64url JSON with the expected shapes
        missing_field    one of t/r/n/e absent
        bad_return_url   return address fails validate_return_url()
        unknown_tenant   key lookup returned nothing
        bad_signature    HMAC mismatch
        expired          e is in the past
    Callers show a generic message to the browser and log ``reason``.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------------------
# Encoding helpers
# ---------------------------------------------------------------------------

def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def _b64u_decode(text: str) -> bytes:
    if not text or set(text) - _NONCE_ALPHABET:
        raise ValueError('not base64url')
    padded = text + '=' * (-len(text) % 4)
    return base64.urlsafe_b64decode(padded.encode('ascii'))


def _signature(api_key: str, body: str) -> str:
    digest = hmac.new(api_key.encode('utf-8'), body.encode('ascii'),
                      hashlib.sha256).digest()
    return _b64u_encode(digest)


def new_nonce() -> str:
    return secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# Return-address validation
# ---------------------------------------------------------------------------

def validate_return_url(url: str,
                        require_path_suffix: Optional[str] = CALLBACK_PATH_SUFFIX) -> None:
    """Structural checks on the return address. Raises StateError('bad_return_url').

    The address is HMAC-bound to the tenant key, so this is defence in depth,
    not the primary control — but it costs nothing and removes whole classes
    of URL trickery (userinfo@, control characters, javascript:, backslashes,
    query/fragment smuggling, a foreign path).
    """
    if not isinstance(url, str) or not url or len(url) > MAX_RETURN_URL_LENGTH:
        raise StateError('bad_return_url')
    if any(ord(c) < 33 or ord(c) == 127 for c in url) or '\\' in url:
        raise StateError('bad_return_url')
    try:
        parts = urlsplit(url)
        port = parts.port           # raises ValueError on a non-numeric port
    except ValueError:
        raise StateError('bad_return_url')
    if parts.scheme not in ('http', 'https'):
        raise StateError('bad_return_url')
    if not parts.netloc or '@' in parts.netloc or not parts.hostname:
        raise StateError('bad_return_url')
    if port is not None and not (0 < port < 65536):
        raise StateError('bad_return_url')
    if parts.query or parts.fragment:
        raise StateError('bad_return_url')
    if require_path_suffix and not parts.path.endswith(require_path_suffix):
        raise StateError('bad_return_url')


# ---------------------------------------------------------------------------
# Sign / parse / verify
# ---------------------------------------------------------------------------

def sign_state(api_key: str, tenant_id: int, return_url: str,
               nonce: Optional[str] = None,
               ttl_seconds: int = DEFAULT_TTL_SECONDS,
               now: Optional[float] = None,
               require_path_suffix: Optional[str] = CALLBACK_PATH_SUFFIX) -> Tuple[str, str]:
    """Build a signed state. Returns (state, nonce).

    Refuses to sign a return address the broker would reject, so a
    misconfiguration fails here — on the install, with a clear error — rather
    than after the provider round trip.
    """
    if not api_key:
        raise ValueError('api_key is required to sign state')
    validate_return_url(return_url, require_path_suffix)
    nonce = nonce or new_nonce()
    if not (_NONCE_MIN <= len(nonce) <= _NONCE_MAX) or set(nonce) - _NONCE_ALPHABET:
        raise ValueError('nonce must be 16..128 base64url characters')
    now_i = int(time.time() if now is None else now)
    payload = {
        'v': STATE_VERSION,
        't': int(tenant_id),
        'r': return_url,
        'n': nonce,
        'e': now_i + int(ttl_seconds),
    }
    body = _b64u_encode(
        json.dumps(payload, separators=(',', ':'), sort_keys=True).encode('utf-8'))
    return f"{body}.{_signature(api_key, body)}", nonce


def parse_state(state: str) -> Tuple[Dict, str, str]:
    """Structural decode WITHOUT trust. Returns (payload, body, sig).

    Use this only to learn the tenant id for a rate-limit bucket or a log
    line before verify_state(); never act on the payload from here.
    """
    if not isinstance(state, str) or not state or len(state) > MAX_STATE_LENGTH:
        raise StateError('malformed')
    body, sep, sig = state.rpartition('.')
    if not sep or not body or not sig:
        raise StateError('malformed')
    try:
        payload = json.loads(_b64u_decode(body).decode('utf-8'))
    except Exception:
        raise StateError('bad_payload')
    if not isinstance(payload, dict):
        raise StateError('bad_payload')
    for key in _REQUIRED_FIELDS:
        if key not in payload:
            raise StateError('missing_field')
    t, r, n, e = payload['t'], payload['r'], payload['n'], payload['e']
    if isinstance(t, bool) or not isinstance(t, int) or t <= 0:
        raise StateError('bad_payload')
    if isinstance(e, bool) or not isinstance(e, int):
        raise StateError('bad_payload')
    if not isinstance(n, str) or not (_NONCE_MIN <= len(n) <= _NONCE_MAX) \
            or set(n) - _NONCE_ALPHABET:
        raise StateError('bad_payload')
    if not isinstance(r, str):
        raise StateError('bad_payload')
    return payload, body, sig


def verify_state(state: str, key_lookup: Callable[[int], Optional[str]],
                 now: Optional[float] = None,
                 require_path_suffix: Optional[str] = CALLBACK_PATH_SUFFIX) -> Dict:
    """Full verification. Returns the trusted payload or raises StateError.

    Order: structure → return-address shape → tenant key lookup → HMAC
    (constant-time) → expiry. The cheap checks run before the database hit so
    garbage never costs a query; externally every failure is the same refusal.
    """
    payload, body, sig = parse_state(state)
    validate_return_url(payload['r'], require_path_suffix)
    key = key_lookup(payload['t'])
    if not key:
        raise StateError('unknown_tenant')
    expected = _signature(key, body)
    if not hmac.compare_digest(expected.encode('ascii'), sig.encode('ascii', 'replace')):
        raise StateError('bad_signature')
    now_i = int(time.time() if now is None else now)
    if payload['e'] < now_i:
        raise StateError('expired')
    return payload


def verify_state_with_key(state: str, api_key: str, now: Optional[float] = None,
                          require_path_suffix: Optional[str] = CALLBACK_PATH_SUFFIX) -> Dict:
    """On-prem variant: verify against THIS install's key regardless of ``t``."""
    return verify_state(state, lambda _tenant_id: api_key, now=now,
                        require_path_suffix=require_path_suffix)


# ---------------------------------------------------------------------------
# URL helpers used by the bounce
# ---------------------------------------------------------------------------

def origin_of(url: str) -> str:
    """Normalised ``scheme://host[:port]`` (lower-case host, default port dropped)."""
    parts = urlsplit(url)
    scheme = (parts.scheme or '').lower()
    host = (parts.hostname or '').lower()
    if ':' in host and not host.startswith('['):
        host = f'[{host}]'                       # IPv6 literal
    try:
        port = parts.port
    except ValueError:
        port = None
    default = {'http': 80, 'https': 443}.get(scheme)
    if port is None or port == default:
        return f'{scheme}://{host}'
    return f'{scheme}://{host}:{port}'


def append_query(url: str, params: Dict[str, str]) -> str:
    sep = '&' if urlsplit(url).query else '?'
    return f'{url}{sep}{urlencode(params)}'
