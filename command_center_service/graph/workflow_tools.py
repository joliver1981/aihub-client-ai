"""
workflow_tools.py — CC-side deterministic manager for VISUAL workflows
(the native-agent sibling of codeflow_tools.py / automation_tools.py).

Part of the CC_AGENT="native" A/B agent: the Command Center authors visual
workflows with its OWN tools instead of delegating to the builder agent, so
nothing is lost in translation. Everything here is deterministic — the ONLY
LLM on the native build path is the CC agent itself, grounded on these tools'
typed results.

Design rules (from the 2026-07 architecture assessment + pack-09 findings):
- ONE write chokepoint: every save goes through the main app's guarded
  POST /save/workflow (same endpoint as the canvas UI and the builder), so the
  AIHUB-0039 code_flow kind-guard and the AIHUB-0016 deterministic validation
  protect this caller too. No second writer is introduced.
- TRUE read-back (AIHUB-0038/0041): after every save, re-resolve the row BY
  NAME and read it back by id; report the persisted node types, and loudly
  flag a name→row mismatch (the 0041 wrong-row bug class) instead of vouching
  for a row the user will never open.
- Competing edges are a HARD ERROR at wire time (AIHUB-0045): a node gets one
  'pass' OR one 'complete' outgoing edge, plus at most one 'fail'. unwire
  first, then rewire.
- Code Flow rows are UNTOUCHABLE from these tools (AIHUB-0039): a workflow
  whose definition carries kind='code_flow' is refused client-side before the
  server guard even sees it.
- Never raise for a remote failure — {"ok": False, "error": ...} instead
  (same contract as codeflow_tools.manage).

Synchronous; call via asyncio.to_thread from the @lc_tool wrappers.
"""
import json
import logging
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

GET_TIMEOUT = 30
SAVE_TIMEOUT = 60
RUN_START_TIMEOUT = 30

# Canonical node-type catalog — imported from the SAME source the visual
# builder (WorkflowAgent) uses, so there is nothing to drift. cc_config puts
# the main-app dir on sys.path at import time.
try:
    from system_prompts import VALID_WORKFLOW_NODE_TYPES
except Exception:  # pragma: no cover — service running outside the repo tree
    VALID_WORKFLOW_NODE_TYPES = [
        "Database", "AI Action", "AI Extract", "Document", "Loop", "End Loop",
        "Conditional", "Human Approval", "Alert", "Folder Selector", "File",
        "Set Variable", "Execute Application", "Excel Export", "Portal",
        "Integration", "Compliance Process", "Compliance Excel Export",
        "Automation", "Code Step",
    ]

_NODE_TYPE_BY_LOWER = {t.lower(): t for t in VALID_WORKFLOW_NODE_TYPES}

_VALID_EDGE_TYPES = ("pass", "fail", "complete")

# Workflow names become filenames server-side (workflows/<name>.json) and the
# MERGE key in the DB — keep them filesystem- and merge-safe.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _\-\.]{0,118}$")


# ─── HTTP plumbing ────────────────────────────────────────────────────────

def _headers() -> Dict[str, str]:
    from cc_config import AI_HUB_API_KEY
    return {"X-API-Key": AI_HUB_API_KEY}


def _base() -> str:
    from cc_config import get_base_url
    return get_base_url()


def _get(path: str, timeout: int = GET_TIMEOUT) -> Dict[str, Any]:
    url = f"{_base()}{path}"
    try:
        resp = requests.get(url, headers=_headers(), timeout=timeout)
        try:
            data = resp.json()
        except ValueError:
            return {"ok": False, "status_code": resp.status_code,
                    "error": f"non-JSON response (HTTP {resp.status_code})"}
        # /get/workflows double-encodes (jsonify of df.to_json) — unwrap.
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                return {"ok": False, "status_code": resp.status_code,
                        "error": "unparseable response body"}
        return {"ok": resp.status_code < 400, "status_code": resp.status_code,
                "data": data}
    except requests.RequestException as e:
        logger.warning(f"workflow_tools GET {path} failed: {e}")
        return {"ok": False, "status_code": 502,
                "error": f"could not reach the AI Hub app: {e}"}


