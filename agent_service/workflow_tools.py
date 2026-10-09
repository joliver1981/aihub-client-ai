"""Visual WORKFLOW authoring for The Agent (2026-10-09).

The Agent already builds automations with a draft -> dry-run -> promote loop.
These tools give it the same build -> run -> read -> fix loop for the
drag-and-drop workflows of the Workflow designer, through the platform's own
seams (no new execution paths):

  get_workflow_node_reference  GET  /api/workflow/builder/node-reference
                               (the AI Builder's node reference, with each
                               node's exact config keys)
  list_workflows / get_workflow  /api/workflows/list, /get/workflow/<id>
  save_workflow                POST /save/workflow (the Designer's save; runs the
                               deterministic validator and returns its errors
                               and warnings; a failing workflow saves as a draft)
  run_workflow                 POST /api/workflow/run, then waits for the result
  get_workflow_run_report      GET  /api/workflow/builder/run-report

Saving never overwrites another workflow unless replace_existing=true; running
is real (files move, emails go out) and the brain prompt requires the user's
go-ahead first.

Access: the main app trusts The Agent's API key on these routes, so the role
check lives here — the same Developer bar as the Workflow designer itself
(/get/workflow and /save/workflow are min_role=2 for signed-in users). Only the
node reference (static documentation) is open to everyone.
"""

import asyncio
import json
import os
import time
from typing import Any, Optional

from claude_agent_sdk import tool

from platform_tools import _text, _post, _get
from authoring_tools import _authoring_allowed, _user_context

_DENIED = ("Workflows (reading, building, changing or running them) need a Developer role "
           "on this install, the same as the Workflow designer.")

# How long run_workflow waits in one call before handing back the execution id
# (the run itself keeps going; get_workflow_run_report reads it later). A
# responsiveness budget for one chat turn, not a limit on the run.
RUN_WAIT_SECONDS = float(os.getenv("WORKFLOW_RUN_WAIT_SECONDS", "600"))
_TERMINAL = {"completed", "failed", "cancelled", "error"}


async def _workflow_rows() -> list:
    data = await _get("/api/workflows/list")
    if isinstance(data, dict):
        data = data.get("workflows") or data.get("data") or []
    return data if isinstance(data, list) else []


async def _resolve(ref: str) -> tuple[Optional[int], Optional[str], Optional[str]]:
    """(id, name, error) for a workflow id or exact name (case-insensitive)."""
    ref = str(ref or "").strip()
    if not ref:
        return None, None, "Name the workflow (its id or exact name)."
    rows = await _workflow_rows()
    if ref.isdigit():
        for r in rows:
            if str(r.get("id")) == ref:
                return int(r["id"]), r.get("workflow_name"), None
        return None, None, f"No workflow with id {ref}."
    exact = [r for r in rows if str(r.get("workflow_name") or "").lower() == ref.lower()]
    if len(exact) == 1:
        return int(exact[0]["id"]), exact[0].get("workflow_name"), None
    near = [r.get("workflow_name") for r in rows if ref.lower() in str(r.get("workflow_name") or "").lower()]
    hint = f" Did you mean: {', '.join(near[:8])}?" if near else ""
    return None, None, f"No workflow named '{ref}'.{hint}"


def _normalise_definition(defn: dict, user_id: int = 0) -> tuple[Optional[dict], Optional[str]]:
    """Accept the Designer's save shape, plus the common authoring variants
    (from/to connections, missing positions/anchors), and return the shape
    /save/workflow stores. Other top-level keys are kept as they are."""
    if not isinstance(defn, dict):
        return None, "definition_json must be a JSON object with nodes and connections."
    nodes = defn.get("nodes")
    conns = defn.get("connections", [])
    if not isinstance(nodes, list) or not nodes:
        return None, "definition_json needs a non-empty 'nodes' list."
    if not isinstance(conns, list):
        return None, "'connections' must be a list."
    out_nodes = []
    for i, n in enumerate(nodes):
        if not isinstance(n, dict) or not n.get("id") or not n.get("type"):
            return None, f"node #{i + 1} needs at least 'id' and 'type'."
        n = dict(n)
        n.setdefault("label", n.get("type"))
        if not isinstance(n.get("config"), dict):
            n["config"] = {}
        n.setdefault("isStart", False)
        if not isinstance(n.get("position"), dict):
            n["position"] = {"left": f"{60 + (i % 8) * 220}px", "top": f"{120 + (i // 8) * 170}px"}
        # /save/workflow stamps a session saver's id into Portal nodes (portal
        # credentials are per user); an API-key save has no session, so stamp
        # the user this turn runs for.
        if n["type"] == "Portal" and user_id and not n["config"].get("ownerUserId"):
            n["config"] = {**n["config"], "ownerUserId": str(user_id)}
        out_nodes.append(n)
    if len({n["id"] for n in out_nodes}) != len(out_nodes):
        return None, "two nodes share the same id; every node id must be unique."
    ids = {n["id"] for n in out_nodes}
    out_conns = []
    for j, c in enumerate(conns):
        if not isinstance(c, dict):
            return None, f"connection #{j + 1} must be an object."
        src = c.get("source", c.get("from"))
        dst = c.get("target", c.get("to"))
        if src not in ids or dst not in ids:
            return None, f"connection #{j + 1} refers to a node id that does not exist ({src} -> {dst})."
        out_conns.append({"source": src, "target": dst,
                          "type": c.get("type") or c.get("connection_type") or "pass",
                          "sourceAnchor": c.get("sourceAnchor", "Right"),
                          "targetAnchor": c.get("targetAnchor", "Left")})
    variables = defn.get("variables") or {}
    if isinstance(variables, list):  # [{"name","type","defaultValue"}] -> {name: {...}}
        variables = {v.get("name"): v for v in variables if isinstance(v, dict) and v.get("name")}
    if not isinstance(variables, dict):
        return None, "'variables' must be an object {name: {name, type, defaultValue}}."
    for k, v in list(variables.items()):
        if not isinstance(v, dict):
            variables[k] = {"name": k, "type": "string", "defaultValue": v}
        else:
            v.setdefault("name", k)
            v.setdefault("type", "string")
    out = dict(defn)
    out.update(nodes=out_nodes, connections=out_conns, variables=variables)
    return out, None


