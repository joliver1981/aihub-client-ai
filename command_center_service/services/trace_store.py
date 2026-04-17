"""Command Center Service — Trace Store

File-based execution traces for observability/debugging.

Design goals:
- No new SQL tables.
- Append-only event log for reliability.
- One file per user message (trace_id) => no concurrent writers.

Trace format: JSON Lines (one JSON object per line).
"""

from __future__ import annotations

import json
import uuid
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TraceMeta:
    trace_id: str
    user_id: str
    session_id: str
    user_message: str
    created_at: str


class TraceStore:
    def __init__(self, data_dir: Path):
        self.base_dir = Path(data_dir) / "traces"
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _trace_dir(self, user_id: str, session_id: str) -> Path:
        # Keep as strings to support anon/unknown.
        safe_user = str(user_id) if user_id is not None else "anon"
        safe_sess = str(session_id) if session_id is not None else "unknown"
        d = self.base_dir / safe_user / safe_sess
        d.mkdir(parents=True, exist_ok=True)
        return d

    def start_trace(
        self,
        *,
        user_id: Optional[Any],
        session_id: str,
        user_message: str,
        user_context: Optional[dict] = None,
        system_prompts: Optional[dict] = None,
    ) -> TraceMeta:
        trace_id = str(uuid.uuid4())
        created_at = _utc_now_iso()
        uid = str(user_id) if user_id is not None else "anon"
        meta = TraceMeta(
            trace_id=trace_id,
            user_id=uid,
            session_id=session_id,
            user_message=user_message,
            created_at=created_at,
        )

        self.log_event(
            meta,
            event_type="trace_start",
            node="/api/chat",
            payload={
                "user_message": user_message,
                "user_context": user_context,
                "system_prompts": system_prompts,
            },
        )
        return meta

    def _trace_file(self, meta: TraceMeta) -> Path:
        return self._trace_dir(meta.user_id, meta.session_id) / f"{meta.trace_id}.jsonl"

    def log_event(
        self,
        meta: TraceMeta,
        *,
        event_type: str,
        node: str,
        payload: Optional[dict] = None,
        level: str = "info",
        summary: Optional[str] = None,
    ):
        evt = {
            "ts": _utc_now_iso(),
            "level": level,
            "trace_id": meta.trace_id,
            "user_id": meta.user_id,
            "session_id": meta.session_id,
            "event_type": event_type,
            "node": node,
        }
        if summary is not None:
            evt["summary"] = summary
        if payload is not None:
            evt["payload"] = payload

        path = self._trace_file(meta)
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(evt, default=str, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"Failed to write trace event {event_type} to {path}: {e}")

    def read_trace(self, *, user_id: str, session_id: str, trace_id: str) -> list[dict]:
        path = self._trace_dir(user_id, session_id) / f"{trace_id}.jsonl"
        if not path.exists():
            return []
        events: list[dict] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except Exception:
                        continue
        except Exception as e:
            logger.warning(f"Failed to read trace {path}: {e}")
        return events

    def list_traces(self, *, user_id: str, session_id: str, limit: int = 50) -> list[dict]:
        d = self._trace_dir(user_id, session_id)
        files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        result: list[dict] = []
        for p in files[: max(1, limit)]:
            trace_id = p.stem
            # Read first line for metadata.
            created_at = None
            user_message = None
            try:
                with open(p, "r", encoding="utf-8") as f:
                    first = f.readline().strip()
                if first:
                    j = json.loads(first)
                    created_at = j.get("ts")
                    payload = (j.get("payload") or {})
                    user_message = payload.get("user_message")
            except Exception:
                pass
            result.append({
                "trace_id": trace_id,
                "created_at": created_at,
                "user_message": user_message,
                "path": str(p),
            })
        return result