def _post(path: str, body: Dict[str, Any], timeout: int = SAVE_TIMEOUT) -> Dict[str, Any]:
    url = f"{_base()}{path}"
    try:
        resp = requests.post(url, json=body, headers=_headers(), timeout=timeout)
        try:
            data = resp.json()
        except ValueError:
            data = {"error": f"non-JSON response (HTTP {resp.status_code})"}
        if not isinstance(data, dict):
            data = {"data": data}
        data["ok"] = resp.status_code < 400
        data["status_code"] = resp.status_code
        return data
    except requests.RequestException as e:
        logger.warning(f"workflow_tools POST {path} failed: {e}")
        return {"ok": False, "status_code": 502,
                "error": f"could not reach the AI Hub app: {e}"}


# ─── Row resolution (name ⇄ id) ───────────────────────────────────────────

def list_rows() -> Dict[str, Any]:
    """All Workflows rows as [{id, name, kind}] (kind detected from the stored
    definition when the list SQL returns it; '' when undeterminable)."""
    res = _get("/get/workflows")
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error") or f"HTTP {res.get('status_code')}"}
    rows = res.get("data") or []
    if not isinstance(rows, list):
        return {"ok": False, "error": "unexpected /get/workflows shape"}
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = r.get("workflow_name") or r.get("name") or ""
        wf_id = r.get("id") or r.get("workflow_id")
        kind = ""
        raw = r.get("workflow_data")
        if isinstance(raw, str) and '"kind"' in raw:
            try:
                kind = (json.loads(raw) or {}).get("kind") or ""
            except ValueError:
                kind = ""
        elif isinstance(raw, dict):
            kind = raw.get("kind") or ""
        if wf_id is not None and name:
            out.append({"id": int(wf_id), "name": str(name), "kind": kind})
    return {"ok": True, "rows": out}


def resolve(name_or_id: str) -> Dict[str, Any]:
    """Resolve a workflow reference (exact name, case-insensitive, or numeric id)
    to {id, name, kind}. NOT fuzzy on purpose — a build tool must never guess
    which row it is about to modify."""
    listed = list_rows()
    if not listed.get("ok"):
        return {"ok": False, "error": listed.get("error")}
    rows = listed["rows"]
    ref = str(name_or_id or "").strip()
    if not ref:
        return {"ok": False, "error": "empty workflow reference"}
    if ref.isdigit():
        for r in rows:
            if r["id"] == int(ref):
                return {"ok": True, **r}
        return {"ok": False, "error": f"no workflow with id {ref}"}
    matches = [r for r in rows if r["name"].lower() == ref.lower()]
    if not matches:
        return {"ok": False, "error": f"no workflow named '{ref}' (exact match required)"}
    if len(matches) > 1:
        ids = ", ".join(str(r["id"]) for r in matches)
        return {"ok": False, "error": f"'{ref}' matches multiple rows (ids {ids}) — reference by id"}
    return {"ok": True, **matches[0]}


def get_definition(name_or_id: str) -> Dict[str, Any]:
    """Fetch a workflow's stored definition. Refuses Code Flow rows — those are
    owned by the code-flow tools (AIHUB-0039)."""
    ref = resolve(name_or_id)
    if not ref.get("ok"):
        return ref
    res = _get(f"/get/workflow/{ref['id']}")
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error") or f"HTTP {res.get('status_code')}"}
    data = res.get("data")
    if not isinstance(data, dict):
        return {"ok": False, "error": "unexpected workflow definition shape"}
    if (data.get("kind") or "").lower() == "code_flow":
        return {"ok": False, "error":
                f"'{ref['name']}' (id {ref['id']}) is a Code Flow — it must be edited "
                f"with the code-flow tools (get_code_flow / update_step_code / wire_steps), "
                f"never the visual-workflow tools."}
    data.setdefault("nodes", [])
    data.setdefault("connections", [])
    data.setdefault("variables", {})
    return {"ok": True, "id": ref["id"], "name": ref["name"], "definition": data}


# ─── Pure graph surgery (no I/O) ──────────────────────────────────────────

def _find_node(definition: Dict[str, Any], node_id: str) -> Optional[Dict[str, Any]]:
    for n in definition.get("nodes", []):
        if isinstance(n, dict) and n.get("id") == node_id:
            return n
    return None


def _edge_ends(c: Dict[str, Any]) -> Tuple[str, str, str]:
    return (c.get("source", c.get("from", "")),
            c.get("target", c.get("to", "")),
            (c.get("type") or "pass").lower())


def _auto_position(index: int) -> Dict[str, str]:
    """Grid layout so CC-built workflows render readably on the canvas:
    4 columns, left-to-right then wrap."""
    col, row = index % 4, index // 4
    return {"left": f"{40 + col * 230}px", "top": f"{60 + row * 170}px"}


