"""Workflow run report — what happened in one execution, written for an
authoring AI (and a person) to diagnose from.

WHY (2026-10-09): building a workflow with the AI Workflow Builder took seven
rounds because the builder could not see a run: the user had to read the
Debug Panel and relay what it showed. Several failures were silent — a run
"Completed" after finding 0 files, or sent every item down a failure path.
This module turns the three execution tables into one report: per-node outcome
counts, the real error messages, every warning the run logged, loop item
counts, and explicit flags for the silent-success patterns. Pure functions over
plain dicts (no database) so it is unit-testable; the routes fetch the rows.
"""
from __future__ import annotations

import json
import os
from collections import Counter, OrderedDict
from datetime import datetime
from typing import Any, Dict, List, Optional


def _parse(value: Any) -> Any:
    if isinstance(value, (dict, list)) or value is None:
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def _seconds(start: Any, end: Any) -> Optional[float]:
    try:
        a = start if isinstance(start, datetime) else datetime.fromisoformat(str(start))
        b = end if isinstance(end, datetime) else datetime.fromisoformat(str(end))
        return round((b - a).total_seconds(), 1)
    except Exception:
        return None


def _list_lengths(obj: Any) -> Dict[str, int]:
    """Top-level list fields of an extraction result and their lengths."""
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, list):
                out[k] = len(v)
    return out


