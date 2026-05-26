"""Command Center — Data Provenance (WS4)
============================================

Stamps every enriched field with its source so the UI can render it as
such and the audit-trail walkback works.

Schema (v1)
-----------
Sibling-map approach: every artifact / data block that carries enriched
fields gets a parallel ``_provenance`` map keyed by *field path* (dot
notation for nested fields, ``[i]`` index syntax for list items). The
original values stay flat so existing renderers and tests are unchanged.

Example::

    {
        "type": "map",
        "center": [40.71, -74.0],
        "markers": [{"lat": 40.71, "lng": -74.0, "label": "NYC"}],
        "_provenance": {
            "markers[0].lat": {
                "source": "geocoder",
                "source_url": null,
                "source_detail": "nominatim",
                "timestamp": "2026-05-10T15:23:00+00:00",
                "confidence": 0.97,
                "notes": null
            },
            "markers[0].lng": { ... }
        }
    }

See ``docs/data-provenance.md`` for the full contract.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


# Closed vocabulary for the `source` field. Renderers map these to badge
# colors in ops-room.js; keep new entries in sync there.
SOURCE_DB = "db"
SOURCE_WEB_SEARCH = "web_search"
SOURCE_GEOCODER = "geocoder"
SOURCE_MODEL_KNOWLEDGE = "model_knowledge"
SOURCE_USER_INPUT = "user_input"
SOURCE_SYSTEM = "system"
SOURCE_UNKNOWN = "unknown"

VALID_SOURCES = frozenset({
    SOURCE_DB,
    SOURCE_WEB_SEARCH,
    SOURCE_GEOCODER,
    SOURCE_MODEL_KNOWLEDGE,
    SOURCE_USER_INPUT,
    SOURCE_SYSTEM,
    SOURCE_UNKNOWN,
})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProvenanceEntry(BaseModel):
    """One provenance record for a single field.

    The schema is intentionally narrow so the JSON payload stays small —
    bulk evidence (raw API responses, full LLM transcripts) belongs in
    the trace store, not here.
    """

    model_config = ConfigDict(extra="forbid")

    source: str = Field(..., description="One of VALID_SOURCES.")
    source_url: Optional[str] = None
    source_detail: Optional[str] = Field(
        default=None,
        description='Short tag — e.g. "nominatim", "tavily", "Postgres `customers` table".',
    )
    timestamp: str = Field(default_factory=_now_iso, description="ISO 8601 UTC.")
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    notes: Optional[str] = None

    def model_post_init(self, __context: Any) -> None:  # pragma: no cover - trivial
        if self.source not in VALID_SOURCES:
            # Accept but downgrade — better than blowing up the agent turn.
            object.__setattr__(self, "source", SOURCE_UNKNOWN)


class Provenance:
    """Convenience wrapper around ``dict[str, ProvenanceEntry]``.

    Keyed by field path; methods deliberately small so call sites stay
    readable. Not thread-safe — it's expected to live for a single agent
    turn and get serialized into the block JSON at the end.
    """

    def __init__(self, entries: Optional[Dict[str, ProvenanceEntry]] = None):
        self._entries: Dict[str, ProvenanceEntry] = dict(entries or {})

    # -- Mutators ---------------------------------------------------------

    def set(self, path: str, entry: ProvenanceEntry) -> None:
        if not isinstance(entry, ProvenanceEntry):
            raise TypeError(f"entry must be ProvenanceEntry, got {type(entry)!r}")
        self._entries[path] = entry

    def stamp(
        self,
        path: str,
        *,
        source: str,
        source_url: Optional[str] = None,
        source_detail: Optional[str] = None,
        confidence: Optional[float] = None,
        notes: Optional[str] = None,
        timestamp: Optional[str] = None,
    ) -> ProvenanceEntry:
        """Construct + store an entry in one call. Returns the entry."""
        entry = ProvenanceEntry(
            source=source,
            source_url=source_url,
            source_detail=source_detail,
            timestamp=timestamp or _now_iso(),
            confidence=confidence,
            notes=notes,
        )
        self.set(path, entry)
        return entry

    def downgrade_confidence(self, path: str, new_confidence: float, note: str) -> None:
        """Lower confidence on an existing entry and append a note.

        Used by the fabrication check. No-op if the path isn't already
        stamped — fabrication detection shouldn't *introduce* provenance.
        """
        existing = self._entries.get(path)
        if existing is None:
            return
        old_note = existing.notes
        merged_note = f"{old_note}; {note}" if old_note else note
        # Only lower, never raise.
        new_conf = min(existing.confidence, new_confidence) if existing.confidence is not None else new_confidence
        self._entries[path] = existing.model_copy(update={
            "confidence": new_conf,
            "notes": merged_note,
        })

    def merge(self, other: "Provenance") -> None:
        """Merge another Provenance into self. Later writers win."""
        if not isinstance(other, Provenance):
            raise TypeError(f"merge target must be Provenance, got {type(other)!r}")
        self._entries.update(other._entries)

    # -- Accessors --------------------------------------------------------

    def get(self, path: str) -> Optional[ProvenanceEntry]:
        return self._entries.get(path)

    def paths(self):  # pragma: no cover - trivial
        return list(self._entries.keys())

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self._entries)

    def __contains__(self, path: str) -> bool:  # pragma: no cover - trivial
        return path in self._entries

    # -- Serialization ----------------------------------------------------

    def to_dict(self) -> Dict[str, Dict[str, Any]]:
        """Serialize for embedding in a block as the ``_provenance`` sibling map."""
        return {k: v.model_dump(mode="json", exclude_none=False) for k, v in self._entries.items()}

    @classmethod
    def from_dict(cls, raw: Optional[Dict[str, Dict[str, Any]]]) -> "Provenance":
        if not raw:
            return cls()
        out: Dict[str, ProvenanceEntry] = {}
        for path, entry_dict in raw.items():
            if not isinstance(entry_dict, dict):
                continue
            try:
                out[path] = ProvenanceEntry.model_validate(entry_dict)
            except Exception:
                # Skip malformed entries rather than fail the whole load.
                continue
        return cls(out)


# ---------------------------------------------------------------------------
# Helpers for stamping common block shapes
# ---------------------------------------------------------------------------

def attach_to_block(block: Dict[str, Any], prov: Provenance) -> None:
    """Attach (or merge into) the ``_provenance`` sibling map on a block dict.

    Renderers and tests that don't know about provenance treat the key as
    opaque — that's the point of the sibling-map approach.
    """
    if not isinstance(block, dict):
        return
    existing = Provenance.from_dict(block.get("_provenance"))
    existing.merge(prov)
    block["_provenance"] = existing.to_dict()
