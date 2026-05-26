"""
JWT helper for the optional `?prefill=<JWT>` runtime URL parameter.

Phase 1.3 of the plan. The token is PURELY OPTIONAL — the runtime URL
continues to work without it. When present and valid, the server pre-populates
the new session's collected_data, sets up an external callback URL, and may
override branding for the session.

Token payload contract (from the plan):
    {
      "sub":            "user-id-from-caller",
      "name":           "Jane Rep",
      "email":          "jane@example.com",
      "iat":            <issued-at>,
      "exp":            <expiry — required, default 1 hour>,
      "config_id":      "speaker_event_scheduler",       # optional, must match URL path if provided
      "prefill":        { "section_id": { "field_id": "value", ... }, ... },
      "callback_url":   "https://mer360.example.com/api/dca/submission",  # optional
      "callback_secret_ref": "mer360_webhook_secret",    # optional, references local secret
      "branding":       { ... },                         # optional, per-session override
      "return_url":     "https://mer360.example.com/...", # optional, for "Back to MER360"
      "aud":            "data_collection_agent",         # optional but recommended
      "iss":            "mer360"                         # optional but recommended
    }

Symmetric HS256 by default. The shared secret is read from
DCA_PREFILL_SECRET (env). Without a secret configured, the validation path
is dormant — `?prefill=` query params are silently ignored and a warning
is logged. This means local-dev / testing works with no setup at all.

CLI:
    python -m data_collection_agent.auth_token mint --user james --email james@example.com --config-id example
    python -m data_collection_agent.auth_token verify <token>
"""

import json
import logging
import os
import sys
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


# Default token lifetime when not specified
DEFAULT_TTL_SECONDS = 3600

# Expected audience claim when present — accepted-only set
EXPECTED_AUDIENCE = 'data_collection_agent'