def canonical_node_type(node_type: str) -> Optional[str]:
    return _NODE_TYPE_BY_LOWER.get((node_type or "").strip().lower())


def add_node(definition: Dict[str, Any], node_type: str, label: str,
             config: Dict[str, Any], user_context: Optional[Dict[str, Any]] = None,
             make_start: bool = False) -> Dict[str, Any]:
    canon = canonical_node_type(node_type)
    if not canon:
        return {"ok": False, "error":
                f"'{node_type}' is not a valid node type. Valid types: "
                + ", ".join(VALID_WORKFLOW_NODE_TYPES)
                + ". There is NO node for SFTP/FTP/HTTP-API pushes or custom code — "
                  "use a Code Flow, or an Automation node running a promoted Automation."}
    nodes = definition.setdefault("nodes", [])
    node_id = f"n_{uuid.uuid4().hex[:8]}"
    cfg = dict(config or {})
    # Portal ownership is bound at save time by the canvas (session auth); the
    # CC path saves with the service key, so stamp the owner explicitly here.
    if canon == "Portal" and not cfg.get("ownerUserId"):
        owner = (user_context or {}).get("user_id")
        if owner:
            cfg["ownerUserId"] = str(owner)
    node = {
        "id": node_id,
        "type": canon,
        "label": (label or canon).strip() or canon,
        "position": _auto_position(len(nodes)),
        "config": cfg,
        "isStart": False,
    }
    nodes.append(node)
    if make_start or not any(n.get("isStart") for n in nodes):
        set_start(definition, node_id)
    return {"ok": True, "node_id": node_id, "type": canon}


def update_node(definition: Dict[str, Any], node_id: str,
                config_patch: Optional[Dict[str, Any]] = None,
                label: Optional[str] = None) -> Dict[str, Any]:
    node = _find_node(definition, node_id)
    if not node:
        return {"ok": False, "error": f"no node '{node_id}' in this workflow"}
    if config_patch:
        cfg = node.setdefault("config", {})
        cfg.update(config_patch)
    if label:
        node["label"] = label.strip()
    return {"ok": True, "node_id": node_id}


def remove_node(definition: Dict[str, Any], node_id: str) -> Dict[str, Any]:
    node = _find_node(definition, node_id)
    if not node:
        return {"ok": False, "error": f"no node '{node_id}' in this workflow"}
    was_start = bool(node.get("isStart"))
    definition["nodes"] = [n for n in definition.get("nodes", []) if n.get("id") != node_id]
    before = len(definition.get("connections", []))
    definition["connections"] = [
        c for c in definition.get("connections", [])
        if node_id not in (_edge_ends(c)[0], _edge_ends(c)[1])
    ]
    dropped_edges = before - len(definition["connections"])
    note = ""
    if was_start and definition["nodes"]:
        definition["nodes"][0]["isStart"] = True
        note = f"start moved to '{definition['nodes'][0].get('id')}'"
    return {"ok": True, "removed_edges": dropped_edges, "note": note}


def wire(definition: Dict[str, Any], from_id: str, to_id: str, on: str = "pass") -> Dict[str, Any]:
    on = (on or "pass").strip().lower()
    if on not in _VALID_EDGE_TYPES:
        return {"ok": False, "error": f"edge type must be one of {_VALID_EDGE_TYPES}"}
    if not _find_node(definition, from_id):
        return {"ok": False, "error": f"no node '{from_id}' in this workflow"}
    if not _find_node(definition, to_id):
        return {"ok": False, "error": f"no node '{to_id}' in this workflow"}
    if from_id == to_id:
        return {"ok": False, "error": "a node cannot connect to itself"}
    # Slot rule (canvas + engine contract): SUCCESS slot holds ONE 'pass' OR ONE
    # 'complete' (mutually exclusive); FAILURE slot holds at most one 'fail'.
    # A competing edge is a hard error — the engine would silently follow the
    # old edge (the AIHUB-0045 untraversed-step trap).
    for c in definition.get("connections", []):
        src, dst, typ = _edge_ends(c)
        if src != from_id:
            continue
        if src == from_id and dst == to_id and typ == on:
            return {"ok": True, "note": "edge already exists"}
        if on in ("pass", "complete") and typ in ("pass", "complete"):
            return {"ok": False, "error":
                    f"'{from_id}' already has a '{typ}' edge to '{dst}' — a node gets one "
                    f"pass/complete edge. unwire_workflow_nodes({from_id} → {dst}) first, then rewire."}
        if on == "fail" and typ == "fail":
            return {"ok": False, "error":
                    f"'{from_id}' already has a 'fail' edge to '{dst}' — unwire it first."}
    definition.setdefault("connections", []).append({
        "source": from_id, "target": to_id, "type": on,
        "sourceAnchor": "Right", "targetAnchor": "Left",
    })
    return {"ok": True}


