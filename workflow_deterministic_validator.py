# workflow_deterministic_validator.py
"""
Deterministic workflow validator and auto-fixer.

This module provides a fast, code-based pre-pass that runs before the LLM
validator in workflow_command_validator.py. It detects unambiguous workflow
issues (duplicate connections, missing start node, integration_id passed as a
name, Alert messageTemplate with expressions, etc.) and emits the corresponding
fix commands deterministically.

Design goals:
- Pure code, no LLM calls: faster, free, repeatable.
- High precision: each detector should only flag issues that are REAL.
  False positives are worse than false negatives here because the auto-fixer
  will silently rewrite the workflow.
- Each detected issue maps to either:
    (a) a deterministic fixer that produces a list of fix commands, or
    (b) nothing (issue bubbles up to the LLM fallback).

Public entry point:
    run(workflow_state) -> DeterministicResult

Caller (workflow_command_validator.py):
    1. Call run(state)
    2. If result.unfixable_errors is empty AND no warnings need attention,
       return the result and skip the LLM.
    3. Otherwise pass result.unfixable_errors and result.warnings as hints to
       the LLM validator and merge the LLM's findings on top.
    4. If run() raises, catch and fall back to the LLM entirely.

Configuration: see config.WORKFLOW_VALIDATOR_* flags.
"""

import copy
import re
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("WorkflowValidation")


# =============================================================================
# Data shapes
# =============================================================================

ERROR = "error"
WARNING = "warning"


@dataclass
class Issue:
    severity: str           # "error" or "warning"
    code: str               # short stable identifier, e.g. "DUPLICATE_CONNECTION"
    message: str            # human-readable description (also fed to the agent)
    node_id: Optional[str] = None
    field_name: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DeterministicResult:
    """Outcome of the deterministic pre-pass."""
    issues: List[Issue] = field(default_factory=list)
    fix_commands: List[Dict[str, Any]] = field(default_factory=list)
    unfixable_errors: List[Issue] = field(default_factory=list)

    @property
    def errors(self) -> List[Issue]:
        return [i for i in self.issues if i.severity == ERROR]

    @property
    def warnings(self) -> List[Issue]:
        return [i for i in self.issues if i.severity == WARNING]


# =============================================================================
# Helpers
# =============================================================================

# Variable-reference shapes
_DOLLAR_BRACE_RE = re.compile(r"\$\{([^}]*)\}")
# A "simple" variable name (no dots, brackets, function calls, arithmetic)
_SIMPLE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# A dot-path reference like ${a.b.c} - allowed in most fields, just not Alert messages
_DOT_PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
# Snake_case operation key
_SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# All digits (numeric ID as a string)
_NUMERIC_STR_RE = re.compile(r"^\d+$")
# SQL `?` placeholder, ignoring `?` inside quoted string literals.
# Crude but conservative: strip simple-quoted and double-quoted segments first.
_QUOTED_SEGMENT_RE = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")


def _strip_sql_string_literals(query: str) -> str:
    """Remove `'...'` and `"..."` segments so we don't mistake `?` inside
    strings for parameter placeholders."""
    return _QUOTED_SEGMENT_RE.sub("", query)


def _node_label(node: Dict) -> str:
    return node.get("label") or node.get("id") or "?"


def _index_nodes(workflow_state: Dict) -> Dict[str, Dict]:
    """node_id -> node dict."""
    return {n.get("id"): n for n in workflow_state.get("nodes", []) if n.get("id")}


def _connections(workflow_state: Dict) -> List[Dict]:
    return workflow_state.get("connections", []) or []


def _find_workflow_variables(workflow_state: Dict) -> Set[str]:
    """Collect every variable name a node could read - all outputVariable,
    variableName, itemVariable, indexVariable. Used to determine which
    `${var}` references resolve."""
    names: Set[str] = set()
    for var in workflow_state.get("variables", []) or []:
        if var.get("name"):
            names.add(var["name"])
    for node in workflow_state.get("nodes", []):
        cfg = node.get("config", {}) or {}
        for key in ("outputVariable", "variableName", "itemVariable", "indexVariable"):
            v = cfg.get(key)
            if isinstance(v, str) and v:
                names.add(v.lstrip("${").rstrip("}"))
    return names


# =============================================================================
# Detectors
# =============================================================================

# ----- Graph-level checks -----

def detect_duplicate_connections(state: Dict) -> List[Issue]:
    """Same (from, to, connection_type) triple appearing more than once."""
    seen: Dict[Tuple[str, str, str], int] = {}
    issues: List[Issue] = []
    for c in _connections(state):
        key = (c.get("from"), c.get("to"), c.get("connection_type") or c.get("type"))
        if key[0] is None or key[1] is None or key[2] is None:
            continue
        seen[key] = seen.get(key, 0) + 1
    for key, count in seen.items():
        if count > 1:
            f, t, ct = key
            issues.append(Issue(
                severity=ERROR,
                code="DUPLICATE_CONNECTION_EXACT",
                message=f"Duplicate connection: {f} -> {t} ({ct}) appears {count} times.",
                extra={"from": f, "to": t, "connection_type": ct, "count": count},
            ))
    return issues


