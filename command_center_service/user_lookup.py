"""
user_lookup.py - resolve a platform user's contact info (name / email / phone) by user_id.

Reads the main app's /get/users (admin-gated, called server-side with the platform API key -
the same endpoint chat.py uses to resolve a user's role). Used by the CC `get_my_contact_info`
tool to resolve "me"/"my email", and by self-scheduling to snapshot the task owner's email.
Returns only the requested user's record; callers pass the signed-in user's own id.
"""
import json
import logging
from typing import Any, Dict

import requests

logger = logging.getLogger(__name__)


def _base_and_key():
    try:
        from cc_config import get_base_url, AI_HUB_API_KEY
        return get_base_url(), AI_HUB_API_KEY
    except Exception:
        import os
        return os.getenv("AI_HUB_INTERNAL_URL", ""), os.getenv("API_KEY", "")


def get_user_contact(user_id: Any) -> Dict[str, Any]:
    """Return {user_id, name, email, phone, username} for a user, or {} on failure."""
    if user_id in (None, ""):
        return {}
    base, key = _base_and_key()
    if not base:
        return {}
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return {}
    try:
        r = requests.get(f"{base.rstrip('/')}/get/users",
                         headers={"X-API-Key": key}, timeout=8)
        if r.status_code != 200:
            return {}
        data = r.json()
        # /get/users returns jsonify(df.to_json(orient='records')) — i.e. a JSON-encoded
        # STRING of a records list — so r.json() is a str that must be parsed again.
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                return {}
        if isinstance(data, list):
            users = data
        elif isinstance(data, dict):
            users = data.get("users", [])
        else:
            users = []
        for u in users:
            if not isinstance(u, dict):
                continue
            try:
                row_id = int(u.get("id", u.get("user_id", 0)) or 0)
            except (TypeError, ValueError):
                continue
            if row_id == uid:
                return {
                    "user_id": uid,
                    "name": u.get("name") or u.get("user_name") or "",
                    "email": u.get("email") or "",
                    "phone": u.get("phone") or "",
                    "username": u.get("user_name") or u.get("username") or "",
                }
        return {}
    except Exception as e:
        logger.warning(f"[user_lookup] get_user_contact({user_id}) failed: {e}")
        return {}