@tool(
    "get_workflow_node_reference",
    "The node reference for building VISUAL WORKFLOWS (the Workflow designer): every node "
    "type, what it does, its EXACT config keys, and the general authoring rules (route "
    "failures through FAIL connections, settings as workflow variables, …). Read it before "
    "designing or changing a workflow. node_types: 'all' (default, includes the overview) "
    "or a comma-separated list such as 'Folder Selector, Loop, AI Extract'.",
    {"type": "object",
     "properties": {"node_types": {"type": "string"}},
     "additionalProperties": False},
)
async def get_workflow_node_reference(args: dict[str, Any]) -> dict[str, Any]:
    from urllib.parse import quote
    types = str(args.get("node_types") or "all")
    try:
        data = await _get(f"/api/workflow/builder/node-reference?types={quote(types)}")
    except Exception as e:
        return _text(f"Could not read the node reference: {e}", is_error=True)
    if not isinstance(data, dict) or data.get("status") != "success":
        return _text(f"Could not read the node reference: {data}", is_error=True)
    parts = []
    if data.get("overview"):
        parts.append("OVERVIEW\n" + data["overview"])
    parts.append("NODE DETAILS\n" + str(data.get("details") or ""))
    parts.append("Valid node types: " + ", ".join(data.get("node_types") or []))
    return _text("\n\n".join(parts))


@tool(
    "list_workflows",
    "List the platform's visual workflows (id and name). Optional search filters by a "
    "case-insensitive part of the name.",
    {"type": "object",
     "properties": {"search": {"type": "string"}},
     "additionalProperties": False},
)
async def list_workflows(args: dict[str, Any]) -> dict[str, Any]:
    if not _authoring_allowed():
        return _text(_DENIED, is_error=True)
    try:
        rows = await _workflow_rows()
    except Exception as e:
        return _text(f"Could not list workflows: {e}", is_error=True)
    q = str(args.get("search") or "").strip().lower()
    rows = [r for r in rows if not q or q in str(r.get("workflow_name") or "").lower()]
    if not rows:
        return _text("No workflows match." if q else "There are no workflows yet.")
    lines = [f"- {r.get('workflow_name')} (id {r.get('id')})" for r in rows]
    return _text(f"{len(rows)} workflow(s):\n" + "\n".join(lines))


@tool(
    "get_workflow",
    "Read a visual workflow's full definition (nodes with their config, connections, "
    "workflow variables) by id or exact name. ALWAYS read the current definition before "
    "changing a workflow: save_workflow replaces the whole definition.",
    {"type": "object",
     "properties": {"workflow": {"type": "string", "description": "Workflow id or exact name"}},
     "required": ["workflow"], "additionalProperties": False},
)
async def get_workflow(args: dict[str, Any]) -> dict[str, Any]:
    if not _authoring_allowed():
        return _text(_DENIED, is_error=True)
    try:
        wid, name, err = await _resolve(args.get("workflow"))
    except Exception as e:
        return _text(f"Could not look up workflows: {e}", is_error=True)
    if err:
        return _text(err, is_error=True)
    try:
        defn = await _get(f"/get/workflow/{wid}")
    except Exception as e:
        return _text(f"Could not read workflow {wid}: {e}", is_error=True)
    return _text(f"Workflow '{name}' (id {wid}):\n" + json.dumps(defn, indent=1, ensure_ascii=False))


