"""Reverse-engineer production workflow JSONs into (plan, commands) pairs.

For each .json file in workflows/ (and solutions_builtin/**/workflows/):
  1. Load the saved workflow (nodes + connections + variables).
  2. Convert it BACKWARDS into a list of build commands — pure-Python, no LLM.
     This step is deterministic: given a node, we emit the add_node command
     that would recreate it, and given a connection, the connect_nodes command.
  3. Ask a frontier model to synthesize the natural-language *plan* that a
     user would write to produce this workflow. The output is exactly the
     style of numbered plan that CommandGenerator expects.
  4. Write one training record per workflow to seeds/from_workflows.jsonl.

Cost control:
  --dry-run    don't call the LLM; emit records with a placeholder plan so
               you can preview the pipeline shape.
  --limit N    only process the first N workflows (for quick sanity checks).
  --skip F     skip the first F workflows (for resuming).

When run for real, prints a running tally of LLM calls and estimated tokens.
Run this ONLY after the user authorizes the spend.
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from training.capture.extract_seeds import load_system_prompt_text
from training.llm import complete, usage_summary

logger = logging.getLogger("extract_workflow_json_seeds")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


WORKFLOW_DIRS = [
    os.path.join(_REPO_ROOT, "workflows"),
    os.path.join(_REPO_ROOT, "solutions_builtin"),
]

PLAN_SYNTHESIS_SYSTEM = """You are given a saved workflow definition (nodes, connections, variables) from an automation platform. Your task is to write the natural-language PLAN that a user would have given to generate this workflow.

