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

import json
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

    # AIHUB-0045: a code flow is single-path per outcome — the walk/engine follows
    # ONE next step. Multiple outgoing edges of the SAME type from one step used
    # to be silently first-match-wins (live: after inserting a step "before the
    # upload", the stale direct pass edge kept winning and the new step never
    # ran). Make the ambiguity a hard error naming the competing edges and the
    # fix (unwire the stale one).
    from collections import Counter
    edge_counts = Counter(
        (e.get("from"), e.get("on", "pass"))
        for e in (defn.get("edges") or []) if isinstance(e, dict))
    for (frm, on), n in sorted(edge_counts.items(), key=lambda kv: str(kv[0])):
        if n > 1 and frm in id_set:
            targets = [e.get("to") for e in defn["edges"]
                       if isinstance(e, dict) and e.get("from") == frm and e.get("on", "pass") == on]
            errors.append(
                f"step '{frm}' has {n} competing '{on}' edges (to {targets}) — the flow "
                f"would silently follow only the first. unwire the stale edge(s) so each "
                f"step has at most one '{on}' edge.")

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


_AIHUB_INPUT_RE = re.compile(r"aihub\.input\(\s*['\"]([^'\"]+)['\"]")


def lint_input_names(code: str, inputs: Optional[List[Dict]]) -> Optional[str]:
    """Return an error if the step code reads an `aihub.input('NAME')` that is
    NOT in the step's declared inputs (AIHUB-0037: the reconcile step read
    aihub.input('src_csv') but declared 'parsed_csv' -> None -> crash). Catches
    the name-mismatch at author time. Returns None when every referenced input
    is declared."""
    declared = {i.get("name") for i in (inputs or []) if isinstance(i, dict) and i.get("name")}
    referenced = set(_AIHUB_INPUT_RE.findall(code or ""))
    missing = sorted(n for n in referenced if n not in declared)
    if not missing:
        return None
    return ("step code reads undeclared input(s) via aihub.input(): "
            + ", ".join(missing) + " — declared inputs are ["
            + ", ".join(sorted(declared)) + "]. Fix the name to match a declared input, "
            "or declare it (cross-step files come via an input defaulting to "
            "${<upstream_step_id>_files[0]}).")


_TRANSFER_OUTPUT_KINDS = {"sftp_upload", "ftp_upload"}
# Libraries/machinery a step must use for a remote transfer to actually happen.
_TRANSFER_LIB_RE = re.compile(
    r"\b(paramiko|pysftp|ftplib|fabric|socket|requests|urllib|http\.client|smbclient)\b")


def lint_transfer_honesty(code: str, outputs: Optional[List[Dict]]) -> Optional[str]:
    """AIHUB-0040: authoring-time honesty lint for remote-transfer outputs.

    The live reward-hack: the authoring agent declared an sftp_upload output,
    wrote code commented '# Simulated upload placeholder' that read the secret
    and logged 'Uploading …' WITHOUT any network operation, and omitted the
    output 'name' so verification had 'nothing to check'. Both patterns are
    rejected at save time:
      1. a transfer output must carry a verifiable 'name' (or 'remote_path');
      2. a step declaring a transfer output must actually contain transfer
         machinery (paramiko/pysftp/ftplib/socket/requests/urllib/...) — a
         'simulated'/placeholder step can never claim a transfer output.
    Returns an error string, or None when honest."""
    transfer_outs = [o for o in (outputs or [])
                     if isinstance(o, dict) and o.get("kind") in _TRANSFER_OUTPUT_KINDS]
    if not transfer_outs:
        return None
    unnamed = [o for o in transfer_outs if not (o.get("name") or o.get("remote_path"))]
    if unnamed:
        kinds = ", ".join(o.get("kind", "?") for o in unnamed)
        return (f"declared {kinds} output(s) without a 'name' (or 'remote_path') — there would be "
                f"nothing to verify at run time ('nothing to check' is not an acceptable outcome "
                f"for a transfer). Declare the uploaded filename, e.g. "
                f'{{"kind":"sftp_upload","name":"report.csv","remote_dir":"/outgoing",'
                f'"secret":"MY_SFTP","verify":{{"remote_listing":true}}}}.')
    if not _TRANSFER_LIB_RE.search(code or ""):
        return ("step declares a remote-transfer output (sftp_upload/ftp_upload) but the code "
                "never opens a network connection — no paramiko/pysftp/ftplib/socket/requests/"
                "urllib usage found. Placeholder or 'simulated' transfer steps are not allowed: "
                "write the real transfer (paramiko for SFTP), or remove the transfer output.")
    return None


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
                "allowUnverified": bool(s.get("allowUnverified", False)),
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
    """${expr} substitution on a string input value. Mirrors the ENGINE's
    _replace_variable_references so the dry-run walk predicts the scheduled path
    faithfully: a reference always resolves to TEXT — a resolved str is kept
    verbatim, a resolved dict/list is json.dumps'd, other scalars are str()'d
    (this is why cross-step passing uses a string element like ${s1_files[0]},
    not a whole ${s1_out} object)."""
    if not isinstance(value, str):
        return value

    def _as_text(r):
        if isinstance(r, str):
            return r
        if isinstance(r, (dict, list)):
            return json.dumps(r)
        return str(r)

    whole = _SUB_RE.fullmatch(value.strip())
    if whole:
        resolved = _get_path(variables, whole.group(1))
        return _as_text(resolved) if resolved is not None else value

    def _repl(mo):
        r = _get_path(variables, mo.group(1))
        return _as_text(r) if r is not None else mo.group(0)
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
            # Unique per VISIT (not just per step id) so a re-entered step on a
            # loop/back-edge gets a fresh dir — matches the engine's fresh
            # uuid-workdir-per-execution and avoids counting a prior visit's
            # files as this visit's output.
            workdir=os.path.join(base_workdir, f"{current}_{visits[current]}"),
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
            # AIHUB-0040: transfer-claim evidence rides along so the chat summary
            # can say "nothing was transferred" deterministically.
            "egress": result.get("egress"),
            "no_egress_transfer": bool(result.get("no_egress_transfer")),
        })

        # A step "passes" (takes the pass edge) on success; 'unverified' passes
        # only if the step opted in (allowUnverified), mirroring the Automation
        # node; continueOnError forces the pass edge regardless.
        success = (status == "success"
                   or (status == "unverified" and bool(step.get("allowUnverified")))
                   or bool(step.get("continueOnError")))
        nxt = _next_step(edges, current, success)
        if not success and nxt is None:
            overall_failed = True
            break
        current = nxt

    # If the loop stopped because we hit the step ceiling with more graph to run
    # (current still set), that is a truncated/runaway flow — NOT a success.
    if current and len(trace) >= max_steps:
        trace.append({"step_id": current, "status": "error",
                      "error": f"max_steps ({max_steps}) reached before the flow terminated"})
        overall_failed = True

    return {
        "status": "failed" if overall_failed else "success",
        "steps": trace,
        "variables": {k: v for k, v in variables.items() if k.endswith("_files")},
    }
