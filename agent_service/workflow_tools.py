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
  schedule_workflow / list_workflow_schedules / cancel_workflow_schedule
                               the Workflow Monitor's scheduler routes
                               (/api/scheduler/.../types/workflow/schedules)

Saving never overwrites another workflow unless replace_existing=true; running
is real (files move, emails go out) and the brain prompt requires the user's
go-ahead first. Scheduling never changes or removes an existing schedule.

ONLY VISUAL WORKFLOWS (2026-10-09). "Workflow" names four things on this
platform: visual workflows, Code Flows (stored in the SAME Workflows table and
scheduled through the SAME 'workflow' job type), recorded portal workflows and
automations. Every tool here resolves visual workflows only: a Code Flow is
refused with the code-flow tools named, and a name that belongs to a portal
workflow or an automation is answered with the tool that owns it — so a wrong
tool choice corrects itself instead of acting on the wrong thing.

Access: the main app trusts The Agent's API key on these routes, so the role
check lives here — the same Developer bar as the Workflow designer itself
(/get/workflow and /save/workflow are min_role=2 for signed-in users). Only the
node reference (static documentation) is open to everyone.
"""

import asyncio
import json
import os
import re
import time
from typing import Any, Optional

from claude_agent_sdk import tool

from platform_tools import CURRENT_USER, _text, _post, _get
from authoring_tools import _authoring_allowed, _user_context

_DENIED = ("Workflows (reading, building, changing or running them) need a Developer role "
           "on this install, the same as the Workflow designer.")

# How long run_workflow waits in one call before handing back the execution id
# (the run itself keeps going; get_workflow_run_report reads it later). A
# responsiveness budget for one chat turn, not a limit on the run.
RUN_WAIT_SECONDS = float(os.getenv("WORKFLOW_RUN_WAIT_SECONDS", "600"))
_TERMINAL = {"completed", "failed", "cancelled", "error"}


async def _workflow_rows() -> list:
    """Every row of the Workflows table: {id, workflow_name, kind, ...} where
    kind is 'workflow' (visual) or 'code_flow'."""
    data = await _get("/api/workflows/list")
    if isinstance(data, dict):
        data = data.get("workflows") or data.get("data") or []
    return data if isinstance(data, list) else []


def _is_code_flow(row: dict) -> bool:
    return str(row.get("kind") or "") == "code_flow"


def _code_flow_refusal(name: str) -> str:
    return (f"'{name}' is a CODE FLOW (Python steps), not a visual workflow — use the "
            "code-flow tools instead (get_code_flow, run_code_flow, schedule_code_flow).")


async def _other_kind_hint(ref: str) -> str:
    """For a name that is not a visual workflow: which OTHER kind of thing has
    that name, and the tool that owns it."""
    hints = []
    try:
        from portal_tools import _uid
        from command_center.tools import portal_workflows as wf_store
        if await asyncio.to_thread(wf_store.get_workflow, _uid(), ref):
            hints.append(f"'{ref}' is a recorded PORTAL workflow (a browser replay) — use "
                         "run_portal_workflow / schedule_portal_workflow / "
                         "cancel_portal_workflow_schedule")
    except Exception:
        pass
    try:
        from authoring_tools import _resolve_automation
        auto_id, auto_err = await _resolve_automation(ref)
        if auto_id and not auto_err:
            hints.append(f"'{ref}' is an AUTOMATION — use run_automation / schedule_automation")
    except Exception:
        pass
    return (" " + "; ".join(hints) + ".") if hints else ""


async def workflow_kind_of(name: str) -> Optional[str]:
    """'workflow' | 'code_flow' | None for an exact (case-insensitive) name —
    for the OTHER schedule tools' "not found" answers."""
    try:
        rows = await _workflow_rows()
    except Exception:
        return None
    for r in rows:
        if str(r.get("workflow_name") or "").lower() == str(name or "").strip().lower():
            return "code_flow" if _is_code_flow(r) else "workflow"
    return None


