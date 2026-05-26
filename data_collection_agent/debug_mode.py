"""
Debug mode — per-session in-memory event ring buffers.

Gives the runtime visibility into the LLM-driven flow:

    - System prompt sent to the agent each turn
    - User message
    - Tool calls + arguments + return values
    - Voice-normalizer LLM calls (input transcript, output value, model)
    - Final agent response + metadata

The frontend test-mode overlay reads from this buffer and renders a per-turn
expandable inspection panel so you can see exactly why the agent decided
what it decided, without grepping logs.

Storage: in-memory dict keyed by session_id, capped to MAX_EVENTS per
session (oldest events drop). Process-local, not persisted across
restarts. Event timestamps are server-side ISO-8601.

Gating: this module only emits events when debug mode is on. Turn it on
via:
    1. Env var DCA_DEBUG_MODE=True
    2. OR DATA_COLLECTION_TEST_MODE=True (test mode implies debug —
       keeps developer ergonomics simple)
"""

import json
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger("DataCollectionAgent.Debug")

# How many events to keep per session (oldest dropped beyond this).
# Each event is small (a dict + ISO timestamp), so this is fine in memory.
MAX_EVENTS_PER_SESSION = 500

# event_type -> short purpose
KNOWN_EVENT_TYPES = {
    'turn_start':         'A user message arrived and the agent is about to process it',
    'system_prompt':      'The full system prompt the agent will see this turn',
    'user_message':       'The user message text',
    'llm_response':       'The agent\'s final text response (post tool calls)',
    'tool_call':          'A tool call from the agent (name, args)',
    'tool_result':        'The tool\'s return value',
    'voice_normalize':    'A voice-normalizer LLM call (input/output)',
    'extract_call':       'Pre-agent field extractor was invoked with the user message',
    'extract_step':       'Intermediate extractor step (client_ready, prompt_built, llm_attempts, llm_raw_response)',
    'extract_result':     'Pre-agent field extractor finished — what was returned + applied',
    'turn_end':           'Turn complete with metadata',
    'error':              'An error during processing',
    'note':               'Free-form developer note',
}


_buffers: Dict[str, Deque[Dict[str, Any]]] = {}
_lock = threading.Lock()


def is_enabled() -> bool:
    """Debug mode is on when either DCA_DEBUG_MODE is set OR test mode is
    on. We delegate the test-mode check to `identity.is_test_mode()` so
    both env-var AND platform-config sources are honored — keeps the
    debug button visible in every environment where test mode is."""
    if os.getenv('DCA_DEBUG_MODE', '').strip().lower() in ('1', 'true', 'yes', 'on'):
        return True
    try:
        from .identity import is_test_mode
        if is_test_mode():
            return True
    except Exception:
        pass
    return False


def _record(session_id: str, event_type: str, payload: Dict[str, Any]) -> None:
    if not session_id:
        return
    event = {
        'ts':      datetime.now(timezone.utc).isoformat(),
        'ts_ms':   int(time.time() * 1000),
        'type':    event_type,
        'payload': payload,
    }
    with _lock:
        buf = _buffers.get(session_id)
        if buf is None:
            buf = deque(maxlen=MAX_EVENTS_PER_SESSION)
            _buffers[session_id] = buf
        buf.append(event)


def log_event(session_or_id: Any, event_type: str, payload: Dict[str, Any]) -> None:
    """
    Public entry point. Accepts either a session object (with `.session_id`)
    or a session_id string. No-op when debug mode is disabled.

    Always wrapped in a try/except by the convenience helper `debug_log`
    below — debug should NEVER break a real turn.
    """
    if not is_enabled():
        return
    if event_type not in KNOWN_EVENT_TYPES:
        # Allow unknown types but log a developer warning so we catch
        # typos like 'tool_results' (plural) early.
        logger.debug("debug_mode.log_event: unknown event_type %r", event_type)

    sid = getattr(session_or_id, 'session_id', None) or session_or_id
    if not isinstance(sid, str):
        return
    _record(sid, event_type, payload or {})


def debug_log(session_or_id: Any, event_type: str, payload: Dict[str, Any]) -> None:
    """
    Convenience wrapper that swallows ANY exception. Use this from inside
    agent / tools / normalizer code so a bad payload doesn't crash a turn.
    """
    if not is_enabled():
        return
    try:
        log_event(session_or_id, event_type, payload)
    except Exception as e:
        logger.debug("debug_log swallowed exception: %s", e)


def get_events(session_id: str, since_ms: Optional[int] = None,
               types: Optional[List[str]] = None,
               limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Read events for a session. Optional filters:
      - since_ms: only return events with ts_ms > since_ms (for polling)
      - types:   only include these event types
      - limit:   cap the returned list (most recent first if applied)
    Returns events in chronological order (oldest first).
    """
    with _lock:
        buf = _buffers.get(session_id)
        events = list(buf) if buf else []

    if since_ms is not None:
        events = [e for e in events if e.get('ts_ms', 0) > since_ms]
    if types:
        type_set = set(types)
        events = [e for e in events if e.get('type') in type_set]
    if limit is not None and limit > 0 and len(events) > limit:
        events = events[-limit:]
    return events


def clear_events(session_id: Optional[str] = None) -> None:
    """Drop the buffer for a session (or all sessions when session_id is None)."""
    with _lock:
        if session_id:
            _buffers.pop(session_id, None)
        else:
            _buffers.clear()


def truncate_for_display(value: Any, max_len: int = 4000) -> Any:
    """Helper: truncate large strings/JSON for the inspection panel so we
    don't ship megabytes of context."""
    if isinstance(value, str):
        if len(value) > max_len:
            return value[:max_len] + f'...(+{len(value) - max_len} chars)'
        return value
    try:
        s = json.dumps(value, default=str)
    except Exception:
        s = str(value)
    if len(s) > max_len:
        return s[:max_len] + f'...(+{len(s) - max_len} chars)'
    return value
