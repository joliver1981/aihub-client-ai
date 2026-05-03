"""Deduplicate training records.

Uses the structural hash computed in normalize.record_hash. Two records are
considered duplicates if they have the same hash — meaning the same plan
text (case-insensitive, whitespace-trimmed) AND the same structural command
signature (type + node_type + node_id sequence).

This is deliberately NOT over-dedupe on plan text alone: the same workflow
built from different user phrasings is *valuable* training signal and must
be kept. But exact replays of the same (plan, commands) pair from an export
button double-click add noise.
"""

from __future__ import annotations

from typing import Iterable, Iterator


def dedupe_iter(records: Iterable[dict]) -> Iterator[dict]:
    """Yield records with duplicate hashes removed (first-seen kept)."""
    seen: set = set()
    for record in records:
        h = record.get("_meta", {}).get("hash")
        if h is None:
            yield record
            continue
        if h in seen:
            continue
        seen.add(h)
        yield record