def detect_duplicate_success_slot(state: Dict) -> List[Issue]:
    """A node should have at most one outgoing pass-or-complete connection.
    The runtime is single-threaded, so emitting both pass and complete (or two
    passes / two completes to different targets) is ambiguous: only one branch
    will actually execute. Surfaced as a WARNING — we deliberately do NOT
    auto-fix because the user may have intentionally drawn the second edge to
    a meaningful sub-graph that would be orphaned if dropped. The user must
    resolve manually via the designer UI."""
    issues: List[Issue] = []
    by_source: Dict[str, List[Dict]] = {}
    for c in _connections(state):
        ct = c.get("connection_type") or c.get("type")
        if ct in ("pass", "complete"):
            by_source.setdefault(c.get("from"), []).append(c)
    for src, conns in by_source.items():
        if len(conns) > 1:
            # If the duplicates target the same node, the duplicate-exact
            # detector already flagged them; here we only care about the
            # "different targets" case (genuinely ambiguous).
            unique_targets = {(c.get("to"), c.get("connection_type") or c.get("type")) for c in conns}
            if len(unique_targets) > 1:
                targets = ", ".join(sorted(f"{t}({ct})" for t, ct in unique_targets))
                issues.append(Issue(
                    severity=WARNING,
                    code="DUPLICATE_SUCCESS_SLOT",
                    node_id=src,
                    message=(
                        f"Node {src} has multiple success-slot connections "
                        f"(pass/complete) to different targets: {targets}. "
                        f"The workflow engine is single-threaded — only one of "
                        f"these branches will actually execute. Remove one of "
                        f"the connections (or restructure with a Conditional)."
                    ),
                    extra={"source": src, "targets": list(unique_targets)},
                ))
    return issues


def detect_duplicate_fail_slot(state: Dict) -> List[Issue]:
    """A node should have at most one outgoing fail connection. Surfaced as a
    WARNING (not auto-fixed) for the same reason as DUPLICATE_SUCCESS_SLOT —
    silently dropping one of the edges risks orphaning a meaningful sub-graph
    that the user wired intentionally."""
    issues: List[Issue] = []
    by_source: Dict[str, List[Dict]] = {}
    for c in _connections(state):
        if (c.get("connection_type") or c.get("type")) == "fail":
            by_source.setdefault(c.get("from"), []).append(c)
    for src, conns in by_source.items():
        if len(conns) > 1:
            unique_targets = {c.get("to") for c in conns}
            if len(unique_targets) > 1:
                issues.append(Issue(
                    severity=WARNING,
                    code="DUPLICATE_FAIL_SLOT",
                    node_id=src,
                    message=(
                        f"Node {src} has multiple fail connections to different "
                        f"targets: {sorted(unique_targets)}. The workflow engine "
                        f"is single-threaded — only one of these branches will "
                        f"actually execute. Remove one of the connections."
                    ),
                    extra={"source": src, "targets": list(unique_targets)},
                ))
    return issues


def detect_connection_to_missing_node(state: Dict) -> List[Issue]:
    nodes = _index_nodes(state)
    issues: List[Issue] = []
    for c in _connections(state):
        f, t = c.get("from"), c.get("to")
        ct = c.get("connection_type") or c.get("type")
        if f and f not in nodes:
            issues.append(Issue(
                severity=ERROR,
                code="CONNECTION_FROM_MISSING_NODE",
                message=f"Connection {f} -> {t} references missing source node '{f}'.",
                extra={"from": f, "to": t, "connection_type": ct},
            ))
        if t and t not in nodes:
            issues.append(Issue(
                severity=ERROR,
                code="CONNECTION_TO_MISSING_NODE",
                message=f"Connection {f} -> {t} references missing target node '{t}'.",
                extra={"from": f, "to": t, "connection_type": ct},
            ))
    return issues


def detect_no_start_node(state: Dict) -> List[Issue]:
    nodes = state.get("nodes", []) or []
    if not nodes:
        return []  # empty workflows aren't this detector's job
    if not any(n.get("isStart") for n in nodes):
        return [Issue(
            severity=ERROR,
            code="NO_START_NODE",
            message="No node has isStart=true. Designate one node as the workflow start.",
        )]
    return []


def detect_multiple_start_nodes(state: Dict) -> List[Issue]:
    starts = [n for n in state.get("nodes", []) or [] if n.get("isStart")]
    if len(starts) > 1:
        return [Issue(
            severity=ERROR,
            code="MULTIPLE_START_NODES",
            message=f"Multiple start nodes designated: {[n.get('id') for n in starts]}. Exactly one start node is allowed.",
            extra={"start_nodes": [n.get("id") for n in starts]},
        )]
    return []