async def _resolve(ref: str) -> tuple[Optional[int], Optional[str], Optional[str]]:
    """(id, name, error) for a VISUAL workflow's id or exact name
    (case-insensitive). Code Flows are refused; names owned by a portal
    workflow or an automation are answered with the right tool."""
    ref = str(ref or "").strip()
    if not ref:
        return None, None, "Name the workflow (its id or exact name)."
    rows = await _workflow_rows()
    if ref.isdigit():
        for r in rows:
            if str(r.get("id")) == ref:
                if _is_code_flow(r):
                    return None, None, _code_flow_refusal(r.get("workflow_name") or ref)
                return int(r["id"]), r.get("workflow_name"), None
        return None, None, f"No workflow with id {ref}."
    exact = [r for r in rows if str(r.get("workflow_name") or "").lower() == ref.lower()]
    visual = [r for r in exact if not _is_code_flow(r)]
    if len(visual) == 1:
        return int(visual[0]["id"]), visual[0].get("workflow_name"), None
    if exact and not visual:
        return None, None, _code_flow_refusal(exact[0].get("workflow_name") or ref)
    near = [r.get("workflow_name") for r in rows
            if not _is_code_flow(r) and ref.lower() in str(r.get("workflow_name") or "").lower()]
    hint = f" Visual workflows with similar names: {', '.join(near[:8])}." if near else ""
    return None, None, f"No visual workflow named '{ref}'.{hint}{await _other_kind_hint(ref)}"


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


_CONTRACT_RE = re.compile(r"EXACT CONFIG KEYS for (.+?) \(the engine[^)]*\): (.*)(?:\n  Required: (.*))?")


@tool(
    "get_workflow_node_reference",
    "The node reference for building VISUAL WORKFLOWS (the Workflow designer). Without "
    "node_types: an overview of every node type, the general authoring rules (route failures "
    "through FAIL connections, settings as workflow variables, …) and each node's EXACT config "
    "keys. With node_types (comma-separated, e.g. 'Folder Selector, Loop, AI Extract'): the "
    "full settings documentation of those types. Read the overview first, then the details "
    "of the types you will use. Never use a config key that is not listed for its type.",
    {"type": "object",
     "properties": {"node_types": {"type": "string"}},
     "additionalProperties": False},
)
async def get_workflow_node_reference(args: dict[str, Any]) -> dict[str, Any]:
    from urllib.parse import quote
    types = str(args.get("node_types") or "").strip()
    overview_mode = not types or types.lower() in ("all", "overview")
    try:
        data = await _get("/api/workflow/builder/node-reference?types="
                          + quote("all" if overview_mode else types))
    except Exception as e:
        return _text(f"Could not read the node reference: {e}", is_error=True)
    if not isinstance(data, dict) or data.get("status") != "success":
        return _text(f"Could not read the node reference: {data}", is_error=True)
    details = str(data.get("details") or "")
    parts = []
    if overview_mode:
        # The full details of every type (~40 KB) are too long for one tool
        # result; the overview plus each type's exact keys is what planning
        # needs, and the details come per type on the next call.
        contract = _CONTRACT_RE.findall(details)
        parts.append("OVERVIEW\n" + str(data.get("overview") or ""))
        if contract:
            parts.append("EXACT CONFIG KEYS PER NODE TYPE (the engine ignores any other key):\n"
                         + "\n".join(f"- {t}: {k}" + (f"  [required: {r}]" if r else "")
                                     for t, k, r in contract))
            parts.append("Next: call get_workflow_node_reference with node_types = the types you "
                         "will use, for their full settings (values, formats, examples).")
        else:
            parts.append("NODE DETAILS\n" + details)
    else:
        parts.append("NODE DETAILS\n" + details)
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
    code_flows = [r for r in rows if _is_code_flow(r)]
    rows = [r for r in rows if not _is_code_flow(r)]
    left_out = (f"\n({len(code_flows)} Code Flow(s) {'match' if q else 'exist'} too; they are not "
                "visual workflows — list_code_flows shows them.)") if code_flows else ""
    if not rows:
        return _text(("No visual workflows match." if q else "There are no visual workflows yet.")
                     + left_out)
    lines = [f"- {r.get('workflow_name')} (id {r.get('id')})" for r in rows]
    return _text(f"{len(rows)} visual workflow(s):\n" + "\n".join(lines) + left_out)


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
    if existing and _is_code_flow(existing[0]):
        return _text(f"Not saved: '{existing[0].get('workflow_name')}' is the name of a CODE FLOW. "
                     "Choose another name for the visual workflow (Code Flows are changed with "
                     "the code-flow tools).", is_error=True)
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


