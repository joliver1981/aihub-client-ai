"""Stratified train/dev/eval split.

Stratifies on:
  - source           (real_export, driver, seed_workflow, seed_e2e, synth_template, synth_mutation, synth_adversarial)
  - complexity bucket (simple/medium/complex, from n_commands)
  - footgun flag     (example exercises a known failure mode)

Proportions (default): 85% train / 10% dev / 5% eval.
Eval is chosen to maximize coverage diversity, not randomly.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple


@dataclass(frozen=True)
class SplitRatios:
    train: float = 0.85
    dev: float = 0.10
    eval: float = 0.05


def _complexity_bucket(n_commands: int) -> str:
    if n_commands <= 6:
        return "simple"
    if n_commands <= 14:
        return "medium"
    return "complex"


def _strata_key(record: dict) -> Tuple[str, str, bool]:
    meta = record.get("_meta", {}) or {}
    source = meta.get("source", "unknown")
    n = meta.get("n_commands", 0)
    footgun = bool(meta.get("footgun", False))
    return (source, _complexity_bucket(n), footgun)


def split_records(
    records: Iterable[dict],
    ratios: SplitRatios = SplitRatios(),
    seed: int = 42,
) -> Dict[str, List[dict]]:
    """Materialize records into train/dev/eval dicts, stratified."""
    buckets: Dict[Tuple, List[dict]] = defaultdict(list)
    for r in records:
        buckets[_strata_key(r)].append(r)

    rng = random.Random(seed)
    out = {"train": [], "dev": [], "eval": []}

    for key, items in buckets.items():
        rng.shuffle(items)
        n = len(items)
        n_eval = max(1, round(n * ratios.eval)) if n >= 3 else 0
        n_dev = max(1, round(n * ratios.dev)) if n >= 10 else 0
        n_train = n - n_dev - n_eval
        if n_train < 0:
            n_train, n_dev, n_eval = n, 0, 0

        out["train"].extend(items[:n_train])
        out["dev"].extend(items[n_train : n_train + n_dev])
        out["eval"].extend(items[n_train + n_dev :])

    for k in out:
        rng.shuffle(out[k])
    return out


def write_jsonl(records: Iterable[dict], path: str) -> int:
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    return n
