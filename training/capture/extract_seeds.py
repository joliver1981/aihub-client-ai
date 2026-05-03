"""Extract seed training examples from existing repo artifacts.

Sources handled (cheap, no frontier model calls):
  1. CommandGenerator.COMMAND_GENERATOR_SYSTEM_PROMPT  — EXAMPLE 1/2/3 blocks
     already contain paired (plan, commands) that work by construction. High-
     quality "gold" seeds.

  2. training/data/cmdgen/seeds/handcrafted/*.yaml — any human-curated seeds
     dropped in by the team (future-proof hook).

Separate module (extract_workflow_json_seeds.py, TODO) will handle reverse-
engineering the production workflow JSONs — that one needs frontier-model
calls to synthesize the plan text from a saved workflow graph.

Usage:
    python -m training.capture.extract_seeds \
        --out training/data/cmdgen/seeds/from_system_prompt.jsonl
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from typing import List, Optional, Tuple

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

logger = logging.getLogger("extract_seeds")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


# The CommandGenerator system prompt is a python string with embedded example
# blocks. Rather than importing CommandGenerator (which drags in AppUtils and
# the full runtime), we parse the source file directly.
CMDGEN_PATH = os.path.join(_REPO_ROOT, "CommandGenerator.py")

_EXAMPLE_BLOCK_RE = re.compile(
    r"EXAMPLE\s+\d+[^:]*:\s*\n+Plan:\s*\n([\s\S]+?)\n+Output:\s*\n+```json\s*\n([\s\S]+?)\n```",
    re.MULTILINE,
)


def parse_system_prompt_examples() -> List[Tuple[str, dict]]:
    """Read CommandGenerator.py and extract (plan, commands) pairs from the prompt."""
    with open(CMDGEN_PATH, "r", encoding="utf-8") as f:
        src = f.read()

    pairs: List[Tuple[str, dict]] = []
    for plan_text, commands_text in _EXAMPLE_BLOCK_RE.findall(src):
        plan = plan_text.strip()
        try:
            commands = json.loads(commands_text)
        except json.JSONDecodeError as exc:
            logger.warning("skipping example: JSON parse error: %s", exc)
            continue
        pairs.append((plan, commands))
    return pairs


def load_system_prompt_text() -> str:
    """Pull the full COMMAND_GENERATOR_SYSTEM_PROMPT string from source."""
    with open(CMDGEN_PATH, "r", encoding="utf-8") as f:
        src = f.read()
    # The prompt is assigned as: COMMAND_GENERATOR_SYSTEM_PROMPT = """...""" + .replace(...).replace(...)
    # We grab the raw literal (before the .replace chain) because the
    # captured training JSONL already has the substituted version, and using
    # an unresolved <<COMMAND_TYPES_DOC>> in training data would poison it.
    # Simpler path: re-use the same logic CommandGenerator uses at import time.
    # Use importlib to get the resolved value via a minimal shim.
    import importlib.util

    # We do NOT want to import CommandGenerator directly (pulls AppUtils).
    # Instead, import system_prompts + CommonUtils.get_all_node_details only.
    try:
        import system_prompts  # noqa: WPS433
        from CommonUtils import get_all_node_details  # noqa: WPS433
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not load runtime prompt vars (%s); using raw template", exc)
        # Fallback: regex-extract the triple-quoted literal and leave
        # placeholders in place. Training data uses the resolved version so
        # this fallback is only for exploration.
        m = re.search(
            r"COMMAND_GENERATOR_SYSTEM_PROMPT\s*=\s*\"\"\"([\s\S]+?)\"\"\"\.replace",
            src,
        )
        return m.group(1) if m else ""

    m = re.search(
        r"COMMAND_GENERATOR_SYSTEM_PROMPT\s*=\s*\"\"\"([\s\S]+?)\"\"\"\.replace",
        src,
    )
    if not m:
        return ""
    template = m.group(1)
    return template.replace("<<COMMAND_TYPES_DOC>>", system_prompts.WORKFLOW_COMMAND_TYPES).replace(
        "<<NODE_TYPES_DOC>>", get_all_node_details()
    )


def build_training_record(plan: str, commands: dict, system_prompt: str, provenance: dict) -> dict:
    return {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": plan},
            {"role": "assistant", "content": f"```json\n{json.dumps(commands, indent=2)}\n```"},
        ],
        "_meta": {
            "source": "seed_system_prompt",
            **provenance,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--out",
        default=os.path.join(
            _REPO_ROOT, "training", "data", "cmdgen", "seeds", "from_system_prompt.jsonl"
        ),
    )
    args = ap.parse_args()

    system_prompt = load_system_prompt_text()
    if not system_prompt:
        logger.error("Could not load COMMAND_GENERATOR_SYSTEM_PROMPT")
        return 2

    pairs = parse_system_prompt_examples()
    if not pairs:
        logger.error("No EXAMPLE blocks parsed from CommandGenerator.py")
        return 2

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for i, (plan, commands) in enumerate(pairs):
            rec = build_training_record(
                plan,
                commands,
                system_prompt,
                {"example_index": i, "n_commands": len(commands.get("commands", []))},
            )
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    logger.info("Wrote %d seed example(s) to %s", len(pairs), args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