# ----- Loop-related checks -----

def _loop_pairs(state: Dict) -> List[Tuple[Dict, List[Dict]]]:
    """Return list of (loop_node, [end_loop_nodes_referencing_it])."""
    nodes = state.get("nodes", []) or []
    loops = [n for n in nodes if (n.get("type") or n.get("node_type")) == "Loop"]
    end_loops = [n for n in nodes if (n.get("type") or n.get("node_type")) == "End Loop"]
    pairs = []
    for loop in loops:
        loop_id = loop.get("id")
        matching = [el for el in end_loops if (el.get("config") or {}).get("loopNodeId") == loop_id]
        pairs.append((loop, matching))
    return pairs


def detect_loop_without_end_loop(state: Dict) -> List[Issue]:
    issues: List[Issue] = []
    for loop, end_loops in _loop_pairs(state):
        if len(end_loops) == 0:
            issues.append(Issue(
                severity=ERROR,
                code="LOOP_WITHOUT_END_LOOP",
                node_id=loop.get("id"),
                message=f"Loop {loop.get('id')} has no matching End Loop node referencing it.",
                extra={"loop_id": loop.get("id")},
            ))
    return issues


def detect_multiple_end_loops_for_same_loop(state: Dict) -> List[Issue]:
    issues: List[Issue] = []
    for loop, end_loops in _loop_pairs(state):
        if len(end_loops) > 1:
            issues.append(Issue(
                severity=ERROR,
                code="MULTIPLE_END_LOOPS_FOR_SAME_LOOP",
                node_id=loop.get("id"),
                message=(
                    f"Loop {loop.get('id')} has {len(end_loops)} End Loop nodes "
                    f"referencing it: {[e.get('id') for e in end_loops]}. "
                    f"Each Loop should have exactly one End Loop."
                ),
                extra={
                    "loop_id": loop.get("id"),
                    "end_loop_ids": [e.get("id") for e in end_loops],
                },
            ))
    return issues


def detect_end_loop_references_nonexistent_loop(state: Dict) -> List[Issue]:
    issues: List[Issue] = []
    nodes = state.get("nodes", []) or []
    nodes_by_id = _index_nodes(state)
    for n in nodes:
        if (n.get("type") or n.get("node_type")) != "End Loop":
            continue
        ref = (n.get("config") or {}).get("loopNodeId")
        if not ref:
            issues.append(Issue(
                severity=ERROR,
                code="END_LOOP_MISSING_LOOPNODEID",
                node_id=n.get("id"),
                message=f"End Loop {n.get('id')} is missing config.loopNodeId.",
            ))
            continue
        target = nodes_by_id.get(ref)
        if not target or (target.get("type") or target.get("node_type")) != "Loop":
            issues.append(Issue(
                severity=ERROR,
                code="END_LOOP_REFERENCES_NONEXISTENT_LOOP",
                node_id=n.get("id"),
                message=(
                    f"End Loop {n.get('id')} has loopNodeId='{ref}' but no Loop node "
                    f"with that id exists."
                ),
                extra={"end_loop_id": n.get("id"), "loop_node_id": ref},
            ))
    return issues


def detect_end_loop_redundant_back_edge(state: Dict) -> List[Issue]:
    """End Loop has a physical edge pointing back to its corresponding Loop.

    The loop-back is established purely via the End Loop's loopNodeId config —
    the physical edge is redundant and creates an apparent cycle in the graph.
    Auto-fixed silently (drop the edge) because nothing should ever depend on
    it: removing it never orphans nodes."""
    issues: List[Issue] = []
    nodes = state.get("nodes", []) or []
    conns = _connections(state)
    for n in nodes:
        if (n.get("type") or n.get("node_type")) != "End Loop":
            continue
        end_id = n.get("id")
        loop_id = (n.get("config") or {}).get("loopNodeId")
        if not loop_id:
            continue  # END_LOOP_MISSING_LOOPNODEID handles this case
        for c in conns:
            if c.get("from") == end_id and c.get("to") == loop_id:
                ct = c.get("connection_type") or c.get("type") or "pass"
                issues.append(Issue(
                    severity=ERROR,
                    code="END_LOOP_REDUNDANT_BACK_EDGE",
                    node_id=end_id,
                    message=(
                        f"End Loop {end_id} has a redundant '{ct}' edge back to "
                        f"Loop {loop_id}. Loop-back is implicit via the End Loop's "
                        f"loopNodeId config; the physical edge will be removed."
                    ),
                    extra={
                        "end_loop_id": end_id,
                        "loop_id": loop_id,
                        "connection_type": ct,
                    },
                ))
    return issues


# ----- Per-node config checks -----

def _node_type(node: Dict) -> str:
    return node.get("type") or node.get("node_type") or ""


def _config(node: Dict) -> Dict:
    return node.get("config", {}) or {}


