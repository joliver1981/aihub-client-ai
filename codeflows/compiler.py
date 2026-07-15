"""
Code Flow compiler + in-process dry-run walk.

A Code Flow DEFINITION (what the manager stores) is:

    {
      "name": "nightly-recon",
      "steps": [
        {"id": "s1", "name": "pull", "code": "...python...",
         "connections": ["ERPDB"], "secrets": ["ACME_SFTP"],
         "packages": ["pdfplumber"], "inputs": [{"name": "period", "default": "current"}],
         "outputs": [{"kind": "file", "path": "out.csv", "verify": {"min_rows": 1}}],
         "timeout": 600},
        ...
      ],
      "edges": [{"from": "s1", "to": "s2", "on": "pass"},
                {"from": "s1", "to": "alert", "on": "fail"}],
      "start": "s1"          # optional; defaults to the first step
    }

`compile_to_workflow()` turns that into the exact dict the engine runs
(`{nodes, connections, variables, kind}` with each step a "Code Step" node,
`isStart` on the start, `source/target/type` edges). Verified against the
engine's reader: nodes use top-level id/type/label/isStart/config; edges are a
FLAT list keyed source/target/type; routing prefers the first 'pass' edge,
then 'complete', and on failure the first 'fail' edge then 'complete'.

`dry_run_walk()` executes the graph IN-PROCESS (no DB, no async engine) by
running each Code Step through the Automations runner and following the same
pass/fail routing — synchronous per-step results for the "dry-run and show me"
UX. Each step's result is published to shared variables as
`<step_id>_out` / `<step_id>_files`, and a later step's string inputs get
${var} / ${var[0]} / ${var.key} substitution so steps pass data (absolute
file paths) forward.
"""

import os
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

CODE_FLOW_KIND = "code_flow"
_VALID_EDGE_TYPES = ("pass", "fail", "complete")
_SUB_RE = re.compile(r"\$\{([^}]+)\}")


# --------------------------------------------------------------------- validate

def validate_definition(defn: Dict) -> Tuple[bool, List[str]]:
    """Structural validation of a code-flow definition. (ok, errors)."""
    errors: List[str] = []
    if not isinstance(defn, dict):
        return False, ["definition must be an object"]
    if not (defn.get("name") or "").strip():
        errors.append("'name' is required")

    steps = defn.get("steps") or []
    if not isinstance(steps, list):
        return False, ["'steps' must be a list"]
    ids = []
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            errors.append(f"steps[{i}] must be an object")
            continue
        sid = s.get("id")
        if not sid or not isinstance(sid, str):
            errors.append(f"steps[{i}] needs a string 'id'")
        else:
            ids.append(sid)
        if not (s.get("code") or "").strip():
            errors.append(f"step '{sid or i}' has no code")
        for key in ("connections", "secrets", "packages", "inputs", "outputs"):
            if key in s and not isinstance(s[key], list):
                errors.append(f"step '{sid or i}': '{key}' must be a list")
    if len(set(ids)) != len(ids):
        errors.append("duplicate step ids")

    id_set = set(ids)
    for j, e in enumerate(defn.get("edges") or []):
        if not isinstance(e, dict):
            errors.append(f"edges[{j}] must be an object")
            continue
        if e.get("from") not in id_set:
            errors.append(f"edges[{j}]: 'from' '{e.get('from')}' is not a step id")
        if e.get("to") not in id_set:
            errors.append(f"edges[{j}]: 'to' '{e.get('to')}' is not a step id")
        on = e.get("on", "pass")
        if on not in _VALID_EDGE_TYPES:
            errors.append(f"edges[{j}]: 'on' must be one of {_VALID_EDGE_TYPES}")

    start = _start_id(defn)
    if steps and start not in id_set:
        errors.append(f"start step '{start}' is not a step id")
    return (not errors), errors


def _start_id(defn: Dict) -> Optional[str]:
    if defn.get("start"):
        return defn["start"]
    steps = defn.get("steps") or []
    return steps[0]["id"] if steps and isinstance(steps[0], dict) else None


def new_step_id() -> str:
    return "s" + uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------- compile

def _step_manifest(step: Dict) -> Dict:
    return {
        "entrypoint": "step.py",
        "connections": step.get("connections", []) or [],
        "secrets": step.get("secrets", []) or [],
        "packages": step.get("packages", []) or [],
        "inputs": step.get("inputs", []) or [],
        "outputs": step.get("outputs", []) or [],
        "timeout_seconds": int(step.get("timeout") or 600),
    }


def compile_to_workflow(defn: Dict) -> Dict:
    """Compile a code-flow definition into the engine's workflow_data dict.
    Each step -> a 'Code Step' node whose config carries the code + manifest;
    each step auto-exposes <id>_out / <id>_files variables so downstream steps
    can reference produced files. Edges -> flat source/target/type connections."""
    start = _start_id(defn)
    steps = defn.get("steps") or []
    nodes = []
    for i, s in enumerate(steps):
        sid = s["id"]
        m = _step_manifest(s)
        nodes.append({
            "id": sid,
            "type": "Code Step",
            "label": s.get("name") or sid,
            "isStart": (sid == start),
            "position": {"left": f"{120 + i * 260}px", "top": "160px"},
            "config": {
                "code": s.get("code", ""),
                "connections": m["connections"],
                "secrets": m["secrets"],
                "packages": m["packages"],
                "inputs": m["inputs"],
                "outputs": m["outputs"],
                "timeout": m["timeout_seconds"],
                "environmentId": s.get("environmentId"),
                "outputVariable": f"{sid}_out",
                "filesVariable": f"{sid}_files",
                "continueOnError": bool(s.get("continueOnError", False)),
            },
        })
    connections = []
    for e in defn.get("edges") or []:
        connections.append({
            "source": e["from"], "target": e["to"],
            "type": e.get("on", "pass"),
            "sourceAnchor": "Right", "targetAnchor": "Left",
        })
    return {"kind": CODE_FLOW_KIND, "nodes": nodes, "connections": connections, "variables": {}}