# ─────────────────────────────────────────────── scheduling (2026-10-09)
# Through the Workflow Monitor's own routes, so what The Agent schedules is
# listed, edited and removed on that page like any other schedule. A cron's
# times are the user's local times: the zone is stored with that one schedule
# (the scheduler reads it before anything else) — the workflow's other
# schedules are never touched.

# How long schedule_workflow waits for the scheduler to compute the first run
# (it re-reads schedules about once a minute). Responsiveness, not a limit.
SCHEDULE_CONFIRM_SECONDS = float(os.getenv("WORKFLOW_SCHEDULE_CONFIRM_SECONDS", "150"))


def _parse_utc(value):
    """Naive-UTC datetime from the scheduler's ISO text, or None."""
    import datetime as _dt
    if not value:
        return None
    try:
        return _dt.datetime.fromisoformat(str(value).replace("Z", "")).replace(tzinfo=None)
    except ValueError:
        return None


async def _workflow_schedules(wid: Optional[int] = None) -> list:
    """Schedules of visual workflows (Code Flow schedules share the job type and
    are left out), optionally of one workflow."""
    rows = await _get("/api/scheduler/types/workflow/schedules")
    rows = [r for r in (rows if isinstance(rows, list) else []) if r.get("workflow_kind") != "code_flow"]
    if wid is not None:
        rows = [r for r in rows if str(r.get("workflow_id")) == str(wid)]
    return rows


def _schedule_cadence(s: dict, zone: str) -> str:
    from work_tools import fmt_local
    kind = s.get("type")
    if kind == "cron":
        return f"cron `{s.get('cron_expression')}` in {s.get('timezone') or 'UTC'}"
    if kind == "interval":
        parts = [f"{s[k]} {k.split('_', 1)[1]}" for k in
                 ("interval_weeks", "interval_days", "interval_hours", "interval_minutes",
                  "interval_seconds") if s.get(k)]
        return "every " + ", ".join(parts) if parts else "interval"
    if kind == "date":
        return "once at " + (fmt_local(_parse_utc(s.get("start_date")), zone) or "?")
    return str(kind or "?")


def _schedule_line(s: dict, zone: str) -> str:
    from work_tools import fmt_local
    nxt = fmt_local(_parse_utc(s.get("next_run_time")), zone) or "not computed yet"
    last = fmt_local(_parse_utc(s.get("last_run_time")), zone)
    runs = f"{s.get('current_runs') or 0}" + (f" of {s['max_runs']}" if s.get("max_runs") else "")
    end = fmt_local(_parse_utc(s.get("end_date")), zone)
    return (f"- schedule #{s.get('id')} for '{s.get('workflow_name')}' "
            f"({'active' if s.get('is_active') else 'INACTIVE'}): {_schedule_cadence(s, zone)}; "
            f"next run {nxt}" + (f"; last run {last}" if last else "") + f"; runs so far {runs}"
            + (f"; ends {end}" if end else ""))


def _cron_time_mismatch(cron: str, next_run_utc, zone: str) -> Optional[str]:
    """For a cron with a fixed minute and hour: the scheduler's next run must
    land on that wall-clock time in `zone`. Returns the local HH:MM it actually
    lands on when it does not (a zone the scheduler did not apply)."""
    import datetime as _dt
    from work_tools import _zone_tzinfo
    parts = str(cron or "").split()
    if len(parts) != 5 or not parts[0].isdigit() or not parts[1].isdigit() or next_run_utc is None:
        return None
    local = next_run_utc.replace(tzinfo=_dt.timezone.utc).astimezone(_zone_tzinfo(zone))
    if (local.hour, local.minute) != (int(parts[1]), int(parts[0])):
        return local.strftime("%H:%M")
    return None