def unwire(definition: Dict[str, Any], from_id: str, to_id: str,
           on: Optional[str] = None) -> Dict[str, Any]:
    on_l = (on or "").strip().lower() or None
    before = len(definition.get("connections", []))
    definition["connections"] = [
        c for c in definition.get("connections", [])
        if not (_edge_ends(c)[0] == from_id and _edge_ends(c)[1] == to_id
                and (on_l is None or _edge_ends(c)[2] == on_l))
    ]
    removed = before - len(definition["connections"])
    if not removed:
        return {"ok": False, "error": f"no {on_l or ''} edge {from_id} → {to_id} to remove".replace("  ", " ")}
    return {"ok": True, "removed": removed}


def insert_between(definition: Dict[str, Any], node_type: str, label: str,
                   config: Dict[str, Any], from_id: str, to_id: str,
                   user_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """AIHUB-0048 F2: ATOMIC in-memory insert-between — add the node and rewire
    from→new→to as ONE operation (the caller saves once), so a multi-call tool
    sequence can never be interrupted half-way and leave the graph edge-less
    (live: the second wire was suppressed and slot-repro-wf persisted with
    edges=[]). The from→to edge must exist; its edge type carries over to
    from→new and new→to gets 'pass'. On ANY failure the definition is restored
    exactly as it was."""
    import copy as _copy
    snapshot = _copy.deepcopy(definition)
    edge = next((c for c in definition.get("connections", [])
                 if _edge_ends(c)[0] == from_id and _edge_ends(c)[1] == to_id), None)
    if edge is None:
        return {"ok": False, "error":
                f"no edge {from_id} → {to_id} exists to insert into — check the ids "
                f"(get_workflow_structure) or wire them first"}
    edge_on = _edge_ends(edge)[2]
    added = add_node(definition, node_type, label, config, user_context=user_context)
    if not added.get("ok"):
        definition.clear()
        definition.update(snapshot)
        return added
    new_id = added["node_id"]
    r1 = unwire(definition, from_id, to_id, on=edge_on)
    r2 = wire(definition, from_id, new_id, on=edge_on) if r1.get("ok") else r1
    r3 = wire(definition, new_id, to_id, on="pass") if r2.get("ok") else r2
    if not (r1.get("ok") and r2.get("ok") and r3.get("ok")):
        _err = (r1 if not r1.get("ok") else r2 if not r2.get("ok") else r3).get("error")
        definition.clear()
        definition.update(snapshot)
        return {"ok": False, "error":
                f"insert-between failed and was fully rolled back (workflow unchanged) — {_err}"}
    return {"ok": True, "node_id": new_id,
            "rewired": f"{from_id} —[{edge_on}]→ {new_id} —[pass]→ {to_id}"}


def set_start(definition: Dict[str, Any], node_id: str) -> Dict[str, Any]:
    target = _find_node(definition, node_id)
    if not target:
        return {"ok": False, "error": f"no node '{node_id}' in this workflow"}
    for n in definition.get("nodes", []):
        n["isStart"] = False
    target["isStart"] = True
    return {"ok": True}


def set_variable(definition: Dict[str, Any], name: str, var_type: str = "string",
                 default_value: str = "", description: str = "") -> Dict[str, Any]:
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "variable name is required"}
    definition.setdefault("variables", {})[name] = {
        "type": (var_type or "string").strip() or "string",
        "defaultValue": default_value,
        "description": description,
    }
    return {"ok": True}


def local_issues(definition: Dict[str, Any]) -> List[str]:
    """Cheap pre-save lint mirroring the server validator's structural basics.
    The server verdict (is_valid/validation_errors on save) stays authoritative."""
    issues = []
    nodes = definition.get("nodes", [])
    ids = {n.get("id") for n in nodes}
    if not nodes:
        issues.append("workflow has no nodes")
    elif not any(n.get("isStart") for n in nodes):
        issues.append("no start node is set (use set_workflow_start)")
    for c in definition.get("connections", []):
        src, dst, typ = _edge_ends(c)
        if src not in ids or dst not in ids:
            issues.append(f"edge {src} → {dst} references a missing node")
    return issues


