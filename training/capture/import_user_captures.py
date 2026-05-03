"""Import a third-party plan_to_commands.jsonl into a task's seeds folder.

Use this when real-user-curated training data lives outside the main repo
(e.g. captured from the standalone trainer app). The script:

  1. Reads each record.
  2. Runs the standard normalize -> scrub pipeline.
  3. Validates against schema + compile gates; drops failures.
  4. Dedupes by structural hash (within this file only).
  5. Tags _meta.source = "real_user_capture" with provenance fields.
  6. Writes to training/data/<task>/seeds/from_real_users.jsonl.

Example:
    python -m training.capture.import_user_captures \
        --task cmdgen_v3 \
        --input C:/src/aihub-trainer/training_data/workflows/plan_to_commands.jsonl
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

from training.curate.normalize import normalize_record
from training.curate.scrub import scrub_record
from training.curate.validate import validate_record

logger = logging.getLogger("import_user_captures")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--input", required=True, help="Path to plan_to_commands.jsonl from external app")
    ap.add_argument("--out", default=None, help="Default: training/data/<task>/seeds/from_real_users.jsonl")
    ap.add_argument("--source-tag", default="real_user_capture")
    ap.add_argument("--label", default=None, help="Free-text label kept in _meta.import_label for traceability")
    args = ap.parse_args()

    if not os.path.exists(args.input):
        logger.error("input not found: %s", args.input)
        return 2

    out_path = args.out or os.path.join(
        _REPO_ROOT, "training", "data", args.task, "seeds", "from_real_users.jsonl"
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    seen: set = set()
    written = 0
    dropped_norm = 0
    dropped_validate = 0
    dropped_dupe = 0
    scrub_totals: Dict[str, int] = {}

    with open(args.input, "r", encoding="utf-8") as fin, open(out_path, "w", encoding="utf-8") as fout:
        for line_no, line in enumerate(fin, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                dropped_norm += 1
                continue
            norm = normalize_record(raw)
            if norm is None:
                dropped_norm += 1
                continue
            scrubbed, scrubs = scrub_record(norm)
            for k, v in scrubs.items():
                scrub_totals[k] = scrub_totals.get(k, 0) + v

            ok, details = validate_record(scrubbed)
            if not ok:
                dropped_validate += 1
                logger.warning("rec %d validate fail: %s", line_no, details.get("schema_errors") or details.get("compile_error"))
                continue

            h = scrubbed.get("_meta", {}).get("hash")
            if h and h in seen:
                dropped_dupe += 1
                continue
            if h:
                seen.add(h)

            meta = scrubbed.setdefault("_meta", {})
            meta["source"] = args.source_tag
            meta["import_source_path"] = os.path.basename(args.input)
            meta["import_source_line"] = line_no
            if args.label:
                meta["import_label"] = args.label

            fout.write(json.dumps(scrubbed, ensure_ascii=False) + "\n")
            written += 1

    logger.info(
        "Imported: written=%d  dropped_normalize=%d  dropped_validate=%d  dropped_duplicate=%d",
        written,
        dropped_norm,
        dropped_validate,
        dropped_dupe,
    )
    if scrub_totals:
        logger.info("Scrub substitutions: %s", json.dumps(scrub_totals))
    logger.info("Wrote %s", out_path)
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
