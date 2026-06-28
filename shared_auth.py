"""
shared_auth.py — shared signed-JWT helpers for cross-service identity in AI Hub.

Three token kinds, distinguished by their audience (`aud`):
  - CC session token       (AUD_CC)        — the Command Center web/session JWT
  - delegation assertion   (AUD_INTERNAL)  — short-lived, a single delegated call
  - co-browse token        (AUD_COBROWSE)  — portal take-over live-view access

Secret resolution (get_jwt_secret), in priority order:
  1. CC_JWT_SECRET env, if set — pin this in the shared .env to make every process agree.
  2. else HMAC-SHA256(API_KEY, b"cc-jwt-v1").hexdigest() — deterministic, so any process that
     shares the same API_KEY derives the SAME secret without a pinned value. (This is why the
     CC service and main app must resolve the same API_KEY / secrets store; see cc_config /
     frozen-onedir APP_ROOT handling.)
  3. else None (not configured) — signing/verifying will report it instead of crashing.

Both the main app (signs CC tokens) and the CC service (verifies them) import this module, so the
secret + algorithm + audiences stay in lock-step.
"""
import hashlib
import hmac
import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

ALGO = "HS256"
AUD_CC = "command-center"
AUD_INTERNAL = "aihub-internal"
AUD_COBROWSE = "portal-cobrowse"
DEFAULT_CC_TTL = 172800          # 2 days
DEFAULT_ASSERTION_TTL = 300      # 5 minutes
DEFAULT_COBROWSE_TTL = 900       # 15 minutes
_SECRET_INFO = b"cc-jwt-v1"


def get_cc_token_ttl() -> int:
    """CC session token lifetime (seconds); overridable via CC_TOKEN_TTL_SECONDS."""
    try:
        return int(os.environ.get("CC_TOKEN_TTL_SECONDS", DEFAULT_CC_TTL))
    except (TypeError, ValueError):
        return DEFAULT_CC_TTL


def get_jwt_secret() -> Optional[str]:
    """Resolve the shared signing secret: explicit CC_JWT_SECRET, else derived from API_KEY,
    else None. The derived form is HMAC-SHA256(API_KEY, "cc-jwt-v1") so every process with the
    same API_KEY agrees without a pinned secret."""
    secret = os.environ.get("CC_JWT_SECRET")
    if secret:
        return secret
    api_key = os.environ.get("API_KEY") or os.environ.get("AI_HUB_API_KEY")
    if not api_key:
        return None
    return hmac.new(api_key.encode("utf-8"), _SECRET_INFO, hashlib.sha256).hexdigest()


def is_configured() -> bool:
    """Whether a signing secret is available in this process."""
    return bool(get_jwt_secret())


def _encode(payload: Dict[str, Any], aud: str, ttl_seconds: int,
            secret: Optional[str] = None) -> str:
    import jwt
    secret = secret or get_jwt_secret()
    if not secret:
        raise RuntimeError(
            "No JWT secret configured. Set CC_JWT_SECRET, or ensure API_KEY is loaded "
            "(secure_config.load_secure_config()).")
    payload = dict(payload)
    now = int(time.time())
    payload.setdefault("iat", now)
    payload.setdefault("exp", now + int(ttl_seconds))
    payload["aud"] = aud
    return jwt.encode(payload, secret, algorithm=ALGO)


def sign_cc_token(user_context: Dict[str, Any], ttl_seconds: Optional[int] = None,
                  secret: Optional[str] = None) -> str:
    """Mint a CC session token from a user_context dict."""
    ttl = get_cc_token_ttl() if ttl_seconds is None else ttl_seconds
    payload = {
        "sub": str(user_context.get("user_id")),
        "role": user_context.get("role"),
        "tenant_id": user_context.get("tenant_id"),
        "username": user_context.get("username", ""),
        "name": user_context.get("name", ""),
    }
    return _encode(payload, AUD_CC, ttl, secret)


def sign_user_assertion(user_id, tenant_id, role, ttl_seconds: Optional[int] = None,
                        secret: Optional[str] = None) -> str:
    """Mint a short-lived delegation assertion for a single delegated call."""
    ttl = DEFAULT_ASSERTION_TTL if ttl_seconds is None else ttl_seconds
    payload = {"sub": str(user_id), "tenant_id": tenant_id, "role": role}
    return _encode(payload, AUD_INTERNAL, ttl, secret)


def sign_cobrowse_token(run_id, user_id, role, ttl_seconds: Optional[int] = None,
                        secret: Optional[str] = None) -> str:
    """Mint a co-browse take-over token scoped to one portal run."""
    ttl = DEFAULT_COBROWSE_TTL if ttl_seconds is None else ttl_seconds
    payload = {"sub": str(user_id), "role": role, "run_id": str(run_id)}
    return _encode(payload, AUD_COBROWSE, ttl, secret)


