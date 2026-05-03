"""Mutate existing (plan, commands) seeds along one structural dimension.

Where templates produce *fresh* combinations, mutations exercise the model's
ability to make targeted edits — the same skill needed for refinement-mode
workflow editing in production.

Strategies (one chosen per mutation):
  swap_node_type     swap a Database for an Integration, an Alert for an
                     Excel Export, etc. Keeps surrounding structure.
  add_conditional    insert a Conditional + an alternate-branch Alert
                     between two adjacent nodes.
  wrap_loop          wrap an interior segment in a Folder Selector + Loop +
                     End Loop, propagating a per-item variable.
  add_human_approval insert a Human Approval node before the final action.
  add_step           insert a Set Variable, an Excel Export, or an Alert.
  swap_destination   swap an Alert's recipient or an Excel path.
  remove_step        drop a non-critical step (keeps workflow coherent).
  refinement_edit    "I forgot to add X" — tells the model to add one node
                     to an existing workflow, the production-relevant case.

Each mutation is a two-call LLM flow:
  1. plan-rewrite call: ask the model to produce the mutated plan
     (preserving everything about the original except the chosen dimension).
  2. commands call: feed the new plan to the actual CommandGenerator system
     prompt and let JSON-mode emit fresh commands.

Then the four-gate judge in synthesize.judge runs to filter rejects.

Inputs are read from seeds/*.jsonl by default. For v2, point at v1 seeds:

    python -m training.synthesize.mutations --task cmdgen_v2 \
        --input-globs "training/data/cmdgen_v2/seeds/*.jsonl,training/data/cmdgen_v2/synthetic/templates.jsonl" \
        --n 200 --backend openai --model gpt-5.4-mini
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import random
import re
import sys
from typing import Dict, List, Optional, Tuple

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from training.capture.extract_seeds import load_system_prompt_text
from training.curate.normalize import extract_assistant_json, normalize_record
from training.llm import complete, usage_summary
from training.synthesize.judge import pass_all_gates

logger = logging.getLogger("synthesize.mutations")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


MUTATIONS = {
    "swap_node_type": (
        "Swap exactly ONE node's type for a related one (Database <-> Integration, "
        "Alert <-> Excel Export to a file, AI Action <-> AI Extract for a structured-output need, "
        "Folder Selector <-> File for single-file vs many-file scenarios). "
        "Adjust that node's config to match the new type's required fields. "
        "Keep all other nodes, variables, and connections identical."
    ),
    "add_conditional": (
        "Insert a single new Conditional node between two existing adjacent nodes. "
        "The condition should make business sense for the data flowing between them "
        "(e.g. row count > 0, total > threshold, status equals X). "
        "Add an Alert on the fail branch and route the original downstream node on the pass branch."
    ),
    "wrap_loop": (
        "Wrap an existing interior segment of nodes in a Loop and End Loop. "
        "Introduce a Folder Selector or Database upstream that produces the iterable. "
        "Rebind interior node references to the Loop's itemVariable. "
        "Don't change what those interior nodes do, only what they iterate over."
    ),
    "add_human_approval": (
        "Insert a single Human Approval node before the final action node, gating the workflow "
        "on an approver review. Pick a sensible assignee (a group or a user id), title, and timeout."
    ),
    "add_step": (
        "Insert one new step that adds value but doesn't change the existing structure. "
        "Choose ONE of: a Set Variable that prepares a derived value, an Alert that notifies a stakeholder, "
        "an Excel Export that archives a key dataset to file, or a File-write that snapshots output."
    ),
    "swap_destination": (
        "Swap a destination value: change an Alert's recipient address, change an Excel Export's "
        "output path, or change a Database connection ID. Keep all other config fields identical."
    ),
    "remove_step": (
        "Remove a single non-load-bearing step. The remaining workflow must still compile. "
        "Pick a redundant Alert, a duplicate Set Variable, or an extra confirmation Alert."
    ),
    "refinement_edit": (
        "Refinement-mode edit: the user is amending an EXISTING workflow. Pick ONE small change a "
        "real user would request mid-build: 'also email finance', 'add a check for null first', "
        "'route the high-value cases to a human approval', 'export to Excel as well'. "
        "Implement that change minimally."
    ),
}


PLAN_REWRITE_SYSTEM = """You rewrite an existing workflow plan applying a single structural mutation.

