"""
codeflow_tools.py — CC-side client for the main app's Code Flows internal
management endpoint (/codeflows/api/internal/manage).

A Code Flow is the multi-step sibling of an Automation: a workflow of inline
Code Step nodes, authored step by step. The CC agent's code-flow tools (in
nodes.py) are thin async wrappers around `manage(...)` here. Auth mirrors
automation_tools: platform X-API-Key + CC-verified user_context; the main app
re-checks Developer role at the chokepoint, so CC-side gating is UX only.

Synchronous (called via asyncio.to_thread), returns plain dicts, and NEVER
raises for a remote failure — {"ok": False, "error": ...} instead.
"""
import logging
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

# A dry-run actually executes every step's subprocess, so allow the same
# generous ceiling as automations; per-step timeouts are enforced server-side.
DEFAULT_TIMEOUT = 900


def manage(action: str, user_context: Dict[str, Any],
           payload: Optional[Dict[str, Any]] = None,
           timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """POST one code-flow management action to the main app. Returns the
    response JSON with ok/status_code added; never raises."""
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
    url = f"{get_base_url()}/codeflows/api/internal/manage"
    try:
        resp = requests.post(url, json=body,
                             headers={"X-API-Key": AI_HUB_API_KEY}, timeout=timeout)
        try:
            data = resp.json()
        except ValueError:
            data = {"error": f"non-JSON response (HTTP {resp.status_code})"}
        data["ok"] = resp.status_code < 400
        data["status_code"] = resp.status_code
        return data
    except requests.RequestException as e:
        logger.warning(f"code flows manage({action}) failed: {e}")
        return {"ok": False, "status_code": 502,
                "error": f"could not reach the code flows service: {e}"}


def summarize_walk(result: Dict[str, Any]) -> str:
    """Honest multi-step summary of a dry_run/run walk for the chat — one line
    per step with its outcome, files produced, and (on failure) the stderr
    tail so the dev can fix the offending step."""
    status = result.get("status", "?")
    if status == "error":
        return f"Code flow could not run: {result.get('error')}"
    steps = result.get("steps") or []
    header = (f"Walk outcome: **{status}** — {len(steps)} step(s) executed")
    lines = [header]
    for i, s in enumerate(steps, 1):
        mark = {"success": "✓", "failed": "✗", "error": "✗"}.get(s.get("status"), "?")
        label = s.get("name") or s.get("step_id")
        extra = ""
        if s.get("exit_code") is not None and s.get("status") != "success":
            extra += f" (exit {s['exit_code']})"
        lines.append(f"{mark} step {i} — {label}{extra}: {s.get('status')}")
        files = s.get("output_files") or []
        if files:
            lines.append("    files: " + ", ".join(files[:6]))
        if s.get("status") in ("failed", "error"):
            tail = (s.get("stderr_tail") or s.get("error") or "").strip()
            if tail:
                lines.append("    stderr: " + tail[-500:])
    return "\n".join(lines)