The plan must:
- Be a numbered list, one step per node, in execution order (follow the pass/fail connections from the start node).
- Describe the node's purpose and key configuration values inline (e.g. "Folder Selector node: select all *.pdf files from C:\\\\invoices into variable inputFiles").
- Preserve exact details from the config (file paths, variable names, SQL queries verbatim, agent IDs, assignee IDs, exact field lists for AI Extract nodes, prompt text for AI Action nodes). Do NOT summarize or paraphrase these.
- For Conditional nodes, describe both the pass branch and the fail branch.
- Use the same phrasing style as a business user describing a process, not developer jargon.
- Output ONLY the plan text — no preamble, no markdown fences, no explanation.
"""


def _commands_from_workflow(workflow: Dict) -> Dict:
    """Invert materialize_commands: produce a commands list from a saved workflow.

    The resulting commands, when fed back through materialize_commands, should
    reproduce the workflow modulo node_id ordering.
    """
    commands: List[Dict] = []
    nodes = workflow.get("nodes", []) or []
    connections = workflow.get("connections", []) or []

    start_node_id: Optional[str] = None
    for node in nodes:
        cmd = {
            "type": "add_node",
            "node_type": node.get("type", ""),
            "label": node.get("label", node.get("type", "")),
            "config": node.get("config", {}) or {},
            "position": node.get("position", {"left": "20px", "top": "40px"}),
            "node_id": node.get("id"),
        }
        commands.append(cmd)
        if node.get("isStart"):
            start_node_id = node.get("id")

    for conn in connections:
        frm = conn.get("source") or conn.get("from")
        to = conn.get("target") or conn.get("to")
        ct = conn.get("type") or "pass"
        if frm and to:
            commands.append({
                "type": "connect_nodes",
                "from": frm,
                "to": to,
                "connection_type": ct,
            })

    if start_node_id:
        commands.append({"type": "set_start_node", "node_id": start_node_id})

    return {"action": "build_workflow", "commands": commands}


def _discover_workflow_files() -> List[str]:
    paths: List[str] = []
    for root in WORKFLOW_DIRS:
        if not os.path.isdir(root):
            continue
        paths.extend(glob.glob(os.path.join(root, "*.json")))
        paths.extend(glob.glob(os.path.join(root, "**", "*.json"), recursive=True))
    # Dedupe, sort, and drop obviously-non-workflow JSONs later by shape check.
    return sorted(set(paths))


def _looks_like_workflow(data: Dict) -> bool:
    if not isinstance(data, dict):
        return False
    if "nodes" not in data or not isinstance(data["nodes"], list):
        return False
    return True


def _make_summary_for_prompt(workflow: Dict, commands: Dict) -> str:
    """Compact representation of the workflow for the synthesis prompt."""
    return json.dumps(
        {
            "nodes": workflow.get("nodes", []),
            "connections": workflow.get("connections", []),
            "variables": workflow.get("variables", {}),
        },
        indent=2,
        ensure_ascii=False,
    )


def synthesize_plan(workflow: Dict, commands: Dict, *, backend: str, model: Optional[str], dry_run: bool) -> str:
    if dry_run:
        return "<DRY RUN: plan would be synthesized from the workflow JSON>"
    summary = _make_summary_for_prompt(workflow, commands)
    user = "Here is the saved workflow. Write the numbered plan.\n\n" + summary
    text = complete(
        PLAN_SYNTHESIS_SYSTEM,
        user,
        backend=backend,
        model=model,
        temperature=0.3,
        max_tokens=2000,
    )
    # Strip any ``` fencing the model may add despite instructions.
    return re.sub(r"^```[a-z]*\s*|```\s*$", "", text.strip(), flags=re.MULTILINE).strip()


def build_record(plan: str, commands: Dict, system_prompt: str, provenance: Dict) -> Dict:
    return {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": plan},
            {"role": "assistant", "content": f"```json\n{json.dumps(commands, indent=2)}\n```"},
        ],
        "_meta": {
            "source": "seed_workflow",
            **provenance,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--out",
        default=os.path.join(_REPO_ROOT, "training", "data", "cmdgen", "seeds", "from_workflows.jsonl"),
    )
    ap.add_argument("--dry-run", action="store_true", help="Skip LLM calls; emit placeholder plans")
    ap.add_argument("--limit", type=int, default=0, help="Process only the first N workflows")
    ap.add_argument("--skip", type=int, default=0, help="Skip the first N workflows")
    ap.add_argument("--backend", default=None, help="Override TRAINING_LLM_BACKEND")
    ap.add_argument("--model", default=None, help="Override model name")
    args = ap.parse_args()

    system_prompt = load_system_prompt_text()
    if not system_prompt:
        logger.error("Could not load COMMAND_GENERATOR_SYSTEM_PROMPT")
        return 2

    paths = _discover_workflow_files()
    logger.info("Discovered %d JSON file(s) under workflow dirs", len(paths))
    if args.skip:
        paths = paths[args.skip :]
    if args.limit:
        paths = paths[: args.limit]
    logger.info("Processing %d file(s) (skip=%d, limit=%d)", len(paths), args.skip, args.limit)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    written = 0
    skipped = 0
    failed = 0
    with open(args.out, "w", encoding="utf-8") as out:
        for i, p in enumerate(paths, start=1):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    workflow = json.load(f)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[%d/%d] load failed %s: %s", i, len(paths), p, exc)
                skipped += 1
                continue

            if not _looks_like_workflow(workflow):
                skipped += 1
                continue

            commands = _commands_from_workflow(workflow)
            if not commands["commands"]:
                logger.warning("[%d/%d] no commands produced for %s", i, len(paths), p)
                skipped += 1
                continue

            try:
                plan = synthesize_plan(
                    workflow,
                    commands,
                    backend=args.backend or os.getenv("TRAINING_LLM_BACKEND", "anthropic"),
                    model=args.model,
                    dry_run=args.dry_run,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("[%d/%d] plan synthesis failed for %s: %s", i, len(paths), p, exc)
                failed += 1
                continue

            record = build_record(
                plan,
                commands,
                system_prompt,
                {
                    "source_workflow_path": os.path.relpath(p, _REPO_ROOT),
                    "n_nodes": len(workflow.get("nodes", [])),
                    "n_connections": len(workflow.get("connections", [])),
                    "n_commands": len(commands["commands"]),
                    "dry_run": bool(args.dry_run),
                },
            )
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1
            logger.info(
                "[%d/%d] %s  (nodes=%d connections=%d commands=%d)",
                i,
                len(paths),
                os.path.basename(p),
                record["_meta"]["n_nodes"],
                record["_meta"]["n_connections"],
                record["_meta"]["n_commands"],
            )

    logger.info(
        "Done. written=%d  skipped=%d  failed=%d  out=%s",
        written,
        skipped,
        failed,
        args.out,
    )
    summary = usage_summary()
    logger.info("LLM usage: %s", json.dumps(summary))
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