async def _delete(path: str):
    import httpx
    from platform_tools import _headers
    from agent_config import get_base_url
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.delete(f"{get_base_url()}{path}", headers=_headers())
        try:
            data = r.json()
        except Exception:
            data = {"error": r.text[:300]}
        return data, r.status_code


async def _await_next_run(wid: int, sid, seconds: float) -> Optional[dict]:
    """The schedule row once the scheduler has computed its next run (or the
    latest row when the wait ends first)."""
    deadline = time.monotonic() + max(0.0, seconds)
    row = None
    while True:
        rows = await _workflow_schedules(wid)
        row = next((r for r in rows if str(r.get("id")) == str(sid)), None)
        if row is None or row.get("next_run_time") or time.monotonic() >= deadline:
            return row
        await asyncio.sleep(10)


@tool(
    "schedule_workflow",
    "Schedule a VISUAL workflow (one built in the Workflow Designer) to run automatically. "
    "NOT for Code Flows (schedule_code_flow), recorded portal workflows "
    "(schedule_portal_workflow) or automations (schedule_automation) — given one of those "
    "names it refuses and names the right tool. Give cron_expression (5 fields, in the "
    "user's LOCAL time; pass timezone only when they name another zone), OR every_minutes / "
    "every_hours / every_days, OR run_at 'YYYY-MM-DD HH:MM' (local) for a single run; "
    "for_minutes or occurrences bound a repeat. It NEVER changes or removes existing "
    "schedules: when the workflow already has one it lists them and stops — ask the user, "
    "then call again with add_alongside=true to add another (cancel_workflow_schedule "
    "removes one). Each run is real, like run_workflow. Report ONLY the schedule id and the "
    "next run this returns.",
    {"type": "object",
     "properties": {
         "workflow": {"type": "string", "description": "Visual workflow id or exact name"},
         "cron_expression": {"type": "string",
                             "description": "5-field cron in the user's local time"},
         "timezone": {"type": "string",
                      "description": "Only when the user names a zone: IANA name, alias "
                                     "(Eastern), 'UTC' or '-05:00'"},
         "every_minutes": {"type": "integer"},
         "every_hours": {"type": "integer"},
         "every_days": {"type": "integer"},
         "run_at": {"type": "string",
                    "description": "One run at this local date-time, 'YYYY-MM-DD HH:MM'"},
         "for_minutes": {"type": "integer", "description": "Bound: stop this many minutes from now"},
         "occurrences": {"type": "integer", "description": "Bound: stop after this many runs"},
         "add_alongside": {"type": "boolean",
                           "description": "true only after the user agreed to ADD a schedule "
                                          "next to the existing one(s)"},
     },
     "required": ["workflow"], "additionalProperties": False},
)
async def schedule_workflow(args: dict[str, Any]) -> dict[str, Any]:
    if not _authoring_allowed():
        return _text(_DENIED, is_error=True)
    import datetime as _dt
    from work_tools import (_build_schedule, default_zone_label, fmt_local,
                            _cadence_text, _bound_text, _bound_was_recorded)
    try:
        wid, name, err = await _resolve(args.get("workflow"))
    except Exception as e:
        return _text(f"Could not look up workflows: {e}", is_error=True)
    if err:
        return _text(err, is_error=True)
    now = _dt.datetime.utcnow()
    zone, zone_src = default_zone_label(CURRENT_USER.get() or {})
    try:
        plan = _build_schedule(args, now=now, default_tz=zone, default_src=zone_src)
    except ValueError as e:
        return _text(f"Nothing was scheduled: {e}", is_error=True)
    zone = plan.get("display_tz") or zone

    try:
        existing = [s for s in await _workflow_schedules(wid) if s.get("is_active")]
    except Exception as e:
        return _text(f"Nothing was scheduled: could not read the workflow's existing schedules ({e}).",
                     is_error=True)
    if existing and not args.get("add_alongside"):
        return _text(f"Nothing was scheduled: '{name}' already has {len(existing)} active schedule(s):\n"
                     + "\n".join(_schedule_line(s, zone) for s in existing)
                     + "\nThis tool never changes or removes a schedule. Ask the user whether to ADD "
                       "the new one alongside (call again with add_alongside=true) or to remove one "
                       "first (cancel_workflow_schedule).", is_error=True)

    body = dict(plan["schedule"])
    body["is_active"] = True
    if plan["kind"] == "cron":
        body["timezone"] = plan["tz_label"]
    try:
        data, status = await _post(f"/api/scheduler/jobs/{wid}/types/workflow/schedules", body, timeout=60)
    except Exception as e:
        return _text(f"Nothing was scheduled (could not reach the scheduler: {e}). "
                     "Do NOT tell the user it was scheduled.", is_error=True)
    sid = data.get("id") if isinstance(data, dict) else None
    if status >= 400 or not sid:
        detail = (data.get("error") or data.get("message")) if isinstance(data, dict) else data
        return _text(f"Nothing was scheduled (HTTP {status}): {detail}. Do NOT tell the user it "
                     "was scheduled.", is_error=True)

    # Read-back: the row exists, is active, and carries the zone and bounds asked for.
    rows = await _workflow_schedules(wid)
    row = next((r for r in rows if str(r.get("id")) == str(sid)), None)
    problem = None
    if row is None or not row.get("is_active"):
        problem = "no active schedule row was found after saving"
    elif plan["kind"] == "cron" and (row.get("timezone") or "") != plan["tz_label"]:
        problem = (f"the scheduler recorded zone '{row.get('timezone') or 'UTC'}' instead of "
                   f"'{plan['tz_label']}'")
    elif not _bound_was_recorded(plan, [row]):
        problem = "the requested end (for_minutes / occurrences) was not recorded"
    if problem:
        await _delete(f"/api/scheduler/jobs/{wid}/types/workflow/schedules/{sid}")
        return _text(f"NOT scheduled: schedule #{sid} was created but {problem}, so it was removed "
                     "again. Tell the user it was not scheduled.", is_error=True)

    # The scheduler's own word on the first run (it re-reads schedules about once a minute).
    row = await _await_next_run(wid, sid, SCHEDULE_CONFIRM_SECONDS) or row
    next_run = _parse_utc(row.get("next_run_time"))
    cadence = _cadence_text(plan) if plan["kind"] != "one_shot" else \
        "once at " + fmt_local(plan.get("first_run_at"), zone)
    bound = _bound_text(plan, now) if plan["kind"] == "interval" or plan.get("end_at") else ""
    text = (f"Scheduled visual workflow '{name}' (id {wid}): schedule #{sid}, {cadence}"
            + (f", {bound}" if bound else "") + ".")
    if next_run:
        text += f" Next run, as computed by the scheduler: {fmt_local(next_run, zone, '%a %Y-%m-%d %H:%M')}."
        if plan["kind"] == "cron":
            off = _cron_time_mismatch(plan["local_cron"], next_run, plan["tz_label"])
            if off:
                text += (f" WARNING: that lands at {off} {plan['tz_label']}, not at the requested "
                         "time — tell the user the schedule needs checking on the Workflow Monitor page.")
    else:
        text += (" The scheduler has not computed the first run yet (it re-reads schedules about "
                 "once a minute); list_workflow_schedules shows it shortly.")
    text += (plan.get("note") or "") + (" Each run is real, like run_workflow. The schedule is "
                                        "listed on the Workflow Monitor page; cancel_workflow_schedule "
                                        "removes it.")
    return _text(text)