@tool(
    "save_workflow",
    "Create or replace a visual workflow (shown in the Workflow designer, editable there). "
    "definition_json = {\"nodes\": [{id, type, label, config, isStart}], \"connections\": "
    "[{source, target, type: pass|fail|complete}], \"variables\": {name: {name, type, "
    "defaultValue}}} — positions are added if missing. The platform validates on save and "
    "returns errors and warnings: fix EVERY one and save again (a workflow with errors is "
    "kept as a draft and must not be called ready). An existing workflow of the same name is "
    "only replaced with replace_existing=true — read it with get_workflow first.",
    {"type": "object",
     "properties": {
         "name": {"type": "string"},
         "definition_json": {"type": "string"},
         "replace_existing": {"type": "boolean"},
     },
     "required": ["name", "definition_json"], "additionalProperties": False},
)
async def save_workflow(args: dict[str, Any]) -> dict[str, Any]:
    if not _authoring_allowed():
        return _text(_DENIED, is_error=True)
    name = str(args.get("name") or "").strip()
    if not name:
        return _text("Give the workflow a name.", is_error=True)
    if name.lower().endswith(".json"):
        name = name[:-5]
    try:
        defn = json.loads(args.get("definition_json") or "")
    except Exception as e:
        return _text(f"definition_json is not valid JSON: {e}", is_error=True)
    workflow, err = _normalise_definition(defn, _user_context()["user_id"])
    if err:
        return _text(f"Not saved: {err}", is_error=True)
    try:
        rows = await _workflow_rows()
    except Exception as e:
        return _text(f"Not saved: could not check existing workflows ({e}).", is_error=True)
    existing = [r for r in rows if str(r.get("workflow_name") or "").lower() == name.lower()]
    if existing and not args.get("replace_existing"):
        return _text(f"Not saved: a workflow named '{existing[0].get('workflow_name')}' already exists "
                     f"(id {existing[0].get('id')}). Read it with get_workflow and save again with "
                     "replace_existing=true to change it, or choose another name.", is_error=True)
    if existing:
        name = existing[0].get("workflow_name") or name  # the stored name is the key
    try:
        data, status = await _post("/save/workflow", {"filename": f"{name}.json", "workflow": workflow},
                                   timeout=120)
    except Exception as e:
        return _text(f"Not saved: could not reach the platform ({e}). Do NOT tell the user it was saved.",
                     is_error=True)
    if status >= 400 or not isinstance(data, dict) or data.get("status") != "success":
        detail = (data.get("message") or data.get("error")) if isinstance(data, dict) else data
        return _text(f"Not saved (HTTP {status}): {detail}. Do NOT tell the user it was saved.", is_error=True)
    wid = data.get("workflow_id")
    errors = data.get("validation_errors") or []
    warnings = data.get("validation_warnings") or []
    # Read-back: the stored definition must hold what was sent.
    try:
        stored = await _get(f"/get/workflow/{wid}")
        got = {n.get("id") for n in (stored.get("nodes") or [])}
        want = {n["id"] for n in workflow["nodes"]}
        n_conn = len(stored.get("connections") or [])
        if got == want and n_conn == len(workflow["connections"]):
            readback = "verified by read-back"
        else:
            readback = (f"READ-BACK MISMATCH: stored {len(got)} nodes / {n_conn} connections, sent "
                        f"{len(want)} / {len(workflow['connections'])} — do not call it saved as intended")
    except Exception as e:
        readback = f"read-back not possible ({e})"
    head = (f"Saved workflow '{name}' (id {wid}, {len(workflow['nodes'])} nodes, "
            f"{len(workflow['connections'])} connections; {readback}). The user finds it on the "
            f"Playbooks screen, whose link opens it in the Workflow designer.")
    if errors:
        head += ("\nSAVED AS A DRAFT — it is not runnable as intended until these errors are fixed:\n"
                 + "\n".join(f"  - {e}" for e in errors))
    if warnings:
        head += ("\nWarnings to fix (an ignored key or a wrong value means the engine will not do what "
                 "you intended):\n" + "\n".join(f"  - {w}" for w in warnings))
    if not errors and not warnings:
        head += "\nValidation: no errors, no warnings."
    return _text(head)


async def _report_text(execution_id: str) -> tuple[str, Optional[dict]]:
    try:
        rep = await _get(f"/api/workflow/builder/run-report?execution_id={execution_id}&examples=5")
    except Exception as e:
        return f"(run report not available: {e})", None
    if not isinstance(rep, dict) or rep.get("status") == "error":
        return f"(run report not available: {rep})", None
    return str(rep.get("text") or ""), rep


