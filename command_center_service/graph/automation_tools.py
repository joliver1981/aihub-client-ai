"""
automation_tools.py — CC-side client for the main app's Automations internal
management endpoint (/automations/api/internal/manage).

The CC agent's automation tools (defined in nodes.py) are thin async wrappers
around `manage(...)` here. Auth is the platform X-API-Key plus the CC-verified
user_context; the main app re-checks Developer role at the chokepoint, so
CC-side gating is UX, not the security boundary.

Synchronous by design (called via asyncio.to_thread), returns plain dicts and
NEVER raises for a remote failure — {"ok": False, "error": ...} instead, so a
tool turn degrades to an honest message rather than a stack trace.
"""
import logging
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

# Long enough for a dry-run that actually executes the script (manifest
# timeout default is 600s); the runner enforces the real limit server-side.
DEFAULT_TIMEOUT = 900


def manage(action: str, user_context: Dict[str, Any],
           payload: Optional[Dict[str, Any]] = None,
           timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """POST one management action to the main app. Returns the response JSON
    with ok=True/False added; never raises."""
    from cc_config import get_base_url, AI_HUB_API_KEY
    uc = user_context or {}
    body = {
        "action": action,
        "user_context": {
            "user_id": uc.get("user_id"),
            "role": uc.get("role"),
            "username": uc.get("username") or uc.get("name") or "",
        },
        "payload": payload or {},
    }
    url = f"{get_base_url()}/automations/api/internal/manage"
    try:
        resp = requests.post(url, json=body,
                             headers={"X-API-Key": AI_HUB_API_KEY}, timeout=timeout)
        try:
            data = resp.json()
        except ValueError:
            data = {"error": f"non-JSON response (HTTP {resp.status_code})"}
        data["ok"] = resp.status_code < 400
        return data
    except requests.RequestException as e:
        logger.warning(f"automations manage({action}) failed: {e}")
        return {"ok": False, "error": f"could not reach the automations service: {e}"}


def summarize_run(result: Dict[str, Any]) -> str:
    """Honest one-paragraph summary of a run/dry-run result for the chat."""
    status = result.get("status", "?")
    lines = [f"Run outcome: **{status}**"
             + (f" (exit code {result.get('exit_code')})" if result.get("exit_code") is not None else "")]
    if result.get("run_id"):
        lines.append(f"run_id: {result['run_id']}")
    if result.get("error"):
        lines.append(f"Error: {result['error']}")
    for entry in result.get("verify_report") or []:
        for check in entry.get("checks", []):
            ok = check.get("ok")
            mark = "✓" if ok is True else ("✗" if ok is False else "?")
            target = entry.get("path") or entry.get("name") or entry.get("kind")
            note = check.get("note") or check.get("check")
            lines.append(f"{mark} {target}: {note}")
    files = result.get("output_files") or []
    if files:
        lines.append("Files produced: " + ", ".join(files[:10]))
    tail = (result.get("stdout_tail") or "").strip()
    if tail:
        lines.append("Output tail:\n" + tail[-800:])
    err_tail = (result.get("stderr_tail") or "").strip()
    if status in ("failed", "error") and err_tail:
        lines.append("Stderr tail:\n" + err_tail[-800:])
    return "\n".join(lines)