@tool(
    "list_workflow_schedules",
    "List the schedules of VISUAL workflows (all of them, or one workflow by id or exact name): "
    "cadence, the zone a cron runs in, next and last run in the user's time zone, run counts. "
    "Code Flow schedules are not included (the code-flow tools manage those).",
    {"type": "object",
     "properties": {"workflow": {"type": "string", "description": "Optional workflow id or exact name"}},
     "additionalProperties": False},
)
async def list_workflow_schedules(args: dict[str, Any]) -> dict[str, Any]:
    if not _authoring_allowed():
        return _text(_DENIED, is_error=True)
    from work_tools import default_zone_label
    zone, _src = default_zone_label(CURRENT_USER.get() or {})
    wid, name = None, None
    if str(args.get("workflow") or "").strip():
        try:
            wid, name, err = await _resolve(args.get("workflow"))
        except Exception as e:
            return _text(f"Could not look up workflows: {e}", is_error=True)
        if err:
            return _text(err, is_error=True)
    try:
        rows = await _workflow_schedules(wid)
    except Exception as e:
        return _text(f"Could not read schedules: {e}", is_error=True)
    if not rows:
        return _text(f"'{name}' has no schedules." if name else "No visual workflow has a schedule.")
    head = f"{len(rows)} schedule(s)" + (f" for '{name}'" if name else "") + f" (times in {zone}):"
    return _text(head + "\n" + "\n".join(_schedule_line(s, zone) for s in rows))


