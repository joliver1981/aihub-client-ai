"""Four-gate quality filter for synthetic training records.

Usage:
    from training.synthesize.judge import pass_all_gates
    ok, details = pass_all_gates(plan, commands, run_judge=True)

Gates (in order, cheap-to-expensive):
  1. JSON round-trip: serialize/deserialize assistant block with no escape
     issues.
  2. Schema: every command is a known type with required fields.
  3. Compile: materialize_commands() returns without error.
  4. Judge (LLM): given (plan, commands), confirm the commands faithfully
     realize the plan. Returns a score + rationale.

Gates 1-3 are deterministic and free. Gate 4 calls an LLM and costs money —
disable with run_judge=False when running at scale or when scaffolding.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from typing import Dict, Optional, Tuple

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from training.curate.validate import _schema_errors, _materialize, _CompileUnavailable
from training.llm import complete

logger = logging.getLogger("synthesize.judge")


JUDGE_SYSTEM = """You are grading whether a workflow JSON correctly realizes a user's natural-language plan.

Read the PLAN and the COMMANDS (a list of add_node/connect_nodes/set_start_node operations that build a workflow graph).

Respond with JSON only:
{
  "fulfills_plan": true | false,
  "missing_steps": [ "..." ],   // steps described in the plan that are absent from the commands
  "extra_steps":   [ "..." ],   // commands that are not in the plan (positions/ids don't count)
  "severity":      "none" | "minor" | "major",
  "notes":         "short rationale"
}

- Position/node_id differences DO NOT count as mismatches.
- Ignore cosmetic label wording differences.
- "Minor" = small defaults or layout choices differ but logic is right.
- "Major" = a required step is missing, wrong node type, or mis-wired branch.
"""


def _json_round_trip(assistant_block: str) -> Tuple[bool, Optional[dict]]:
    match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", assistant_block)
    if match:
        try:
            data = json.loads(match.group(1))
            json.dumps(data, ensure_ascii=False)  # serialize back to prove it's clean
            return True, data
        except json.JSONDecodeError:
            return False, None
    try:
        data = json.loads(assistant_block)
        json.dumps(data, ensure_ascii=False)
        return True, data
    except json.JSONDecodeError:
        return False, None


def judge_commands(
    plan: str,
    commands: Dict,
    *,
    backend: Optional[str] = None,
    model: Optional[str] = None,
    dry_run: bool = False,
) -> Dict:
    """Call the LLM judge. Returns the parsed verdict (or a stub in dry-run)."""
    if dry_run:
        return {
            "fulfills_plan": True,
            "missing_steps": [],
            "extra_steps": [],
            "severity": "none",
            "notes": "<dry_run>",
        }
    user = (
        "PLAN:\n"
        + plan.strip()
        + "\n\nCOMMANDS:\n"
        + json.dumps(commands, indent=2, ensure_ascii=False)
    )
    raw = complete(JUDGE_SYSTEM, user, backend=backend, model=model, temperature=0.0, max_tokens=800)
    # Extract first JSON object from the response.
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return {"fulfills_plan": False, "severity": "major", "notes": f"judge unparseable: {raw[:200]}"}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        return {"fulfills_plan": False, "severity": "major", "notes": f"judge JSON error: {exc}"}


def pass_all_gates(
    plan: str,
    assistant_block: str,
    *,
    run_compile: bool = True,
    run_judge: bool = False,
    judge_backend: Optional[str] = None,
    judge_model: Optional[str] = None,
    judge_dry_run: bool = False,
) -> Tuple[bool, Dict]:
    """Run gates 1-4. Returns (pass, details)."""
    details: Dict = {"gates": {}}

    ok_rt, commands = _json_round_trip(assistant_block)
    details["gates"]["json_round_trip"] = bool(ok_rt)
    if not ok_rt or commands is None:
        return False, details

    sch_errs = _schema_errors(commands)
    details["gates"]["schema"] = not sch_errs
    details["schema_errors"] = sch_errs
    if sch_errs:
        return False, details

    if run_compile:
        try:
            _materialize(commands)
            details["gates"]["compile"] = True
        except _CompileUnavailable as exc:
            # Don't fail the record, but flag that we couldn't check.
            details["gates"]["compile"] = None
            details["compile_unavailable"] = str(exc)
        except Exception as exc:  # noqa: BLE001
            details["gates"]["compile"] = False
            details["compile_error"] = f"{type(exc).__name__}: {exc}"
            return False, details
    else:
        details["gates"]["compile"] = None

    if run_judge:
        verdict = judge_commands(
            plan, commands, backend=judge_backend, model=judge_model, dry_run=judge_dry_run
        )
        details["gates"]["judge"] = verdict
        # Fail only on major severity; minor is kept but flagged.
        if verdict.get("severity") == "major" or not verdict.get("fulfills_plan", True):
            return False, details
    else:
        details["gates"]["judge"] = None

    return True, details
