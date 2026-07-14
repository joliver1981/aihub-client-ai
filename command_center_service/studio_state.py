"""
Studio state — the tiny seam that lets the Automation Studio panel react to
what the CC agent is doing WITHOUT parsing chat text or touching the agent.

The automation tool wrappers in graph/nodes.py call update() as they act
(create → save → dry-run → promote → run/schedule); the frontend polls
GET /api/studio/state and renders. In-memory by design: the CC service runs
single-process, the state is a pure UI hint (losing it on restart costs
nothing — the panel refetches authoritative data from the automations API),
and no DB/table churn is warranted for a hint.
"""

import threading
import time
from typing import Any, Dict, Optional

_TTL_SECONDS = 3600
_MAX_SESSIONS = 500

_lock = threading.Lock()
_state: Dict[str, Dict[str, Any]] = {}

# Build phases in rail order — the frontend renders these as the stepper.
PHASES = ["gather", "create", "code", "dry_run", "confirm", "promote", "live"]


def update(session_id: str, **fields):
    """Merge fields into the session's studio state and bump its version so
    the poller knows something changed. Never raises."""
    if not session_id:
        return
    try:
        with _lock:
            now = time.time()
            entry = _state.get(session_id) or {"version": 0, "created": now}
            entry.update(fields)
            entry["version"] += 1
            entry["updated"] = now
            _state[session_id] = entry
            if len(_state) > _MAX_SESSIONS:
                _evict_locked(now)
    except Exception:
        pass


def get(session_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        entry = _state.get(session_id)
        if not entry:
            return None
        if time.time() - entry.get("updated", 0) > _TTL_SECONDS:
            _state.pop(session_id, None)
            return None
        return dict(entry)


def clear(session_id: str):
    with _lock:
        _state.pop(session_id, None)


def _evict_locked(now: float):
    stale = [k for k, v in _state.items() if now - v.get("updated", 0) > _TTL_SECONDS]
    for k in stale:
        _state.pop(k, None)
    while len(_state) > _MAX_SESSIONS:
        oldest = min(_state, key=lambda k: _state[k].get("updated", 0))
        _state.pop(oldest, None)
