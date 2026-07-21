"""
portal_workflows.py - local store of SAVED portal WORKFLOWS for Command Center "Workflow mode"
(the deterministic-steps + woven-in-LLM-steps alternative to one-shot "Auto-mode").

Mirrors portal_registry.py exactly: a single local JSON (data/portal_workflows.json), scoped
per user, atomic temp-file + os.replace under a threading.Lock, slug-canonical lookup. It holds
ONLY non-sensitive data - the ordered typed steps with stable anchors (css/text/role/name/xpath),
a natural-language `goal` for the agent fallback, and a `portal_slug` REFERENCE to the matching
portal_registry entry (which is where the credential KEY NAMES live). No credential, value, or
secret is ever written here. The browser_use_service receives the steps over HTTP and resolves
secrets server-side by key name, identical to the portal_fetch path.

Step schema (see browser_use_service/workflow_runner.py for the executor):
  {"type":"goto",  "url":"https://..."}
  {"type":"login", "username_anchor":{...},"password_anchor":{...},"submit_anchor":{...}}
  {"type":"click", "anchor":{"text":"Invoices","role":"link"}}
  {"type":"fill",  "anchor":{...},"value":"2026"}            # or {"secret":"username|password|totp"}
  {"type":"wait",  "until":{"text":"Invoice"},"timeout":10}  # or just {"timeout":3}
  {"type":"agent", "prompt":"download the most recent invoice","max_steps":8}  # the LLM step
  {"type":"verify","downloaded":true}                        # or {"text":"..."} / {"anchor":{...}}
"""
import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

_LOCK = threading.Lock()

# Allowed step types + which anchor/keys each may carry (light validation, not a full schema).
_STEP_TYPES = {"goto", "login", "click", "fill", "wait", "agent", "verify", "human",
               "verify_code", "upload"}


def _app_root() -> str:
    return os.getenv("APP_ROOT") or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )


def _store_path() -> str:
    return os.path.join(_app_root(), "data", "portal_workflows.json")


def slug(name: str) -> str:
    """Canonical lookup key: lowercased, runs of non-alphanumerics collapsed to one '_'."""
    return "_".join("".join(c if c.isalnum() else " " for c in (name or "")).split()).lower()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load() -> Dict[str, Any]:
    p = _store_path()
    if not os.path.isfile(p):
        return {"users": {}}
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh) or {"users": {}}
    except Exception:
        return {"users": {}}


def _atomic_write(data: Dict[str, Any]) -> None:
    p = _store_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, p)


def _user_workflows(data: Dict[str, Any], user_id: Any) -> Dict[str, Any]:
    uid = str(user_id or "anon")
    return (data.get("users", {}).get(uid, {}) or {}).get("workflows", {}) or {}


def valid_url(u: Any) -> bool:
    """True if `u` is an absolute http(s) URL (scheme + host). Used to reject an invalid Start /
    Navigate URL (e.g. a bare 'abc') at SAVE time — otherwise it saves fine and only fails during
    execution when the browser tries to navigate to it."""
    try:
        p = urlparse(str(u).strip())
    except Exception:
        return False
    return p.scheme in ("http", "https") and bool(p.netloc)


def _is_negative(v: Any) -> bool:
    """True if `v` is a number (or numeric string) less than zero. Non-numeric → False (a separate
    check flags non-numeric where it matters)."""
    try:
        return float(v) < 0
    except (TypeError, ValueError):
        return False


def validate_steps(steps: Any) -> List[str]:
    """Return a list of human-readable problems (empty == valid). Cheap structural checks only."""
    problems: List[str] = []
    if not isinstance(steps, list) or not steps:
        return ["workflow must have at least one step"]
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            problems.append(f"step {i}: not an object")
            continue
        t = s.get("type")
        if t not in _STEP_TYPES:
            problems.append(f"step {i}: unknown type {t!r}")
            continue
        if t == "goto":
            u = s.get("url")
            if not u:
                problems.append(f"step {i} (goto): missing url")
            elif not valid_url(u):
                problems.append(f"step {i} (goto): invalid url {u!r} — use a full http(s):// address")
        if t == "click" and not s.get("anchor"):
            problems.append(f"step {i} (click): missing anchor")
        if t == "fill" and not s.get("anchor"):
            problems.append(f"step {i} (fill): missing anchor")
        if t == "fill" and s.get("value") is None and not s.get("secret"):
            problems.append(f"step {i} (fill): needs value or secret")
        if t == "agent" and not (s.get("prompt") or s.get("task")):
            problems.append(f"step {i} (agent): missing prompt")
        if t == "human" and not s.get("reason"):
            problems.append(f"step {i} (human): missing reason")
        # Numeric-field guards: a negative wait ran instantly and reported "waited -1.0s"; a
        # negative timeout/max_steps is never meaningful. Reject at save (all callers funnel here).
        if t == "wait" and s.get("timeout") is not None:
            try:
                if float(s.get("timeout")) < 0:
                    problems.append(f"step {i} (wait): timeout must be 0 or greater")
            except (TypeError, ValueError):
                problems.append(f"step {i} (wait): timeout must be a number")
        if t in ("human", "verify_code") and _is_negative(s.get("timeout")):
            problems.append(f"step {i} ({t}): timeout must be 0 or greater")
        if t == "agent" and _is_negative(s.get("max_steps")):
            problems.append(f"step {i} (agent): max_steps must be 0 or greater")
    return problems