def build_run_report(execution: Dict, steps: List[Dict], logs: List[Dict],
                     max_examples: int = 3,
                     variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Summarise one workflow execution.

    execution: a WorkflowExecutions row; steps: StepExecutions rows; logs:
    ExecutionLogs rows (any order); variables: the workflow's own variables
    (the ones its definition declares) with their values in this run, or None.
    Returns {status, duration_s, nodes, warnings, flags, variables, text}.
    `max_examples` only limits how many distinct error / warning texts are
    quoted per node in `text` (every one is still counted)."""
    status = str(execution.get("status") or "unknown")
    duration = _seconds(execution.get("started_at"), execution.get("completed_at"))

    steps = sorted(steps or [], key=lambda s: str(s.get("started_at") or ""))
    by_step_id = {str(s.get("step_execution_id") or "").lower(): s for s in steps}

    nodes: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    for s in steps:
        nid = str(s.get("node_id") or "?")
        n = nodes.setdefault(nid, {
            "node_id": nid, "name": s.get("node_name") or nid, "type": s.get("node_type") or "",
            "runs": 0, "status": Counter(), "errors": Counter(), "warnings": Counter(),
            "condition": Counter(), "files_found": [], "loop_items": [], "rows_written": 0,
            "moved_to": Counter(), "extracted": Counter(), "automation": Counter(),
        })
        n["runs"] += 1
        n["status"][str(s.get("status") or "?")] += 1
        err = s.get("error_message")
        if err and str(err) != "None":
            n["errors"][str(err)] += 1
        out = _parse(s.get("output_data"))
        if not isinstance(out, dict):
            continue
        t = n["type"]
        if t == "Conditional" and "conditionResult" in out:
            n["condition"]["TRUE" if out.get("conditionResult") else "FALSE"] += 1
        elif t == "Folder Selector":
            files = out.get("allFiles")
            if isinstance(files, list):
                n["files_found"].append(len(files))
            elif out.get("filesFound") is False:
                n["files_found"].append(0)
            elif out.get("selectedFile"):
                n["files_found"].append(1)
        elif t == "Loop":
            st = out.get("_loopStats") or {}
            if isinstance(st, dict) and "totalItems" in st:
                n["loop_items"].append((st.get("totalItems"), st.get("processedItems")))
        elif t == "Excel Export":
            try:
                n["rows_written"] += int(out.get("rows_written") or 0)
            except (TypeError, ValueError):
                pass
        elif t == "File" and out.get("operation") in ("move", "copy"):
            dest = out.get("destinationPath") or ""
            n["moved_to"][os.path.dirname(str(dest)) or str(dest)] += 1
        elif t == "AI Extract":
            for k, v in _list_lengths(out.get("extraction")).items():
                n["extracted"][k] += v
        elif t == "Automation":
            n["automation"][str(out.get("status") or "?")] += 1

    run_warnings: Counter = Counter()
    for lg in logs or []:
        level = str(lg.get("log_level") or "").lower()
        if level not in ("warning", "error"):
            continue
        msg = str(lg.get("message") or "").strip()
        if not msg:
            continue
        step = by_step_id.get(str(lg.get("step_execution_id") or "").lower())
        if step is not None:
            node = nodes.get(str(step.get("node_id") or "?"))
            if node is not None:
                node["warnings"][f"{level}: {msg}"] += 1
                continue
        run_warnings[f"{level}: {msg}"] += 1

    # ------------------------------------------------ silent-success flags
    flags: List[str] = []
    for n in nodes.values():
        if n["type"] == "Folder Selector" and n["files_found"] and max(n["files_found"]) == 0:
            flags.append(f"'{n['name']}' found 0 files — nothing after it had anything to work on.")
        if n["type"] == "Loop":
            if n["loop_items"] and all((tot or 0) == 0 for tot, _ in n["loop_items"]):
                flags.append(f"Loop '{n['name']}' processed 0 items.")
        if n["type"] == "Conditional" and n["runs"] > 1 and len(n["condition"]) == 1:
            only = next(iter(n["condition"]))
            flags.append(f"Conditional '{n['name']}' was {only} on all {n['runs']} runs — "
                         f"every item took the same branch.")
        if n["type"] == "Excel Export" and n["runs"] and n["rows_written"] == 0 \
                and n["status"].get("Completed"):
            flags.append(f"Excel Export '{n['name']}' completed but wrote 0 rows.")
        if n["errors"] and n["status"].get("Failed", 0) == n["runs"] and n["runs"] > 1:
            flags.append(f"'{n['name']}' failed on every one of its {n['runs']} runs.")
        unresolved = [w for w in n["warnings"] if "could not be resolved" in w]
        if unresolved:
            flags.append(f"'{n['name']}' referenced a variable that does not exist at that point "
                         f"(see its warnings).")

    # Every item of a loop ended in a failure-looking folder (a run that
    # "Completed" while routing all of its work to failed/error/rejected).
    processed = sum((p or 0) for n in nodes.values() if n["type"] == "Loop" for _, p in n["loop_items"])
    failed_moves = Counter()
    for n in nodes.values():
        for dest, c in n["moved_to"].items():
            if any(w in os.path.basename(str(dest).rstrip("\\/")).lower() for w in ("fail", "error", "reject")):
                failed_moves[dest] += c
    if processed and sum(failed_moves.values()) >= processed:
        where = ", ".join(f"{d} ({c})" for d, c in failed_moves.items())
        flags.append(f"All {processed} loop item(s) ended up in a failure folder: {where}.")

    # A workflow setting (folder, workbook, recipient) with no value makes every
    # ${name} that uses it resolve to nothing — the step then fails or works on
    # the wrong thing, and nothing else in the report names the variable.
    variables = dict(variables or {})
    empty_vars = [n for n, v in variables.items()
                  if v is None or (isinstance(v, str) and not v.strip())]
    if empty_vars:
        flags.append(f"Workflow variable(s) {', '.join(empty_vars)} had no value in this run; "
                     f"every reference to them resolved to an empty value.")

    # ------------------------------------------------------------ text
    lines = [f"RUN REPORT — workflow '{execution.get('workflow_name') or execution.get('workflow_id')}', "
             f"execution {execution.get('execution_id')}",
             f"Status: {status}" + (f" in {duration:g} s" if duration is not None else "")]
    if flags:
        lines.append("")
        lines.append("ATTENTION:")
        lines.extend(f"  - {f}" for f in flags)
    if variables:
        lines.append("")
        lines.append("WORKFLOW VARIABLES (value in this run):")
        for name, value in variables.items():
            shown = json.dumps(value, ensure_ascii=False, default=str)
            lines.append(f"  - {name} = {shown[:160]}{'…' if len(shown) > 160 else ''}")
    lines.append("")
    lines.append("STEPS (in order of first run):")
    for n in nodes.values():
        st = ", ".join(f"{k} {v}" for k, v in n["status"].items())
        lines.append(f"- {n['name']} [{n['type']}] ran {n['runs']}x: {st}")
        if n["files_found"]:
            lines.append(f"    files found: {', '.join(str(x) for x in n['files_found'])}")
        if n["loop_items"]:
            lines.append("    items: " + ", ".join(f"{t} total / {p} processed" for t, p in n["loop_items"]))
        if n["condition"]:
            lines.append("    outcome: " + ", ".join(f"{k} {v}" for k, v in n["condition"].items()))
        if n["type"] == "Excel Export":
            lines.append(f"    rows written: {n['rows_written']}")
        if n["moved_to"]:
            lines.append("    moved to: " + ", ".join(f"{d} ({c})" for d, c in n["moved_to"].items()))
        if n["extracted"]:
            lines.append("    extracted: " + ", ".join(f"{k} {v} item(s)" for k, v in n["extracted"].items()))
        if n["automation"]:
            lines.append("    automation outcome: " + ", ".join(f"{k} {v}" for k, v in n["automation"].items()))
        for msg, c in list(n["errors"].items())[:max_examples]:
            lines.append(f"    ERROR x{c}: {msg}")
        if len(n["errors"]) > max_examples:
            lines.append(f"    ... {len(n['errors']) - max_examples} more distinct error(s)")
        for msg, c in list(n["warnings"].items())[:max_examples]:
            lines.append(f"    {msg}" + (f"  (x{c})" if c > 1 else ""))
        if len(n["warnings"]) > max_examples:
            lines.append(f"    ... {len(n['warnings']) - max_examples} more distinct warning(s)")
    if run_warnings:
        lines.append("")
        lines.append("RUN-LEVEL WARNINGS / ERRORS:")
        for msg, c in run_warnings.items():
            lines.append(f"  - {msg}" + (f"  (x{c})" if c > 1 else ""))

    serial_nodes = []
    for n in nodes.values():
        serial_nodes.append({
            "node_id": n["node_id"], "name": n["name"], "type": n["type"], "runs": n["runs"],
            "status": dict(n["status"]), "errors": dict(n["errors"]), "warnings": dict(n["warnings"]),
            "condition": dict(n["condition"]), "files_found": n["files_found"],
            "loop_items": n["loop_items"], "rows_written": n["rows_written"],
            "moved_to": dict(n["moved_to"]), "extracted": dict(n["extracted"]),
            "automation": dict(n["automation"]),
        })
    return {"status": status, "duration_s": duration, "nodes": serial_nodes,
            "warnings": dict(run_warnings), "flags": flags, "variables": variables,
            "text": "\n".join(lines)}


def fetch_run_report(execution_id: str, connection_factory, tenant_key: Optional[str] = None,
                     max_examples: int = 3) -> Optional[Dict[str, Any]]:
    """Read the three tables for one execution and build the report. Returns
    None when the execution does not exist."""
    conn = connection_factory()
    try:
        cur = conn.cursor()
        if tenant_key:
            cur.execute("EXEC tenant.sp_setTenantContext ?", tenant_key)

        def rows(sql, *params):
            cur.execute(sql, *params)
            cols = [c[0] for c in cur.description]
            out = []
            for r in cur.fetchall():
                d = {}
                for c, v in zip(cols, r):
                    d[c] = v.isoformat() if isinstance(v, datetime) else v
                out.append(d)
            return out

        ex = rows("SELECT * FROM WorkflowExecutions WHERE execution_id = ?", execution_id)
        if not ex:
            return None
        steps = rows("SELECT * FROM StepExecutions WHERE execution_id = ? ORDER BY started_at ASC", execution_id)
        logs = rows("SELECT * FROM ExecutionLogs WHERE execution_id = ? ORDER BY timestamp ASC", execution_id)
        # The workflow's own variables (the ones its definition declares) and
        # their values in this run. Best effort: the report stands without them.
        variables = None
        try:
            wf = rows("SELECT workflow_data FROM Workflows WHERE id = ?", ex[0].get("workflow_id"))
            declared = list(((_parse(wf[0]["workflow_data"]) or {}).get("variables") or {}).keys()) if wf else []
            if declared:
                vals = {str(r["variable_name"]): _parse(r["variable_value"]) for r in
                        rows("SELECT variable_name, variable_value FROM WorkflowVariables WHERE execution_id = ?",
                             execution_id)}
                variables = {n: vals.get(n) for n in declared}
        except Exception:
            variables = None
        return build_run_report(ex[0], steps, logs, max_examples=max_examples, variables=variables)
    finally:
        try:
            conn.close()
        except Exception:
            pass
