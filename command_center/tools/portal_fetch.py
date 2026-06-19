"""
portal_fetch.py - CC tool core: call the isolated Browser Use service to log into a web
portal and download files, then register the downloads as CC artifacts (download chips).

The service runs in its own conda env (aihub-browseruse) and is reached over HTTP via
CommonUtils.get_browser_use_api_base_url(). Credentials never pass through here or the LLM:
the tool sends only the secret KEY NAMES (derived from portal_name), which the service
resolves from the encrypted LocalSecretsManager. Artifact registration mirrors
code_interpreter.harvest_outputs so downloads render as download chips with no frontend change.
"""
import logging
import os
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# ext -> CC ArtifactType value (same families code_interpreter uses for common files)
_EXT_TO_ARTIFACT_TYPE = {
    ".csv": "csv", ".xlsx": "excel", ".xls": "excel", ".pdf": "pdf",
    ".json": "json", ".txt": "text", ".png": "image", ".jpg": "image", ".jpeg": "image",
}


def _secret_keys(portal_name: str) -> Dict[str, str]:
    """Map a portal name to the local_secrets KEY NAMES the service will resolve.
    'Acme Vendor' -> PORTAL_ACME_VENDOR_USERNAME / _PASSWORD / _TOTP."""
    slug = "".join(c if c.isalnum() else "_" for c in (portal_name or "")).upper().strip("_")
    return {
        "username_secret": f"PORTAL_{slug}_USERNAME",
        "password_secret": f"PORTAL_{slug}_PASSWORD",
        "totp_secret": f"PORTAL_{slug}_TOTP",
    }


def _register_artifacts(files: List[str], session_id: str,
                        user_context: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Read each downloaded file (shared filesystem) and save it as a CC artifact;
    return artifact content blocks (download chips). Mirrors code_interpreter."""
    blocks: List[Dict[str, Any]] = []
    if not files:
        return blocks
    uc = user_context or {}
    user_id = str(uc.get("user_id", "anonymous"))
    scoped_session = f"{user_id}/{session_id}" if session_id else user_id

    mgr = None
    try:
        from routes.artifacts import _get_artifact_manager
        mgr = _get_artifact_manager()
    except Exception as e:
        logger.warning(f"[portal_fetch] artifact manager unavailable: {e}")
    try:
        from command_center.artifacts.artifact_models import ArtifactType
    except Exception:
        ArtifactType = None

    for fpath in files:
        try:
            if not fpath or not os.path.isfile(fpath):
                logger.warning(f"[portal_fetch] downloaded file missing on disk: {fpath}")
                continue
            name = os.path.basename(fpath)
            with open(fpath, "rb") as fh:
                data = fh.read()
            if mgr is not None and ArtifactType is not None:
                ext = os.path.splitext(name)[1].lower()
                type_val = _EXT_TO_ARTIFACT_TYPE.get(ext, "text")
                try:
                    atype = ArtifactType(type_val)
                except Exception:
                    atype = ArtifactType.TEXT
                meta = mgr.create(name, atype, data, scoped_session)
                blocks.append(meta.to_content_block())
        except Exception as e:
            logger.warning(f"[portal_fetch] could not register {fpath}: {e}")
    return blocks


def fetch_portal(portal_name: str, start_url: str, task: str,
                 session_id: str = "", user_context: Optional[Dict[str, Any]] = None,
                 timeout: int = 360,
                 secret_key_overrides: Optional[Dict[str, str]] = None,
                 inline_creds: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Call the Browser Use service to log into `start_url` and do `task`, then register any
    downloaded files as CC artifacts. Returns {status, error, final_result, blocks, file_count}.
    Synchronous (uses requests) - call via asyncio.to_thread from async code.

    Credential resolution (in priority order):
      * inline_creds={username,password,totp} - a ONE-OFF ad-hoc run; raw creds are passed to
        the (loopback) service. Used the first time, before a portal is saved.
      * secret_key_overrides={username_secret,...} - KEY NAMES from a saved registry entry.
      * otherwise _secret_keys(portal_name) - the legacy global PORTAL_<SLUG>_* convention.
    Only the inline path puts raw creds on the wire; saved/legacy send key names only."""
    try:
        from CommonUtils import get_browser_use_api_base_url
        base = get_browser_use_api_base_url()
    except Exception as e:
        return {"status": "error", "error": f"service URL unavailable: {e}",
                "blocks": [], "file_count": 0, "final_result": None}

    payload = {
        "task": task,
        "start_url": start_url,
        "portal_name": portal_name,
        "session_id": session_id or None,
        "user_id": str((user_context or {}).get("user_id", "")) or None,
    }
    if inline_creds and inline_creds.get("username") and inline_creds.get("password"):
        payload["username"] = inline_creds["username"]
        payload["password"] = inline_creds["password"]
        if inline_creds.get("totp"):
            payload["totp"] = inline_creds["totp"]
    else:
        keys = secret_key_overrides or _secret_keys(portal_name)
        payload.update({k: v for k, v in keys.items() if v})

    headers = {}
    api_key = os.getenv("API_KEY")
    if api_key:
        headers["X-AIHub-Internal"] = api_key  # matches the service's internal-token gate

    try:
        resp = requests.post(f"{base}/portal/fetch", json=payload, headers=headers, timeout=timeout)
    except Exception as e:
        return {"status": "error", "error": f"could not reach Browser Use service at {base}: {e}",
                "blocks": [], "file_count": 0, "final_result": None}

    if resp.status_code != 200:
        return {"status": "error",
                "error": f"service returned {resp.status_code}: {resp.text[:300]}",
                "blocks": [], "file_count": 0, "final_result": None}

    try:
        data = resp.json()
    except Exception as e:
        return {"status": "error", "error": f"bad service response: {e}",
                "blocks": [], "file_count": 0, "final_result": None}

    files = data.get("files") or []
    blocks = _register_artifacts(files, session_id, user_context)
    return {
        "status": data.get("status", "ok"),
        "error": data.get("error"),
        "final_result": data.get("final_result"),
        "blocks": blocks,
        "file_count": len(blocks),
    }
