"""Run a model against eval_frozen.jsonl and score its output.

    python -m training.evaluate.baseline --model gpt-4o-mini --out runs/baseline_gpt4o_mini.json

For each record in the eval set:
  1. Take the `system` + `user` messages verbatim from the eval record.
  2. Call the specified model (OpenAI/Azure/Anthropic backend).
  3. Score the candidate output against the gold assistant block via harness.score_record.
  4. Write per-record verdicts + aggregate report.

The eval set is the frozen held-out file from training/data/<task>/eval_frozen.jsonl.
Outputs go to training/runs/baselines/<task>/<model>_<timestamp>/.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime
from typing import Dict, List, Optional

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from training.curate.normalize import extract_assistant_json
from training.evaluate.harness import aggregate, score_record
from training.llm import complete, usage_summary

logger = logging.getLogger("evaluate.baseline")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _read_eval(path: str) -> List[dict]:
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def _run_model(
    record: dict,
    backend: str,
    model: Optional[str],
    temperature: float,
    max_tokens: int,
    response_format: Optional[dict],
) -> str:
    system = next(m for m in record["messages"] if m["role"] == "system")["content"]
    user = next(m for m in record["messages"] if m["role"] == "user")["content"]
    return complete(
        system,
        user,
        backend=backend,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format=response_format,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="cmdgen")
    ap.add_argument("--eval-path", default=None, help="Default: training/data/<task>/eval_frozen.jsonl")
    ap.add_argument("--model", default="gpt-4o-mini")
    ap.add_argument("--backend", default="openai", choices=["openai", "azure", "anthropic"])
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--max-tokens", type=int, default=2500)
    ap.add_argument(
        "--json-mode",
        action="store_true",
        default=True,
        help="Pass response_format={'type':'json_object'} (OpenAI only). Default on.",
    )
    ap.add_argument("--run-judge", action="store_true", help="Also score semantic (costs extra LLM calls)")
    ap.add_argument("--judge-model", default=None)
    ap.add_argument("--limit", type=int, default=0, help="Score only first N records (smoke test)")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--label", default=None, help="Run label; default = model + timestamp")
    args = ap.parse_args()

    eval_path = args.eval_path or os.path.join(
        _REPO_ROOT, "training", "data", args.task, "eval_frozen.jsonl"
    )
    if not os.path.exists(eval_path):
        logger.error("eval file not found: %s", eval_path)
        return 2

    records = _read_eval(eval_path)
    if args.limit:
        records = records[: args.limit]
    logger.info("Eval records to score: %d", len(records))

    label = args.label or f"{args.model.replace('/', '_')}_{datetime.now():%Y%m%d_%H%M%S}"
    out_dir = args.out_dir or os.path.join(_REPO_ROOT, "training", "runs", "baselines", args.task, label)
    os.makedirs(out_dir, exist_ok=True)

    response_format = {"type": "json_object"} if (args.json_mode and args.backend == "openai") else None

    verdicts = []
    per_record_path = os.path.join(out_dir, "per_record.jsonl")
    with open(per_record_path, "w", encoding="utf-8") as fout:
        for i, rec in enumerate(records, start=1):
            meta = rec.get("_meta", {})
            plan = next(m for m in rec["messages"] if m["role"] == "user")["content"]
            gold_asst = next(m for m in rec["messages"] if m["role"] == "assistant")["content"]
            gold_cmds = extract_assistant_json(gold_asst) or {}

            t0 = time.monotonic()
            try:
                pred_text = _run_model(
                    rec, args.backend, args.model, args.temperature, args.max_tokens, response_format
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[%d/%d] LLM call failed: %s", i, len(records), exc)
                pred_text = ""
            dt = time.monotonic() - t0

            verdict = score_record(
                gold_commands=gold_cmds,
                pred_assistant_text=pred_text,
                plan=plan,
                run_compile=True,
                run_judge=args.run_judge,
                judge_backend=args.backend,
                judge_model=args.judge_model,
            )
            verdicts.append(verdict)

            out_rec = {
                "index": i,
                "source": meta.get("source"),
                "footgun": meta.get("footgun"),
                "n_gold_commands": meta.get("n_commands"),
                "latency_s": round(dt, 2),
                "verdict": verdict.to_dict(),
                "pred_assistant_text": pred_text[:8000],   # cap for inspectability
            }
            fout.write(json.dumps(out_rec, ensure_ascii=False) + "\n")
            fout.flush()

            if i % 10 == 0 or i == len(records):
                interim = aggregate(verdicts)
                logger.info(
                    "[%d/%d] json=%.2f schema=%.2f compile=%s rule=%.2f struct=%.2f cgs=%.2f",
                    i,
                    len(records),
                    interim.get("json_parse", 0) or 0,
                    interim.get("schema", 0) or 0,
                    f"{interim.get('compile'):.2f}" if interim.get("compile") is not None else "n/a",
                    interim.get("rule", 0) or 0,
                    interim.get("structural", 0) or 0,
                    interim.get("commandgen_score", 0) or 0,
                )

    report = aggregate(verdicts)
    report["label"] = label
    report["model"] = args.model
    report["backend"] = args.backend
    report["eval_path"] = eval_path
    report["temperature"] = args.temperature
    report["max_tokens"] = args.max_tokens
    report["timestamp"] = datetime.now().isoformat(timespec="seconds")
    report["llm_usage"] = usage_summary()

    # Also slice the score by source + footgun + complexity so we can see
    # where a model is strong/weak. The whole point of specialization is to
    # win on the slices where frontier models are weakest.
    by_source: Dict[str, List] = {}
    by_footgun: Dict[str, List] = {}
    by_complexity: Dict[str, List] = {}
    for rec, v in zip(records, verdicts):
        meta = rec.get("_meta", {})
        by_source.setdefault(meta.get("source", "unknown"), []).append(v)
        fg = meta.get("footgun")
        if fg:
            by_footgun.setdefault(fg, []).append(v)
        n_cmds = meta.get("n_commands", 0) or 0
        bucket = "simple" if n_cmds <= 6 else ("medium" if n_cmds <= 14 else "complex")
        by_complexity.setdefault(bucket, []).append(v)

    report["by_source"] = {k: aggregate(vs) for k, vs in by_source.items()}
    report["by_footgun"] = {k: aggregate(vs) for k, vs in by_footgun.items()}
    report["by_complexity"] = {k: aggregate(vs) for k, vs in by_complexity.items()}

    report_path = os.path.join(out_dir, "report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    logger.info("Wrote %s", report_path)
    logger.info(
        "SUMMARY  n=%d  cgs=%.3f  json=%.3f schema=%.3f compile=%s rule=%.3f struct=%.3f",
        report["n"],
        report["commandgen_score"] or 0,
        report["json_parse"] or 0,
        report["schema"] or 0,
        f"{report.get('compile'):.3f}" if report.get("compile") is not None else "n/a",
        report["rule"] or 0,
        report["structural"] or 0,
    )
    logger.info("LLM usage: %s", json.dumps(report["llm_usage"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
