"""
portal_workflow_run.py - CC tool core: run a SAVED portal WORKFLOW (deterministic steps +
woven-in LLM steps) by calling the isolated Browser Use service's /workflow/run, then register
any downloads as CC artifacts (download chips). This is the "Workflow mode" sibling of
portal_fetch.fetch_portal ("Auto-mode").

Credentials never pass through here or the LLM: the workflow stores only step anchors + a
`portal_slug` reference; this tool resolves the credential KEY NAMES from the matching
portal_registry entry and sends those (the service decrypts server-side). Artifact registration
reuses portal_fetch._register_artifacts so downloads render identically to Auto-mode.
"""
import logging
import os
from typing import Any, Dict, Optional

import requests

from command_center.tools import portal_registry, portal_workflows
from command_center.tools.portal_fetch import _register_artifacts

logger = logging.getLogger(__name__)


def _credential_key_names(user_id: Any, workflow: Dict[str, Any]) -> Dict[str, str]:
    """Find the credential KEY NAMES for a workflow: prefer the linked saved portal (by
    portal_slug), else derive from the workflow name. Never returns secret values."""
    portal_slug = workflow.get("portal_slug")
    if portal_slug:
        entry = portal_registry.lookup_portal(user_id, portal_slug)
        if entry:
            return {
                "username_secret": entry.get("username_secret"),
                "password_secret": entry.get("password_secret"),
                "totp_secret": entry.get("totp_secret"),
            }
    keys = portal_registry.secret_key_names(user_id, workflow.get("name", ""))
    return keys


def run_workflow_by_name(name: str, session_id: str = "",
                         user_context: Optional[Dict[str, Any]] = None,
                         timeout: int = 600, agent_fallback: bool = True,
                         inline_creds: Optional[Dict[str, Any]] = None,
                         inputs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Load the saved workflow `name` for this user and execute it via the Browser Use service.
    Returns {status, error, final_result, blocks, file_count, steps}. Synchronous (requests) -
    call via asyncio.to_thread from async code."""
    uc = user_context or {}
    user_id = uc.get("user_id")
    workflow = portal_workflows.get_workflow(user_id, name)
    if not workflow:
        return {"status": "error", "error": f"no saved workflow named {name!r}",
                "blocks": [], "file_count": 0, "final_result": None, "steps": []}
    try:
        from CommonUtils import get_browser_use_api_base_url
        base = get_browser_use_api_base_url()
    except Exception as e:
        return {"status": "error", "error": f"service URL unavailable: {e}",
                "blocks": [], "file_count": 0, "final_result": None, "steps": []}

    payload = {
        "workflow": {
            "name": workflow.get("name"),
            "start_url": workflow.get("start_url"),
            "goal": workflow.get("goal"),
            "agent_oversight": workflow.get("agent_oversight", True),
            "takeover_timeout": workflow.get("takeover_timeout"),
            "steps": workflow.get("steps") or [],
        },
        "portal_name": workflow.get("portal_slug") or workflow.get("name"),
        "session_id": session_id or None,
        "user_id": str(user_id) if user_id is not None else None,
        "timeout": timeout,
        "agent_fallback": agent_fallback,
        "inputs": inputs or None,
    }

    if inline_creds and inline_creds.get("username") and inline_creds.get("password"):
        payload["username"] = inline_creds["username"]
        payload["password"] = inline_creds["password"]
        if inline_creds.get("totp"):
            payload["totp"] = inline_creds["totp"]
    else:
        keys = _credential_key_names(user_id, workflow)
        payload.update({k: v for k, v in keys.items() if v})

    headers = {}
    api_key = os.getenv("API_KEY")
    if api_key:
        headers["X-AIHub-Internal"] = api_key

    try:
        resp = requests.post(f"{base}/workflow/run", json=payload, headers=headers, timeout=timeout)
    except Exception as e:
        return {"status": "error", "error": f"could not reach Browser Use service at {base}: {e}",
                "blocks": [], "file_count": 0, "final_result": None, "steps": []}

    if resp.status_code != 200:
        return {"status": "error",
                "error": f"service returned {resp.status_code}: {resp.text[:300]}",
                "blocks": [], "file_count": 0, "final_result": None, "steps": []}
    try:
        data = resp.json()
    except Exception as e:
        return {"status": "error", "error": f"bad service response: {e}",
                "blocks": [], "file_count": 0, "final_result": None, "steps": []}

    files = data.get("files") or []
    blocks = _register_artifacts(files, session_id, user_context)
    try:
        portal_workflows.record_run(user_id, name, data.get("status", "error"))
    except Exception:
        pass

    _has_upload = any(isinstance(s, dict) and s.get("type") == "upload"
                      for s in (workflow.get("steps") or []))
    return {
        "status": data.get("status", "ok"),
        "error": data.get("error"),
        "final_result": data.get("final_result"),
        "steps": data.get("steps") or [],
        "blocks": blocks,
        "file_count": len(blocks),
        "is_upload": _has_upload,
        # NEUTRAL signal (replaces the download-as-success proxy): the delivery layer decides what
        # "done" means from these instead of assuming a file was downloaded.
        "expects_download": not _has_upload,
        "files": files,
    }