def list_workflows(user_id: Any) -> List[Dict[str, Any]]:
    """All saved workflows for a user (metadata + step count, never any secret)."""
    wfs = _user_workflows(_load(), user_id)
    out = []
    for k, v in wfs.items():
        steps = v.get("steps") or []
        types = [s.get("type") for s in steps if isinstance(s, dict)]
        out.append({
            "slug": k, "name": v.get("name", k), "portal_slug": v.get("portal_slug"),
            "start_url": v.get("start_url"), "goal": v.get("goal"),
            "step_count": len(steps),
            "step_types": types,
            "uploads": ("upload" in types),   # lets a caller see what the workflow DOES (no secrets)
            "success_count": v.get("success_count", 0),
            "last_run_status": v.get("last_run_status"),
            "updated_at": v.get("updated_at"),
        })
    return out


def get_workflow(user_id: Any, name: str) -> Optional[Dict[str, Any]]:
    """Resolve a saved workflow by name (exact slug, then loose contains-match)."""
    target = slug(name)
    wfs = _user_workflows(_load(), user_id)
    if not target:
        return None
    if target in wfs:
        return {"slug": target, **wfs[target]}
    for k, v in wfs.items():
        if target in k or k in target:
            return {"slug": k, **v}
    return None


def workflow_exists(user_id: Any, name: str) -> bool:
    """True if a workflow with this EXACT slug already exists for the user (exact key match, not
    the loose contains-match `get_workflow` uses). The save endpoint calls this to detect a
    duplicate-name collision before it would silently overwrite a different workflow."""
    target = slug(name)
    if not target:
        return False
    return target in _user_workflows(_load(), user_id)


def save_workflow(user_id: Any, name: str, steps: List[Dict[str, Any]],
                  portal_slug: Optional[str] = None, start_url: Optional[str] = None,
                  goal: Optional[str] = None,
                  agent_oversight: Optional[bool] = None,
                  takeover_timeout: Optional[int] = None) -> Dict[str, Any]:
    """Persist (create or update) a workflow's non-sensitive definition. Raises ValueError if
    the steps fail structural validation. Returns the saved {slug, name, step_count}.

    `agent_oversight` (default ON) lets a supervising LLM step in when a recorded step gets stuck.
    `takeover_timeout` (seconds, optional) is the per-workflow human/2FA take-over window; None
    keeps the prior value (or the service default), 0 also clears it back to the default."""
    problems = validate_steps(steps)
    if problems:
        raise ValueError("; ".join(problems))
    if start_url and not valid_url(start_url):
        raise ValueError(f"invalid start_url {start_url!r} — use a full http(s):// address")
    s = slug(name)
    with _LOCK:
        data = _load()
        wfs = data.setdefault("users", {}).setdefault(
            str(user_id or "anon"), {}).setdefault("workflows", {})
        prev = wfs.get(s, {})
        wfs[s] = {
            "name": name,
            "portal_slug": portal_slug or prev.get("portal_slug"),
            "start_url": start_url or prev.get("start_url"),
            "goal": goal or prev.get("goal"),
            "agent_oversight": (prev.get("agent_oversight", True) if agent_oversight is None
                                else bool(agent_oversight)),
            "takeover_timeout": (prev.get("takeover_timeout") if takeover_timeout is None
                                 else (int(takeover_timeout) or None)),
            "steps": steps,
            "created_at": prev.get("created_at") or _now(),
            "updated_at": _now(),
            "success_count": prev.get("success_count", 0),
            "last_run_status": prev.get("last_run_status"),
        }
        _atomic_write(data)
    return {"slug": s, "name": name, "step_count": len(steps)}


def record_run(user_id: Any, name: str, status: str) -> None:
    """Update run telemetry after an execution (success_count + last_run_status)."""
    s = slug(name)
    with _LOCK:
        data = _load()
        wfs = _user_workflows(data, name and user_id)
        if s in wfs:
            entry = data["users"][str(user_id or "anon")]["workflows"][s]
            entry["last_run_status"] = status
            if status in ("ok", "success"):
                entry["success_count"] = entry.get("success_count", 0) + 1
            entry["updated_at"] = _now()
            _atomic_write(data)


def delete_workflow(user_id: Any, name: str) -> bool:
    """Remove a saved workflow. Returns True if an entry was removed."""
    target = slug(name)
    with _LOCK:
        data = _load()
        wfs = _user_workflows(data, user_id)
        if target in wfs:
            del data["users"][str(user_id or "anon")]["workflows"][target]
            _atomic_write(data)
            return True
    return False
