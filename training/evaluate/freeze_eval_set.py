"""Freeze a stratified eval set from the curated corpus and prevent leakage.

Two responsibilities:

1. Pick a stratified eval set (default n=300) from the full pool of curated +
   raw records. Coverage targets every source × complexity × footgun bucket
   and every valid node type at least once.

2. **Anti-leakage**: rewrite train.jsonl, dev.jsonl, eval.jsonl to exclude any
   record whose COMMANDS structurally match a record in the frozen eval set.
   Catches the common rephrase leakage case where a record has the same
   target commands as an eval record but different plan text — the model
   would otherwise learn the test answers from training paraphrases.


Selects ~N records (default 150) from the curated train/dev/eval pool and
writes them to training/data/cmdgen/eval_frozen.jsonl + a manifest.json.

Stratification dimensions:
  - source             (real_export, seed_system_prompt, seed_workflow, synth_*, synth_adversarial)
  - complexity         (simple <=6 cmds, medium 7-14, complex 15+)
  - footgun           (adversarial cases exercising a specific rule)
  - node-type coverage (best-effort: try to include every valid node type at least once)

Selection policy (greedy to maximize coverage):
  1. ALL adversarial cases are included (they ARE the footgun test cases).
  2. For every (source, complexity) cell, pick up to ceil(quota) records.
  3. Fill remaining slots by greedily picking records that add node types
     not yet represented.

Writes:
  training/data/cmdgen/eval_frozen.jsonl
  training/data/cmdgen/eval_manifest.json

Once written, this file is the regression suite and should not be regenerated
casually. Delete it explicitly and re-run if the schema materially changes.
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import math
import os
import sys
from collections import Counter, defaultdict
from typing import Dict, List, Set, Tuple

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from training.curate.normalize import extract_assistant_json, normalize_record
from training.curate.validate import VALID_NODE_TYPES

logger = logging.getLogger("evaluate.freeze")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _complexity_bucket(n_cmds: int) -> str:
    if n_cmds <= 6:
        return "simple"
    if n_cmds <= 14:
        return "medium"
    return "complex"


def _node_types_in(record: dict) -> Set[str]:
    asst = next((m for m in record["messages"] if m.get("role") == "assistant"), None)
    if not asst:
        return set()
    cmds = extract_assistant_json(asst["content"])
    if not cmds:
        return set()
    out: Set[str] = set()
    for c in cmds.get("commands", []):
        if c.get("type") == "add_node":
            nt = c.get("node_type")
            if nt in VALID_NODE_TYPES:
                out.add(nt)
    return out


def _load_pool(paths: List[str]) -> List[dict]:
    """Load + dedupe by _meta.hash. Records without a hash are kept as-is."""
    pool: List[dict] = []
    seen: Set[str] = set()
    for p in paths:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                h = rec.get("_meta", {}).get("hash")
                if not h:
                    # Compute a hash on the fly via normalize. Preserve the
                    # original record's metadata (especially `source`) so the
                    # stratifier still sees e.g. synth_adversarial.
                    orig_meta = dict(rec.get("_meta") or {})
                    norm = normalize_record(rec)
                    if norm is not None:
                        new_meta = norm.get("_meta") or {}
                        # Merge: computed fields win; original source/footgun preserved.
                        merged = {**new_meta, **orig_meta, "hash": new_meta.get("hash")}
                        rec = {"messages": norm["messages"], "_meta": merged}
                        h = merged.get("hash")
                if h:
                    if h in seen:
                        continue
                    seen.add(h)
                pool.append(rec)
    return pool


def freeze(
    pool: List[dict],
    n_target: int,
    *,
    eval_share: float = 0.25,
    rng_seed: int = 42,
) -> Tuple[List[dict], Dict]:
    """Per-source proportional sampling.

    Each source contributes `eval_share` of its records to eval (default 25%),
    capped so the total reaches `n_target`. Within a source, records are
    stratified across complexity buckets and across footguns (so every
    adversarial footgun and every node-type bucket has representation).

    This replaces the older "auto-include all adversarial + greedy coverage"
    policy, which pulled 100% of every small high-trust source into eval and
    starved train of the most valuable training signal.
    """
    import random

    rng = random.Random(rng_seed)

    # Group records by source.
    by_source: Dict[str, List[dict]] = defaultdict(list)
    for r in pool:
        src = r.get("_meta", {}).get("source", "unknown")
        by_source[src].append(r)

    picked: List[dict] = []
    # First pass: each source gets max(1, ceil(len * eval_share)) records.
    # Within source, stratify by (complexity, footgun) and pick proportionally.
    for src, records in by_source.items():
        n_for_source = max(1, math.ceil(len(records) * eval_share))
        # Sub-stratify within source by (complexity, footgun-or-none).
        sub_buckets: Dict[Tuple, List[dict]] = defaultdict(list)
        for r in records:
            meta = r.get("_meta", {})
            n_cmds = meta.get("n_commands", 0) or 0
            fg = meta.get("footgun") or "_none"
            sub_buckets[(_complexity_bucket(n_cmds), fg)].append(r)

        # Shuffle each sub-bucket for fair sampling.
        for k in sub_buckets:
            rng.shuffle(sub_buckets[k])

        # Round-robin pull from sub-buckets until we have n_for_source records.
        bucket_keys = list(sub_buckets.keys())
        rng.shuffle(bucket_keys)
        cursor = 0
        sub_picked: List[dict] = []
        positions = {k: 0 for k in bucket_keys}
        while len(sub_picked) < n_for_source and bucket_keys:
            k = bucket_keys[cursor % len(bucket_keys)]
            if positions[k] < len(sub_buckets[k]):
                sub_picked.append(sub_buckets[k][positions[k]])
                positions[k] += 1
                cursor += 1
            else:
                # Bucket exhausted — drop it.
                bucket_keys.pop(cursor % len(bucket_keys))
                if cursor >= len(bucket_keys) > 0:
                    cursor = 0
        picked.extend(sub_picked)

    # If we're under target, top up with leftover records (proportional).
    # If over target, trim — remove from the largest sources first.
    if len(picked) > n_target:
        # Sort sources descending by current contribution, trim from top.
        contrib = Counter(r.get("_meta", {}).get("source", "unknown") for r in picked)
        excess = len(picked) - n_target
        # Mark records to drop: take from sources with the highest count first.
        to_drop: Set[int] = set()
        # Walk the picked list in reverse, removing from the most-represented source.
        while excess > 0:
            target_src, _ = contrib.most_common(1)[0]
            for i in range(len(picked) - 1, -1, -1):
                if i in to_drop:
                    continue
                if picked[i].get("_meta", {}).get("source") == target_src:
                    to_drop.add(i)
                    contrib[target_src] -= 1
                    excess -= 1
                    break
            else:
                break  # no more matches, defensive
        picked = [r for i, r in enumerate(picked) if i not in to_drop]
    elif len(picked) < n_target:
        already_ids = set(id(r) for r in picked)
        leftovers = [r for r in pool if id(r) not in already_ids]
        rng.shuffle(leftovers)
        picked.extend(leftovers[: n_target - len(picked)])

    # Manifest.
    src_counts = Counter(r.get("_meta", {}).get("source", "unknown") for r in picked)
    complexity_counts = Counter(_complexity_bucket(r.get("_meta", {}).get("n_commands", 0) or 0) for r in picked)
    node_types_present: Set[str] = set()
    for r in picked:
        node_types_present |= _node_types_in(r)
    missing_node_types = sorted(VALID_NODE_TYPES - node_types_present)
    footguns = Counter(r.get("_meta", {}).get("footgun") for r in picked if r.get("_meta", {}).get("footgun"))

    manifest = {
        "n_records": len(picked),
        "target": n_target,
        "source_distribution": dict(src_counts),
        "complexity_distribution": dict(complexity_counts),
        "node_types_covered": sorted(node_types_present),
        "node_types_missing": missing_node_types,
        "footgun_distribution": dict(footguns),
    }
    return picked, manifest


def _commands_fingerprint(record: dict) -> Optional[str]:
    """Hash the structural shape of the assistant commands.

    Identical to normalize.record_hash but ignores the plan text — two
    records with the same commands but different plans collide here. Used
    to detect rephrase leakage when filtering train splits after freeze.
    """
    import hashlib

    asst = next((m for m in record.get("messages", []) if m.get("role") == "assistant"), None)
    if not asst:
        return None
    cmds = extract_assistant_json(asst.get("content", ""))
    if not cmds:
        return None
    structural = []
    for cmd in cmds.get("commands", []):
        structural.append((cmd.get("type"), cmd.get("node_type", ""), cmd.get("node_id", "")))
    blob = json.dumps(structural, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _strip_leakage(file_path: str, fingerprints_to_drop: set) -> Tuple[int, int]:
    """Rewrite a JSONL file in place, dropping records whose commands fingerprint
    matches any in the held-out set. Returns (kept, dropped)."""
    if not os.path.exists(file_path):
        return 0, 0
    kept_records: List[dict] = []
    dropped = 0
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            fp = _commands_fingerprint(rec)
            if fp and fp in fingerprints_to_drop:
                dropped += 1
                continue
            kept_records.append(rec)
    with open(file_path, "w", encoding="utf-8") as f:
        for r in kept_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(kept_records), dropped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="cmdgen")
    ap.add_argument(
        "--pool-glob",
        default=None,
        help="Default: all curated train/dev/eval + seeds + synthetic",
    )
    ap.add_argument("--n", type=int, default=150, help="Target eval set size")
    ap.add_argument(
        "--out",
        default=None,
    )
    ap.add_argument("--force", action="store_true", help="Overwrite existing frozen set")
    ap.add_argument(
        "--strip-leakage",
        action="store_true",
        default=True,
        help="After freeze, rewrite train/dev/eval to drop records whose commands match the frozen eval (default true).",
    )
    ap.add_argument(
        "--no-strip-leakage",
        dest="strip_leakage",
        action="store_false",
    )
    ap.add_argument(
        "--exclude-rephrase",
        action="store_true",
        default=True,
        help=(
            "Exclude synth_rephrase records from the eval pool. Rephrase records "
            "are plan-variants of other records, so picking them into eval would "
            "force-drop many siblings from train via leakage stripping. Defaults "
            "true; pass --include-rephrase to disable."
        ),
    )
    ap.add_argument(
        "--include-rephrase",
        dest="exclude_rephrase",
        action="store_false",
    )
    args = ap.parse_args()

    task_dir = os.path.join(_REPO_ROOT, "training", "data", args.task)
    out_path = args.out or os.path.join(task_dir, "eval_frozen.jsonl")
    manifest_path = os.path.join(task_dir, "eval_manifest.json")

    if os.path.exists(out_path) and not args.force:
        logger.error(
            "Frozen eval set already exists at %s. Re-run with --force to overwrite.", out_path
        )
        return 2

    if args.pool_glob:
        pool_paths = sorted(glob.glob(args.pool_glob))
    else:
        pool_paths = []
        for sub in ("train.jsonl", "dev.jsonl", "eval.jsonl"):
            p = os.path.join(task_dir, sub)
            if os.path.exists(p):
                pool_paths.append(p)
        pool_paths += sorted(glob.glob(os.path.join(task_dir, "seeds", "*.jsonl")))
        pool_paths += sorted(glob.glob(os.path.join(task_dir, "synthetic", "*.jsonl")))
        # Exclude rejected dumps.
        pool_paths = [p for p in pool_paths if "rejected" not in os.path.basename(p)]

    if not pool_paths:
        logger.error("No input pools found.")
        return 2

    logger.info("Pool paths (%d):", len(pool_paths))
    for p in pool_paths:
        logger.info("  %s", p)

    pool = _load_pool(pool_paths)
    logger.info("Loaded %d record(s) into pool.", len(pool))

    if args.exclude_rephrase:
        before = len(pool)
        pool = [r for r in pool if r.get("_meta", {}).get("source") != "synth_rephrase"]
        logger.info(
            "Excluded synth_rephrase from pool: %d -> %d records",
            before,
            len(pool),
        )

    picked, manifest = freeze(pool, args.n)
    with open(out_path, "w", encoding="utf-8") as f:
        for r in picked:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    logger.info("Wrote %d record(s) to %s", len(picked), out_path)

    if args.strip_leakage:
        fingerprints = {fp for fp in (_commands_fingerprint(r) for r in picked) if fp}
        logger.info(
            "Stripping leakage: %d unique commands fingerprints to drop from train/dev/eval",
            len(fingerprints),
        )
        for sub in ("train.jsonl", "dev.jsonl", "eval.jsonl"):
            sub_path = os.path.join(task_dir, sub)
            kept, dropped = _strip_leakage(sub_path, fingerprints)
            if kept or dropped:
                logger.info("  %s: kept=%d dropped=%d", sub, kept, dropped)
        manifest["leakage_stripped"] = True

        # Re-write manifest with the leakage flag.
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

    logger.info("Manifest: %s", json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