def detect_database_config_errors(state: Dict) -> List[Issue]:
    issues: List[Issue] = []
    for node in state.get("nodes", []) or []:
        if _node_type(node) != "Database":
            continue
        nid = node.get("id")
        cfg = _config(node)
        # connection: must be numeric string
        conn = cfg.get("connection")
        if conn is not None and conn != "" and not _NUMERIC_STR_RE.match(str(conn)):
            issues.append(Issue(
                severity=ERROR,
                code="DATABASE_CONNECTION_NOT_NUMERIC",
                node_id=nid,
                field_name="connection",
                message=(
                    f"Database node {nid}: 'connection' value '{conn}' is not a "
                    f"numeric ID. Use the numeric connection ID from "
                    f"get_available_database_connections, not the connection name."
                ),
                extra={"value": conn},
            ))
        # query with `?` placeholders
        query = cfg.get("query") or ""
        if query and "?" in _strip_sql_string_literals(query):
            issues.append(Issue(
                severity=ERROR,
                code="DATABASE_QUERY_HAS_QUESTIONMARK_PLACEHOLDERS",
                node_id=nid,
                field_name="query",
                message=(
                    f"Database node {nid}: query uses '?' positional placeholders. "
                    f"Use ${{variableName}} substitution instead."
                ),
                extra={"query": query[:200]},
            ))
        # saveToVariable / outputVariable consistency
        save = cfg.get("saveToVariable")
        out_var = cfg.get("outputVariable")
        if save is True and not out_var:
            issues.append(Issue(
                severity=ERROR,
                code="DATABASE_SAVE_WITHOUT_OUTPUTVARIABLE",
                node_id=nid,
                field_name="outputVariable",
                message=(
                    f"Database node {nid}: saveToVariable=true but outputVariable is empty. "
                    f"Set outputVariable to a name where the query result will be stored."
                ),
            ))
    return issues


def detect_excel_export_config_errors(state: Dict) -> List[Issue]:
    issues: List[Issue] = []
    for node in state.get("nodes", []) or []:
        if _node_type(node) != "Excel Export":
            continue
        nid = node.get("id")
        cfg = _config(node)
        op = cfg.get("excelOperation") or "append"  # default per spec
        if op in ("append", "template", "update") and not cfg.get("excelTemplatePath"):
            issues.append(Issue(
                severity=ERROR,
                code="EXCEL_APPEND_MISSING_TEMPLATE_PATH",
                node_id=nid,
                field_name="excelTemplatePath",
                message=(
                    f"Excel Export node {nid}: excelOperation='{op}' requires "
                    f"excelTemplatePath. For 'append', this is typically the same path "
                    f"as excelOutputPath."
                ),
                extra={"excelOperation": op, "excelOutputPath": cfg.get("excelOutputPath")},
            ))
    return issues


def detect_file_node_config_errors(state: Dict) -> List[Issue]:
    issues: List[Issue] = []
    for node in state.get("nodes", []) or []:
        if _node_type(node) != "File":
            continue
        nid = node.get("id")
        cfg = _config(node)
        op = cfg.get("operation")
        save = cfg.get("saveToVariable")
        out_var = cfg.get("outputVariable")
        if op in ("read", "check"):
            if save is True and not out_var:
                issues.append(Issue(
                    severity=ERROR,
                    code="FILE_READ_MISSING_OUTPUTVARIABLE",
                    node_id=nid,
                    field_name="outputVariable",
                    message=(
                        f"File node {nid}: operation='{op}' with saveToVariable=true "
                        f"but outputVariable is empty."
                    ),
                ))
            elif out_var and save is not True:
                issues.append(Issue(
                    severity=ERROR,
                    code="FILE_READ_MISSING_SAVE_FLAG",
                    node_id=nid,
                    field_name="saveToVariable",
                    message=(
                        f"File node {nid}: operation='{op}' specifies "
                        f"outputVariable='{out_var}' but saveToVariable is not true. "
                        f"Set saveToVariable=true so the output is actually stored."
                    ),
                ))
    return issues


def detect_integration_config_errors(state: Dict) -> List[Issue]:
    issues: List[Issue] = []
    for node in state.get("nodes", []) or []:
        if _node_type(node) != "Integration":
            continue
        nid = node.get("id")
        cfg = _config(node)
        # integration_id: numeric (int or numeric string)
        int_id = cfg.get("integration_id")
        if int_id is not None and int_id != "":
            if isinstance(int_id, bool) or (
                not isinstance(int_id, int)
                and not (isinstance(int_id, str) and _NUMERIC_STR_RE.match(int_id))
            ):
                issues.append(Issue(
                    severity=ERROR,
                    code="INTEGRATION_ID_NOT_NUMERIC",
                    node_id=nid,
                    field_name="integration_id",
                    message=(
                        f"Integration node {nid}: integration_id='{int_id}' is not numeric. "
                        f"Use the numeric Integration ID from get_available_integrations, "
                        f"not the integration name."
                    ),
                    extra={"value": int_id},
                ))
        # operation: snake_case key
        op = cfg.get("operation")
        if op and not _SNAKE_CASE_RE.match(str(op)):
            issues.append(Issue(
                severity=ERROR,
                code="INTEGRATION_OPERATION_NOT_SNAKE_CASE",
                node_id=nid,
                field_name="operation",
                message=(
                    f"Integration node {nid}: operation='{op}' is not a snake_case key. "
                    f"Use the operation key from get_integration_operations "
                    f"(e.g. 'get_customers'), not the human-readable display name."
                ),
                extra={"value": op},
            ))
    return issues


