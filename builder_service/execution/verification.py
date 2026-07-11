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

import json
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

CONFIRMED = "confirmed"
DISPROVED = "disproved"
INCONCLUSIVE = "inconclusive"

# (status, human-readable detail) — checks may optionally return a third
# element: a dict merged into result.data (e.g. workflow_validation state).
CheckResult = Tuple[str, str]


# ─── helpers ────────────────────────────────────────────────────────────────

def _norm(value: Any) -> str:
    return str(value).strip().lower() if value is not None else ""


def _as_list(data: Any, key: str) -> Optional[list]:
    """Read a list from an execute_step `.data` payload that may be either
    {key: [...]} (response-mapping extracted) or a raw [...] (mapping missed a
    non-wrapped list, so the executor returned the raw body). None if neither.

    Several main-app list routes (/api/connections, /get/workflows) return
    jsonify(dataframe_to_json(df)) — a JSON-encoded STRING of a JSON array —
    so a str payload is decoded once before the shape checks."""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (ValueError, TypeError):
            return None
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        v = data.get(key)
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except (ValueError, TypeError):
                return None
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
    # #18: the create endpoint defaults server_type to 'local' when omitted, silently
    # writing a URL server as the wrong type. The schema now defaults server_type='remote'
    # (the action can only express remote servers), so verify the created row is that type
    # — a name/url-only match would otherwise CONFIRM a wrong-type no-op row.
    expected_type = _norm(params.get("server_type") or "remote")
    matched = None
    for s in servers:
        if not isinstance(s, dict):
            continue
        if (want_name and _norm(s.get("server_name")) == want_name) or \
           (want_url and _norm(s.get("server_url")) == want_url):
            matched = s
            break
    if matched is None:
        return DISPROVED, (f"created MCP server (name={params.get('server_name')!r}, "
                           f"url={params.get('server_url')!r}) not found in mcp.list_servers")
    got_type = _norm(matched.get("server_type"))
    if expected_type and got_type and got_type != expected_type:
        return DISPROVED, (f"MCP server {params.get('server_name')!r} was created as "
                           f"'{got_type}' but '{expected_type}' was requested")
    return CONFIRMED, f"MCP server {params.get('server_name')!r} present (type={got_type or 'n/a'})"


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


def _canonical_node_types() -> set:
    """Node types the workflow RUNTIME implements. Prefer the repo-root canon
    (system_prompts.VALID_WORKFLOW_NODE_TYPES — excludes the removed 'Server'
    type); fall back to the compiler's canvas set minus 'Server'."""
    try:
        from system_prompts import VALID_WORKFLOW_NODE_TYPES
        return set(VALID_WORKFLOW_NODE_TYPES)
    except Exception:
        from .workflow_compiler import VALID_NODE_TYPES
        return set(VALID_NODE_TYPES) - {"Server"}


def _workflow_problems(workflow: Any) -> List[str]:
    """Structural certainties only — problems that make a saved workflow
    unrunnable (AIHUB-0016 F1). Anything debatable is NOT flagged: a false
    'draft' on a good workflow is almost as bad as a false 'ready'."""
    if not isinstance(workflow, dict):
        return ["workflow payload is not an object"]
    nodes = workflow.get("nodes") or []
    conns = workflow.get("connections") or []
    if not isinstance(nodes, list) or not nodes:
        return ["workflow has no nodes"]

    problems: List[str] = []
    known = _canonical_node_types()
    node_ids = set()
    for n in nodes:
        if not isinstance(n, dict):
            continue
        node_ids.add(str(n.get("id")))
        ntype = n.get("type")
        if known and ntype and ntype not in known:
            problems.append(
                f"unknown node type '{ntype}' (node {n.get('id')}) — "
                f"the workflow engine cannot execute it"
            )

    connected_ids = set()
    for c in conns:
        if not isinstance(c, dict):
            continue
        # Tolerate both the save format (source/target) and the validator
        # format (from/to).
        src = str(c.get("source", c.get("from", "")) or "")
        dst = str(c.get("target", c.get("to", "")) or "")
        connected_ids.update({src, dst})
        for endpoint in (src, dst):
            if endpoint and endpoint not in node_ids:
                problems.append(
                    f"connection references missing node '{endpoint}'"
                )

    if len(nodes) > 1:
        for n in nodes:
            if isinstance(n, dict) and str(n.get("id")) not in connected_ids:
                problems.append(
                    f"node '{n.get('label') or n.get('id')}' is not connected "
                    f"to the workflow"
                )

    if not any(isinstance(n, dict) and n.get("isStart") for n in nodes):
        problems.append("workflow has no start node")

    return problems