# --------------------------------------------------------------- dry-run walk

def _get_path(variables: Dict, expr: str) -> Any:
    """Resolve a ${...} path like var, var[0], var.key, var.key[2] against the
    accumulated variables. Returns None if unresolved."""
    m = re.match(r"^([A-Za-z_][\w]*)(.*)$", expr.strip())
    if not m:
        return None
    cur = variables.get(m.group(1))
    rest = m.group(2)
    for tok in re.findall(r"\.([A-Za-z_]\w*)|\[(\d+)\]", rest):
        key, idx = tok
        try:
            if key:
                cur = cur.get(key) if isinstance(cur, dict) else None
            else:
                cur = cur[int(idx)] if isinstance(cur, (list, tuple)) else None
        except (KeyError, IndexError, TypeError):
            return None
        if cur is None:
            return None
    return cur


def _substitute(value: Any, variables: Dict) -> Any:
    """${expr} substitution on a string input value; a whole-string ${x} keeps
    x's native type, an embedded ${x} stringifies."""
    if not isinstance(value, str):
        return value
    whole = _SUB_RE.fullmatch(value.strip())
    if whole:
        resolved = _get_path(variables, whole.group(1))
        return resolved if resolved is not None else value

    def _repl(mo):
        r = _get_path(variables, mo.group(1))
        return str(r) if r is not None else mo.group(0)
    return _SUB_RE.sub(_repl, value)


def _resolve_step_inputs(step: Dict, variables: Dict) -> Dict:
    out = {}
    for spec in step.get("inputs", []) or []:
        if not isinstance(spec, dict) or not spec.get("name"):
            continue
        name = spec["name"]
        val = variables.get(name, spec.get("default"))
        out[name] = _substitute(val, variables) if isinstance(val, str) else val
    return out


def _next_step(edges: List[Dict], current: str, success: bool) -> Optional[str]:
    """Mirror the engine routing: on success prefer the first 'pass' edge then
    'complete'; on failure the first 'fail' edge then 'complete'."""
    outgoing = [e for e in edges if e.get("from") == current]
    primary = "pass" if success else "fail"
    for e in outgoing:
        if e.get("on", "pass") == primary:
            return e["to"]
    for e in outgoing:
        if e.get("on") == "complete":
            return e["to"]
    return None


def dry_run_walk(defn: Dict, runner, base_workdir: str, max_steps: int = 50) -> Dict:
    """Execute the code-flow graph IN-PROCESS via runner.run_code_step, honoring
    pass/fail edges. Returns {status, steps:[{step_id,name,status,...}],
    variables}. `status` is 'success' only if the walk reached a natural end
    with no failed step outside a handled fail-edge; 'failed' if a step failed
    with nowhere to route; 'error' on a definition problem."""
    ok, errors = validate_definition(defn)
    if not ok:
        return {"status": "error", "error": "; ".join(errors), "steps": []}

    steps_by_id = {s["id"]: s for s in defn.get("steps") or []}
    edges = defn.get("edges") or []
    variables: Dict[str, Any] = {}
    trace: List[Dict] = []
    current = _start_id(defn)
    visits: Dict[str, int] = {}
    overall_failed = False

    while current and len(trace) < max_steps:
        visits[current] = visits.get(current, 0) + 1
        if visits[current] > 10:
            trace.append({"step_id": current, "status": "error", "error": "cycle guard tripped"})
            overall_failed = True
            break
        step = steps_by_id.get(current)
        if not step:
            trace.append({"step_id": current, "status": "error", "error": "edge to missing step"})
            overall_failed = True
            break

        inputs = _resolve_step_inputs(step, variables)
        result = runner.run_code_step(
            step.get("code", ""), _step_manifest(step), step.get("name") or current,
            inputs=inputs, environment_id=step.get("environmentId"),
            workdir=os.path.join(base_workdir, current),
        )
        status = result.get("status", "error")
        workdir = result.get("workdir") or ""
        abs_files = [os.path.join(workdir, f) for f in (result.get("output_files") or [])] if workdir else []
        variables[f"{current}_out"] = result
        variables[f"{current}_files"] = abs_files
        trace.append({
            "step_id": current, "name": step.get("name") or current, "status": status,
            "exit_code": result.get("exit_code"), "error": result.get("error"),
            "output_files": abs_files, "verify_report": result.get("verify_report"),
            "stdout_tail": result.get("stdout_tail"), "stderr_tail": result.get("stderr_tail"),
        })

        success = status == "success" or bool(step.get("continueOnError"))
        nxt = _next_step(edges, current, success)
        if not success and nxt is None:
            overall_failed = True
            break
        current = nxt

    return {
        "status": "failed" if overall_failed else "success",
        "steps": trace,
        "variables": {k: v for k, v in variables.items() if k.endswith("_files")},
    }
