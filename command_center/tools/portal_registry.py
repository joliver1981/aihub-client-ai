"""
portal_registry.py - local registry of known web portals for the Command Center
fetch_from_portal tool. Two LOCAL stores (no database table):

  * Registry (this module): data/portal_registry.json - NON-sensitive metadata only
    (display name, slug, login URL, allowed domains, and the KEY NAMES under which the
    credentials live). Scoped per user, so one user's saved portals never leak to another.

  * Credentials: the encrypted LocalSecretsManager (data/secrets/secrets.json.enc),
    stored under user-scoped key names PORTAL_U<uid>_<SLUG>_USERNAME/_PASSWORD/_TOTP.

The registry NEVER holds a raw credential - only a reference (key name) to one. This is
what lets the agent re-use a saved portal seamlessly: it looks up the URL + the credential
key names by portal name, and the browser service resolves the actual secrets server-side.
"""
import json
import os
import threading
from typing import Any, Dict, List, Optional

_LOCK = threading.Lock()


def _app_root() -> str:
    return os.getenv("APP_ROOT") or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )


def _registry_path() -> str:
    return os.path.join(_app_root(), "data", "portal_registry.json")


def slug(name: str) -> str:
    """Canonical lookup key: lowercased, runs of non-alphanumerics collapsed to one '_'.
    'Acme Vendor, Inc.' -> 'acme_vendor_inc'."""
    return "_".join("".join(c if c.isalnum() else " " for c in (name or "")).split()).lower()


def secret_key_names(user_id: Any, name: str) -> Dict[str, str]:
    """User-scoped local_secrets KEY NAMES for a portal's credentials (not the values)."""
    base = f"PORTAL_U{str(user_id or 'anon')}_{slug(name).upper()}"
    return {
        "username_secret": f"{base}_USERNAME",
        "password_secret": f"{base}_PASSWORD",
        "totp_secret": f"{base}_TOTP",
    }


def _load() -> Dict[str, Any]:
    p = _registry_path()
    if not os.path.isfile(p):
        return {"users": {}}
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh) or {"users": {}}
    except Exception:
        return {"users": {}}


def _atomic_write(data: Dict[str, Any]) -> None:
    p = _registry_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, p)


def _user_portals(data: Dict[str, Any], user_id: Any) -> Dict[str, Any]:
    uid = str(user_id or "anon")
    return (data.get("users", {}).get(uid, {}) or {}).get("portals", {}) or {}


def list_portals(user_id: Any) -> List[Dict[str, Any]]:
    """All saved portals for a user (metadata only - never credentials)."""
    portals = _user_portals(_load(), user_id)
    return [
        {"name": v.get("name", k), "slug": k, "url": v.get("url"),
         "allowed_domains": v.get("allowed_domains") or []}
        for k, v in portals.items()
    ]


def lookup_portal(user_id: Any, name: str) -> Optional[Dict[str, Any]]:
    """Resolve a saved portal by name for this user. Exact slug match first, then a loose
    contains-match so 'the acme one' still finds 'acme'. Returns the entry (incl. the
    credential KEY NAMES) or None."""
    target = slug(name)
    portals = _user_portals(_load(), user_id)
    if not target:
        return None
    if target in portals:
        return {"slug": target, **portals[target]}
    for k, v in portals.items():
        if target in k or k in target:
            return {"slug": k, **v}
    return None


def save_portal(user_id: Any, name: str, url: str, username: str, password: str,
                totp: Optional[str] = None,
                allowed_domains: Optional[List[str]] = None) -> Dict[str, Any]:
    """Persist a portal for later seamless re-use: store the credentials in the encrypted
    LocalSecretsManager under user-scoped key names, and record the non-sensitive metadata
    (name, url, allowed domains, key-name references) in the local registry JSON.
    Returns the saved entry's {slug, name, url} (no secrets)."""
    keys = secret_key_names(user_id, name)

    # 1) credentials -> encrypted local store (never the registry json)
    from local_secrets import set_local_secret
    set_local_secret(keys["username_secret"], username, category="portal")
    set_local_secret(keys["password_secret"], password, category="portal")
    if totp:
        set_local_secret(keys["totp_secret"], totp, category="portal")

    # 2) non-sensitive metadata -> local registry json
    s = slug(name)
    with _LOCK:
        data = _load()
        portals = data.setdefault("users", {}).setdefault(
            str(user_id or "anon"), {}).setdefault("portals", {})
        portals[s] = {
            "name": name,
            "url": url,
            "allowed_domains": allowed_domains or [],
            "username_secret": keys["username_secret"],
            "password_secret": keys["password_secret"],
            "totp_secret": keys["totp_secret"] if totp else None,
        }
        _atomic_write(data)
    return {"slug": s, "name": name, "url": url}


def delete_portal(user_id: Any, name: str) -> bool:
    """Remove a saved portal's registry entry (credentials are left in the encrypted store;
    they're inert once unreferenced). Returns True if an entry was removed."""
    target = slug(name)
    with _LOCK:
        data = _load()
        portals = _user_portals(data, user_id)
        if target in portals:
            del data["users"][str(user_id or "anon")]["portals"][target]
            _atomic_write(data)
            return True
    return False