def _check_workflows_create(params, result_data, read_data):
    """Read-back for workflows.create/update (POST /save/workflow). Presence in
    workflows.list decides CONFIRMED/DISPROVED; validation state NEVER flips a
    landed save to DISPROVED — an invalid-but-saved workflow is a DRAFT that
    gates messaging only (F2 semantics, repo-root workflow_compiler.py:899-914).
    Returns a 3-tuple whose extra dict carries workflow_validation."""
    wfs = _as_list(read_data, "workflows")
    if wfs is None:
        return INCONCLUSIVE, "workflows.list returned no readable workflow list", None

    new_id = _numeric_id(result_data.get("workflow_id") or result_data.get("database_version"))
    fn = str(params.get("filename") or "")
    stem = fn[:-5] if fn.lower().endswith(".json") else fn
    want = _norm(stem)

    matched = None
    for w in wfs:
        if not isinstance(w, dict):
            continue
        if new_id and str(w.get("id")) == new_id:
            matched = w
            break
        if want and _norm(w.get("workflow_name")) == want:
            matched = w
            break
    if matched is None:
        return DISPROVED, (
            f"saved workflow (id={new_id!r}, name={stem!r}) not found in workflows.list"
        ), None

    # Validation verdict: prefer the save route's authoritative deterministic
    # validation (present once /save/workflow returns is_valid/saved_as_draft);
    # fall back to the local structural check for older main-app builds.
    if "is_valid" in result_data or "saved_as_draft" in result_data:
        is_valid = bool(result_data.get("is_valid", not result_data.get("saved_as_draft")))
        problems = [str(e) for e in (result_data.get("validation_errors") or [])]
    else:
        problems = _workflow_problems(params.get("workflow"))
        is_valid = not problems

    extra = {"workflow_validation": {"is_valid": is_valid, "problems": problems}}
    label = matched.get("workflow_name") or stem or new_id
    if is_valid:
        return CONFIRMED, f"workflow {label!r} present in workflows.list (valid structure)", extra
    return CONFIRMED, (
        f"workflow {label!r} present but saved as DRAFT — needs fixes: "
        + "; ".join(problems[:5])
    ), extra


def _check_connections_create(params, result_data, read_data):
    """Read-back for connections.create. The real create body is
    {status:'success', response:'<id>'} (no 'connection' object), and the list
    body is a double-encoded dataframe array — both handled here/_as_list."""
    conns = _as_list(read_data, "connections")
    if conns is None:
        return INCONCLUSIVE, "connections.list returned no readable connection list"
    new_id = _numeric_id(result_data.get("connection_id") or result_data.get("response"))
    want = _norm(params.get("name"))
    for c in conns:
        if not isinstance(c, dict):
            continue
        if new_id and str(c.get("id")) == new_id:
            return CONFIRMED, f"connection id {new_id} present in connections.list"
        if want and _norm(c.get("connection_name")) == want:
            return CONFIRMED, f"connection named {params.get('name')!r} present in connections.list"
    return DISPROVED, (
        f"created connection (id={new_id!r}, name={params.get('name')!r}) "
        f"not found in connections.list"
    )


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


def _check_schedules_create(params, result_data, read_data) -> CheckResult:
    """AIHUB-0018 F1: schedule verification previously depended on an
    LLM-planned schedules.get that passed the WRONG id (ScheduledJobId where
    the route wants the workflow TargetId), 404ing schedules that were live
    and firing. This deterministic read-back uses the ids the create was
    actually called with."""
    if not isinstance(read_data, dict):
        return INCONCLUSIVE, "schedules.get returned no readable schedule"
    sched = read_data.get("schedule") if isinstance(read_data.get("schedule"), dict) else read_data
    want_id = str((result_data or {}).get("schedule_id") or (result_data or {}).get("id") or "")
    got_id = str(sched.get("id") or sched.get("schedule_id") or "")
    if want_id and got_id and want_id != got_id:
        return DISPROVED, f"read-back returned schedule {got_id}, expected {want_id}"
    # Only an ACTIVE-requested schedule that reads back inactive is a
    # disproof — a deliberately-paused create (is_active=False) is a success.
    wanted_active = bool(params.get("is_active", True))
    if "is_active" in sched and not sched.get("is_active") and wanted_active:
        return DISPROVED, f"schedule {want_id or got_id} exists but is NOT active"
    if got_id or "is_active" in sched:
        state_word = "active" if sched.get("is_active") else "inactive (as requested)"
        detail = f"schedule {got_id or want_id} present and {state_word}"
        if sched.get("next_run_time"):
            detail += f", next run {sched['next_run_time']}"
        return CONFIRMED, detail
    return INCONCLUSIVE, "schedules.get response shape not recognized"


# ─── spec table ─────────────────────────────────────────────────────────────
# capability_id -> {read_capability, read_params(params, result_data)->dict, check}

def _agent_id_params(params, result_data):
    return {"agent_id": params.get("agent_id")}


def _schedule_read_params(params, result_data):
    # job_id = the WORKFLOW id the create was called with (the by-type
    # scheduler routes resolve job_id as TargetId — never ScheduledJobId).
    return {
        "job_id": params.get("job_id"),
        "schedule_id": (result_data or {}).get("schedule_id") or (result_data or {}).get("id"),
    }


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
    # AIHUB-0016 F1 / AIHUB-0015 F2: workflow and connection writes previously
    # had NO spec — every save degraded to verified=None and CC could only emit
    # the generic "could not be independently verified" for valid AND invalid
    # workflows alike.
    "workflows.create": {
        "read_capability": "workflows.list",
        "check": _check_workflows_create,
    },
    "workflows.update": {
        "read_capability": "workflows.list",
        "check": _check_workflows_create,
    },
    "workflows.delete": {
        # NB list rows key their id as "id", not "workflow_id".
        "read_capability": "workflows.list",
        "check": _check_absent("workflows", "workflow_id", "id", "workflow"),
    },
    "connections.create": {
        "read_capability": "connections.list",
        "check": _check_connections_create,
    },
    "connections.delete": {
        "read_capability": "connections.list",
        "check": _check_absent("connections", "connection_id", "id", "connection"),
    },
    # AIHUB-0018 F1: deterministic schedule read-back with the CORRECT ids —
    # replaces the LLM-planned verify step that passed the ScheduledJobId and
    # falsely reported live schedules as unverified.
    "schedules.create": {
        "read_capability": "schedules.get",
        "read_params": _schedule_read_params,
        "check": _check_schedules_create,
    },
}