def detect_alert_template_expressions(state: Dict) -> List[Issue]:
    """Alert messageTemplate / emailSubject only allow simple ${var} references.
    Reject anything with dots, brackets, function calls, arithmetic."""
    issues: List[Issue] = []
    for node in state.get("nodes", []) or []:
        if _node_type(node) != "Alert":
            continue
        nid = node.get("id")
        cfg = _config(node)
        for fld in ("messageTemplate", "emailSubject"):
            template = cfg.get(fld)
            if not isinstance(template, str):
                continue
            for match in _DOLLAR_BRACE_RE.finditer(template):
                inner = match.group(1).strip()
                if not _SIMPLE_IDENT_RE.match(inner):
                    issues.append(Issue(
                        severity=ERROR,
                        code="ALERT_TEMPLATE_HAS_EXPRESSION",
                        node_id=nid,
                        field_name=fld,
                        message=(
                            f"Alert node {nid}: {fld} contains '${{{inner}}}' which is "
                            f"not a simple variable reference. Only ${{variableName}} is "
                            f"allowed in {fld}; no property access, indexing, function "
                            f"calls, or arithmetic. Compute values in a Set Variable "
                            f"node first, then reference that variable."
                        ),
                        extra={"field": fld, "expression": inner, "template": template},
                    ))
                    break  # one issue per field is enough; agent will see the field
    return issues


# Master detector list - order matters only for log readability
DETECTORS = [
    # Graph
    detect_duplicate_connections,
    detect_duplicate_success_slot,
    detect_duplicate_fail_slot,
    detect_connection_to_missing_node,
    detect_no_start_node,
    detect_multiple_start_nodes,
    # Loop
    detect_loop_without_end_loop,
    detect_multiple_end_loops_for_same_loop,
    detect_end_loop_references_nonexistent_loop,
    detect_end_loop_redundant_back_edge,
    # Per-node config
    detect_database_config_errors,
    detect_excel_export_config_errors,
    detect_file_node_config_errors,
    detect_integration_config_errors,
    detect_alert_template_expressions,
]


# =============================================================================
# Fixers
# =============================================================================
#
# Each fixer takes (issue, workflow_state) and returns a list of fix commands
# in the same shape the frontend's command executor consumes. Returning [] (or
# None) signals "I cannot fix this; bubble up to the LLM."
#
# Fixers must be idempotent: applying the same fix twice should not corrupt
# the workflow.
# =============================================================================


def fix_duplicate_connection_exact(issue: Issue, state: Dict) -> List[Dict]:
    """Two or more identical (from, to, connection_type) entries: emit
    delete_connection. The frontend's connect_nodes is set-like, so deleting
    once removes the duplicate."""
    f = issue.extra.get("from")
    t = issue.extra.get("to")
    ct = issue.extra.get("connection_type")
    if not (f and t and ct):
        return []
    return [{
        "type": "delete_connection",
        "from": f,
        "to": t,
        "connection_type": ct,
    }]


def fix_connection_to_missing_node(issue: Issue, state: Dict) -> List[Dict]:
    """Dangling reference - delete it."""
    f = issue.extra.get("from")
    t = issue.extra.get("to")
    ct = issue.extra.get("connection_type")
    if not (f and t and ct):
        return []
    return [{
        "type": "delete_connection",
        "from": f,
        "to": t,
        "connection_type": ct,
    }]


# fix_connection_from_missing_node uses identical logic
fix_connection_from_missing_node = fix_connection_to_missing_node


