"""
trace_converter.py - turn a SUCCESSFUL auto-mode run (browser-use AgentHistoryList) into a
DRAFT Workflow of typed blocks the user can edit in the builder. This is the "Save as workflow"
seed: the thing that already worked becomes the starting point, so building a workflow is
editing, not authoring from scratch.

browser-use records, per interacted element (DOMInteractedElement.to_dict()): x_path, node_name
(tag), attributes (id/name/aria-label/type/...), and ax_name (visible/accessibility text). We
distill each navigate/input/click action into a stable, multi-strategy anchor (NOT the run's
volatile element index) so replay survives reloads.

Two deliberate behaviours:
  * Credentials typed via browser-use `sensitive_data` show up in history as the PLACEHOLDER
    (portal_username/portal_password/portal_totp), never the real value - so a fill of a
    placeholder becomes a `secret` fill, and a username+password+submit run collapses to one
    `login` block. No secret is ever written into the draft.
  * The draft is LITERAL: a click the LLM made by judgment ("the most recent invoice") is
    captured as a deterministic click on THAT element. The user is expected to replace such a
    step with an `agent` prompt block in the builder - that human curation is the whole point.
"""
import re
from typing import Any, Dict, List, Optional

# A bare CSS id selector (#foo) is only valid for simple identifiers; otherwise fall back to
# the [id="..."] attribute form.
_SIMPLE_ID = re.compile(r'^[A-Za-z_][\w-]*$')

_SECRET_PLACEHOLDERS = {"portal_username": "username", "portal_password": "password",
                        "portal_totp": "totp"}


def _norm_element(el: Any) -> Dict[str, Any]:
    """DOMInteractedElement (object) or its to_dict() form -> plain dict."""
    if el is None:
        return {}
    if hasattr(el, "to_dict"):
        try:
            return el.to_dict() or {}
        except Exception:
            return {}
    return el if isinstance(el, dict) else {}


def _anchor_from_element(el: Any) -> Dict[str, Any]:
    """Build a replay-safe anchor (css/xpath/text/role/name tried in priority order at replay)."""
    d = _norm_element(el)
    attrs = d.get("attributes") or {}
    anchor = {}

    node = (d.get("node_name") or "").lower()
    if node == "a":
        anchor["role"] = "link"
    elif node == "button" or attrs.get("type") in ("submit", "button"):
        anchor["role"] = "button"

    text = d.get("ax_name") or attrs.get("aria-label") or attrs.get("title")
    if text and text.strip():
        anchor["text"] = text.strip()

    if attrs.get("id"):
        _id = attrs["id"]
        anchor["css"] = f"#{_id}" if _SIMPLE_ID.match(_id) else f'[id="{_id}"]'
    elif attrs.get("name"):
        anchor["css"] = f'[name="{attrs["name"]}"]'
    elif attrs.get("placeholder"):
        anchor["css"] = f'[placeholder="{attrs["placeholder"]}"]'

    if d.get("x_path"):
        anchor["xpath"] = d["x_path"]
    return anchor


def _secret_for(text: str) -> Optional[str]:
    low = (text or "").lower()
    for placeholder, kind in _SECRET_PLACEHOLDERS.items():
        if placeholder in low:
            return kind
    return None


def _action_name_and_params(action: Dict[str, Any]):
    for k, v in action.items():
        if k == "interacted_element":
            continue
        return k, v or {}
    return None, {}


def _step_from_action(action: Dict[str, Any],
                      upload_files: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    name, params = _action_name_and_params(action)
    if not name:
        return None
    el = action.get("interacted_element")

    if "go_to_url" in name or name in ("navigate", "open_tab") or "navigate" in name:
        url = params.get("url")
        return {"type": "goto", "url": url} if url else None

    if "input_text" in name or name in ("type", "input") or "send_keys" in name:
        anchor = _anchor_from_element(el)
        text = params.get("text", "")
        secret = _secret_for(text)
        step = {"type": "fill", "anchor": anchor}
        if secret:
            step["secret"] = secret
        else:
            step["value"] = text
        return step

    if (name in ("submit_verification_code", "request_human_takeover")
            or "verification_code" in name or "human_takeover" in name):
        return {"type": "verify_code",
                "reason": "Enter the verification / 2-step (2FA) code", "timeout": 900}

    if "upload" in name:
        step = {"type": "upload", "anchor": _anchor_from_element(el)}
        path = params.get("path") or params.get("file_path") or params.get("file")
        if not path and upload_files:
            path = upload_files[0]
        if path:
            step["path"] = path
        return step

    if "click" in name:
        return {"type": "click", "anchor": _anchor_from_element(el)}

    return None


def _collapse_login(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fold a [fill(secret username), fill(secret password), (fill totp)?, click] run into one
    `login` block. Keeps the draft readable and matches the builder's Log-in block."""
    out = []
    i = 0
    n = len(steps)
    while i < n:
        s = steps[i]
        if s.get("type") == "fill" and s.get("secret") == "username":
            j = i + 1
            user_anchor = s.get("anchor")
            pass_anchor = totp_anchor = None
            if (j < n and steps[j].get("type") == "fill"
                    and steps[j].get("secret") == "password"):
                pass_anchor = steps[j].get("anchor")
                j += 1
                if (j < n and steps[j].get("type") == "fill"
                        and steps[j].get("secret") == "totp"):
                    totp_anchor = steps[j].get("anchor")
                    j += 1
                submit_anchor = None
                if j < n and steps[j].get("type") == "click":
                    submit_anchor = steps[j].get("anchor")
                    j += 1
                block = {"type": "login", "username_anchor": user_anchor,
                         "password_anchor": pass_anchor}
                if totp_anchor:
                    block["totp_anchor"] = totp_anchor
                if submit_anchor:
                    block["submit_anchor"] = submit_anchor
                out.append(block)
                i = j
                continue
        out.append(s)
        i += 1
    return out


def history_to_workflow(history: Any, start_url: Optional[str] = None, goal: Optional[str] = None,
                        name: Optional[str] = None, human_takeover: bool = False,
                        upload_files: Optional[List[str]] = None) -> Dict[str, Any]:
    """Convert an AgentHistoryList (or anything exposing model_actions()) into a draft workflow:
    {name, start_url, goal, steps:[...]}. Best-effort and side-effect free; never raises on a
    shape it doesn't recognise (returns whatever it could distill).

    `human_takeover` = a person took over mid-run (e.g. typed a 2FA code in the live view). That
    action is NOT in the agent history, so we insert an explicit `verify_code` step after login so
    a replay also handles the verification gate (auto via TOTP, else pause for a person) instead of
    skipping it."""
    steps = []
    try:
        actions = history.model_actions() if hasattr(history, "model_actions") else (history or [])
    except Exception:
        actions = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        step = _step_from_action(action, upload_files)
        if not step:
            continue
        steps.append(step)

    if start_url and not (steps and steps[0].get("type") == "goto"):
        steps.insert(0, {"type": "goto", "url": start_url})

    steps = _collapse_login(steps)

    if human_takeover and not any(s.get("type") == "verify_code" for s in steps):
        insert_at = 0
        for idx, s in enumerate(steps):
            if s.get("type") in ("goto", "login", "fill"):
                insert_at = idx + 1
            elif s.get("type") == "click":
                break
        steps.insert(insert_at, {"type": "verify_code",
                                 "reason": "Enter the verification / 2-step (2FA) code",
                                 "timeout": 900})
    return {
        "name": name or "Recorded workflow",
        "start_url": start_url,
        "goal": goal,
        "steps": steps,
    }