def _get_secret() -> Optional[str]:
    """
    Resolve the shared HS256 secret. Sources, in priority order:
      1. DCA_PREFILL_SECRET env var
      2. The local secrets store (data/dca_secrets.json) under key
         'prefill_secret'

    Returns None if not configured. Callers should treat None as "tokens
    are dormant" — silently ignore any incoming tokens.
    """
    secret = os.environ.get('DCA_PREFILL_SECRET')
    if secret:
        return secret

    # Try local secrets file
    secrets_path = os.path.join(
        os.getenv('APP_ROOT', os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        'data',
        'dca_secrets.json',
    )
    try:
        if os.path.exists(secrets_path):
            with open(secrets_path, 'r', encoding='utf-8') as f:
                store = json.load(f) or {}
            val = store.get('prefill_secret')
            if val:
                return str(val)
    except Exception as e:
        logger.warning(f"Could not read {secrets_path}: {e}")

    return None


def is_configured() -> bool:
    """Whether the JWT path is configured at all (a secret is set somewhere)."""
    return _get_secret() is not None


def encode_token(
    payload: Dict[str, Any],
    *,
    secret: Optional[str] = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    issuer: Optional[str] = None,
) -> str:
    """
    Mint a token. Mostly used by tests / the CLI / external callers (in
    practice MER360 will mint these on its side).

    Adds `iat` and `exp` if not already in payload. Adds `aud` if not present.
    Returns the encoded JWT string.
    """
    import jwt  # local import — keeps the module loadable without PyJWT

    if secret is None:
        secret = _get_secret()
    if not secret:
        raise RuntimeError(
            "No JWT secret configured. Set DCA_PREFILL_SECRET env var or add "
            "'prefill_secret' to data/dca_secrets.json."
        )

    body = dict(payload)
    now = int(time.time())
    body.setdefault('iat', now)
    body.setdefault('exp', now + int(ttl_seconds))
    body.setdefault('aud', EXPECTED_AUDIENCE)
    if issuer:
        body.setdefault('iss', issuer)

    return jwt.encode(body, secret, algorithm='HS256')


def decode_token(token: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Validate a token and return (claims, error).

    Behavior:
      - If no secret is configured: returns (None, "JWT path not configured").
        Callers should treat this as "ignore the token, continue without
        prefill, log a warning."
      - If the token is missing / empty: returns (None, "no token").
      - If the token is invalid (bad sig, expired, malformed):
        returns (None, "<reason>"). Callers should log and fall through.
      - If valid: returns (claims_dict, None).

    Never raises — every error path returns (None, message).
    """
    if not token:
        return None, "no token"

    secret = _get_secret()
    if not secret:
        return None, "JWT path not configured (no DCA_PREFILL_SECRET)"

    try:
        import jwt
        from jwt.exceptions import (
            ExpiredSignatureError, InvalidSignatureError, InvalidTokenError,
            DecodeError, InvalidAudienceError,
        )
    except ImportError:
        return None, "PyJWT not installed in this environment"

    try:
        # Decode with audience check ONLY when an `aud` claim is set.
        # Easier path: peek at the headers/payload first to decide, but
        # PyJWT's decode handles this — if `aud` is missing, decoding without
        # `audience` arg passes through.
        # We try with `audience` first; if it errors due to missing audience
        # in the token, retry without (older callers may not set it).
        try:
            claims = jwt.decode(
                token,
                secret,
                algorithms=['HS256'],
                audience=EXPECTED_AUDIENCE,
                options={'require': ['exp']},
            )
        except InvalidAudienceError:
            return None, "wrong audience claim"
        except DecodeError as e:
            # Could be that the token has no aud claim; allow without aud
            try:
                claims = jwt.decode(
                    token,
                    secret,
                    algorithms=['HS256'],
                    options={'require': ['exp'], 'verify_aud': False},
                )
            except Exception as e2:
                return None, f"decode failed: {e2}"

        if not isinstance(claims, dict):
            return None, "claims is not an object"

        return claims, None

    except ExpiredSignatureError:
        return None, "token expired"
    except InvalidSignatureError:
        return None, "bad signature"
    except InvalidTokenError as e:
        return None, f"invalid token: {e}"
    except Exception as e:
        return None, f"unexpected error: {e}"


def extract_session_overrides(claims: Dict[str, Any], expected_config_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Pull the parts of a validated claim payload we use server-side to
    seed a new session. Returns a dict suitable for merging into a
    CollectionSession on creation.

    If `expected_config_id` is provided AND the token has a `config_id`
    claim that doesn't match, this returns a special key
    `__config_id_mismatch` so the caller can choose to ignore.

    Keys returned (any may be missing if not in claims):
      - user_id, user_name, user_email
      - prefill (dict of section_id -> {field_id: value})
      - callback_url, callback_secret_ref
      - return_url
      - branding (dict)
      - jwt_claims (the raw claims, for downstream use like branding resolver)
    """
    if not isinstance(claims, dict):
        return {}

    out: Dict[str, Any] = {'jwt_claims': claims}

    if claims.get('sub'):
        out['user_id'] = str(claims['sub'])
    if claims.get('name'):
        out['user_name'] = str(claims['name'])
    if claims.get('email'):
        out['user_email'] = str(claims['email'])

    if expected_config_id and claims.get('config_id') and claims['config_id'] != expected_config_id:
        out['__config_id_mismatch'] = (claims['config_id'], expected_config_id)
        # Don't return early — still surface other fields; caller decides

    if isinstance(claims.get('prefill'), dict):
        out['prefill'] = claims['prefill']

    if claims.get('callback_url'):
        out['callback_url'] = str(claims['callback_url'])
    if claims.get('callback_secret_ref'):
        out['callback_secret_ref'] = str(claims['callback_secret_ref'])

    if claims.get('return_url'):
        out['return_url'] = str(claims['return_url'])

    if isinstance(claims.get('branding'), dict):
        out['branding'] = claims['branding']

    return out


# ----------------------------------------------------------------------
# CLI — `python -m data_collection_agent.auth_token`
# ----------------------------------------------------------------------

def _cli_mint(args):
    """Hand-mint a token for testing."""
    payload: Dict[str, Any] = {}
    if args.user:
        payload['sub'] = args.user
    if args.name:
        payload['name'] = args.name
    if args.email:
        payload['email'] = args.email
    if args.config_id:
        payload['config_id'] = args.config_id
    if args.callback_url:
        payload['callback_url'] = args.callback_url
    if args.return_url:
        payload['return_url'] = args.return_url
    if args.prefill_json:
        try:
            payload['prefill'] = json.loads(args.prefill_json)
        except json.JSONDecodeError as e:
            print(f"ERROR: --prefill-json is not valid JSON: {e}", file=sys.stderr)
            return 2
    if args.branding_json:
        try:
            payload['branding'] = json.loads(args.branding_json)
        except json.JSONDecodeError as e:
            print(f"ERROR: --branding-json is not valid JSON: {e}", file=sys.stderr)
            return 2

    secret = args.secret or _get_secret()
    if not secret:
        print(
            "ERROR: no secret. Set DCA_PREFILL_SECRET, add to "
            "data/dca_secrets.json, or pass --secret <value>.",
            file=sys.stderr,
        )
        return 1

    token = encode_token(
        payload,
        secret=secret,
        ttl_seconds=args.ttl,
        issuer=args.issuer,
    )
    print(token)
    return 0


def _cli_verify(args):
    """Verify and dump a token's claims."""
    if args.secret:
        os.environ['DCA_PREFILL_SECRET'] = args.secret
    claims, err = decode_token(args.token)
    if err:
        print(f"INVALID: {err}", file=sys.stderr)
        return 1
    print(json.dumps(claims, indent=2, default=str))
    return 0


def _cli_main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="Mint / verify DCA prefill JWTs")
    sub = p.add_subparsers(dest='cmd', required=True)

    pm = sub.add_parser('mint', help='Mint a token')
    pm.add_argument('--user', required=False, help='sub claim (user id)')
    pm.add_argument('--name', required=False, help='name claim')
    pm.add_argument('--email', required=False, help='email claim')
    pm.add_argument('--config-id', required=False, help='constrain token to a schema')
    pm.add_argument('--callback-url', required=False, help='external callback for submission webhook')
    pm.add_argument('--return-url', required=False, help='URL for a "Back to caller" button')
    pm.add_argument('--prefill-json', required=False,
                    help='JSON string of prefill data: {"section_id": {"field_id": "value"}}')
    pm.add_argument('--branding-json', required=False,
                    help='JSON string of branding overrides')
    pm.add_argument('--ttl', type=int, default=DEFAULT_TTL_SECONDS,
                    help=f'Token lifetime in seconds (default {DEFAULT_TTL_SECONDS})')
    pm.add_argument('--issuer', required=False, default='dca-cli',
                    help='iss claim')
    pm.add_argument('--secret', required=False,
                    help='Override the secret (otherwise read from env / secrets store)')
    pm.set_defaults(func=_cli_mint)

    pv = sub.add_parser('verify', help='Verify and dump a token')
    pv.add_argument('token', help='The JWT to verify')
    pv.add_argument('--secret', required=False, help='Override the secret')
    pv.set_defaults(func=_cli_verify)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(_cli_main())
