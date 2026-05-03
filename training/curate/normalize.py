"""Normalize raw training JSONL records.

A record is the OpenAI chat format:
    {"messages": [
        {"role": "system",    "content": <full COMMAND_GENERATOR_SYSTEM_PROMPT>},
        {"role": "user",      "content": <natural-language plan>},
        {"role": "assistant", "content": <```json\\n{...}\\n```>}
    ],
     "_meta": {...optional provenance fields...}
    }

Normalization steps:
  1. Parse the assistant JSON block; drop records whose JSON is unrecoverable.
  2. Re-serialize with stable formatting (json.dumps sort_keys=False, ensure_ascii=False).
  3. Trim the user plan (leading/trailing whitespace, collapse 3+ blank lines).
  4. Attach a stable record hash for downstream dedupe.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Optional

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.DOTALL)
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def extract_assistant_json(assistant_text: str) -> Optional[dict]:
    """Extract the first JSON object from an assistant message.

    Tries fenced ```json blocks first, then bare JSON between first { and last }.
    Returns the parsed dict or None if nothing parses cleanly.
    """
    if not assistant_text:
        return None

    for match in _JSON_BLOCK_RE.findall(assistant_text):
        try:
            return json.loads(match)
        except json.JSONDecodeError:
            continue

    start = assistant_text.find("{")
    end = assistant_text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(assistant_text[start : end + 1])
        except json.JSONDecodeError:
            return None

    return None


def stable_commands_json(commands: dict) -> str:
    """Re-serialize commands dict for stable training format."""
    return json.dumps(commands, ensure_ascii=False, indent=2)


def normalize_plan(plan_text: str) -> str:
    """Trim and normalize whitespace in a plan."""
    if not plan_text:
        return ""
    stripped = plan_text.strip()
    return _BLANK_LINES_RE.sub("\n\n", stripped)


def record_hash(plan: str, commands: dict) -> str:
    """Stable hash for dedupe: plan text + structural commands signature.

    Structural = list of (type, node_type) tuples for each command, so two
    workflows that differ only in label text / positions are not falsely
    considered distinct.
    """
    structural = []
    for cmd in commands.get("commands", []):
        structural.append((cmd.get("type"), cmd.get("node_type", ""), cmd.get("node_id", "")))
    blob = json.dumps({"plan": plan.strip().lower(), "commands": structural}, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def normalize_record(record: dict) -> Optional[dict]:
    """Apply all normalization steps. Returns None if record is unrecoverable."""
    messages = record.get("messages", [])
    if len(messages) < 3:
        return None

    # Accept records where messages may be [system, user, assistant] or have
    # more turns; we only care about the last user and last assistant.
    user_msg = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    asst_msg = next((m for m in reversed(messages) if m.get("role") == "assistant"), None)
    if not user_msg or not asst_msg:
        return None

    plan = normalize_plan(user_msg.get("content", ""))
    commands = extract_assistant_json(asst_msg.get("content", ""))
    if commands is None or "commands" not in commands:
        return None

    # Rewrite the assistant content with the re-serialized JSON block.
    asst_msg["content"] = f"```json\n{stable_commands_json(commands)}\n```"
    user_msg["content"] = plan

    meta = dict(record.get("_meta") or {})
    meta["hash"] = record_hash(plan, commands)
    meta.setdefault("n_commands", len(commands.get("commands", [])))

    return {"messages": messages, "_meta": meta}


def iter_normalized(path: str):
    """Yield normalized records from a JSONL file. Logs dropped records."""
    dropped = 0
    total = 0
    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                dropped += 1
                continue
            norm = normalize_record(raw)
            if norm is None:
                dropped += 1
                continue
            yield norm
    # Caller can inspect these via the module-level counters if desired
    iter_normalized.last_total = total
    iter_normalized.last_dropped = dropped


iter_normalized.last_total = 0
iter_normalized.last_dropped = 0