# ─── Save + true read-back ────────────────────────────────────────────────

def save_definition(name: str, definition: Dict[str, Any]) -> Dict[str, Any]:
    """Persist through the guarded generic save, then read the row back BY NAME.
    Returns the server's honest validity verdict plus the read-back —
    the wrapper composes the user-facing message from THIS, never from memory."""
    name = (name or "").strip()
    if not _NAME_RE.match(name or ""):
        return {"ok": False, "error":
                "workflow name must be 1-119 chars of letters/digits/space/_-. "
                "and start with a letter or digit"}
    if (definition.get("kind") or "").lower() == "code_flow":
        return {"ok": False, "error": "refusing to write a code_flow row from the visual-workflow tools"}
    body = {"filename": f"{name}.json", "workflow": definition}
    res = _post("/save/workflow", body, timeout=SAVE_TIMEOUT)
    if not res.get("ok"):
        # 400s carry the server's real refusal (e.g. the AIHUB-0039 code-flow
        # guard) in "message" — surface it verbatim.
        return {"ok": False, "status_code": res.get("status_code"),
                "error": res.get("message") or res.get("error") or "save failed"}
    saved_id = res.get("workflow_id")

    # TRUE read-back of the row the user will open: resolve by NAME again and
    # read that row. If the name resolves to a different id than the save
    # reported, say so loudly (AIHUB-0041 wrong-row class) instead of vouching.
    readback: Dict[str, Any] = {}
    ref = resolve(name)
    if ref.get("ok"):
        rb = _get(f"/get/workflow/{ref['id']}")
        if rb.get("ok") and isinstance(rb.get("data"), dict):
            rb_nodes = rb["data"].get("nodes") or []
            readback = {
                "id": ref["id"],
                "node_count": len(rb_nodes),
                "node_types": [n.get("type") for n in rb_nodes if isinstance(n, dict)],
                "labels": [n.get("label") or n.get("type") for n in rb_nodes if isinstance(n, dict)],
                "mismatch": (saved_id is not None and ref["id"] != saved_id),
            }
    return {
        "ok": True,
        "workflow_id": saved_id,
        "is_valid": bool(res.get("is_valid")),
        "saved_as_draft": bool(res.get("saved_as_draft")),
        "validation_errors": res.get("validation_errors") or [],
        "readback": readback,
    }


# ─── Run + honest outcome ─────────────────────────────────────────────────

_ACTIVE_RUN_STATUSES = {"running", "pending", "queued", "starting"}