@tool(
    "cancel_workflow_schedule",
    "Remove one schedule of a VISUAL workflow. Two-step: call without confirmed to see the "
    "workflow's schedules, then again with schedule_id and confirmed=true after the user agreed. "
    "Never removes more than the one schedule named.",
    {"type": "object",
     "properties": {
         "workflow": {"type": "string", "description": "Visual workflow id or exact name"},
         "schedule_id": {"type": "integer"},
         "confirmed": {"type": "boolean", "description": "true only after the user confirmed"},
     },
     "required": ["workflow"], "additionalProperties": False},
)
async def cancel_workflow_schedule(args: dict[str, Any]) -> dict[str, Any]:
    if not _authoring_allowed():
        return _text(_DENIED, is_error=True)
    from work_tools import default_zone_label
    zone, _src = default_zone_label(CURRENT_USER.get() or {})
    try:
        wid, name, err = await _resolve(args.get("workflow"))
    except Exception as e:
        return _text(f"Could not look up workflows: {e}", is_error=True)
    if err:
        return _text(err, is_error=True)
    try:
        rows = await _workflow_schedules(wid)
    except Exception as e:
        return _text(f"Could not read schedules: {e}", is_error=True)
    if not rows:
        return _text(f"'{name}' has no schedule — nothing to cancel.")
    listing = "\n".join(_schedule_line(s, zone) for s in rows)
    sid = args.get("schedule_id")
    target = next((s for s in rows if str(s.get("id")) == str(sid)), None) if sid else None
    if sid and target is None:
        return _text(f"'{name}' has no schedule #{sid}. Its schedules:\n{listing}", is_error=True)
    if target is None and len(rows) == 1:
        target = rows[0]
    if target is None:
        return _text(f"'{name}' has {len(rows)} schedules:\n{listing}\nAsk the user which one to "
                     "remove, then call again with schedule_id and confirmed=true.")
    if not args.get("confirmed"):
        return _text(f"Found {_schedule_line(target, zone)[2:]}\nAsk the user to confirm, then call "
                     f"again with schedule_id={target.get('id')} and confirmed=true.")
    data, status = await _delete(f"/api/scheduler/jobs/{wid}/types/workflow/schedules/{target.get('id')}")
    still = [s for s in await _workflow_schedules(wid) if str(s.get("id")) == str(target.get("id"))]
    if status >= 400 or still:
        detail = (data.get("error") or data.get("message")) if isinstance(data, dict) else data
        return _text(f"Could not remove schedule #{target.get('id')} (HTTP {status}: {detail}). "
                     "Tell the user it is still scheduled; it can be removed on the Workflow "
                     "Monitor page.", is_error=True)
    return _text(f"Removed schedule #{target.get('id')} of '{name}' (verified gone by read-back).")


WORKFLOW_TOOLS = [get_workflow_node_reference, list_workflows, get_workflow,
                  save_workflow, run_workflow, get_workflow_run_report,
                  schedule_workflow, list_workflow_schedules, cancel_workflow_schedule]
