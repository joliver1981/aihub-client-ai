"""Rephrase seeds: produce N plan variants with the SAME commands.

Strategy: take an existing high-quality (plan, commands) seed and ask the LLM
to rewrite the plan in N different registers (terse/verbose/non-technical/
impatient/pedantic), keeping the target commands identical. This teaches the
model that user phrasing is irrelevant to the correct output.

No judge needed — commands are unchanged from the seed. The four gates
(JSON, schema, compile) ran once when the seed was validated.

Reads:
    training/data/cmdgen/seeds/*.jsonl  (or a specific --input)

Writes:
    training/data/cmdgen/synthetic/rephrase.jsonl
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import sys
from typing import Dict, List

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from training.curate.normalize import extract_assistant_json, normalize_record
from training.llm import complete, usage_summary

logger = logging.getLogger("synthesize.rephrase")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


REGISTERS = {
    "terse":          "Rewrite as a curt, technical user would — short sentences, minimal fluff, assume reader knows the domain.",
    "verbose":        "Rewrite as a chatty non-technical user describing their process — full sentences, explanations of why, some redundancy.",
    "bullets":        "Rewrite as bulleted plan with sub-bullets for node configuration details.",
    "impatient":      "Rewrite as an impatient user who just wants it done — skip pleasantries, imperative tone, lists only what matters.",
    "pedantic":       "Rewrite as a pedantic user who specifies every edge case and default value.",
}

REPHRASE_SYSTEM = """You rewrite natural-language workflow plans in different user registers.

Rules:
- Preserve EVERY specific detail from the original: file paths, variable names, agent IDs, connection IDs, SQL queries verbatim, exact field lists, assignee groups/users, recipient emails. Do not invent values and do not drop values.
- Change only the phrasing, register, and structure.
- The output must still describe the same nodes, in the same execution order, with the same branching.
- Output ONLY the rewritten plan. No preamble, no markdown fences, no explanation.
"""


def rephrase_plan(plan: str, register: str, *, backend=None, model=None, dry_run=False) -> str:
    if dry_run:
        return f"<DRY RUN: rephrase({register})>"
    guidance = REGISTERS[register]
    user = (
        f"Register: {register}\nGuidance: {guidance}\n\n"
        f"Original plan:\n{plan.strip()}\n\nRewritten plan:"
    )
    return complete(REPHRASE_SYSTEM, user, backend=backend, model=model, temperature=0.6, max_tokens=1500).strip()


def process_seed_file(path: str, n_variants: int, registers: List[str], dry_run: bool, backend, model) -> List[Dict]:
    out: List[Dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
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
            user_msg = next((m for m in norm["messages"] if m.get("role") == "user"), None)
            asst_msg = next((m for m in norm["messages"] if m.get("role") == "assistant"), None)
            system_msg = next((m for m in norm["messages"] if m.get("role") == "system"), None)
            if not (user_msg and asst_msg and system_msg):
                continue
            commands = extract_assistant_json(asst_msg["content"])
            if commands is None:
                continue

            # Skip seeds whose plan is a dry-run placeholder — they have no real
            # plan text to rephrase.
            if user_msg["content"].lstrip().startswith("<DRY RUN"):
                continue

            for i, register in enumerate(registers[:n_variants]):
                try:
                    new_plan = rephrase_plan(user_msg["content"], register, backend=backend, model=model, dry_run=dry_run)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("rephrase failed for seed line %d register %s: %s", line_no, register, exc)
                    continue
                out.append({
                    "messages": [
                        {"role": "system", "content": system_msg["content"]},
                        {"role": "user", "content": new_plan},
                        {"role": "assistant", "content": asst_msg["content"]},  # commands unchanged
                    ],
                    "_meta": {
                        "source": "synth_rephrase",
                        "register": register,
                        "seed_path": os.path.basename(path),
                        "seed_line": line_no,
                        "n_commands": len(commands.get("commands", [])),
                        "dry_run": bool(dry_run),
                    },
                })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="cmdgen")
    ap.add_argument(
        "--input-glob",
        default=None,
        help="Default: training/data/<task>/seeds/*.jsonl",
    )
    ap.add_argument(
        "--input-globs",
        default=None,
        help="Multiple comma-separated globs (overrides --input-glob). Used to rephrase across seeds + synthetic.",
    )
    ap.add_argument(
        "--out",
        default=None,
        help="Default: training/data/<task>/synthetic/rephrase.jsonl",
    )
    ap.add_argument("--n-variants", type=int, default=5, help="Variants per seed (max = len(REGISTERS))")
    ap.add_argument("--registers", default=None, help="Comma-separated subset of registers; default = all")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--backend", default=None)
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    registers = [r.strip() for r in args.registers.split(",")] if args.registers else list(REGISTERS.keys())
    for r in registers:
        if r not in REGISTERS:
            logger.error("unknown register: %s (valid: %s)", r, list(REGISTERS.keys()))
            return 2

    if args.input_globs:
        globs = [g.strip() for g in args.input_globs.split(",")]
    elif args.input_glob:
        globs = [args.input_glob]
    else:
        globs = [os.path.join(_REPO_ROOT, "training", "data", args.task, "seeds", "*.jsonl")]
    paths = []
    for g in globs:
        paths.extend(sorted(glob.glob(g)))
    # Exclude rejected dumps.
    paths = [p for p in paths if "rejected" not in os.path.basename(p) and "rephrase" not in os.path.basename(p)]
    logger.info("Input seed files: %d", len(paths))

    if args.out is None:
        args.out = os.path.join(_REPO_ROOT, "training", "data", args.task, "synthetic", "rephrase.jsonl")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    total = 0
    with open(args.out, "w", encoding="utf-8") as f:
        for p in paths:
            records = process_seed_file(
                p,
                args.n_variants,
                registers,
                dry_run=args.dry_run,
                backend=args.backend,
                model=args.model,
            )
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
                total += 1
            logger.info("  %s -> %d variant(s)", os.path.basename(p), len(records))

    logger.info("Wrote %d rephrased records to %s", total, args.out)
    logger.info("LLM usage: %s", json.dumps(usage_summary()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
