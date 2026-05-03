"""Regenerate report.json from an existing per_record.jsonl.

When the aggregate logic is improved (e.g. new slicing dimensions) but you
don't want to re-spend LLM tokens on a fresh baseline run, this recomputes
the report from the already-scored per-record verdicts.

    python -m training.evaluate.regen_report \
        --run-dir training/runs/baselines/cmdgen/baseline_gpt4o_mini_full
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Dict, List

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from training.evaluate.harness import RecordVerdict, aggregate

logger = logging.getLogger("evaluate.regen_report")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _read_perrecord(path: str):
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def _verdict_from_dict(d: dict) -> RecordVerdict:
    return RecordVerdict(
        json_parse=d.get("json_parse", 0.0),
        schema=d.get("schema", 0.0),
        compile=d.get("compile"),
        rule=d.get("rule"),
        structural=d.get("structural", 0.0),
        judge=d.get("judge"),
        schema_errors=d.get("schema_errors", []) or [],
        compile_error=d.get("compile_error"),
        rule_warnings=d.get("rule_warnings", []) or [],
        structural_details=d.get("structural_details", {}) or {},
        judge_details=d.get("judge_details"),
        commandgen_score=d.get("commandgen_score", 0.0),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True, help="Path to baselines/<task>/<label> directory")
    ap.add_argument(
        "--eval-path",
        default=None,
        help="If provided, use this eval file's _meta for slicing (source/footgun/n_commands)",
    )
    args = ap.parse_args()

    perrec_path = os.path.join(args.run_dir, "per_record.jsonl")
    if not os.path.exists(perrec_path):
        logger.error("per_record.jsonl not found: %s", perrec_path)
        return 2
    per = _read_perrecord(perrec_path)
    logger.info("Loaded %d per-record entries", len(per))

    # Meta for slicing — prefer eval file if given, else fall back to per-record stored fields.
    eval_meta: List[dict] = []
    if args.eval_path and os.path.exists(args.eval_path):
        with open(args.eval_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    eval_meta.append(json.loads(line).get("_meta", {}))
    if len(eval_meta) != len(per):
        eval_meta = [None] * len(per)  # type: ignore[list-item]

    verdicts: List[RecordVerdict] = []
    by_source: Dict[str, List] = {}
    by_footgun: Dict[str, List] = {}
    by_complexity: Dict[str, List] = {}
    for entry, em in zip(per, eval_meta):
        v = _verdict_from_dict(entry.get("verdict", {}))
        verdicts.append(v)
        src = (em or {}).get("source") or entry.get("source") or "unknown"
        fg = (em or {}).get("footgun") or entry.get("footgun")
        n_cmds = (em or {}).get("n_commands") or entry.get("n_gold_commands") or 0
        bucket = "simple" if n_cmds <= 6 else ("medium" if n_cmds <= 14 else "complex")
        by_source.setdefault(src, []).append(v)
        if fg:
            by_footgun.setdefault(fg, []).append(v)
        by_complexity.setdefault(bucket, []).append(v)

    report = aggregate(verdicts)
    report["by_source"] = {k: aggregate(vs) for k, vs in by_source.items()}
    report["by_footgun"] = {k: aggregate(vs) for k, vs in by_footgun.items()}
    report["by_complexity"] = {k: aggregate(vs) for k, vs in by_complexity.items()}
    report["regen"] = True

    out_path = os.path.join(args.run_dir, "report.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    logger.info("Wrote %s", out_path)
    logger.info(
        "SUMMARY cgs=%.3f n=%d json=%.3f schema=%.3f compile=%s rule=%s struct=%.3f",
        report["commandgen_score"] or 0,
        report["n"],
        report["json_parse"] or 0,
        report["schema"] or 0,
        f"{report.get('compile'):.3f}" if report.get("compile") is not None else "n/a",
        f"{report.get('rule'):.3f}" if report.get("rule") is not None else "n/a",
        report["structural"] or 0,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
