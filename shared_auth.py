"""
shared_auth.py — Shared HS256 JWT helpers for cross-service identity.

Top-level module so BOTH the main Flask app and the command_center_service
(and any other service) can import it directly: ``from shared_auth import ...``.
Both processes add the repo root to ``sys.path`` and call
``secure_config.load_secure_config()``, so ``API_KEY`` is present in
``os.environ`` in each — which is what makes a shared signing secret possible
without provisioning anything new.

Two token types, distinguished by the ``aud`` claim:

  * CC session token  (``aud="command-center"``, ~4h) — carries the
    logged-in user's identity to the CC frontend; replaces the old opaque,
    in-memory token.
  * Delegation assertion (``aud="aihub-internal"``, ~5m) — minted by CC for
    each delegated call so downstream endpoints can authorize the *user*
    (not merely the tenant API key).

Secret resolution (``get_jwt_secret``):
  1. ``CC_JWT_SECRET`` env var, if set (rotation / override).
  2. otherwise a dedicated key derived as ``HMAC-SHA256(API_KEY, "cc-jwt-v1")``.
``API_KEY`` is per-install, so tokens never validate across tenants — which
is consistent with the platform's tenant boundary.

Design notes:
  * ``import jwt`` is done lazily (like data_collection_agent/auth_token.py) so
    this module stays importable even where PyJWT is not installed.
  * Verification never raises — it returns ``(claims, error)``.
"""
import hashlib
import hmac
import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

ALGO = "HS256"

# Audience (``aud``) values — one per token type.
AUD_CC = "command-center"
AUD_INTERNAL = "aihub-internal"
AUD_COBROWSE = "portal-cobrowse"

# Default lifetimes (seconds).
DEFAULT_CC_TTL = 172800
DEFAULT_ASSERTION_TTL = 300
DEFAULT_COBROWSE_TTL = 900


def get_cc_token_ttl() -> int:
    """Resolve the CC session-token lifetime in seconds.

    Single source of truth for token expiry: the ``CC_TOKEN_TTL_SECONDS`` env
    var, defaulting to 48 hours. Read at call time so a .env change takes
    effect on restart without import-order concerns.
    """
    try:
        return int(os.environ.get("CC_TOKEN_TTL_SECONDS", DEFAULT_CC_TTL))
    except (TypeError, ValueError):
        return DEFAULT_CC_TTL


_SECRET_INFO = b"cc-jwt-v1"


def get_jwt_secret() -> Optional[str]:
    """Resolve the shared HS256 secret, or None if nothing is configured.

    Priority: CC_JWT_SECRET env, else HMAC-SHA256(API_KEY, 'cc-jwt-v1').
    Callers should treat None as 'JWT path not configured'.
    """
    explicit = os.environ.get("CC_JWT_SECRET")
    if explicit:
        return explicit
    api_key = os.environ.get("API_KEY") or os.environ.get("AI_HUB_API_KEY")
    if not api_key:
        return None
    return hmac.new(api_key.encode("utf-8"), _SECRET_INFO, hashlib.sha256).hexdigest()


def is_configured() -> bool:
    """Whether a signing secret is available in this process."""
    return get_jwt_secret() is not None


def _encode(payload: Dict[str, Any], aud: str, ttl_seconds: int,
            secret: Optional[str] = None) -> str:
    import jwt
    if secret is None:
        secret = get_jwt_secret()
    if not secret:
        raise RuntimeError(
            "No JWT secret configured. Set CC_JWT_SECRET, or ensure API_KEY is loaded (secure_config.load_secure_config()).")
    body = dict(payload)
    now = int(time.time())
    body.setdefault("iat", now)
    body.setdefault("exp", now + int(ttl_seconds))
    body["aud"] = aud
    return jwt.encode(body, secret, algorithm=ALGO)


def sign_cc_token(user_context: Dict[str, Any], ttl_seconds: Optional[int] = None,
                  secret: Optional[str] = None) -> str:
    """Mint a CC session token from a user_context dict."""
    if ttl_seconds is None:
        ttl_seconds = get_cc_token_ttl()
    uc = user_context or {}
    uid = uc.get("user_id")
    payload = {
        "sub": str(uid) if uid is not None else None,
        "role": uc.get("role"),
        "tenant_id": uc.get("tenant_id"),
        "username": uc.get("username", ""),
        "name": uc.get("name", ""),
    }
    return _encode(payload, AUD_CC, ttl_seconds, secret)


def sign_user_assertion(user_id: Any, tenant_id: Any, role: Any,
                        ttl_seconds: int = DEFAULT_ASSERTION_TTL,
                        secret: Optional[str] = None) -> str:
    """Mint a short-lived delegation assertion for a single delegated call."""
    payload = {
        "sub": str(user_id) if user_id is not None else None,
        "tenant_id": tenant_id,
        "role": role,
    }
    return _encode(payload, AUD_INTERNAL, ttl_seconds, secret)


def sign_cobrowse_token(run_id: str, user_id: Any = None, role: Any = None,
                        ttl_seconds: int = DEFAULT_COBROWSE_TTL,
                        secret: Optional[str] = None) -> str:
    """Mint a short-lived token scoped to ONE run for the live-view / takeover endpoints.
    Bound to run_id + the operator's identity so the browser_use_service can authorize the
    co-browse WebSocket and control calls without a full CC session."""
    payload = {
        "sub": str(user_id) if user_id is not None else None,
        "role": role,
        "run_id": run_id,
    }
    return _encode(payload, AUD_COBROWSE, ttl_seconds, secret)


