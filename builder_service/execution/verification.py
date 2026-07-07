"""
Deterministic read-back verification  (Phase 2 — silent-success remediation)
============================================================================

After a mutating action's HTTP call reports success, re-read platform state and
confirm the intended change actually landed. This is the deterministic core of
the principle "never report success from the response alone — only from a fresh
read of the world, and only for what the caller asked".

It is driven by a small per-capability spec table. Each spec names a read-back
capability (the action's existing list/get route) and a pure `check(params,
result_data, read_data)` function that returns one of:

  CONFIRMED    – read-back proves the change; the action stays SUCCESS (verified=True).
  DISPROVED    – read-back proves the change did NOT land; the executor downgrades
                 the result to FAILED. This is the key new catch — but ONLY a
                 positive disproof downgrades a success.
  INCONCLUSIVE – no spec, no read path, missing id, unreadable shape, or the
                 read-back itself errored/timed out. The result is left as-is and
                 flagged verified=None (UNVERIFIED metadata for the Phase 4
                 messaging work). We never regress a success we cannot disprove.

The check functions are pure and shape-tolerant (the live read endpoints were
probed to pin their shapes, but the helpers degrade to INCONCLUSIVE rather than
DISPROVED whenever a response can't be confidently interpreted).
"""

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

CONFIRMED = "confirmed"
DISPROVED = "disproved"
INCONCLUSIVE = "inconclusive"

CheckResult = Tuple[str, str]  # (status, human-readable detail)


# ─── helpers ────────────────────────────────────────────────────────────────

def _norm(value: Any) -> str:
    return str(value).strip().lower() if value is not None else ""


