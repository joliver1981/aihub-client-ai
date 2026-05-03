"""Scoring engine for cmdgen candidate outputs.

Given a gold (plan, commands) pair and a candidate model's assistant output,
produce a per-metric verdict plus a composite CommandGen Score.

Metrics (all are 0.0..1.0 floats for easy averaging across a dataset):

  json_parse       1.0 if the assistant block contains extractable valid JSON
  schema           1.0 if the commands pass the deterministic schema checks
                   (known command types, known node types, required fields,
                   set_start_node present).
  compile          1.0 if workflow_compiler.materialize_commands() accepts it.
                   None (skipped in aggregate) when the compiler can't be
                   loaded in the scoring env.
  rule             1.0 if no validator rule-check warnings fired (saveToVariable
                   missing, unresolvable ${var} refs, etc).
  structural       0.0..1.0 graph-structure overlap with the gold workflow
                   (node-type multiset + connection-type edge list). Uses
                   topology, not node IDs, so different ID schemes don't hurt.
  judge            0.0 / 0.5 / 1.0 if the LLM judge says "major" / "minor" /
                   "fulfills". None if judge is disabled.

CommandGen Score = geometric mean of the available metrics, excluding
metrics set to None. This mirrors the composite specified in the plan. A
single-metric failure bounds the composite hard — which is what we want.
"""

from __future__ import annotations

import math
import os
import sys
from collections import Counter
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Tuple

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from training.curate.normalize import extract_assistant_json
from training.curate.validate import (
    _CompileUnavailable,
    _materialize,
    _rule_checks,
    _schema_errors,
)
from training.synthesize.judge import judge_commands


@dataclass
class RecordVerdict:
    json_parse: float = 0.0
    schema: float = 0.0
    compile: Optional[float] = 0.0   # None means "compiler unavailable, skip"
    rule: float = 0.0
    structural: float = 0.0
    judge: Optional[float] = None    # None when judge disabled
    # Diagnostics
    schema_errors: List[str] = field(default_factory=list)
    compile_error: Optional[str] = None
    rule_warnings: List[str] = field(default_factory=list)
    structural_details: Dict = field(default_factory=dict)
    judge_details: Optional[Dict] = None
    commandgen_score: float = 0.0

    def to_dict(self) -> Dict:
        return asdict(self)


def _commands_from_text(assistant_text: str) -> Optional[dict]:
    """Best-effort extraction. Accepts fenced ```json or bare JSON objects."""
    return extract_assistant_json(assistant_text)


def _structural_match(pred_cmds: dict, gold_cmds: dict) -> Tuple[float, Dict]:
    """Graph-structural overlap between predicted and gold commands.

    Score = mean of three sub-scores, each 0..1:
      node_type_multiset:    Jaccard over (node_type) multiset
      connection_edges:      Jaccard over (from_type -> to_type, conn_type) edges
      start_match:           1.0 if start node's type matches

    Deliberately ignores node_id, position, label text, and config values —
    those can legitimately differ. Logic shape is what matters.
    """
    def extract(commands: dict) -> Tuple[Counter, Counter, Optional[str]]:
        id_to_type: Dict[str, str] = {}
        start_id: Optional[str] = None
        for c in commands.get("commands", []):
            if c.get("type") == "add_node":
                nid = c.get("node_id")
                if nid:
                    id_to_type[nid] = c.get("node_type", "")
            elif c.get("type") == "set_start_node":
                start_id = c.get("node_id")
        node_types = Counter(id_to_type.values())
        edges: Counter = Counter()
        for c in commands.get("commands", []):
            if c.get("type") == "connect_nodes":
                frm = id_to_type.get(c.get("from"), "?")
                to = id_to_type.get(c.get("to"), "?")
                ct = c.get("connection_type", "pass")
                edges[(frm, to, ct)] += 1
        start_type = id_to_type.get(start_id) if start_id else None
        return node_types, edges, start_type

    p_types, p_edges, p_start = extract(pred_cmds)
    g_types, g_edges, g_start = extract(gold_cmds)

    def jaccard(a: Counter, b: Counter) -> float:
        if not a and not b:
            return 1.0
        # Multiset Jaccard = sum(min(a,b)) / sum(max(a,b))
        keys = set(a) | set(b)
        inter = sum(min(a.get(k, 0), b.get(k, 0)) for k in keys)
        union = sum(max(a.get(k, 0), b.get(k, 0)) for k in keys)
        return inter / union if union else 1.0

    node_score = jaccard(p_types, g_types)
    edge_score = jaccard(p_edges, g_edges)
    start_score = 1.0 if p_start and g_start and p_start == g_start else 0.0

    score = (node_score + edge_score + start_score) / 3.0
    details = {
        "node_multiset_jaccard": round(node_score, 3),
        "edge_jaccard": round(edge_score, 3),
        "start_node_type_match": start_score,
        "pred_node_types": dict(p_types),
        "gold_node_types": dict(g_types),
        "pred_start_type": p_start,
        "gold_start_type": g_start,
    }
    return score, details