def verify_cobrowse_token(token: str,
                          secret: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Verify a co-browse token; returns (claims, error). claims carry run_id + sub + role."""
    return verify_token(token, AUD_COBROWSE, secret)


def verify_token(token: str, expected_aud: str,
                 secret: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Verify a token and return (claims, error). Never raises.

    On success returns (claims_dict, None). On any failure returns
    (None, "<reason>"). ``exp`` is required; ``aud`` must equal expected_aud.
    """
    if not token:
        return None, "no token"
    if secret is None:
        secret = get_jwt_secret()
    if not secret:
        return None, "JWT not configured (no CC_JWT_SECRET / API_KEY)"
    try:
        import jwt
        from jwt.exceptions import (
            ExpiredSignatureError,
            InvalidSignatureError,
            InvalidTokenError,
            InvalidAudienceError,
            DecodeError,
        )
    except ImportError:
        return None, "PyJWT not installed in this environment"
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[ALGO],
            audience=expected_aud,
            options={"require": ["exp"]},
        )
        if not isinstance(claims, dict):
            return None, "claims is not an object"
        return claims, None
    except ExpiredSignatureError:
        return None, "token expired"
    except InvalidAudienceError:
        return None, "wrong audience"
    except InvalidSignatureError:
        return None, "bad signature"
    except (InvalidTokenError, DecodeError) as e:
        return None, "invalid token: " + f"{e}"
    except Exception as e:
        return None, "unexpected error: " + f"{e}"


def claim_user_id(claims: Dict[str, Any]) -> Any:
    """Return the `sub` claim parsed back to int when possible (else raw)."""
    sub = (claims or {}).get("sub")
    try:
        return int(sub) if sub is not None else None
    except (TypeError, ValueError):
        return sub


def cc_user_context_from_claims(claims: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize verified CC-session claims into the legacy user_context shape."""
    return {
        "user_id": claim_user_id(claims),
        "role": claims.get("role"),
        "tenant_id": claims.get("tenant_id"),
        "username": claims.get("username", ""),
        "name": claims.get("name", ""),
    }


def verify_cc_token(token: str, legacy_lookup=None) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Verify a CC session token, returning (user_context, error).

    During migration, if ``token`` is not a valid JWT and ``legacy_lookup`` is
    provided (a callable ``token -> stored_dict | None``, e.g. ``cc_tokens.get``),
    fall back to the legacy in-memory store so live sessions survive cutover.
    Remove the fallback once no LEGACY-fallback warnings are observed.
    """
    user_ctx_claims, err = verify_token(token, AUD_CC)
    if user_ctx_claims is not None:
        return cc_user_context_from_claims(user_ctx_claims), None
    if legacy_lookup is not None:
        try:
            stored = legacy_lookup(token)
        except Exception:
            stored = None
        if stored:
            import datetime as _dt
            exp = stored.get("expires")
            if exp is not None and _dt.datetime.now(_dt.timezone.utc) > exp:
                return None, "token expired (legacy)"
            logger.warning(
                "shared_auth: validated CC token via LEGACY in-memory fallback — migrate this caller to JWT and remove the fallback.")
            return {
                "user_id": stored.get("user_id"),
                "role": stored.get("role"),
                "tenant_id": stored.get("tenant_id"),
                "username": stored.get("username", ""),
                "name": stored.get("name", ""),
            }, None
    return None, err


def _cli_main(argv=None):
    import argparse
    import json
    p = argparse.ArgumentParser(description="Mint / verify AI Hub shared JWTs")
    sub = p.add_subparsers(dest="cmd", required=True)

    pm = sub.add_parser("mint-cc", help="Mint a CC session token")
    pm.add_argument("--user-id", type=int, required=True)
    pm.add_argument("--role", type=int, default=1)
    pm.add_argument("--tenant-id", type=int, default=1)
    pm.add_argument("--name", default="")
    pm.add_argument("--username", default="")
    pm.add_argument("--ttl", type=int, default=DEFAULT_CC_TTL)

    pa = sub.add_parser("mint-assertion", help="Mint a delegation assertion")
    pa.add_argument("--user-id", type=int, required=True)
    pa.add_argument("--role", type=int, default=1)
    pa.add_argument("--tenant-id", type=int, default=1)
    pa.add_argument("--ttl", type=int, default=DEFAULT_ASSERTION_TTL)

    pv = sub.add_parser("verify", help="Verify a token and dump claims")
    pv.add_argument("token")
    pv.add_argument("--aud", default=AUD_CC, choices=[AUD_CC, AUD_INTERNAL])

    args = p.parse_args(argv)

    if not is_configured():
        print("ERROR: no JWT secret (set CC_JWT_SECRET or API_KEY).", flush=True)
        return 1

    if args.cmd == "mint-cc":
        print(sign_cc_token({
            "user_id": args.user_id,
            "role": args.role,
            "tenant_id": args.tenant_id,
            "name": args.name,
            "username": args.username,
        }, ttl_seconds=args.ttl))
    elif args.cmd == "mint-assertion":
        print(sign_user_assertion(args.user_id, args.tenant_id, args.role, ttl_seconds=args.ttl))
    elif args.cmd == "verify":
        claims, err = verify_token(args.token, args.aud)
        if err:
            print("INVALID: " + f"{err}")
            return 1
        print(json.dumps(claims, indent=2, default=str))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli_main(sys.argv[1:]))