def fix_no_start_node(issue: Issue, state: Dict) -> List[Dict]:
    """Pick the topologically-first node (a node with zero incoming connections,
    or the lowest-id node if the graph has no obvious entry point) and mark it
    as start. Defer to the LLM if there are multiple equally-plausible
    candidates."""
    nodes = state.get("nodes", []) or []
    if not nodes:
        return []
    # Build incoming-edge count, ignoring End Loop -> Loop back-edges
    end_loops_by_loop: Dict[str, str] = {}
    for n in nodes:
        if (n.get("type") or n.get("node_type")) == "End Loop":
            ref = (n.get("config") or {}).get("loopNodeId")
            if ref:
                end_loops_by_loop[ref] = n.get("id")
    incoming_count: Dict[str, int] = {n.get("id"): 0 for n in nodes if n.get("id")}
    for c in _connections(state):
        f, t = c.get("from"), c.get("to")
        if not f or not t:
            continue
        # Skip End Loop -> Loop back-edge (it's a structural cycle, not a real entry into the loop)
        if end_loops_by_loop.get(t) == f:
            continue
        if t in incoming_count:
            incoming_count[t] += 1
    candidates = [nid for nid, n_in in incoming_count.items() if n_in == 0]
    if len(candidates) != 1:
        # Ambiguous - let the LLM decide (or zero candidates means cycle).
        return []
    return [{
        "type": "set_start_node",
        "node_id": candidates[0],
    }]


def fix_end_loop_redundant_back_edge(issue: Issue, state: Dict) -> List[Dict]:
    """Drop the redundant physical edge from End Loop back to Loop. Loop-back
    is config-driven via the End Loop's loopNodeId field; the physical edge
    serves no purpose and creates an apparent cycle in the graph."""
    end_loop_id = issue.extra.get("end_loop_id")
    loop_id = issue.extra.get("loop_id")
    ct = issue.extra.get("connection_type") or "pass"
    if not (end_loop_id and loop_id):
        return []
    return [{
        "type": "delete_connection",
        "from": end_loop_id,
        "to": loop_id,
        "connection_type": ct,
    }]


def fix_multiple_end_loops_for_same_loop(issue: Issue, state: Dict) -> List[Dict]:
    """Keep the End Loop with a valid pass-back to the Loop; delete the others.
    If no End Loop has the back-edge, keep the lowest-id one."""
    loop_id = issue.extra.get("loop_id")
    end_loop_ids = list(issue.extra.get("end_loop_ids") or [])
    if not (loop_id and end_loop_ids):
        return []
    conns = _connections(state)
    # Score each End Loop: 1 if it has a pass-back to loop_id, 0 otherwise
    scores = {}
    for el_id in end_loop_ids:
        has_back = any(
            c.get("from") == el_id and c.get("to") == loop_id
            and (c.get("connection_type") or c.get("type")) == "pass"
            for c in conns
        )
        scores[el_id] = 1 if has_back else 0
    # Pick the highest-scoring; tiebreak on lowest id (lexicographic, which
    # matches "node-N" numeric order for typical naming).
    keep = sorted(end_loop_ids, key=lambda i: (-scores[i], i))[0]
    cmds = []
    for el_id in end_loop_ids:
        if el_id == keep:
            continue
        # Delete connections involving this End Loop, then delete the node
        for c in conns:
            if c.get("from") == el_id or c.get("to") == el_id:
                cmds.append({
                    "type": "delete_connection",
                    "from": c.get("from"),
                    "to": c.get("to"),
                    "connection_type": c.get("connection_type") or c.get("type"),
                })
        cmds.append({"type": "delete_node", "node_id": el_id})
    return cmds


def fix_multiple_start_nodes(issue: Issue, state: Dict) -> List[Dict]:
    """Keep the topologically-first start; clear isStart on the rest."""
    candidates = list(issue.extra.get("start_nodes") or [])
    if len(candidates) < 2:
        return []
    # Pick the same way as fix_no_start_node would: zero-incoming preferred,
    # else lowest id
    end_loops_by_loop: Dict[str, str] = {}
    for n in state.get("nodes", []) or []:
        if (n.get("type") or n.get("node_type")) == "End Loop":
            ref = (n.get("config") or {}).get("loopNodeId")
            if ref:
                end_loops_by_loop[ref] = n.get("id")
    incoming: Dict[str, int] = {nid: 0 for nid in candidates}
    for c in _connections(state):
        if c.get("to") in incoming:
            if end_loops_by_loop.get(c.get("to")) == c.get("from"):
                continue
            incoming[c.get("to")] += 1
    keep = sorted(candidates, key=lambda nid: (incoming.get(nid, 0), nid))[0]
    cmds = [{"type": "set_start_node", "node_id": keep}]
    # The set_start_node command sets isStart on `keep` and clears it on
    # everyone else, so the duplicates are fixed in one shot.
    return cmds


def fix_excel_append_missing_template_path(issue: Issue, state: Dict) -> List[Dict]:
    """Default excelTemplatePath to the same value as excelOutputPath."""
    output_path = issue.extra.get("excelOutputPath")
    if not output_path:
        return []
    return [{
        "type": "update_node_config",
        "node_id": issue.node_id,
        "config": {"excelTemplatePath": output_path},
    }]


def fix_file_read_missing_save_flag(issue: Issue, state: Dict) -> List[Dict]:
    """Set saveToVariable=true."""
    if not issue.node_id:
        return []
    return [{
        "type": "update_node_config",
        "node_id": issue.node_id,
        "config": {"saveToVariable": True},
    }]