@tool(
    "run_workflow",
    "Run a saved visual workflow FOR REAL (it reads and moves files, writes outputs, can send "
    "emails and approvals) — only when the user asked to run or test it. Waits for the run to "
    "finish (up to wait_seconds, default from the install) and returns the run report: "
    "per-step outcomes, errors, warnings and ATTENTION flags. Fix what the report shows, save, "
    "and run again. A run still going when the wait ends keeps going: read it later with "
    "get_workflow_run_report. variables_json overrides workflow variables for THIS run only, "
    "e.g. {\"inputFolder\": \"C:\\\\test\\\\in\"} to test against a test folder without changing "
    "the saved workflow.",
    {"type": "object",
     "properties": {
         "workflow": {"type": "string", "description": "Workflow id or exact name"},
         "variables_json": {"type": "string",
                            "description": "Optional JSON object {variableName: value} for this run"},
         "wait_seconds": {"type": "integer"},
     },
     "required": ["workflow"], "additionalProperties": False},
)
async def run_workflow(args: dict[str, Any]) -> dict[str, Any]:
    if not _authoring_allowed():
        return _text(_DENIED, is_error=True)
    try:
        wid, name, err = await _resolve(args.get("workflow"))
    except Exception as e:
        return _text(f"Could not look up workflows: {e}", is_error=True)
    if err:
        return _text(err, is_error=True)
    body: dict[str, Any] = {"workflow_id": wid}
    if args.get("variables_json"):
        try:
            overrides = json.loads(args["variables_json"])
        except Exception as e:
            return _text(f"variables_json is not valid JSON: {e}", is_error=True)
        if not isinstance(overrides, dict):
            return _text("variables_json must be a JSON object {variableName: value}.", is_error=True)
        body["variables"] = overrides
    u = _user_context()
    body["initiator"] = f"the-agent:{u['username'] or u['user_id']}"
    try:
        data, status = await _post("/api/workflow/run", body, timeout=60)
    except Exception as e:
        return _text(f"The run did not start (could not reach the platform: {e}). "
                     "Do NOT tell the user it ran.", is_error=True)
    eid = data.get("execution_id") if isinstance(data, dict) else None
    if status >= 400 or not eid:
        detail = (data.get("message") or data.get("error")) if isinstance(data, dict) else data
        return _text(f"The run did not start (HTTP {status}): {detail}. Do NOT tell the user it ran.",
                     is_error=True)
    try:
        wait = float(args.get("wait_seconds") or RUN_WAIT_SECONDS)
    except (TypeError, ValueError):
        wait = RUN_WAIT_SECONDS
    deadline = time.monotonic() + max(5.0, wait)
    st, read_errors, last_error = "running", 0, ""
    while time.monotonic() < deadline:
        await asyncio.sleep(5)
        try:
            ex = await _get(f"/api/workflow/executions/{eid}")
            st = str((ex.get("execution") or ex).get("status") or "").lower()
            read_errors = 0
        except Exception as e:
            read_errors, last_error = read_errors + 1, str(e)
            if read_errors >= 5:  # the status endpoint itself is failing; stop waiting
                break
            continue
        if st in _TERMINAL or st == "paused":
            break
    text, _ = await _report_text(eid)
    if st not in _TERMINAL and st != "paused":
        why = (f"its status could not be read ({last_error})" if read_errors >= 5
               else "it was still running when the wait ended")
        return _text(f"Workflow '{name}' (id {wid}) run {eid} started; {why}. It keeps running — read "
                     f"the outcome later with get_workflow_run_report. So far:\n\n{text}")
    note = " It is PAUSED waiting for a Human Approval (My Approvals)." if st == "paused" else ""
    return _text(f"Workflow '{name}' (id {wid}) run {eid} finished with status {st}.{note}\n\n{text}")


@tool(
    "get_workflow_run_report",
    "The run report of one workflow execution: per-step outcome counts, real error messages, "
    "warnings, loop item counts and ATTENTION flags for silent failures (0 files found, every "
    "item down a failure path, …). Use it after run_workflow, or for a run the user started.",
    {"type": "object",
     "properties": {"execution_id": {"type": "string"}},
     "required": ["execution_id"], "additionalProperties": False},
)
async def get_workflow_run_report(args: dict[str, Any]) -> dict[str, Any]:
    if not _authoring_allowed():
        return _text(_DENIED, is_error=True)
    eid = str(args.get("execution_id") or "").strip()
    if not eid:
        return _text("Give the execution id.", is_error=True)
    text, rep = await _report_text(eid)
    if rep is None:
        return _text(text, is_error=True)
    return _text(text)


WORKFLOW_TOOLS = [get_workflow_node_reference, list_workflows, get_workflow,
                  save_workflow, run_workflow, get_workflow_run_report]