def _geom_mean(values: List[float]) -> float:
    if not values:
        return 0.0
    # Clamp zeros so product doesn't collapse on one failed metric — we want
    # the composite to be bounded BY bad metrics, not zeroed. Clamp to a
    # small floor instead.
    FLOOR = 0.01
    log_sum = 0.0
    for v in values:
        log_sum += math.log(max(v, FLOOR))
    return math.exp(log_sum / len(values))


def score_record(
    *,
    gold_commands: dict,
    pred_assistant_text: str,
    plan: str,
    run_compile: bool = True,
    run_judge: bool = False,
    judge_backend: Optional[str] = None,
    judge_model: Optional[str] = None,
    judge_dry_run: bool = False,
) -> RecordVerdict:
    v = RecordVerdict()

    pred_cmds = _commands_from_text(pred_assistant_text)
    if not pred_cmds:
        # Nothing downstream is scorable.
        v.commandgen_score = _geom_mean([0.0, 0.0, 0.0, 0.0])
        return v
    v.json_parse = 1.0

    errs = _schema_errors(pred_cmds)
    v.schema_errors = errs
    v.schema = 0.0 if errs else 1.0

    if run_compile and not errs:
        try:
            _materialize(pred_cmds)
            v.compile = 1.0
        except _CompileUnavailable as exc:
            v.compile = None
            v.compile_error = f"unavailable: {exc}"
        except Exception as exc:  # noqa: BLE001
            v.compile = 0.0
            v.compile_error = f"{type(exc).__name__}: {exc}"
    elif errs:
        v.compile = 0.0
        v.compile_error = "skipped: schema errors"
    else:
        v.compile = None

    if not errs and v.compile == 1.0:
        try:
            workflow_state = _materialize(pred_cmds)
            v.rule_warnings = _rule_checks(workflow_state)
            v.rule = 1.0 if not v.rule_warnings else 0.0
        except Exception as exc:  # noqa: BLE001
            v.rule = 0.0
            v.rule_warnings = [f"rule_check_error: {exc}"]
    elif errs or v.compile == 0.0:
        v.rule = 0.0
    else:
        # Compile was skipped (unavailable). Rule is not meaningful either.
        v.rule = None  # type: ignore[assignment]

    s_score, s_details = _structural_match(pred_cmds, gold_commands)
    v.structural = round(s_score, 3)
    v.structural_details = s_details

    if run_judge:
        verdict = judge_commands(
            plan,
            pred_cmds,
            backend=judge_backend,
            model=judge_model,
            dry_run=judge_dry_run,
        )
        v.judge_details = verdict
        sev = verdict.get("severity", "major")
        if not verdict.get("fulfills_plan", False):
            v.judge = 0.0
        elif sev == "none":
            v.judge = 1.0
        elif sev == "minor":
            v.judge = 0.5
        else:
            v.judge = 0.0
    else:
        v.judge = None

    # Composite: geometric mean of available metrics (None excluded).
    metrics = [v.json_parse, v.schema]
    if v.compile is not None:
        metrics.append(v.compile)
        # Only include rule + structural if compile is defined — otherwise
        # they're measuring different things than the plan intends.
        metrics.append(v.rule)
    metrics.append(v.structural)
    if v.judge is not None:
        metrics.append(v.judge)
    v.commandgen_score = round(_geom_mean(metrics), 3)
    return v


def aggregate(verdicts: List[RecordVerdict]) -> Dict:
    """Aggregate per-record verdicts into a dataset-level report."""
    n = len(verdicts)
    if not n:
        return {"n": 0}

    def pct(attr: str) -> Optional[float]:
        vals = [getattr(v, attr) for v in verdicts if getattr(v, attr) is not None]
        if not vals:
            return None
        return round(sum(vals) / len(vals), 3)

    report = {
        "n": n,
        "json_parse": pct("json_parse"),
        "schema": pct("schema"),
        "compile": pct("compile"),
        "rule": pct("rule"),
        "structural": pct("structural"),
        "judge": pct("judge"),
        "commandgen_score": pct("commandgen_score"),
    }
    # Error catalog: top schema errors and compile errors.
    schema_err_counter: Counter = Counter()
    compile_err_counter: Counter = Counter()
    rule_warn_counter: Counter = Counter()
    for v in verdicts:
        for e in v.schema_errors:
            schema_err_counter[e[:100]] += 1
        if v.compile_error:
            compile_err_counter[v.compile_error[:100]] += 1
        for w in v.rule_warnings:
            rule_warn_counter[w[:100]] += 1
    report["top_schema_errors"] = schema_err_counter.most_common(10)
    report["top_compile_errors"] = compile_err_counter.most_common(10)
    report["top_rule_warnings"] = rule_warn_counter.most_common(10)
    return report