def verify_token(token: str, expected_aud: str,
                 secret: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Verify a signed token for the expected audience. Returns (claims, None) on success or
    (None, error_message) otherwise. Never raises."""
    if not token:
        return None, "no token"
    secret = secret or get_jwt_secret()
    if not secret:
        return None, "no secret configured"
    try:
        import jwt
        claims = jwt.decode(token, secret, algorithms=[ALGO], audience=expected_aud,
                            options={"require": ["exp"]})
        if not isinstance(claims, dict):
            return None, "invalid token: not a claims object"
        return claims, None
    except ImportError:
        return None, "PyJWT not installed"
    except Exception as e:
        # Map the specific PyJWT exception types to stable messages (callers/tests rely on them).
        name = type(e).__name__
        if name == "ExpiredSignatureError":
            return None, "token expired"
        if name == "InvalidAudienceError":
            return None, "wrong audience"
        if name == "InvalidSignatureError":
            return None, "bad signature"
        if name in ("DecodeError", "InvalidTokenError") or "InvalidToken" in name:
            return None, "invalid token: " + str(e)
        return None, "unexpected error: " + str(e)


def verify_cobrowse_token(token: str, secret: Optional[str] = None):
    """Verify a co-browse token; returns (claims, error). claims carry run_id + sub + role."""
    return verify_token(token, AUD_COBROWSE, secret)


def claim_user_id(claims: Dict[str, Any]):
    """Return the `sub` claim parsed back to int when possible (else the raw value)."""
    sub = claims.get("sub")
    try:
        return int(sub)
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
    """Verify a CC session token to a user_context. Primary path is the signed JWT; during the
    migration window an optional `legacy_lookup(token) -> record` resolves an opaque in-memory
    session (record carries user fields + an `expires` aware-datetime)."""
    claims, err = verify_token(token, AUD_CC)
    if claims is not None:
        return cc_user_context_from_claims(claims), None
    if legacy_lookup is not None:
        try:
            rec = legacy_lookup(token)
        except Exception:
            rec = None
        if rec:
            import datetime as _dt
            expires = rec.get("expires")
            if expires is not None and _dt.datetime.now(_dt.timezone.utc) >= expires:
                return None, "session expired"
            logger.warning("shared_auth: validated CC token via LEGACY in-memory fallback - "
                           "migrate this caller to JWT and remove the fallback.")
            return {
                "user_id": rec.get("user_id"),
                "role": rec.get("role"),
                "tenant_id": rec.get("tenant_id"),
                "username": rec.get("username", ""),
                "name": rec.get("name", ""),
            }, None
    return None, err


def _cli_main(argv) -> int:
    """Mint / verify AI Hub shared JWTs from the command line (debug aid)."""
    import argparse
    import json
    p = argparse.ArgumentParser(description="Mint / verify AI Hub shared JWTs")
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("mint-cc", help="Mint a CC session token")
    m.add_argument("--user-id", required=True)
    m.add_argument("--role", type=int, default=1)
    m.add_argument("--tenant-id", default=None)
    m.add_argument("--name", default="")
    m.add_argument("--username", default="")
    m.add_argument("--ttl", type=int, default=DEFAULT_CC_TTL)
    a = sub.add_parser("mint-assertion", help="Mint a delegation assertion")
    a.add_argument("--user-id", required=True)
    a.add_argument("--role", type=int, default=1)
    a.add_argument("--tenant-id", default=None)
    a.add_argument("--ttl", type=int, default=DEFAULT_ASSERTION_TTL)
    v = sub.add_parser("verify", help="Verify a token and dump claims")
    v.add_argument("token")
    v.add_argument("--aud", default=AUD_CC)
    args = p.parse_args(argv)
    if not is_configured():
        print("ERROR: no JWT secret (set CC_JWT_SECRET or API_KEY).")
        return 2
    if args.cmd == "mint-cc":
        print(sign_cc_token({"user_id": args.user_id, "role": args.role,
                             "tenant_id": args.tenant_id, "name": args.name,
                             "username": args.username}, ttl_seconds=args.ttl))
    elif args.cmd == "mint-assertion":
        print(sign_user_assertion(args.user_id, args.tenant_id, args.role, ttl_seconds=args.ttl))
    elif args.cmd == "verify":
        claims, err = verify_token(args.token, args.aud)
        if err:
            print("INVALID: " + err)
            return 1
        print(json.dumps(claims))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli_main(sys.argv[1:]))
