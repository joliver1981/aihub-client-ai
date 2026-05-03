"""End-to-end curation driver.

    python -m training.curate.run --task cmdgen
      --raw-glob "training/data/cmdgen/raw/*.jsonl"
      --out-dir  training/data/cmdgen

Pipeline:  raw jsonl files  -->  normalize  -->  scrub  -->  validate  -->
           dedupe  -->  stratified split  -->  train/dev/eval jsonl.

Records that fail normalization or compile validation are written to a
`rejected.jsonl` for inspection but excluded from the training splits.
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import sys
from typing import Iterable, List

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from training.curate.dedupe import dedupe_iter
from training.curate.normalize import iter_normalized
from training.curate.scrub import scrub_record
from training.curate.split import SplitRatios, split_records, write_jsonl
from training.curate.validate import validate_record

logger = logging.getLogger("curate.run")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _load_and_clean(paths: Iterable[str], skip_compile: bool) -> List[dict]:
    out: List[dict] = []
    rejected: List[dict] = []
    for p in paths:
        logger.info("Reading %s", p)
        for record in iter_normalized(p):
            scrubbed, _ = scrub_record(record)
            ok, details = validate_record(scrubbed, run_compile=not skip_compile)
            scrubbed.setdefault("_meta", {})["validate"] = details
            # Preserve provenance on the record: source filename.
            scrubbed["_meta"].setdefault("source_file", os.path.basename(p))
            scrubbed["_meta"].setdefault("source", _infer_source(p))
            if ok:
                out.append(scrubbed)
            else:
                rejected.append(scrubbed)
        logger.info(
            "  normalized=%d dropped=%d from %s",
            iter_normalized.last_total - iter_normalized.last_dropped,
            iter_normalized.last_dropped,
            p,
        )
    return out, rejected


def _infer_source(path: str) -> str:
    """Infer the `source` stratification key from the filename."""
    name = os.path.basename(path).lower()
    if "export" in name or "plan_to_commands" in name:
        return "real_export"
    if "driver" in name:
        return "driver"
    if "seed_workflow" in name or "from_json" in name:
        return "seed_workflow"
    if "seed_e2e" in name or "from_e2e" in name:
        return "seed_e2e"
    if "synth_template" in name or "template" in name:
        return "synth_template"
    if "synth_mutation" in name or "mutation" in name:
        return "synth_mutation"
    if "synth_adversarial" in name or "adversarial" in name:
        return "synth_adversarial"
    return "unknown"


def main() -> int:
    ap = argparse.ArgumentParser(description="Curate training data for a task.")
    ap.add_argument("--task", default="cmdgen")
    ap.add_argument(
        "--raw-glob",
        default=None,
        help="Glob of raw JSONL files. Defaults to training/data/<task>/raw/*.jsonl + seeds/*.jsonl + synthetic/*.jsonl",
    )
    ap.add_argument("--out-dir", default=None, help="Defaults to training/data/<task>")
    ap.add_argument(
        "--skip-compile",
        action="store_true",
        help="Skip materialize_commands() call (for envs without full runtime deps).",
    )
    ap.add_argument(
        "--include-external-seeds",
        action="store_true",
        default=True,
        help="Include training_data/workflows/plan_to_commands.jsonl if it exists.",
    )
    args = ap.parse_args()

    out_dir = args.out_dir or os.path.join(_REPO_ROOT, "training", "data", args.task)
    os.makedirs(out_dir, exist_ok=True)

    if args.raw_glob:
        paths = sorted(glob.glob(args.raw_glob))
    else:
        patterns = [
            os.path.join(out_dir, "raw", "*.jsonl"),
            os.path.join(out_dir, "seeds", "*.jsonl"),
            os.path.join(out_dir, "synthetic", "*.jsonl"),
        ]
        paths = []
        for pat in patterns:
            paths.extend(sorted(glob.glob(pat)))
        # Exclude any *.rejected.jsonl dumps that synth scripts write alongside
        # their accepted outputs.
        paths = [p for p in paths if "rejected" not in os.path.basename(p)]

    if args.include_external_seeds:
        external = os.path.join(_REPO_ROOT, "training_data", "workflows", "plan_to_commands.jsonl")
        if os.path.exists(external):
            paths.append(external)

    if not paths:
        logger.error("No input JSONL files found. Nothing to curate.")
        return 2

    logger.info("Input files (%d):", len(paths))
    for p in paths:
        logger.info("  %s", p)

    clean, rejected = _load_and_clean(paths, skip_compile=args.skip_compile)
    logger.info("Clean records: %d   Rejected: %d", len(clean), len(rejected))

    deduped = list(dedupe_iter(clean))
    logger.info("After dedupe: %d (removed %d duplicates)", len(deduped), len(clean) - len(deduped))

    splits = split_records(deduped)
    n_written = {k: write_jsonl(v, os.path.join(out_dir, f"{k}.jsonl")) for k, v in splits.items()}
    n_rej = write_jsonl(rejected, os.path.join(out_dir, "rejected.jsonl"))

    summary = {
        "task": args.task,
        "inputs": paths,
        "clean": len(clean),
        "rejected": n_rej,
        "after_dedupe": len(deduped),
        "splits": n_written,
    }
    with open(os.path.join(out_dir, "curate_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    logger.info("Summary: %s", json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