def start_run(workflow_id: int, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    body: Dict[str, Any] = {"workflow_id": workflow_id, "initiator": "command_center"}
    if variables:
        body["variables"] = variables
    res = _post("/api/workflow/run", body, timeout=RUN_START_TIMEOUT)
    if not res.get("ok"):
        return {"ok": False, "error": res.get("message") or res.get("error") or "run failed to start"}
    return {"ok": True, "execution_id": res.get("execution_id")}


def get_run_status(execution_id: str) -> Dict[str, Any]:
    ex = _get(f"/api/workflow/executions/{execution_id}")
    if not ex.get("ok"):
        return {"ok": False, "error": ex.get("error") or f"HTTP {ex.get('status_code')}"}
    execution = ex.get("data") or {}
    steps_res = _get(f"/api/workflow/executions/{execution_id}/steps")
    steps = (steps_res.get("data") or {}).get("steps") or [] if steps_res.get("ok") else []
    return {"ok": True, "execution": execution, "steps": steps}


def wait_for_outcome(execution_id: str, max_wait_s: int = 90) -> Dict[str, Any]:
    """Poll the execution until it leaves the active states or the wait budget
    is spent. NEVER converts a timeout into a success claim — the caller gets
    status='still running' and the execution_id to check later."""
    deadline = time.monotonic() + max_wait_s
    last: Dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = get_run_status(execution_id)
        if not last.get("ok"):
            return last
        status = str((last.get("execution") or {}).get("status") or "").lower()
        if status and status not in _ACTIVE_RUN_STATUSES:
            return last
        # A paused run is waiting on a human (approval) — report that honestly
        # rather than burning the whole budget.
        if status == "paused":
            return last
        time.sleep(2)
    return last if last else {"ok": False, "error": "no status available"}


# ─── Deterministic summaries (the tool-result text) ───────────────────────

def summarize_structure(wf_id: int, name: str, definition: Dict[str, Any]) -> str:
    nodes = definition.get("nodes", [])
    conns = definition.get("connections", [])
    variables = definition.get("variables", {})
    lines = [f"Workflow '{name}' (id {wf_id}): {len(nodes)} node(s), {len(conns)} edge(s)"]
    for n in nodes:
        star = " [START]" if n.get("isStart") else ""
        cfg_keys = ", ".join(sorted((n.get("config") or {}).keys())[:8])
        lines.append(f"- [{n.get('id')}] {n.get('type')} — {n.get('label')}{star}"
                     + (f" (config: {cfg_keys})" if cfg_keys else " (no config yet)"))
    for c in conns:
        src, dst, typ = _edge_ends(c)
        lines.append(f"  {src} —[{typ}]→ {dst}")
    if variables:
        lines.append("variables: " + ", ".join(
            f"{k} ({v.get('type', 'string')}={v.get('defaultValue', '')!r})"
            for k, v in variables.items()))
    issues = local_issues(definition)
    if issues:
        lines.append("⚠ structural issues: " + "; ".join(issues))
    return "\n".join(lines)


def summarize_save(name: str, result: Dict[str, Any]) -> str:
    """The honest post-save line. GROUNDING CONTRACT: the persisted step list
    comes from the read-back, never from what the agent intended to build."""
    if not result.get("ok"):
        return f"❌ Save failed: {result.get('error')}"
    rb = result.get("readback") or {}
    parts = [f"Saved '{name}' (id {result.get('workflow_id')})."]
    if rb:
        types = rb.get("node_types") or []
        parts.append(f"🧾 Read-back of the saved row (id {rb.get('id')}): "
                     f"{rb.get('node_count')} node(s) — {', '.join(types) if types else 'EMPTY'}.")
        if rb.get("mismatch"):
            parts.append("🚨 ROW MISMATCH: the save reported id "
                         f"{result.get('workflow_id')} but the name resolves to id {rb.get('id')} — "
                         "tell the user; do NOT claim the save is verified.")
        if rb.get("node_count") == 0:
            parts.append("⚠ The saved workflow is EMPTY — do not describe it as containing any steps.")
    else:
        parts.append("⚠ Read-back unavailable — report the save as UNVERIFIED, not as confirmed.")
    if result.get("saved_as_draft"):
        errs = "; ".join(result.get("validation_errors") or []) or "unspecified"
        parts.append(f"⚠ Saved as DRAFT (not yet runnable): {errs}")
    elif result.get("is_valid"):
        parts.append("Validation: passed (runnable).")
    return " ".join(parts)


def _step_field(step: Dict[str, Any], *names: str) -> str:
    for n in names:
        v = step.get(n)
        if v not in (None, ""):
            return str(v)
    return ""


def summarize_run(result: Dict[str, Any]) -> str:
    """Honest run summary from the executions read-back. A still-running or
    paused execution is reported as exactly that — never as success."""
    if not result.get("ok"):
        return f"❌ Could not read the run: {result.get('error')}"
    execution = result.get("execution") or {}
    steps = result.get("steps") or []
    status = str(execution.get("status") or "unknown")
    exec_id = execution.get("execution_id") or execution.get("id") or "?"
    lines = [f"Run {exec_id}: **{status}**"]
    if status.lower() in _ACTIVE_RUN_STATUSES:
        lines[0] += " — still executing; check again with check_workflow_run before reporting an outcome."
    if status.lower() == "paused":
        waiting = (execution.get("current_step") or {}).get("waiting_for_approval")
        lines[0] += (" — waiting for a human approval" if waiting else " — paused")
    err = _step_field(execution, "error_message", "error")
    if err:
        lines.append(f"error: {err[:400]}")
    for s in steps:
        s_status = _step_field(s, "status") or "?"
        mark = {"completed": "✓", "success": "✓", "failed": "✗", "error": "✗"}.get(s_status.lower(), "·")
        label = _step_field(s, "step_name", "node_label", "node_id", "step_execution_id")
        line = f"{mark} {label}: {s_status}"
        s_err = _step_field(s, "error_message", "error")
        if s_err and s_status.lower() in ("failed", "error"):
            line += f" — {s_err[:300]}"
        lines.append(line)
    if not steps:
        lines.append("(no step records yet)")
    return "\n".join(lines)