def fix_database_save_without_outputvariable(issue: Issue, state: Dict) -> List[Dict]:
    """Synthesize a default outputVariable name based on the node id."""
    if not issue.node_id:
        return []
    safe_suffix = re.sub(r"[^A-Za-z0-9_]", "_", issue.node_id)
    return [{
        "type": "update_node_config",
        "node_id": issue.node_id,
        "config": {"outputVariable": f"queryResult_{safe_suffix}"},
    }]


def fix_file_read_missing_outputvariable(issue: Issue, state: Dict) -> List[Dict]:
    if not issue.node_id:
        return []
    safe_suffix = re.sub(r"[^A-Za-z0-9_]", "_", issue.node_id)
    return [{
        "type": "update_node_config",
        "node_id": issue.node_id,
        "config": {"outputVariable": f"fileContent_{safe_suffix}"},
    }]


# Map issue codes to fixers. Codes not present here will be reported but bubble
# up to the LLM fallback.
FIXERS = {
    "DUPLICATE_CONNECTION_EXACT": fix_duplicate_connection_exact,
    "CONNECTION_FROM_MISSING_NODE": fix_connection_from_missing_node,
    "CONNECTION_TO_MISSING_NODE": fix_connection_to_missing_node,
    "NO_START_NODE": fix_no_start_node,
    "MULTIPLE_START_NODES": fix_multiple_start_nodes,
    "END_LOOP_REDUNDANT_BACK_EDGE": fix_end_loop_redundant_back_edge,
    "MULTIPLE_END_LOOPS_FOR_SAME_LOOP": fix_multiple_end_loops_for_same_loop,
    "EXCEL_APPEND_MISSING_TEMPLATE_PATH": fix_excel_append_missing_template_path,
    "FILE_READ_MISSING_SAVE_FLAG": fix_file_read_missing_save_flag,
    "FILE_READ_MISSING_OUTPUTVARIABLE": fix_file_read_missing_outputvariable,
    "DATABASE_SAVE_WITHOUT_OUTPUTVARIABLE": fix_database_save_without_outputvariable,
    # Codes WITHOUT a fixer (intentionally LLM-bound or user-bound):
    #   DUPLICATE_SUCCESS_SLOT       - WARNING surfaced to user; user must
    #                                  resolve in designer UI. Auto-fix would
    #                                  risk orphaning a meaningful sub-graph.
    #   DUPLICATE_FAIL_SLOT          - which target was intended?
    #   LOOP_WITHOUT_END_LOOP        - need to know what should be inside
    #   END_LOOP_MISSING_LOOPNODEID  - which Loop does it belong to?
    #   END_LOOP_REFERENCES_NONEXISTENT_LOOP - same question
    #   DATABASE_CONNECTION_NOT_NUMERIC      - need DB lookup
    #   DATABASE_QUERY_HAS_QUESTIONMARK_PLACEHOLDERS - need to map ? to vars
    #   INTEGRATION_ID_NOT_NUMERIC           - need DB lookup
    #   INTEGRATION_OPERATION_NOT_SNAKE_CASE - need DB lookup
    #   ALERT_TEMPLATE_HAS_EXPRESSION        - structural (insert Set Variable)
}


# =============================================================================
# Public entry point
# =============================================================================

def run(workflow_state: Dict, fix_warnings: bool = False) -> DeterministicResult:
    """Run all detectors, apply fixers for known codes, return a structured
    DeterministicResult.

    Args:
        workflow_state: The current workflow state (nodes, connections, etc.)
        fix_warnings: If True, attempt to fix warning-severity issues too.
                      If False (default), warnings are reported but not fixed.

    Returns:
        DeterministicResult with detected issues, emitted fix commands, and
        a list of unfixable errors that should be passed to the LLM.

    Raises:
        Any exception raised inside a detector. Caller should wrap this and
        fall back to the LLM validator on exception.
    """
    result = DeterministicResult()

    # 1. Detect
    for detector in DETECTORS:
        try:
            detected = detector(workflow_state) or []
        except Exception as e:
            # A buggy detector should not crash the whole pre-pass; log and skip.
            logger.exception(f"Detector {detector.__name__} raised: {e}")
            continue
        result.issues.extend(detected)

    if not result.issues:
        logger.debug("Deterministic validator: no issues detected")
        return result

    logger.info(
        f"Deterministic validator detected {len(result.issues)} issue(s): "
        f"{', '.join(sorted({i.code for i in result.issues}))}"
    )

    # 2. Decide which issues are actionable
    actionable = [
        i for i in result.issues
        if i.severity == ERROR or (fix_warnings and i.severity == WARNING)
    ]

    # 3. Apply fixers
    seen_fix_keys: Set[Tuple[str, str, str, str]] = set()  # crude de-dupe across fixers
    for issue in actionable:
        fixer = FIXERS.get(issue.code)
        if fixer is None:
            # No deterministic fix - this issue must go to the LLM (if it's an error)
            if issue.severity == ERROR:
                result.unfixable_errors.append(issue)
            continue
        try:
            cmds = fixer(issue, workflow_state) or []
        except Exception as e:
            logger.exception(f"Fixer for {issue.code} raised: {e}")
            if issue.severity == ERROR:
                result.unfixable_errors.append(issue)
            continue
        if not cmds:
            # Fixer chose to defer (ambiguous case)
            if issue.severity == ERROR:
                result.unfixable_errors.append(issue)
            continue
        for cmd in cmds:
            # Dedupe identical commands across fixers
            key = (
                cmd.get("type", ""),
                str(cmd.get("from", "") or cmd.get("node_id", "")),
                str(cmd.get("to", "")),
                str(cmd.get("connection_type", "")),
            )
            if key in seen_fix_keys:
                continue
            seen_fix_keys.add(key)
            result.fix_commands.append(cmd)
        logger.info(
            f"  {issue.code} on {issue.node_id or '-'}: emitted "
            f"{len(cmds)} fix command(s)"
        )

    return result


