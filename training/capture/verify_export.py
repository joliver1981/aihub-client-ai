"""Smoke test: confirm the Export Training button actually writes valid data.

Modes of use:

  1. Offline (default):  reads training_data/workflows/plan_to_commands.jsonl,
     validates the last N records through normalize + schema + compile.
     Exits 0 if every inspected record is clean; 1 otherwise.

     python -m training.capture.verify_export --tail 5

  2. Watch:  monitors the jsonl for growth over a window and prints the delta.
     Useful for running alongside a manual browser session where you click
     Export and want confirmation data hit disk.

     python -m training.capture.verify_export --watch 30

  3. Live:  hits the running AI Hub instance's training-stats endpoint and
     reports plan_to_commands_count before/after a user-driven action.

     python -m training.capture.verify_export --live http://localhost:5001
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from typing import List, Optional

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from training.curate.normalize import extract_assistant_json, normalize_record
from training.curate.scrub import scrub_record
from training.curate.validate import validate_record

logger = logging.getLogger("verify_export")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

DEFAULT_PATH = os.path.join(_REPO_ROOT, "training_data", "workflows", "plan_to_commands.jsonl")


def _read_tail(path: str, n: int) -> List[dict]:
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, "r", encoding="utf-8") as f:
        lines = [ln.strip() for ln in f if ln.strip()]
    tail = lines[-n:] if n > 0 else lines
    out = []
    for ln in tail:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            out.append({"_raw": ln, "_parse_error": True})
    return out


def _check(record: dict) -> dict:
    if record.get("_parse_error"):
        return {"ok": False, "error": "line does not parse as JSON"}
    norm = normalize_record(record)
    if norm is None:
        return {"ok": False, "error": "failed normalization (no user/assistant or no JSON block)"}
    scrub_record(norm)
    ok, details = validate_record(norm)
    details["ok"] = ok
    return details


def run_offline(path: str, tail: int) -> int:
    records = _read_tail(path, tail)
    if not records:
        logger.error("No records found in %s", path)
        return 2
    logger.info("Checking last %d record(s) in %s", len(records), path)
    failures = 0
    for i, r in enumerate(records, start=1):
        result = _check(r)
        if result.get("ok"):
            logger.info(
                "  [%d/%d] OK  n_commands=%s compile=%s rule_warnings=%d",
                i,
                len(records),
                result.get("n_commands"),
                result.get("compile_ok"),
                len(result.get("rule_warnings", [])),
            )
        else:
            failures += 1
            logger.error("  [%d/%d] FAIL  %s", i, len(records), result)
    if failures:
        logger.error("%d/%d record(s) failed", failures, len(records))
        return 1
    logger.info("All %d inspected record(s) passed.", len(records))
    return 0


def run_watch(path: str, seconds: int) -> int:
    start = _file_size_lines(path)
    start_time = time.time()
    logger.info(
        "Watching %s for %ds. Starting count: %d line(s). Click Export Training now.",
        path,
        seconds,
        start,
    )
    last_count = start
    while time.time() - start_time < seconds:
        time.sleep(1)
        now = _file_size_lines(path)
        if now != last_count:
            logger.info("  line count changed: %d -> %d", last_count, now)
            last_count = now
    delta = last_count - start
    logger.info("Done. Net change: +%d line(s). Total now: %d.", delta, last_count)
    if delta > 0:
        # Validate the newly-added tail.
        return run_offline(path, delta)
    logger.warning("No new records written during the watch window.")
    return 1


def run_live(base_url: str, path: str, tail: int) -> int:
    try:
        import requests  # noqa: WPS433
    except ImportError:
        logger.error("`requests` not installed in this env")
        return 2
    url = base_url.rstrip("/") + "/api/workflow/builder/training-stats"
    try:
        resp = requests.get(url, timeout=10)
    except Exception as exc:  # noqa: BLE001
        logger.error("GET %s failed: %s", url, exc)
        return 2
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        logger.error("response was not JSON: %s", resp.text[:300])
        return 2
    logger.info("Live stats: %s", json.dumps(data, indent=2))
    # Also check the local file if accessible.
    if os.path.exists(path):
        return run_offline(path, tail)
    return 0


def _file_size_lines(path: str) -> int:
    if not os.path.exists(path):
        return 0
    with open(path, "r", encoding="utf-8") as f:
        return sum(1 for _ in f)


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify Export Training writes valid data.")
    ap.add_argument("--path", default=DEFAULT_PATH, help="plan_to_commands.jsonl location")
    ap.add_argument("--tail", type=int, default=5, help="offline mode: check last N records")
    ap.add_argument("--watch", type=int, default=0, help="watch mode: monitor file for N seconds")
    ap.add_argument("--live", default=None, help="live mode: base URL of running AI Hub")
    args = ap.parse_args()

    if args.live:
        return run_live(args.live, args.path, args.tail)
    if args.watch > 0:
        return run_watch(args.path, args.watch)
    return run_offline(args.path, args.tail)


if __name__ == "__main__":
    raise SystemExit(main())