You receive:
  ORIGINAL PLAN: a numbered plan a user originally gave a workflow builder.
  MUTATION: a one-sentence directive describing the targeted change.

Output the FULL NEW PLAN. It must:
- Preserve every concrete detail from the original that isn't part of the mutation: file paths, variable names, SQL, agent IDs, connection IDs, exact field lists, recipients.
- Apply ONLY the mutation. Do not invent unrelated changes.
- Read like a real user request — numbered, clear about node ordering, branches described explicitly.
- Output ONLY the plan text. No JSON, no markdown fences, no preamble.
"""


def _load_records(globs: List[str]) -> List[dict]:
    out: List[dict] = []
    for g in globs:
        for p in sorted(glob.glob(g)):
            if "rejected" in os.path.basename(p) or "mutations" in os.path.basename(p):
                continue
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    norm = normalize_record(rec)
                    if norm is None:
                        continue
                    user = next((m for m in norm["messages"] if m["role"] == "user"), None)
                    asst = next((m for m in norm["messages"] if m["role"] == "assistant"), None)
                    if not user or not asst:
                        continue
                    plan = user["content"].strip()
                    cmds = extract_assistant_json(asst["content"])
                    if not plan or not cmds:
                        continue
                    if plan.startswith("<DRY RUN"):
                        continue
                    out.append({
                        "plan": plan,
                        "commands": cmds,
                        "source_file": os.path.basename(p),
                        "source_meta": rec.get("_meta", {}) or {},
                    })
    return out


def mutate_one(
    seed: dict,
    mutation_id: str,
    mutation_desc: str,
    target_system_prompt: str,
    *,
    backend: Optional[str],
    model: Optional[str],
    dry_run: bool,
) -> Optional[Tuple[str, dict]]:
    if dry_run:
        return f"<DRY RUN mutation: {mutation_id}>\n\n" + seed["plan"], seed["commands"]

    # Step 1: rewrite the plan applying the mutation.
    user = (
        "MUTATION:\n" + mutation_desc + "\n\n"
        "ORIGINAL PLAN:\n" + seed["plan"] + "\n\n"
        "Rewrite the plan applying ONLY this mutation."
    )
    new_plan = complete(
        PLAN_REWRITE_SYSTEM,
        user,
        backend=backend,
        model=model,
        temperature=0.6,
        max_tokens=900,
    ).strip()
    new_plan = re.sub(r"^```[a-z]*\s*|```\s*$", "", new_plan, flags=re.MULTILINE).strip()
    if not new_plan or len(new_plan) < 40:
        return None

    # Step 2: generate commands from the mutated plan.
    raw = complete(
        target_system_prompt,
        f"Convert this workflow plan to JSON commands:\n\n{new_plan}",
        backend=backend,
        model=model,
        temperature=0.2,
        max_tokens=2400,
        response_format={"type": "json_object"},
    )
    cmds = extract_assistant_json(raw)
    if not cmds:
        try:
            cmds = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(cmds, dict) or "commands" not in cmds:
        return None
    if "action" not in cmds:
        cmds = {"action": "build_workflow", "commands": cmds["commands"]}
    return new_plan, cmds


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="cmdgen")
    ap.add_argument(
        "--input-globs",
        default=None,
        help="Comma-separated globs. Default: training/data/<task>/seeds/*.jsonl",
    )
    ap.add_argument("--out", default=None)
    ap.add_argument("--rejected-out", default=None)
    ap.add_argument("--n", type=int, default=200, help="Total mutations to generate")
    ap.add_argument(
        "--per-mutation",
        type=int,
        default=0,
        help="If non-zero, force equal coverage: this many per mutation strategy.",
    )
    ap.add_argument("--backend", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    if args.input_globs:
        globs = [g.strip() for g in args.input_globs.split(",")]
    else:
        globs = [os.path.join(_REPO_ROOT, "training", "data", args.task, "seeds", "*.jsonl")]

    seeds = _load_records(globs)
    if not seeds:
        logger.error("No usable seed records found in: %s", globs)
        return 2
    logger.info("Loaded %d seed record(s)", len(seeds))

    if args.out is None:
        args.out = os.path.join(_REPO_ROOT, "training", "data", args.task, "synthetic", "mutations.jsonl")
    if args.rejected_out is None:
        args.rejected_out = os.path.join(
            _REPO_ROOT, "training", "data", args.task, "synthetic", "mutations.rejected.jsonl"
        )
    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    target_sp = load_system_prompt_text()
    if not target_sp:
        logger.error("Could not load CommandGenerator system prompt")
        return 2

    # Resume support: skip (source_file, source_line, mutation_id) tuples already present.
    already: set = set()
    if os.path.exists(args.out):
        with open(args.out, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    m = r.get("_meta", {})
                    already.add((m.get("source_file"), m.get("source_index"), m.get("mutation_id")))
                except json.JSONDecodeError:
                    continue
    if already:
        logger.info("Resuming: %d mutation(s) already in output", len(already))

    rng = random.Random(args.seed)

    plan_list: List[Tuple[dict, str, str, int]] = []
    if args.per_mutation > 0:
        for mid, mdesc in MUTATIONS.items():
            sample = rng.sample(seeds, min(args.per_mutation, len(seeds)))
            for i, s in enumerate(sample):
                plan_list.append((s, mid, mdesc, i))
    else:
        # Random sampling proportional to N.
        for i in range(args.n):
            mid = rng.choice(list(MUTATIONS.keys()))
            mdesc = MUTATIONS[mid]
            s = rng.choice(seeds)
            plan_list.append((s, mid, mdesc, i))

    accepted = 0
    rejected = 0
    gen_failures = 0
    with open(args.out, "a", encoding="utf-8") as out, open(args.rejected_out, "a", encoding="utf-8") as rej:
        for i, (seed, mid, mdesc, sidx) in enumerate(plan_list, start=1):
            key = (seed["source_file"], sidx, mid)
            if key in already:
                continue
            try:
                result = mutate_one(seed, mid, mdesc, target_sp, backend=args.backend, model=args.model, dry_run=args.dry_run)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[%d/%d] %s on %s: gen failed: %s", i, len(plan_list), mid, seed["source_file"], exc)
                gen_failures += 1
                continue
            if result is None:
                gen_failures += 1
                continue
            new_plan, cmds = result

            assistant_block = "```json\n" + json.dumps(cmds, indent=2) + "\n```"
            ok, details = pass_all_gates(new_plan, assistant_block, run_compile=True, run_judge=False)

            record = {
                "messages": [
                    {"role": "system", "content": target_sp},
                    {"role": "user", "content": new_plan},
                    {"role": "assistant", "content": assistant_block},
                ],
                "_meta": {
                    "source": "synth_mutation",
                    "mutation_id": mid,
                    "source_file": seed["source_file"],
                    "source_index": sidx,
                    "n_commands": len(cmds.get("commands", [])),
                    "dry_run": bool(args.dry_run),
                    "gates": details.get("gates"),
                },
            }
            if ok:
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                out.flush()
                accepted += 1
                logger.info("[%d/%d] %s OK cmds=%d (from %s)", i, len(plan_list), mid, record["_meta"]["n_commands"], seed["source_file"])
            else:
                record["_meta"]["reject_reason"] = details
                rej.write(json.dumps(record, ensure_ascii=False) + "\n")
                rej.flush()
                rejected += 1
                bad = ",".join(k for k, v in (details.get("gates") or {}).items() if v is False)
                logger.info("[%d/%d] %s REJECT (%s) (from %s)", i, len(plan_list), mid, bad or "?", seed["source_file"])

    logger.info(
        "Done. attempted=%d accepted=%d rejected=%d gen_failures=%d",
        len(plan_list),
        accepted,
        rejected,
        gen_failures,
    )
    logger.info("LLM usage: %s", json.dumps(usage_summary()))
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