def _as_list(data: Any, key: str) -> Optional[list]:
    """Read a list from an execute_step `.data` payload that may be either
    {key: [...]} (response-mapping extracted) or a raw [...] (mapping missed a
    non-wrapped list, so the executor returned the raw body). None if neither."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        v = data.get(key)
        if isinstance(v, list):
            return v
    return None


def _is_set(params: Dict[str, Any], name: str) -> bool:
    """Whether the caller actually supplied a value for `name` (present & not None)."""
    return name in params and params.get(name) is not None


def _numeric_id(value: Any) -> Optional[str]:
    """Return the value as a string iff it looks like a real numeric id, else None.
    (agents.create maps its new id out of the response `message`, which is not
    always the id — guard against matching on a prose message.)"""
    if value is None:
        return None
    s = str(value).strip()
    return s if s.isdigit() and s != "0" else None


def _package_names(read_data: Any) -> Optional[set]:
    """Normalized set of custom-tool package names from tools.list_packages
    ({packages: [...]}), or None if the payload isn't a readable package list.
    (NB: /api/tools/by-category does NOT list custom tools — verified live — so
    tools verification uses the dedicated /api/tools/packages endpoint.)"""
    pkgs = _as_list(read_data, "packages")
    if pkgs is None:
        return None
    return {_norm(p) for p in pkgs}


# ─── per-capability checks ──────────────────────────────────────────────────

def _check_agents_create(params, result_data, read_data) -> CheckResult:
    agents = _as_list(read_data, "agents")
    if agents is None:
        return INCONCLUSIVE, "agents.list returned no readable agent list"
    new_id = _numeric_id(result_data.get("agent_id"))
    want_name = _norm(params.get("agent_description"))
    for a in agents:
        if not isinstance(a, dict):
            continue
        if new_id and str(a.get("agent_id")) == new_id:
            return CONFIRMED, f"agent id {new_id} present in agents.list"
        if want_name and _norm(a.get("agent_name")) == want_name:
            return CONFIRMED, f"agent named {params.get('agent_description')!r} present in agents.list"
    return DISPROVED, (f"created agent (id={result_data.get('agent_id')!r}, "
                       f"name={params.get('agent_description')!r}) not found in agents.list")


def _check_tools_create(params, result_data, read_data) -> CheckResult:
    names = _package_names(read_data)
    if names is None:
        return INCONCLUSIVE, "tools.list_packages returned no readable package list"
    want = _norm(params.get("name"))
    if not want:
        return INCONCLUSIVE, "no tool name in create params to verify against"
    if want in names:
        return CONFIRMED, f"tool {params.get('name')!r} present in custom tool packages"
    return DISPROVED, f"created tool {params.get('name')!r} not found in custom tool packages"


def _check_mcp_create(params, result_data, read_data) -> CheckResult:
    servers = _as_list(read_data, "servers")
    if servers is None:
        return INCONCLUSIVE, "mcp.list_servers returned no readable server list"
    want_name = _norm(params.get("server_name"))
    want_url = _norm(params.get("server_url"))
    for s in servers:
        if not isinstance(s, dict):
            continue
        if want_name and _norm(s.get("server_name")) == want_name:
            return CONFIRMED, f"MCP server {params.get('server_name')!r} present"
        if want_url and _norm(s.get("server_url")) == want_url:
            return CONFIRMED, f"MCP server url {params.get('server_url')!r} present"
    return DISPROVED, (f"created MCP server (name={params.get('server_name')!r}, "
                       f"url={params.get('server_url')!r}) not found in mcp.list_servers")


def _check_email_configure(params, result_data, read_data) -> CheckResult:
    cfg = read_data.get("config") if isinstance(read_data, dict) else None
    if not isinstance(cfg, dict):
        return INCONCLUSIVE, "email.get returned no config object to verify against"

    confirmed: List[str] = []

    # F5 marquee: the inbound workflow trigger. Verify both that the flag took AND
    # that the trigger can actually fire (needs inbound + a workflow_id). This is
    # what catches the endpoint's full-replace clobbering of inbound_enabled.
    if _is_set(params, "workflow_trigger_enabled"):
        want = bool(params.get("workflow_trigger_enabled"))
        got = bool(cfg.get("workflow_trigger_enabled"))
        if got != want:
            return DISPROVED, f"workflow_trigger_enabled={got}, expected {want}"
        confirmed.append(f"workflow_trigger_enabled={got}")
        if want:
            if str(cfg.get("workflow_id") or "") in ("", "None"):
                return DISPROVED, "workflow trigger enabled but no workflow_id is set — it will never fire"
            if _is_set(params, "workflow_id") and str(cfg.get("workflow_id")) != str(params.get("workflow_id")):
                return DISPROVED, (f"workflow_id={cfg.get('workflow_id')}, "
                                   f"expected {params.get('workflow_id')}")
            if not bool(cfg.get("inbound_enabled")):
                return DISPROVED, "workflow trigger enabled but inbound email is disabled — it will never fire"
            confirmed.append(f"workflow_id={cfg.get('workflow_id')}")

    # Other boolean settings the caller explicitly set.
    for f in ("inbound_enabled", "auto_respond_enabled", "is_active",
              "inbox_tools_enabled", "require_approval"):
        if _is_set(params, f):
            want = bool(params.get(f))
            got = bool(cfg.get(f))
            if got != want:
                return DISPROVED, f"{f}={got}, expected {want}"
            confirmed.append(f"{f}={got}")

    if confirmed:
        return CONFIRMED, "; ".join(confirmed)
    return INCONCLUSIVE, "email config saved but no independently-verifiable field was set"


def _check_absent(list_key: str, id_param: str, id_field: str, label: str):
    """Build a delete-verifier: DISPROVED if the entity is still present, else CONFIRMED."""
    def _check(params, result_data, read_data) -> CheckResult:
        items = _as_list(read_data, list_key)
        if items is None:
            return INCONCLUSIVE, f"{label}.list returned no readable list"
        del_id = params.get(id_param)
        if del_id is None:
            return INCONCLUSIVE, f"no {id_param} in delete params"
        for it in items:
            if isinstance(it, dict) and str(it.get(id_field)) == str(del_id):
                return DISPROVED, f"{label} {del_id} is still present after delete"
        return CONFIRMED, f"{label} {del_id} absent after delete"
    return _check


def _check_tools_delete(params, result_data, read_data) -> CheckResult:
    names = _package_names(read_data)
    if names is None:
        return INCONCLUSIVE, "tools.list_packages returned no readable package list"
    want = _norm(params.get("package_name"))
    if not want:
        return INCONCLUSIVE, "no package_name in delete params"
    if want in names:
        return DISPROVED, f"tool {params.get('package_name')!r} still present after delete"
    return CONFIRMED, f"tool {params.get('package_name')!r} absent after delete"


# ─── spec table ─────────────────────────────────────────────────────────────
# capability_id -> {read_capability, read_params(params, result_data)->dict, check}

def _agent_id_params(params, result_data):
    return {"agent_id": params.get("agent_id")}


VERIFICATION_SPECS: Dict[str, Dict[str, Any]] = {
    "agents.create": {
        "read_capability": "agents.list",
        "check": _check_agents_create,
    },
    "tools.create": {
        "read_capability": "tools.list_packages",
        "check": _check_tools_create,
    },
    "mcp.create_server": {
        "read_capability": "mcp.list_servers",
        "check": _check_mcp_create,
    },
    "email.configure": {
        "read_capability": "email.get",
        "read_params": _agent_id_params,
        "check": _check_email_configure,
    },
    # Deletes — verify the entity is actually gone.
    "agents.delete": {
        "read_capability": "agents.list",
        "check": _check_absent("agents", "agent_id", "agent_id", "agent"),
    },
    "tools.delete": {
        "read_capability": "tools.list_packages",
        "check": _check_tools_delete,
    },
    "mcp.delete_server": {
        "read_capability": "mcp.list_servers",
        "check": _check_absent("servers", "server_id", "server_id", "MCP server"),
    },
}