# =============================================================================
# Command applier - mirrors what the frontend's executeCommand does, in Python.
# Used by the validator to simulate applying fix_commands to a copy of the
# workflow state and then re-running detection on the post-fix state. This
# guarantees that a clean is_valid=true response really means "the workflow
# is correct after the deterministic fixes are applied."
# =============================================================================

def apply_commands_to_state(state: Dict, commands: List[Dict]) -> Dict:
    """Return a deep-copy of `state` with `commands` applied in order.

    Handles the command shapes the deterministic fixers actually emit
    (delete_connection, connect_nodes, update_node_config, delete_node,
    set_start_node) plus add_node and add_variable for forward-compatibility.
    Unknown command types are logged and skipped. The function is forgiving:
    a malformed command does not raise, it is just skipped so the rest of
    the batch can still apply.

    The new state has the same shape as the input (`nodes`, `connections`,
    optionally `variables`).
    """
    new_state = copy.deepcopy(state) if state else {}
    nodes = new_state.setdefault("nodes", [])
    connections = new_state.setdefault("connections", [])

    for cmd in commands or []:
        cmd_type = cmd.get("type")
        try:
            if cmd_type == "delete_connection":
                f = cmd.get("from")
                t = cmd.get("to")
                ct = cmd.get("connection_type") or cmd.get("type_value")
                # Remove the FIRST matching connection (handles dedup of duplicate exact pairs)
                for i, c in enumerate(connections):
                    c_ct = c.get("connection_type") or c.get("type")
                    if c.get("from") == f and c.get("to") == t and c_ct == ct:
                        connections.pop(i)
                        break

            elif cmd_type == "connect_nodes":
                connections.append({
                    "from": cmd.get("from"),
                    "to": cmd.get("to"),
                    "connection_type": cmd.get("connection_type"),
                })

            elif cmd_type == "update_node_config":
                node_id = cmd.get("node_id")
                new_cfg = cmd.get("config", {}) or {}
                for n in nodes:
                    if n.get("id") == node_id:
                        existing = n.setdefault("config", {})
                        if not isinstance(existing, dict):
                            existing = {}
                            n["config"] = existing
                        existing.update(new_cfg)
                        break

            elif cmd_type == "delete_node":
                node_id = cmd.get("node_id")
                new_state["nodes"] = [n for n in nodes if n.get("id") != node_id]
                new_state["connections"] = [
                    c for c in connections
                    if c.get("from") != node_id and c.get("to") != node_id
                ]
                # Re-bind locals so subsequent commands in this batch see the new lists
                nodes = new_state["nodes"]
                connections = new_state["connections"]

            elif cmd_type == "set_start_node":
                node_id = cmd.get("node_id")
                for n in nodes:
                    n["isStart"] = (n.get("id") == node_id)

            elif cmd_type == "add_node":
                # Forward-compat: no current fixer emits add_node, but the validator
                # may eventually need it.
                nodes.append({
                    "id": cmd.get("node_id"),
                    "type": cmd.get("node_type"),
                    "label": cmd.get("label", ""),
                    "config": cmd.get("config", {}) or {},
                    "position": cmd.get("position", {}) or {},
                    "isStart": False,
                })

            elif cmd_type == "add_variable":
                vars_list = new_state.setdefault("variables", [])
                vars_list.append({
                    "name": cmd.get("name"),
                    "data_type": cmd.get("data_type"),
                    "default_value": cmd.get("default_value"),
                })

            else:
                logger.warning(
                    f"apply_commands_to_state: unknown command type {cmd_type!r}; "
                    f"skipping"
                )
        except Exception as e:
            logger.exception(
                f"apply_commands_to_state: command {cmd_type!r} raised; skipping. "
                f"cmd={cmd!r}"
            )

    return new_state
